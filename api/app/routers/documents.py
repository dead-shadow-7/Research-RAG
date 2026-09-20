from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.ingestion.parsers import UnsupportedSource, detect_source_type
from app.ingestion.pipeline import delete_document_data
from app.models import DocStatus, Document, IngestJob, SourceType
from app.queue import get_pool
from app.schemas import DocumentOut, UrlIngestRequest

router = APIRouter(prefix="/api/documents", tags=["documents"])

READ_CHUNK = 1 << 20  # 1 MiB


async def _latest_progress(session: AsyncSession, doc_ids: list[uuid.UUID]) -> dict:
    """Most recent job per document, for the progress bar in the UI."""
    if not doc_ids:
        return {}
    rows = await session.execute(
        select(IngestJob)
        .where(IngestJob.document_id.in_(doc_ids))
        .order_by(IngestJob.started_at.asc())
    )
    # Later rows overwrite earlier ones, leaving the latest job per document.
    return {job.document_id: job for job in rows.scalars()}


def _to_out(doc: Document, job: IngestJob | None) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    if job is not None:
        out.progress_pct = job.progress_pct
        out.stage_message = job.message
    if doc.status == DocStatus.READY:
        out.progress_pct = 100
    return out


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentOut)
async def upload_document(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    pool: ArqRedis = Depends(get_pool),
) -> DocumentOut:
    """Accept a file, persist it, and queue ingestion. Never blocks on parsing."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File has no name")
    try:
        source_type = detect_source_type(file.filename)
    except UnsupportedSource as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    doc_id = uuid.uuid4()
    dest = settings.upload_dir / f"{doc_id}{Path(file.filename).suffix.lower()}"

    hasher = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as fh:
            while data := await file.read(READ_CHUNK):
                size += len(data)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"File exceeds MAX_UPLOAD_MB ({settings.max_upload_mb} MB)",
                    )
                hasher.update(data)
                fh.write(data)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    doc = Document(
        id=doc_id,
        title=file.filename,
        source_type=source_type.value,
        source_uri=str(dest),
        byte_size=size,
        content_hash=hasher.hexdigest(),
        status=DocStatus.QUEUED,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    await pool.enqueue_job("ingest_document", str(doc_id))
    return _to_out(doc, None)


@router.post("/url", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentOut)
async def ingest_url(
    payload: UrlIngestRequest,
    session: AsyncSession = Depends(get_session),
    pool: ArqRedis = Depends(get_pool),
) -> DocumentOut:
    """JSON sibling of the upload endpoint, for web pages.

    Split from `POST /api/documents` because FastAPI cannot accept a JSON body and a
    multipart file on the same route.
    """
    url = str(payload.url)
    doc = Document(
        id=uuid.uuid4(),
        title=payload.title or url,
        source_type=SourceType.URL.value,
        source_uri=url,
        byte_size=0,
        content_hash=hashlib.sha256(url.encode()).hexdigest(),
        status=DocStatus.QUEUED,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    await pool.enqueue_job("ingest_document", str(doc.id))
    return _to_out(doc, None)


@router.get("", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)) -> list[DocumentOut]:
    rows = await session.execute(select(Document).order_by(Document.created_at.desc()))
    docs = list(rows.scalars())
    jobs = await _latest_progress(session, [d.id for d in docs])
    return [_to_out(d, jobs.get(d.id)) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    jobs = await _latest_progress(session, [doc.id])
    return _to_out(doc, jobs.get(doc.id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # Vectors first: a failure here must not leave orphans in Pinecone that the
    # database no longer knows about.
    await delete_document_data(document_id)

    if doc.source_type != SourceType.URL.value:
        Path(doc.source_uri).unlink(missing_ok=True)

    await session.delete(doc)  # chunks + jobs cascade
    await session.commit()
