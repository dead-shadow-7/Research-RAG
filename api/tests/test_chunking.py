"""Chunking and cleaning tests -- the stages where data is silently lost."""

from langchain_core.documents import Document

from app.config import settings
from app.embeddings import count_tokens
from app.ingestion.chunking import chunk_documents
from app.ingestion.cleaning import clean_documents, clean_text


def test_hyphenated_line_wrap_is_rejoined():
    assert "bearings" in clean_text("preci-\nsion bear-\nings")


def test_running_header_is_stripped():
    docs = [
        Document(page_content=f"NORTHWIND HANDBOOK\nBody text for page {i} with enough length.")
        for i in range(1, 6)
    ]
    cleaned = clean_documents(docs)
    assert cleaned, "cleaning should not drop every page"
    assert all("NORTHWIND HANDBOOK" not in d.page_content for d in cleaned)


def test_near_empty_blocks_are_dropped():
    assert clean_documents([Document(page_content="  \n  x \n ")]) == []


def test_chunks_respect_the_embedding_window():
    """Load-bearing: the embeddings API rejects anything over 512 tokens outright.

    It used to be advisory -- the local model truncated silently and a chunk just lost its
    tail. Now an oversized chunk fails the whole document's ingestion.
    """
    long_text = " ".join(f"sentence number {i} about precision bearings." for i in range(400))
    chunks = chunk_documents([Document(page_content=long_text, metadata={"page": 1})])

    assert len(chunks) > 1
    for c in chunks:
        assert count_tokens(c.page_content) <= 512, "chunk exceeds the BGE context window"


def test_chunk_metadata_is_populated():
    chunks = chunk_documents(
        [Document(page_content="Warranty is 36 months. " * 50, metadata={"page": 3})]
    )
    assert [c.metadata["ordinal"] for c in chunks] == list(range(len(chunks)))
    assert all(c.metadata["page_from"] == 3 for c in chunks)
    assert all(c.metadata["token_count"] > 0 for c in chunks)


def test_settings_are_consistent():
    assert settings.chunk_overlap < settings.chunk_tokens
    assert settings.context_k <= settings.retrieve_k
