"""Embedding contract tests.

These guard the two silent failure modes in this stack: a vector shape the Pinecone
index would reject, and a missing BGE query prefix (which costs recall with no error).
"""

import json
import math
from pathlib import Path

import pytest

from app.config import settings
from app.embeddings import count_tokens, get_embeddings


@pytest.fixture(scope="module")
def emb():
    return get_embeddings()


def test_passage_vector_matches_index_contract(emb):
    v = emb.embed_documents(["Northwind manufactures precision bearings."])[0]
    assert len(v) == settings.embedding_dim, "vector width must match the Pinecone index"
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-3, (
        f"expected unit-length vectors for cosine similarity, got norm={norm:.4f}. "
        "Either normalise in embed_documents or switch the index metric to dotproduct."
    )


def test_query_prefix_is_actually_applied(emb):
    """BGE v1.5 is asymmetric.

    FastEmbed's own `query_embed()` applies no prefix for BGE, so this asserts our
    override is doing the work -- if someone swaps in FastEmbedEmbeddings, this fails.
    """
    text = "how long is the warranty"
    assert emb.embed_query(text) != emb.embed_documents([text])[0]


def test_lean_onnx_session_has_not_changed_the_embeddings(emb):
    """The memory optimisation must never alter a vector.

    `app.embeddings` patches ONNX Runtime's session options to halve the model's
    footprint (494 MB -> 327 MB). That is only safe while it produces the same numbers:
    if it drifts, every vector already in Pinecone silently stops matching new queries,
    with no error anywhere. The reference was captured from the unpatched session.
    """
    reference = json.loads((Path(__file__).parent / "fixtures" / "reference_vector.json").read_text())
    assert reference["model"] == settings.embedding_model, "reference is for another model"

    actual = emb.embed_documents([reference["text"]])[0]
    expected = reference["vector"]
    assert len(actual) == len(expected)

    dot = sum(a * b for a, b in zip(actual, expected))
    norm = math.sqrt(sum(a * a for a in actual)) * math.sqrt(sum(b * b for b in expected))
    assert dot / norm > 0.99999, "embeddings drifted -- the Pinecone index would be invalidated"


def test_token_counter_tracks_the_embedding_model():
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 2
    # Chunk sizing depends on this being the model's own tokenizer, not a char count.
    assert count_tokens("a" * 400) < 400
