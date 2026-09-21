from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth import tenant_namespace
from app.ratelimit import chat_per_day, chat_per_minute, limited
from app.llm import stream_answer
from app.retrieval import retrieve
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

SNIPPET_CHARS = 280

NO_RESULTS = (
    "I couldn't find anything relevant in the indexed documents. "
    "Try rephrasing, or check that the document you expect has finished indexing."
)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _events(req: ChatRequest, namespace: str) -> AsyncIterator[str]:
    try:
        document_ids = [str(d) for d in req.document_ids] if req.document_ids else None
        docs = await retrieve(req.query, document_ids, namespace=namespace)

        # Sources go out before any token so the UI can render source cards while
        # the model is still thinking.
        yield sse(
            "sources",
            [
                {
                    "vector_id": d.metadata.get("vector_id"),
                    "document_id": d.metadata.get("document_id"),
                    "title": d.metadata.get("title"),
                    "page_from": d.metadata.get("page_from"),
                    "score": d.metadata.get("score"),
                    "snippet": d.page_content[:SNIPPET_CHARS],
                }
                for d in docs
            ],
        )

        if not docs:
            yield sse("token", {"text": NO_RESULTS})
            yield sse("done", {"usage": None})
            return

        usage: dict | None = None
        async for event in stream_answer(req.query, docs, req.history):
            match event["type"]:
                case "token":
                    yield sse("token", {"text": event["text"]})
                case "citation":
                    yield sse("citation", event)
                case "usage":
                    usage = event["usage"]

        yield sse("done", {"usage": usage})
    except Exception as exc:  # noqa: BLE001 - the stream is the only channel back
        logger.exception("chat stream failed")
        yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    user_id: uuid.UUID = Depends(limited(chat_per_minute, chat_per_day)),
) -> StreamingResponse:
    # The dependency resolves before the response is constructed, so an unauthenticated
    # caller gets a real 401 rather than a 200 whose body happens to open with an error
    # frame -- which a streaming client would have to parse to discover it was rejected.
    return StreamingResponse(
        _events(req, tenant_namespace(user_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx and friends buffering the stream into one blob.
            "X-Accel-Buffering": "no",
        },
    )
