"""Rebuild Pinecone from Postgres.

Postgres mirrors every chunk's text, which makes the vector store reconstructible: this
re-embeds those chunks and upserts them into their owner's namespace. It is the recovery
path for a lost or corrupted index, and the repair for vectors written into the wrong
namespace -- which is what happens if the arq worker is still running code from before a
namespace change, since arq has no `--reload`.

    python scripts/reindex.py --all
    python scripts/reindex.py --document <uuid>
    python scripts/reindex.py --all --purge-default

`--purge-default` empties Pinecone's default namespace afterwards. Nothing this app
writes belongs there once documents are owned, so anything left in it is stranded: no
query will ever reach it and no delete will ever clean it up, but it still counts against
the storage quota.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from functools import partial

from langchain_core.documents import Document as LCDocument
from sqlalchemy import select

from app.auth import tenant_namespace
from app.config import settings
from app.db import session_scope
from app.models import Chunk, DocStatus, Document
from app.vectorstore import get_index, get_vector_store

DEFAULT_NAMESPACE = ""


async def _documents(document_id: uuid.UUID | None) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    async with session_scope() as session:
        query = select(Document).where(Document.status == DocStatus.READY)
        if document_id is not None:
            query = query.where(Document.id == document_id)
        rows = await session.execute(query.order_by(Document.created_at))
        return [(d.id, d.owner_id, d.title) for d in rows.scalars()]


async def _chunks(document_id: uuid.UUID) -> list[Chunk]:
    async with session_scope() as session:
        rows = await session.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
        )
        return list(rows.scalars())


async def reindex(document_id: uuid.UUID, owner_id: uuid.UUID, title: str) -> int:
    chunks = await _chunks(document_id)
    if not chunks:
        print(f"  {title}: no chunks in Postgres, skipping")
        return 0

    namespace = tenant_namespace(owner_id)
    store = get_vector_store()
    size = settings.ingest_batch_size

    for start in range(0, len(chunks), size):
        batch = chunks[start : start + size]
        payload = [
            LCDocument(
                page_content=c.text,
                metadata={
                    "document_id": str(document_id),
                    "title": title,
                    "ordinal": c.ordinal,
                    "page_from": c.page_from,
                    "page_to": c.page_to,
                },
            )
            for c in batch
        ]
        await asyncio.to_thread(
            partial(
                store.add_documents,
                payload,
                ids=[c.vector_id for c in batch],
                namespace=namespace,
            )
        )
        print(f"  {title}: {min(start + size, len(chunks))}/{len(chunks)} chunks", end="\r")

    print(f"  {title}: {len(chunks)} chunks -> namespace {namespace}")
    return len(chunks)


async def main(document_id: uuid.UUID | None, purge_default: bool) -> int:
    documents = await _documents(document_id)
    if not documents:
        print("nothing to re-index")
        return 1

    print(f"re-indexing {len(documents)} document(s)\n")
    total = 0
    for doc_id, owner_id, title in documents:
        total += await reindex(doc_id, owner_id, title)

    if purge_default:
        index = get_index()
        stranded = (
            index.describe_index_stats().get("namespaces", {}).get(DEFAULT_NAMESPACE)
            # The console and the API disagree on the name of the unnamed namespace.
            or index.describe_index_stats().get("namespaces", {}).get("__default__")
        )
        count = (stranded or {}).get("vector_count", 0)
        if count:
            await asyncio.to_thread(
                partial(index.delete, delete_all=True, namespace=DEFAULT_NAMESPACE)
            )
            print(f"\npurged {count} stranded vector(s) from the default namespace")
        else:
            print("\ndefault namespace is already empty")

    print(f"\ndone: {total} chunk(s) re-embedded")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="every ready document")
    group.add_argument("--document", help="one document id")
    ap.add_argument(
        "--purge-default",
        action="store_true",
        help="empty Pinecone's default namespace when finished",
    )
    args = ap.parse_args()

    target = uuid.UUID(args.document) if args.document else None
    sys.exit(asyncio.run(main(target, args.purge_default)))
