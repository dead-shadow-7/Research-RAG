"""Local embeddings via FastEmbed (ONNX), exposed through LangChain's `Embeddings`
interface so the vector store can consume it.

Why not `langchain_community.embeddings.FastEmbedEmbeddings`: its `embed_query()`
delegates to FastEmbed's `query_embed()`, which for BGE models applies **no prefix** --
it is just an alias for `embed()`. BGE v1.5 is asymmetric and needs an instruction
prefix on queries only; without it retrieval quality degrades silently, with no error.
Keeping our own class puts that prefix in exactly one place.
"""

from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings
from tokenizers import Tokenizer

from app.config import settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class BGEFastEmbedEmbeddings(Embeddings):
    def __init__(self) -> None:
        self._model = TextEmbedding(
            model_name=settings.embedding_model,
            cache_dir=str(settings.model_cache_dir),
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # `embed()` yields numpy arrays lazily; materialise and convert for JSON transport.
        return [v.tolist() for v in self._model.embed(texts, batch_size=64)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([QUERY_PREFIX + text]))).tolist()


@lru_cache(maxsize=1)
def get_embeddings() -> BGEFastEmbedEmbeddings:
    """Process-wide singleton. Constructing this loads the ONNX model -- never per request."""
    return BGEFastEmbedEmbeddings()


@lru_cache(maxsize=1)
def get_tokenizer() -> Tokenizer:
    """The embedding model's own tokenizer, for chunk sizing.

    Uses the `tokenizers` package (already a FastEmbed dependency) rather than
    `transformers`, which would pull the heavy stack back in.
    """
    return Tokenizer.from_pretrained(settings.embedding_model)


def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text, add_special_tokens=False).ids)


def warm_up() -> None:
    """Force model + tokenizer download/load so the first real request isn't slow."""
    get_embeddings().embed_query("warm up")
    count_tokens("warm up")
