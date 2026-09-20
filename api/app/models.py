"""SQLAlchemy models.

Postgres owns the document registry, ingest job state, and a mirror of every chunk.
Pinecone owns the vectors. The link between them is `Chunk.vector_id`, which is
deterministic (`{document_id}#{ordinal}`) because Pinecone serverless cannot delete
by metadata filter -- deletion works by listing that ID prefix.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SourceType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    TXT = "txt"
    URL = "url"


class DocStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(16))
    source_uri: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default=DocStatus.QUEUED, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # sha256 of the raw bytes / url, for dedupe
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    jobs: Mapped[list[IngestJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_document_ordinal", "document_id", "ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="chunks")


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(16), default=DocStatus.QUEUED)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship(back_populates="jobs")
