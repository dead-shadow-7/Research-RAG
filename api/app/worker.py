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

    # Parses the vendored tokenizer now rather than during the first upload.
    await asyncio.to_thread(warm_up)


class WorkerSettings:
    functions = [ingest_document]
    on_startup = startup
    redis_settings = redis_settings()
    # Ingestion is network-bound now (parsing aside), so this could go higher; 2 keeps
    # a burst of uploads from monopolising the embedding provider's rate limit.
    max_jobs = 2
    job_timeout = 900
