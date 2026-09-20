"""Where ingestion runs.

Two modes, chosen by `INLINE_INGESTION`:

* **worker** (default) -- the upload is queued in Redis and an arq worker picks it up.
  The worker is a second process, so it holds a second copy of the embedding model.
* **inline** -- ingestion runs in this process as a background task. One model instead
  of two and no Redis at all, which is the difference between fitting and not fitting
  on a 1 GB host. The cost is that a large upload competes with request handling.

Inline mode runs one document at a time. Concurrent embeds would each hold their own
batch of activations, and on the hosts where inline mode makes sense that is exactly
the spike there is no headroom for.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import BackgroundTasks

from app.config import settings
from app.ingestion.pipeline import run_ingestion

logger = logging.getLogger(__name__)

_one_at_a_time = asyncio.Semaphore(1)


async def _ingest_serially(document_id: uuid.UUID) -> None:
    async with _one_at_a_time:
        try:
            await run_ingestion(document_id)
        except Exception:
            # run_ingestion already recorded the failure on the document; this is a
            # background task, so there is nobody left to return the error to.
            logger.exception("inline ingestion failed for %s", document_id)


async def schedule_ingestion(document_id: uuid.UUID, background: BackgroundTasks) -> None:
    """Queue a document for ingestion by whichever mode is configured."""
    if settings.inline_ingestion:
        background.add_task(_ingest_serially, document_id)
        return

    from app.queue import get_pool

    await get_pool().enqueue_job("ingest_document", str(document_id))
