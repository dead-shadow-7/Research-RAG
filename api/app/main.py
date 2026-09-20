import asyncio
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.queue import close_pool, init_pool
from app.routers import chat, documents


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()
    await engine.dispose()


app = FastAPI(title="RAG API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)


async def _probe_db() -> str:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "ok"


async def _probe_redis() -> str:
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
        return "ok"
    finally:
        await client.aclose()


async def _probe_pinecone() -> str:
    from app.vectorstore import get_index

    index = await asyncio.to_thread(get_index)
    stats = await asyncio.to_thread(index.describe_index_stats)
    return f"ok ({stats.get('total_vector_count', 0)} vectors)"


@app.get("/api/health")
async def health() -> dict:
    """Per-service status. Never raises -- a failing dependency is reported, not hidden."""
    results: dict[str, str] = {}
    probes = (("database", _probe_db), ("redis", _probe_redis), ("pinecone", _probe_pinecone))
    for name, probe in probes:
        try:
            results[name] = await probe()
        except Exception as exc:  # noqa: BLE001 - health must report, not propagate
            results[name] = f"error: {type(exc).__name__}: {exc}"

    ok = all(v.startswith("ok") for v in results.values())
    results["status"] = "ok" if ok else "degraded"
    return results
