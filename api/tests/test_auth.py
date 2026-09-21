"""Token verification.

Hermetic: the test generates its own ES256 keypair and serves it as a JWKS, so nothing
here touches Supabase or the network. What is being tested is our verification policy,
not their issuer.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth

AUDIENCE = "authenticated"
KID = "test-key-1"


def _keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk |= {"kid": KID, "use": "sig", "alg": "ES256"}
    return key, jwk


@pytest.fixture(scope="module")
def keys():
    return _keypair()


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch, keys):
    """Point the verifier at an in-memory key set."""
    _, jwk = keys
    client = jwt.PyJWKClient("https://example.invalid/jwks.json")
    client.get_signing_key_from_jwt = lambda token: jwt.PyJWK(jwk, algorithm="ES256")  # noqa: ARG005
    monkeypatch.setattr(auth, "get_jwk_client", lambda: client)
    monkeypatch.setattr(auth.settings, "supabase_jwt_audience", AUDIENCE)


def make_token(keys, *, sub=None, exp_offset=3600, audience=AUDIENCE, key=None, alg="ES256"):
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "aud": audience,
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    private, _ = keys
    return jwt.encode(claims, key or private, algorithm=alg, headers={"kid": KID})


async def resolve(token: str | None):
    credentials = (
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
    )
    return await auth.current_user_id(credentials)


async def test_valid_token_resolves_to_its_subject(keys):
    user_id = uuid.uuid4()
    assert await resolve(make_token(keys, sub=str(user_id))) == user_id


async def test_missing_credentials_is_401():
    with pytest.raises(HTTPException) as exc:
        await resolve(None)
    assert exc.value.status_code == 401
    # Without this header a client cannot tell what kind of credential to present.
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


async def test_expired_token_is_rejected(keys):
    with pytest.raises(HTTPException) as exc:
        await resolve(make_token(keys, exp_offset=-60))
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


async def test_token_signed_by_another_key_is_rejected(keys):
    other, _ = _keypair()
    with pytest.raises(HTTPException) as exc:
        await resolve(make_token(keys, key=other))
    assert exc.value.status_code == 401


async def test_wrong_audience_is_rejected(keys):
    with pytest.raises(HTTPException) as exc:
        await resolve(make_token(keys, audience="anon"))
    assert exc.value.status_code == 401


async def test_hs256_token_signed_with_the_public_key_is_rejected(keys):
    """Algorithm confusion -- the reason `ALGORITHMS` names only asymmetric algorithms.

    An attacker who has the public key (it is public) signs an HS256 token with it as the
    HMAC secret. A verifier that accepts HS256 alongside ES256 hands the same key material
    to both code paths and the forgery verifies. Ours must not.
    """
    private, _ = keys
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Hand-rolled, because PyJWT refuses to *sign* HS256 with a PEM -- a real attacker is
    # not constrained by our library's guard rails, only by our verifier's.
    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = b64(
        json.dumps(
            {"sub": str(uuid.uuid4()), "aud": AUDIENCE, "exp": int(time.time()) + 3600}
        ).encode()
    )
    signed = header + b"." + payload
    signature = b64(hmac.new(public_pem, signed, hashlib.sha256).digest())
    forged = (signed + b"." + signature).decode()

    with pytest.raises(HTTPException) as exc:
        await resolve(forged)
    assert exc.value.status_code == 401


async def test_token_without_a_subject_is_rejected(keys):
    private, _ = keys
    token = jwt.encode(
        {"aud": AUDIENCE, "exp": int(time.time()) + 3600},
        private,
        algorithm="ES256",
        headers={"kid": KID},
    )
    with pytest.raises(HTTPException) as exc:
        await resolve(token)
    assert exc.value.status_code == 401


def test_namespace_is_never_empty():
    """Pinecone's default namespace is the empty string, so this must never produce it."""
    assert auth.tenant_namespace(uuid.uuid4())
