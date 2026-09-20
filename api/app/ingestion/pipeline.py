"""Ingestion orchestration: parse -> clean -> chunk -> embed -> upsert.

Runs inside the arq worker, never in the request path. Each stage writes progress to
`ingest_jobs` so the UI can show where a document is, and any failure lands as
`documents.status='failed'` with the error text rather than vanishing into a log.

Each helper opens its own short session: the failure path must be able to record the
error even when the work session is in a bad state.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from langchain_core.documents import Document as LCDocument
from sqlalchemy import delete, select

from app.db import session_scope
from app.ingestion.chunking import chunk_documents
from app.ingestion.cleaning import clean_documents
from app.ingestion.parsers import parse
from app.models import Chunk, DocStatus, Document, IngestJob, SourceType
from app.vectorstore import get_vector_store


async def _start_job(document_id: uuid.UUID) -> uuid.UUID:
    async with session_scope() as session:
        job = IngestJob(document_id=document_id, stage=DocStatus.PARSING, progress_pct=0)
        session.add(job)
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = DocStatus.PARSING
        await session.commit()
        return job.id


async def _progress(
    document_id: uuid.UUID, job_id: uuid.UUID, stage: DocStatus, pct: int, message: str | None = None
) -> None:
    async with session_scope() as session:
        job = await session.get(IngestJob, job_id)
        if job is not None:
            job.stage = stage
            job.progress_pct = pct
            job.message = message
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = stage
        await session.commit()


async def _fail(document_id: uuid.UUID, job_id: uuid.UUID, exc: Exception) -> None:
    detail = f"{type(exc).__name__}: {exc}"
    async with session_scope() as session:
        job = await session.get(IngestJob, job_id)
        if job is not None:
            job.stage = DocStatus.FAILED
            job.message = detail
            job.finished_at = datetime.now(UTC)
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = DocStatus.FAILED
            doc.error = detail
        await session.commit()


async def _load_document(document_id: uuid.UUID) -> tuple[str, str, str] | None:
    """Return (title, source_type, source_uri) without holding the session open."""
    async with session_scope() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return None
        return doc.title, doc.source_type, doc.source_uri


async def _persist(document_id: uuid.UUID, job_id: uuid.UUID, chunks: list[LCDocument]) -> None:
    async with session_scope() as session:
        # Re-ingestion of the same document replaces its chunk rows.
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        session.add_all(
            [
                Chunk(
                    document_id=document_id,
                    ordinal=c.metadata["ordinal"],
                    text=c.page_content,
                    page_from=c.metadata.get("page_from"),
                    page_to=c.metadata.get("page_to"),
                    token_count=c.metadata.get("token_count", 0),
                    vector_id=c.metadata["vector_id"],
                )
                for c in chunks
            ]
        )
        doc = await session.get(Document, document_id)
        if doc is not None:
            doc.status = DocStatus.READY
            doc.chunk_count = len(chunks)
            doc.error = None
        job = await session.get(IngestJob, job_id)
        if job is not None:
            job.stage = DocStatus.READY
            job.progress_pct = 100
            job.finished_at = datetime.now(UTC)
        await session.commit()


async def run_ingestion(document_id: uuid.UUID) -> int:
    """Ingest one document end to end. Returns the number of chunks indexed."""
    loaded = await _load_document(document_id)
    if loaded is None:
        return 0
    title, source_type, source_uri = loaded

    job_id = await _start_job(document_id)
    try:
        # Parsing, chunking and embedding are all blocking/CPU-bound.
        raw = await asyncio.to_thread(parse, SourceType(source_type), source_uri)
        if not raw:
            raise ValueError("No extractable text found in this document")

        await _progress(document_id, job_id, DocStatus.CHUNKING, 35)
        cleaned = await asyncio.to_thread(clean_documents, raw)
        chunks = await asyncio.to_thread(chunk_documents, cleaned)
        if not chunks:
            raise ValueError("Document produced no usable chunks after cleaning")

        # Deterministic vector ids -- deletion walks this prefix (Pinecone serverless
        # has no delete-by-filter).
        for c in chunks:
            c.metadata["document_id"] = str(document_id)
            c.metadata["title"] = title
            c.metadata["vector_id"] = f"{document_id}#{c.metadata['ordinal']}"

        await _progress(document_id, job_id, DocStatus.EMBEDDING, 60)
        store = get_vector_store()
        ids = [c.metadata["vector_id"] for c in chunks]
        # Keep Pinecone metadata lean: the chunk text is stored under `text`, and the
        # bookkeeping fields below are what retrieval and the UI actually need.
        payload = [
            LCDocument(
                page_content=c.page_content,
                metadata={
                    "document_id": c.metadata["document_id"],
                    "title": c.metadata["title"],
                    "ordinal": c.metadata["ordinal"],
                    "page_from": c.metadata.get("page_from"),
                    "page_to": c.metadata.get("page_to"),
                },
            )
            for c in chunks
        ]
        await asyncio.to_thread(store.add_documents, payload, ids=ids)

        await _persist(document_id, job_id, chunks)
        return len(chunks)
    except Exception as exc:
        await _fail(document_id, job_id, exc)
        raise


async def delete_document_data(document_id: uuid.UUID) -> int:
    """Remove a document's vectors from Pinecone. Rows cascade from the DB delete."""
    store = get_vector_store()
    return await asyncio.to_thread(store.delete_document, str(document_id))


async def list_stale_documents() -> list[uuid.UUID]:
    """Documents left mid-flight by a worker restart."""
    async with session_scope() as session:
        rows = await session.execute(
            select(Document.id).where(
                Document.status.in_(
                    [DocStatus.PARSING, DocStatus.CHUNKING, DocStatus.EMBEDDING]
                )
            )
        )
        return list(rows.scalars())
