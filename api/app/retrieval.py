"""Query -> ranked chunks.

The BGE query prefix lives inside `BGEAPIEmbeddings.embed_query`, so callers here get
the asymmetric-embedding handling for free.

`CONTEXT_K` is a ceiling, not a quota. Vector search always returns its top k, however
weak the matches are, so filling a fixed number of context slots means shipping whatever
happened to rank highest -- measured on a mixed corpus, five of six chunks were unrelated
documents. Relevant chunks separate sharply from noise, so a floor relative to the best
match removes it cleanly.
"""

from __future__ import annotations

from langchain_core.documents import Document

from app.config import settings
from app.vectorstore import get_vector_store


def apply_floor(
    scored: list[tuple[Document, float]],
    top_n: int,
    *,
    min_ratio: float,
    min_score: float,
) -> list[Document]:
    """Keep the chunks close enough to the best match to be worth the model's attention.

    Returning nothing is a valid answer: if the best match is weak, the corpus does not
    contain the answer, and saying so beats answering from noise.
    """
    if not scored:
        return []

    best = max(score for _, score in scored)
    if best < min_score:
        return []

    floor = max(best * min_ratio, min_score)
    kept = [(doc, score) for doc, score in scored if score >= floor]
    kept.sort(key=lambda pair: pair[1], reverse=True)

    for doc, score in kept:
        doc.metadata["score"] = score
    return [doc for doc, _ in kept[:top_n]]


async def retrieve(
    query: str,
    document_ids: list[str] | None = None,
    k: int | None = None,
    top_n: int | None = None,
) -> list[Document]:
    """Return the chunks to put in front of the model, best first. May return none."""
    k = k or settings.retrieve_k
    top_n = top_n or settings.context_k

    store = get_vector_store()
    filter_ = {"document_id": {"$in": document_ids}} if document_ids else None

    hits = await store.asimilarity_search_with_score(query, k=k, filter=filter_)
    if not hits:
        return []

    return apply_floor(
        hits,
        top_n,
        min_ratio=settings.context_min_ratio,
        min_score=settings.context_min_score,
    )
