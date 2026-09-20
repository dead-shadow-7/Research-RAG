from __future__ import annotations

import uuid
from datetime import datetime

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
    title: str | None = None


class SourceOut(BaseModel):
    vector_id: str
    document_id: str | None = None
    title: str | None = None
    page_from: int | None = None
    score: float | None = None
    snippet: str


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)
    document_ids: list[str] | None = None
