"""Pinecone vector store.

Implements LangChain's `VectorStore` interface over the official `pinecone` SDK.
We do not use `langchain-pinecone`: it caps at Python <3.14 and depends on
`langchain-openai`, pulling the OpenAI SDK into a project that never calls it.
Implementing the sync methods gives us the async variants (`asimilarity_search*`)
for free from the base class.

Two Pinecone serverless constraints drive the design:
  1. Deleting by metadata filter is not supported -- so vector IDs are deterministic
     (`{document_id}#{ordinal}`) and deletion lists that ID prefix.
  2. Metadata values must be str / number / bool / list[str] -- nulls are rejected,
     so `_clean_metadata` drops them.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from pinecone import Pinecone, ServerlessSpec

from app.config import settings
from app.embeddings import get_embeddings

TEXT_KEY = "text"
UPSERT_BATCH = 100


def _vector_id(item: Any) -> str:
    """Normalise one entry from `index.list()` to a plain id string."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item["id"]
    return item.id


def _clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Pinecone rejects null metadata values; drop them rather than sending nulls."""
    return {k: v for k, v in meta.items() if v is not None}


@lru_cache(maxsize=1)
def get_index():
    """Return the Pinecone index handle, creating the index if it doesn't exist."""
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is not set")

    pc = Pinecone(api_key=settings.pinecone_api_key)

    if not pc.has_index(settings.pinecone_index):
        pc.create_index(
            name=settings.pinecone_index,
            dimension=settings.embedding_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
        )
        # Index creation is asynchronous on Pinecone's side.
        for _ in range(60):
            if pc.describe_index(settings.pinecone_index).status.get("ready"):
                break
            time.sleep(1)

    index = pc.Index(settings.pinecone_index)

    # An index's dimension is immutable: catch a model/index mismatch at startup
    # rather than as a confusing upsert error later.
    actual = pc.describe_index(settings.pinecone_index).dimension
    if actual != settings.embedding_dim:
        raise RuntimeError(
            f"Pinecone index '{settings.pinecone_index}' has dimension {actual}, "
            f"but EMBEDDING_DIM is {settings.embedding_dim}. "
            "Create a new index or change the embedding model back."
        )
    return index


class PineconeStore(VectorStore):
    def __init__(self, index, embeddings: Embeddings, text_key: str = TEXT_KEY) -> None:
        self._index = index
        self._embeddings = embeddings
        self._text_key = text_key

    @property
    def embeddings(self) -> Embeddings:
        return self._embeddings

    # --- writes ---------------------------------------------------------------

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        texts = list(texts)
        if not texts:
            return []
        if ids is None:
            raise ValueError(
                "PineconeStore requires explicit ids: deletion relies on the "
                "'{document_id}#{ordinal}' prefix convention."
            )
        metadatas = metadatas or [{} for _ in texts]
        vectors = self._embeddings.embed_documents(texts)

        records = [
            {
                "id": vid,
                "values": vec,
                "metadata": _clean_metadata({**meta, self._text_key: text}),
            }
            for vid, vec, meta, text in zip(ids, vectors, metadatas, texts, strict=True)
        ]
        for i in range(0, len(records), UPSERT_BATCH):
            self._index.upsert(vectors=records[i : i + UPSERT_BATCH])
        return ids

    def add_documents(
        self, documents: list[Document], *, ids: list[str] | None = None, **kwargs: Any
    ) -> list[str]:
        return self.add_texts(
            [d.page_content for d in documents],
            [dict(d.metadata) for d in documents],
            ids=ids,
            **kwargs,
        )

    # --- reads ----------------------------------------------------------------

    def similarity_search_with_score(
        self, query: str, k: int = 4, filter: dict | None = None, **kwargs: Any
    ) -> list[tuple[Document, float]]:
        vector = self._embeddings.embed_query(query)
        res = self._index.query(
            vector=vector, top_k=k, include_metadata=True, filter=filter or None
        )
        out: list[tuple[Document, float]] = []
        for match in res.get("matches", []):
            meta = dict(match.get("metadata") or {})
            text = meta.pop(self._text_key, "")
            meta["vector_id"] = match["id"]
            out.append((Document(page_content=text, metadata=meta), float(match["score"])))
        return out

    def similarity_search(
        self, query: str, k: int = 4, filter: dict | None = None, **kwargs: Any
    ) -> list[Document]:
        return [d for d, _ in self.similarity_search_with_score(query, k, filter, **kwargs)]

    # --- deletes --------------------------------------------------------------

    def delete(self, ids: list[str] | None = None, **kwargs: Any) -> None:
        if ids:
            self._index.delete(ids=ids)

    def delete_document(self, document_id: str) -> int:
        """Delete every vector belonging to a document.

        Pinecone serverless has no delete-by-filter, so this walks the ID prefix.
        """
        deleted = 0
        for batch in self._index.list(prefix=f"{document_id}#"):
            # `list()` yields ListItem objects, not bare strings, and `delete()`
            # rejects anything that isn't a str.
            ids = [_vector_id(item) for item in batch]
            if ids:
                self._index.delete(ids=ids)
                deleted += len(ids)
        return deleted

    # --- required by the ABC --------------------------------------------------

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> PineconeStore:
        store = cls(get_index(), embedding)
        store.add_texts(texts, metadatas, ids=ids)
        return store


@lru_cache(maxsize=1)
def get_vector_store() -> PineconeStore:
    return PineconeStore(get_index(), get_embeddings())
