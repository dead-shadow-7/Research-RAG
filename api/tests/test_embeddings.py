"""Embedding contract tests.

These guard the two silent failure modes in this stack: a vector shape the Pinecone
index would reject, and a missing BGE query prefix (which costs recall with no error).
"""

import math

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


def test_token_counter_tracks_the_embedding_model():
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 2
    # Chunk sizing depends on this being the model's own tokenizer, not a char count.
    assert count_tokens("a" * 400) < 400
