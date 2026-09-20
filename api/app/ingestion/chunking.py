"""Token-aware chunking.

Sized in **BGE tokens**, not characters: `bge-base-en-v1.5` truncates at 512 tokens
and FastEmbed gives no warning when it does, so an oversized chunk silently loses
its tail from the index.

We pass our own `length_function` rather than using
`RecursiveCharacterTextSplitter.from_huggingface_tokenizer()`, which requires
`transformers` and would undo the lightweight-install win from FastEmbed.
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings
from app.embeddings import count_tokens

SEPARATORS = ["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""]


def get_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_tokens,
        chunk_overlap=settings.chunk_overlap,
        length_function=count_tokens,
        separators=SEPARATORS,
        keep_separator=True,
    )


def chunk_documents(docs: list[Document]) -> list[Document]:
    """Split cleaned blocks into embedding-sized chunks.

    Adds `ordinal`, `page_from`, `page_to` and `token_count` to each chunk's metadata.
    Source metadata (page, sheet, url) is carried through by the splitter.
    """
    chunks = get_splitter().split_documents(docs)

    out: list[Document] = []
    for ordinal, chunk in enumerate(chunks):
        page = chunk.metadata.get("page")
        meta = {
            **chunk.metadata,
            "ordinal": ordinal,
            "page_from": page,
            "page_to": page,
            "token_count": count_tokens(chunk.page_content),
        }
        out.append(Document(page_content=chunk.page_content, metadata=meta))
    return out
