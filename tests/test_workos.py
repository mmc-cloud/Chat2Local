"""Real RSA JWT verification with an offline JWKS HTTP boundary."""

import asyncio
import io
import json
import threading
import time
from urllib.error import URLError

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from chat2local.auth.workos import WorkOSTokenVerifier
from conftest import run

ISSUER = "https://test.authkit.app"
RESOURCE = "https://example.com/mcp"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key, kid="first"):
    return json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {"kid": kid, "alg": "RS256", "use": "sig"}


def token_claims(**overrides):
    now = int(time.time())
    return {"iss": ISSUER, "aud": RESOURCE, "sub": "user_test", "client_id": "client_test",
            "exp": now + 300, "iat": now, "scope": "openid profile"} | overrides


def make_token(key, claims=None, *, kid="first", algorithm="RS256"):
    return jwt.encode(token_claims() if claims is None else claims, key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture
def workos(signing_key, monkeypatch):
    remote = {"payload": {"keys": [public_jwk(signing_key)]}, "calls": [], "error": None}

    class Opener:
        def open(self, request, timeout):
            remote["calls"].append((request.full_url, timeout, threading.get_ident()))
            if remote["error"]:
                raise remote["error"]
            return io.BytesIO(json.dumps(remote["payload"]).encode())

    monkeypatch.setattr(jwt.jwks_client.urllib.request, "build_opener", lambda *handlers: Opener())
    return WorkOSTokenVerifier(ISSUER, RESOURCE), remote


def test_valid_signature_and_claim_mapping(workos, signing_key):
    verifier, remote = workos
    token = make_token(signing_key)
    accepted = run(verifier.verify_token(token))
    assert accepted is not None
    assert accepted.token == token
    assert accepted.client_id == "client_test" and accepted.subject == "user_test"
    assert accepted.scopes == ["openid", "profile"] and accepted.resource == RESOURCE
    assert accepted.claims == {"iss": ISSUER}
    assert accepted.expires_at == jwt.decode(token, signing_key.public_key(), algorithms=["RS256"], audience=RESOURCE)["exp"]
    assert remote["calls"][0][:2] == (ISSUER + "/oauth2/jwks", 10)
    assert remote["calls"][0][2] != threading.get_ident()


@pytest.mark.parametrize("scope,expected", [("", []), ("openid   email\tprofile", ["openid", "email", "profile"]), (None, [])])
def test_optional_scope_mapping(workos, signing_key, scope, expected):
    data = token_claims()
    if scope is None:
        del data["scope"]
    else:
        data["scope"] = scope
    accepted = run(workos[0].verify_token(make_token(signing_key, data)))
    assert accepted.scopes == expected


@pytest.mark.parametrize("overrides", [
    {"iss": "https://wrong.authkit.app"}, {"aud": "https://wrong.example.com/mcp"},
    {"exp": 1}, {"iat": 9999999999}, {"nbf": 9999999999}, {"sub": ""}, {"sub": 1},
    {"client_id": " "}, {"client_id": 1}, {"scope": ["openid"]}, {"scope": None},
    {"exp": True}, {"exp": "9999999999"}, {"iat": "1"},
])
def test_invalid_claims_rejected(workos, signing_key, overrides):
    assert run(workos[0].verify_token(make_token(signing_key, token_claims(**overrides)))) is None


@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "iat", "sub", "client_id"])
def test_required_claims(workos, signing_key, missing):
    data = token_claims()
    del data[missing]
    assert run(workos[0].verify_token(make_token(signing_key, data))) is None


def test_wrong_signature(workos):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert run(workos[0].verify_token(make_token(other))) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "PRIVATE-ACCESS-TOKEN"])
def test_malformed_token_does_not_log_or_fetch(workos, caplog, token):
    assert run(workos[0].verify_token(token)) is None
    assert workos[1]["calls"] == []
    assert not caplog.records


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS512"])
def test_untrusted_algorithm_rejected_before_network(workos, signing_key, algorithm):
    key = None if algorithm == "none" else "symmetric-test-secret-long-enough-32chars" if algorithm == "HS256" else signing_key
    assert run(workos[0].verify_token(make_token(key, algorithm=algorithm))) is None
    assert workos[1]["calls"] == []


def test_missing_kid_rejected_before_network(workos, signing_key):
    token = jwt.encode(token_claims(), signing_key, algorithm="RS256")
    assert run(workos[0].verify_token(token)) is None
    assert workos[1]["calls"] == []


def test_multiple_audiences_select_verified_resource(workos, signing_key):
    accepted = run(workos[0].verify_token(make_token(signing_key, token_claims(aud=["other", RESOURCE]))))
    assert accepted is not None and accepted.resource == RESOURCE


def test_jwks_cached_under_concurrent_requests(workos, signing_key):
    token = make_token(signing_key)

    async def scenario():
        assert all(await asyncio.gather(*(workos[0].verify_token(token) for _ in range(10))))

    run(scenario())
    assert len(workos[1]["calls"]) == 1


def test_jwks_rotation_refresh_after_bounded_cooldown(workos, signing_key):
    verifier, remote = workos
    assert run(verifier.verify_token(make_token(signing_key))) is not None
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    remote["payload"] = {"keys": [public_jwk(rotated, "rotated")]}
    token = make_token(rotated, kid="rotated")
    assert run(verifier.verify_token(token)) is None
    assert len(remote["calls"]) == 1  # Unknown kids cannot force unbounded fetches.
    verifier.jwks_client._last_successful_fetch -= verifier.jwks_client.cooldown_duration + 1
    assert run(verifier.verify_token(token)) is not None
    assert len(remote["calls"]) == 2
    assert run(verifier.verify_token(make_token(signing_key))) is None


def test_expired_jwks_cache_refreshes(workos, signing_key):
    verifier, remote = workos
    token = make_token(signing_key)
    assert run(verifier.verify_token(token)) is not None
    verifier.jwks_client.jwk_set_cache.jwk_set_with_timestamp.timestamp -= 301
    assert run(verifier.verify_token(token)) is not None
    assert len(remote["calls"]) == 2


@pytest.mark.parametrize("payload", [{}, {"keys": []}, [], {"keys": [{"kid": "first", "kty": "unknown"}]}])
def test_bad_jwks_fails_closed(workos, signing_key, payload):
    workos[1]["payload"] = payload
    assert run(workos[0].verify_token(make_token(signing_key))) is None


def test_jwks_network_error_fails_closed_without_token_logs(workos, signing_key, caplog):
    workos[1]["error"] = URLError("PRIVATE-ACCESS-TOKEN")
    assert run(workos[0].verify_token(make_token(signing_key))) is None
    assert "PRIVATE-ACCESS-TOKEN" not in caplog.text
