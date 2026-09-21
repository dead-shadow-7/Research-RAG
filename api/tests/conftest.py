"""Shared fixtures for the tests that exercise the API end to end.

These run against the local Postgres from `docker compose up -d`, because the thing worth
testing -- that one tenant's rows are invisible to another -- lives in the WHERE clauses,
and a mocked session would test the mock. Every test runs inside an outer transaction
that is rolled back afterwards, so the database is left exactly as it was found.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import current_user_id
from app.config import settings
from app.db import get_session
from app.main import app


@pytest_asyncio.fixture
async def db_connection() -> AsyncIterator:
    """One connection with an open transaction, rolled back at the end.

    The routes call `session.commit()`, which becomes a SAVEPOINT release inside this
    outer transaction rather than a real commit -- so the writes are visible to the test
    and invisible to everyone else, and they vanish on rollback.

    A fresh engine with NullPool per test, rather than `app.db.engine`. asyncpg binds a
    connection to the event loop that opened it, and pytest-asyncio gives each test its
    own loop -- so a pooled connection from an earlier test is dead on arrival in the
    next one, and the failure looks like "Postgres is down" rather than like a bug.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        try:
            connection = await engine.connect()
        except Exception as exc:  # noqa: BLE001 - any connect failure means no database
            pytest.skip(
                f"local Postgres is not reachable ({type(exc).__name__}) -- docker compose up -d"
            )

        # No `async with` here: awaiting connect() has already started the connection,
        # and __aenter__ would start it a second time.
        try:
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                await transaction.rollback()
        finally:
            await connection.close()
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(db_connection):
    return async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
def identity() -> dict:
    """Mutable holder so a test can change who it is between requests."""
    return {"user_id": uuid.uuid4()}


@pytest_asyncio.fixture
async def client(session_factory, identity, tmp_path, monkeypatch) -> AsyncIterator[AsyncClient]:
    """An API client whose identity the test controls.

    Auth itself is covered by tests/test_auth.py; overriding it here keeps these tests
    about isolation rather than about token parsing.
    """
    # Uploads land in a temp directory: the rollback undoes rows, not files.
    monkeypatch.setattr(settings, "upload_dir", tmp_path)

    # Ingestion and vector deletion are out of scope here and would hit Pinecone.
    import app.routers.documents as documents_router

    async def no_ingest(*args, **kwargs):
        return None

    async def no_vectors(*args, **kwargs):
        return 0

    monkeypatch.setattr(documents_router, "schedule_ingestion", no_ingest)
    monkeypatch.setattr(documents_router, "delete_document_data", no_vectors)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[current_user_id] = lambda: identity["user_id"]
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()
