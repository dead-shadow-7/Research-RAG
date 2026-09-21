from __future__ import annotations

import uuid
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    source_type: str
    status: str
    error: str | None = None
    chunk_count: int = 0
    byte_size: int = 0
    created_at: datetime
    progress_pct: int = 0
    stage_message: str | None = None


class UrlIngestRequest(BaseModel):
    url: HttpUrl
    # Matches documents.title, which is String(512). Unbounded here meant a long title
    # reached Postgres and came back as a 500 instead of a 422.
    title: str | None = Field(default=None, max_length=512)


class SourceOut(BaseModel):
    vector_id: str
    document_id: str | None = None
    title: str | None = None
    page_from: int | None = None
    score: float | None = None
    snippet: str


# A question, and any one turn of the conversation behind it. Generous for a question,
# tight enough that a request cannot become a prompt nobody meant to pay for.
MAX_MESSAGE_CHARS = 4000

# Turns of history replayed to the model. Everything here is re-sent on every request,
# so this multiplies the cost of each question rather than adding to it once.
MAX_HISTORY_TURNS = 20

# Documents a question may be scoped to. A user can hold MAX_DOCUMENTS_PER_USER of them,
# so this is slack rather than a real constraint -- its job is to stop an arbitrarily
# long list being handed to Pinecone as an $in filter.
MAX_DOCUMENT_FILTER = 100


class ChatTurn(BaseModel):
    """One prior turn.

    Typed rather than `dict[str, str]`: the loose form let a turn with an unrecognised
    role through validation and then silently vanish in `stream_answer`, which drops
    anything that is not user or assistant. A 422 is a better answer than a reply that
    quietly ignored half the conversation.
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_DOCUMENT_FILTER)
