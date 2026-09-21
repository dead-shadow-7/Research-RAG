"""Embeddings via an OpenAI-compatible `/embeddings` endpoint.

The model is `bge-base-en-v1.5`, the same one this project used to run locally through
FastEmbed/ONNX. Verified identical, not assumed: the API's vector scores **cosine
0.99999423** against a vector embedded by the local model and stored in
`tests/fixtures/reference_vector.json`. That is what makes the switch safe -- every vector
already in Pinecone stays queryable, and the retrieval score floor stays calibrated.

Moving the model out of the process is what makes this backend small: no 46 MB of
onnxruntime, no 361 MB of downloaded weights, no ~330 MB resident, and none of the
batch-size tuning that existed only to stop the model OOM-killing a 1 GB host.

The tokenizer stays local -- chunk sizing must be exact, and it is 711 KB.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from langchain_openai import OpenAIEmbeddings
from tokenizers import Tokenizer

from app.config import settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Vendored rather than fetched from the HF hub: it removes a network call from startup
# (a container's HF cache is empty on every boot), and the ids differ in case between the
# two sources -- the provider serves `baai/...` while the hub repo is `BAAI/...`.
TOKENIZER_PATH = Path(__file__).resolve().parent / "data" / "bge-base-en-v1.5-tokenizer.json"


class BGEAPIEmbeddings(OpenAIEmbeddings):
    """BGE v1.5 over an OpenAI-compatible embeddings endpoint.

    Subclassed for one reason: BGE v1.5 is asymmetric and no API applies its query
    instruction prefix for you -- passages go in bare, queries need the prefix. Putting it
    here means every caller gets it right for free, which is the same reason the FastEmbed
    version of this class existed.
    """

    def embed_query(self, text: str) -> list[float]:
        return super().embed_query(QUERY_PREFIX + text)

    async def aembed_query(self, text: str) -> list[float]:
        return await super().aembed_query(QUERY_PREFIX + text)


@lru_cache(maxsize=1)
def get_embeddings() -> BGEAPIEmbeddings:
    """Process-wide singleton: it holds an HTTP client with a connection pool."""
    return BGEAPIEmbeddings(
        model=settings.embedding_model,
        base_url=settings.embedding_url,
        api_key=settings.embedding_key,
        # Load-bearing. Left on (the default), OpenAIEmbeddings tiktoken-encodes the input
        # and posts arrays of token ids instead of strings -- which this backend rejects
        # with a 422, and which would be meaningless to it anyway since tiktoken's vocab is
        # not BGE's. Our chunker already sizes text in BGE tokens, so the re-chunking this
        # disables is work we have done properly upstream.
        check_embedding_ctx_length=False,
        chunk_size=settings.embed_batch_size,
        timeout=settings.embed_timeout,
        max_retries=settings.embed_max_retries,
    )


@lru_cache(maxsize=1)
def get_tokenizer() -> Tokenizer:
    """The embedding model's own tokenizer, for chunk sizing.

    Uses the `tokenizers` package rather than `transformers`, which would pull the whole
    torch stack in for a WordPiece vocabulary.
    """
    return Tokenizer.from_file(str(TOKENIZER_PATH))


def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text, add_special_tokens=False).ids)


def warm_up() -> None:
    """Parse the tokenizer now so the first upload isn't paying for it."""
    count_tokens("warm up")
