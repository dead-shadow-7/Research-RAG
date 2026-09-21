"""Isolation between tenants.

This is the file that justifies the multi-tenancy work. Each test asserts that one user
cannot reach another's documents through a route that looks innocent.

Vector isolation is enforced separately, by the Pinecone namespace, and cannot be checked
without the live index -- see the namespace step in DEPLOYMENT.md.
"""

import uuid

import pytest

SAMPLE = b"Northwind bearings carry a warranty of 36 months from dispatch.\n"


async def upload(client, name="notes.txt", content=SAMPLE):
    response = await client.post("/api/documents", files={"file": (name, content, "text/plain")})
    assert response.status_code in (200, 202), response.text
    return response.json()


async def test_the_library_shows_only_your_own_documents(client, identity):
    alice = identity["user_id"]
    mine = await upload(client, "alice.txt")

    identity["user_id"] = uuid.uuid4()  # become Bob
    await upload(client, "bob.txt", b"Something else entirely.\n")

    listed = (await client.get("/api/documents")).json()
    titles = {d["title"] for d in listed}
    assert titles == {"bob.txt"}
    assert mine["id"] not in {d["id"] for d in listed}

    identity["user_id"] = alice
    assert {d["title"] for d in (await client.get("/api/documents")).json()} == {"alice.txt"}


async def test_fetching_someone_elses_document_is_404_not_403(client, identity):
    """404 on purpose: a 403 would confirm the id exists, which is an enumeration oracle."""
    theirs = await upload(client)

    identity["user_id"] = uuid.uuid4()
    response = await client.get(f"/api/documents/{theirs['id']}")
    assert response.status_code == 404


async def test_deleting_someone_elses_document_is_404_and_leaves_it_intact(client, identity):
    owner = identity["user_id"]
    theirs = await upload(client)

    identity["user_id"] = uuid.uuid4()
    assert (await client.delete(f"/api/documents/{theirs['id']}")).status_code == 404

    identity["user_id"] = owner
    assert (await client.get(f"/api/documents/{theirs['id']}")).status_code == 200


async def test_identical_uploads_by_two_users_are_two_documents(client, identity):
    """Dedupe is per-owner.

    Matching on content_hash alone would return the *other* tenant's row here -- handing
    back their title and attaching this user to a document they can neither see nor
    delete. That is the regression this test exists to catch.
    """
    first = await upload(client)

    identity["user_id"] = uuid.uuid4()
    second = await upload(client)

    assert second["id"] != first["id"]


async def test_the_same_user_uploading_twice_still_dedupes(client):
    first = await upload(client)
    second = await upload(client)
    assert second["id"] == first["id"]


async def test_a_url_already_added_by_another_user_can_still_be_added(client, identity):
    payload = {"url": "https://example.com/handbook"}
    first = await client.post("/api/documents/url", json=payload)
    assert first.status_code == 202

    identity["user_id"] = uuid.uuid4()
    second = await client.post("/api/documents/url", json=payload)
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]


async def test_the_document_quota_is_per_user(client, identity, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_documents_per_user", 1)

    await upload(client, "first.txt", b"one\n")
    full = await client.post(
        "/api/documents", files={"file": ("second.txt", b"two\n", "text/plain")}
    )
    assert full.status_code == 409

    # A different user starts from zero.
    identity["user_id"] = uuid.uuid4()
    fresh = await client.post(
        "/api/documents", files={"file": ("theirs.txt", b"three\n", "text/plain")}
    )
    assert fresh.status_code == 202


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/documents"),
        ("POST", "/api/documents/url"),
        ("GET", "/api/documents/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/api/documents/00000000-0000-0000-0000-000000000000"),
        ("POST", "/api/chat/stream"),
    ],
)
async def test_every_tenant_route_requires_a_token(method, path):
    """No dependency override here -- this is the real auth dependency rejecting.

    `/api/chat/stream` matters most: it must 401 outright rather than return 200 and put
    the rejection inside the stream, where a client would have to parse it to notice.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json={"query": "hello"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_health_stays_public():
    """It is the deployment's liveness signal and reports no tenant data."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/health")).status_code == 200
