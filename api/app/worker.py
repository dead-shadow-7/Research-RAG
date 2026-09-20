"""arq worker: runs ingestion off the request path.

Run with:  arq app.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.embeddings import warm_up
from app.ingestion.pipeline import fail_stale_documents, run_ingestion
from app.queue import redis_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


async def ingest_document(ctx: dict, document_id: str) -> int:
    logger.info("ingesting %s", document_id)
    count = await run_ingestion(uuid.UUID(document_id))
    logger.info("ingested %s -> %d chunks", document_id, count)
    return count


async def startup(ctx: dict) -> None:
    # Anything mid-flight when the worker died has no job left to finish it.
    if stale := await fail_stale_documents():
        logger.warning("cleared %d document(s) interrupted by a previous restart", stale)

    # Downloads/loads the ONNX model now so the first upload isn't mysteriously slow.
    logger.info("warming up embedding model (first run downloads weights)...")
    await asyncio.to_thread(warm_up)
    logger.info("embedding model ready")


class WorkerSettings:
    functions = [ingest_document]
    on_startup = startup
    redis_settings = redis_settings()
    max_jobs = 2  # embedding is CPU-bound; don't thrash
    job_timeout = 900
