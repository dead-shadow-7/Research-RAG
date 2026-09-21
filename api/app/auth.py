"""Identity. The only place a request becomes a tenant.

Supabase Auth issues the tokens; this module verifies them locally against the project's
public keys, so there is no network call to Supabase on the request path once the key set
is cached. The user id in the `sub` claim is both the Postgres `documents.owner_id` and
the Pinecone namespace -- one value, derived in one place, so the two stores can never
disagree about who owns what.

Verification uses the **asymmetric** keys (ES256/RS256) published at the project's JWKS
endpoint. Supabase also supports a legacy shared HS256 secret and advises against it: a
symmetric secret that can verify a token can also mint one, so anything holding it can
impersonate any user.
"""

from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

# Asymmetric only. Including "HS256" here alongside these would reintroduce the classic
# algorithm-confusion attack: a forged HS256 token signed with the *public* key as its
# secret would verify, because the library would use the same key material for both.
ALGORITHMS = ["ES256", "RS256"]

# auto_error=False so the 401 below is ours -- FastAPI's default omits the
# WWW-Authenticate header that tells a client what kind of credential is expected.
bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


@lru_cache(maxsize=1)
def get_jwk_client() -> jwt.PyJWKClient:
    """Cached key set. Fetched once, then refreshed only when a key id is unknown.

    Tests replace this via `get_jwk_client.cache_clear()` plus a monkeypatch, which is why
    it is a function rather than a module-level constant.
    """
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is not set, so no token can be verified")
    return jwt.PyJWKClient(f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json")


def _verify(token: str) -> dict:
    """Blocking: the JWK client may fetch on a cache miss."""
    key = get_jwk_client().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        key,
        algorithms=ALGORITHMS,
        audience=settings.supabase_jwt_audience,
        options={"require": ["exp", "sub"]},
    )


async def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> uuid.UUID:
    """The caller's Supabase user id, or 401.

    Every route that touches tenant data depends on this. That is deliberate: a route
    without it has no way to name an owner, so forgetting the check is not a silent leak
    but an obvious hole in the signature.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated")

    try:
        claims = await asyncio.to_thread(_verify, credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Token has expired") from exc
    except jwt.PyJWTError as exc:
        # Deliberately vague to the client; the specifics belong in logs, not in a
        # response that helps someone probe for a token that would be accepted.
        raise _unauthorized("Invalid token") from exc

    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized("Token has no usable subject") from exc


def tenant_namespace(user_id: uuid.UUID) -> str:
    """The Pinecone namespace holding this user's vectors.

    A plain UUID string: never empty, so it can never collide with Pinecone's default
    namespace, and readable in the console when you need to see whose vectors are whose.
    """
    return str(user_id)
