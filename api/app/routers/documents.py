from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user_id
from app.ratelimit import limited, uploads_per_hour
from app.config import settings
from app.db import get_session
from app.ingestion.parsers import (
    UnsupportedSource,
    assert_fetchable,
    detect_source_type,
)
from app.ingestion.pipeline import delete_document_data
from app.ingestion.scheduler import schedule_ingestion
from app.models import DocStatus, Document, IngestJob, SourceType
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


async def _find_duplicate(
    session: AsyncSession, owner_id: uuid.UUID, content_hash: str
) -> Document | None:
    """An identical upload by the same owner that is already indexed or on its way.

    A failed document is not a duplicate -- re-uploading is how you retry one.

    The owner filter is not optional. Matching on `content_hash` alone would mean that
    uploading a file another tenant has already indexed returns *their* row, handing back
    their title and attaching the uploader to a document they cannot see or delete.
    """
    rows = await session.execute(
        select(Document)
        .where(
            Document.owner_id == owner_id,
            Document.content_hash == content_hash,
            Document.status != DocStatus.FAILED,
        )
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def _owned(session: AsyncSession, document_id: uuid.UUID, owner_id: uuid.UUID) -> Document:
    """Load a document belonging to this user, or 404.

    404 rather than 403 for someone else's id: a 403 confirms the document exists, which
    turns these routes into an oracle for enumerating other tenants' ids.
    """
    rows = await session.execute(
        select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
    )
    doc = rows.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


async def _enforce_quota(session: AsyncSession, owner_id: uuid.UUID) -> None:
    count = await session.scalar(
        select(func.count()).select_from(Document).where(Document.owner_id == owner_id)
    )
    if (count or 0) >= settings.max_documents_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Document limit reached ({settings.max_documents_per_user}). "
            "Delete something before adding more.",
        )


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
    response: Response,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(limited(uploads_per_hour)),
) -> DocumentOut:
    """Accept a file, persist it, and queue ingestion. Never blocks on parsing."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File has no name")
    try:
        source_type = detect_source_type(file.filename)
    except UnsupportedSource as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    await _enforce_quota(session, user_id)

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

    # Re-uploading the same bytes would otherwise duplicate every vector in Pinecone,
    # which costs storage and lets one document answer twice.
    content_hash = hasher.hexdigest()
    if (existing := await _find_duplicate(session, user_id, content_hash)) is not None:
        dest.unlink(missing_ok=True)
        response.status_code = status.HTTP_200_OK
        jobs = await _latest_progress(session, [existing.id])
        return _to_out(existing, jobs.get(existing.id))

    doc = Document(
        id=doc_id,
        owner_id=user_id,
        title=file.filename,
        source_type=source_type.value,
        source_uri=str(dest),
        byte_size=size,
        content_hash=content_hash,
        status=DocStatus.QUEUED,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    await schedule_ingestion(doc_id, background)
    return _to_out(doc, None)


@router.post("/url", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentOut)
async def ingest_url(
    payload: UrlIngestRequest,
    response: Response,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(limited(uploads_per_hour)),
) -> DocumentOut:
    """JSON sibling of the upload endpoint, for web pages.

    Split from `POST /api/documents` because FastAPI cannot accept a JSON body and a
    multipart file on the same route.

    Adding a URL that is already indexed returns the existing document rather than a
    second copy. To re-fetch a page whose content has changed, delete it first.
    """
    url = str(payload.url)
    # Checked here as well as in the parser so the caller gets a 400 straight away
    # rather than a 202 and a document that fails in the background for no visible
    # reason. The parser keeps its own check: it is the one that makes the request.
    try:
        assert_fetchable(url)
    except UnsupportedSource as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    content_hash = hashlib.sha256(url.encode()).hexdigest()
    if (existing := await _find_duplicate(session, user_id, content_hash)) is not None:
        response.status_code = status.HTTP_200_OK
        jobs = await _latest_progress(session, [existing.id])
        return _to_out(existing, jobs.get(existing.id))

    await _enforce_quota(session, user_id)

    doc = Document(
        id=uuid.uuid4(),
        owner_id=user_id,
        title=payload.title or url,
        source_type=SourceType.URL.value,
        source_uri=url,
        byte_size=0,
        content_hash=content_hash,
        status=DocStatus.QUEUED,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    await schedule_ingestion(doc.id, background)
    return _to_out(doc, None)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user_id),
) -> list[DocumentOut]:
    rows = await session.execute(
        select(Document).where(Document.owner_id == user_id).order_by(Document.created_at.desc())
    )
    docs = list(rows.scalars())
    jobs = await _latest_progress(session, [d.id for d in docs])
    return [_to_out(d, jobs.get(d.id)) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user_id),
) -> DocumentOut:
    doc = await _owned(session, document_id, user_id)
    jobs = await _latest_progress(session, [doc.id])
    return _to_out(doc, jobs.get(doc.id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user_id),
) -> None:
    doc = await _owned(session, document_id, user_id)

    # Vectors first: a failure here must not leave orphans in Pinecone that the
    # database no longer knows about.
    await delete_document_data(document_id, user_id)

    if doc.source_type != SourceType.URL.value:
        Path(doc.source_uri).unlink(missing_ok=True)

    await session.delete(doc)  # chunks + jobs cascade
    await session.commit()
