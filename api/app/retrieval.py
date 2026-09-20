"""Query -> ranked chunks.

The BGE query prefix lives inside `BGEFastEmbedEmbeddings.embed_query`, so callers
here get the asymmetric-embedding handling for free.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

from langchain_core.documents import Document

from app.config import settings
from app.vectorstore import get_vector_store


@lru_cache(maxsize=1)
def _get_reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(
        model_name=settings.rerank_model, cache_dir=str(settings.model_cache_dir)
    )


def _rerank(query: str, docs: list[Document], top_n: int) -> list[Document]:
    scores = list(_get_reranker().rerank(query, [d.page_content for d in docs]))
    ranked = sorted(zip(docs, scores, strict=True), key=lambda p: p[1], reverse=True)
    return [d for d, _ in ranked[:top_n]]


async def retrieve(
    query: str,
    document_ids: list[str] | None = None,
    k: int | None = None,
    top_n: int | None = None,
) -> list[Document]:
    """Return the chunks to put in front of the model, best first."""
    k = k or settings.retrieve_k
    top_n = top_n or settings.context_k

    store = get_vector_store()
    filter_ = {"document_id": {"$in": document_ids}} if document_ids else None

    hits = await store.asimilarity_search_with_score(query, k=k, filter=filter_)
    docs = [doc for doc, score in hits]
    for doc, score in hits:
        doc.metadata["score"] = score

    if not docs:
        return []

    if settings.rerank_enabled:
        # Cross-encoder scoring is CPU-bound.
        return await asyncio.to_thread(_rerank, query, docs, top_n)
    return docs[:top_n]
