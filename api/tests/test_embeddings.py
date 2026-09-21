"""Embedding contract tests.

These guard the silent failure modes in this stack: a vector shape the Pinecone index
would reject, a missing BGE query prefix (which costs recall with no error), and the
provider quietly serving different weights under the same model name.

Embedding is a remote call now, so everything but the token counter needs credentials
and network.
"""

import json
import math
from pathlib import Path

import pytest

from app.config import settings
from app.embeddings import count_tokens, get_embeddings

needs_provider = pytest.mark.skipif(
    not settings.embedding_key or not settings.embedding_url,
    reason="embedding provider is not configured",
)


@pytest.fixture(scope="module")
def emb():
    return get_embeddings()


@needs_provider
def test_passage_vector_matches_index_contract(emb):
    v = emb.embed_documents(["Northwind manufactures precision bearings."])[0]
    assert len(v) == settings.embedding_dim, "vector width must match the Pinecone index"
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-3, (
        f"expected unit-length vectors for cosine similarity, got norm={norm:.4f}. "
        "Either normalise in embed_documents or switch the index metric to dotproduct."
    )


@needs_provider
def test_query_prefix_is_actually_applied(emb):
    """BGE v1.5 is asymmetric.

    No embeddings API applies the query instruction prefix for you, so this asserts our
    subclass is doing it -- if someone swaps in a plain OpenAIEmbeddings, this fails.
    """
    text = "how long is the warranty"
    assert emb.embed_query(text) != emb.embed_documents([text])[0]


@needs_provider
def test_provider_vectors_match_the_indexed_vectors(emb):
    """The provider must keep serving the weights our index was built with.

    The reference vector was produced by the local FastEmbed/ONNX model this project used
    before embedding moved to the API. It measured cosine 0.99999423 against the provider
    on the day of the switch, which is what made the switch safe without re-indexing.

    If this drifts, nothing raises anywhere: every vector already in Pinecone silently
    stops matching new queries, and retrieval quality degrades with no error. Re-indexing
    is the only fix, so this test is the alarm.
    """
    reference = json.loads((Path(__file__).parent / "fixtures" / "reference_vector.json").read_text())
    # Compared loosely on purpose: the hub spells this model `BAAI/bge-base-en-v1.5` and
    # the provider spells it `baai/bge-base-en-v1.5`.
    assert reference["model"].lower() == settings.embedding_model.lower(), (
        "reference vector is for another model"
    )

    actual = emb.embed_documents([reference["text"]])[0]
    expected = reference["vector"]
    assert len(actual) == len(expected)

    dot = sum(a * b for a, b in zip(actual, expected))
    norm = math.sqrt(sum(a * a for a in actual)) * math.sqrt(sum(b * b for b in expected))
    assert dot / norm > 0.99999, "embeddings drifted -- the Pinecone index would be invalidated"


@needs_provider
def test_oversized_input_fails_loudly(emb):
    """bge-base truncates at 512 tokens, and the API rejects anything longer.

    Worth pinning: the local model used to truncate *silently*, so an oversized chunk lost
    its tail with no error. Now it is a hard failure, which is why chunk sizing
    (tests/test_chunking.py) is load-bearing rather than advisory.
    """
    with pytest.raises(Exception):
        emb.embed_documents(["overflow " * 2000])


def test_token_counter_tracks_the_embedding_model():
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 2
    # Chunk sizing depends on this being the model's own tokenizer, not a char count.
    assert count_tokens("a" * 400) < 400
