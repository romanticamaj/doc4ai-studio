import time

import pytest

from aidoc.mcp import tokens as T
from aidoc.mcp.principal import SCOPES, AuthFailure, PatVerifier, Principal, current_principal, parse_bearer
from aidoc.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    yield s
    s.close()


SECRET = b"s" * 32


def _issue(store, scopes=("doc4ai:read",), expires_in=3600, rate=None):
    raw = T.generate_token()
    tid = store.create_api_token(name="t", prefix=T.display_prefix(raw), token_hash=T.token_hash(SECRET, raw),
                                 scopes=list(scopes), expires_at=None if expires_in is None else time.time() + expires_in,
                                 rate_limit_per_min=rate)
    return raw, tid


def test_scopes_constant():
    assert SCOPES == ("doc4ai:read", "doc4ai:convert", "doc4ai:convert:local", "doc4ai:manage")


def test_verify_good_token(store):
    raw, tid = _issue(store, scopes=("doc4ai:read", "doc4ai:manage"), rate=5)
    p = PatVerifier(store, SECRET).verify(raw)
    assert isinstance(p, Principal) and p.kind == "pat" and p.subject == "owner" and p.token_id == tid
    assert p.scopes == frozenset({"doc4ai:read", "doc4ai:manage"}) and p.has("doc4ai:manage") and not p.has("doc4ai:convert")
    assert p.rate_limit_per_min == 5 and p.name == "t" and p.expires_at is not None


@pytest.mark.parametrize("raw,reason", [
    (None, "missing_token"), ("", "missing_token"), ("abc", "bad_format"), ("doc4ai_pat_" + "A" * 43 + "_000000", "bad_checksum"),
])
def test_verify_format_failures(store, raw, reason):
    f = PatVerifier(store, SECRET).verify(raw)
    assert isinstance(f, AuthFailure) and f.reason == reason
    assert f.prefix_seen == (raw[:15] if raw and raw.startswith("doc4ai_pat_") else None)


def test_unknown_revoked_expired(store):
    v = PatVerifier(store, SECRET)
    unknown = T.generate_token()
    f = v.verify(unknown)
    assert f.reason == "unknown_token" and f.prefix_seen == unknown[:15]
    raw, tid = _issue(store)
    store.revoke_api_token(tid, "bye", at=time.time())
    assert v.verify(raw).reason == "revoked"
    raw2, _ = _issue(store, expires_in=-1)
    assert v.verify(raw2).reason == "expired"
    raw3, _ = _issue(store, expires_in=None)                      # never expires
    assert isinstance(v.verify(raw3), Principal)


def test_future_revocation_is_a_grace_window(store):
    raw, tid = _issue(store)
    store.revoke_api_token(tid, "rotated", at=time.time() + 60)   # Phase 1.5 grace hook
    v = PatVerifier(store, SECRET)
    assert isinstance(v.verify(raw), Principal)
    assert v.verify(raw, now=time.time() + 61).reason == "revoked"


def test_wrong_secret_never_matches(store):
    raw, _ = _issue(store)
    assert PatVerifier(store, b"x" * 32).verify(raw).reason == "unknown_token"


@pytest.mark.parametrize("header,expect", [
    ("Bearer abc", "abc"), ("bearer abc", "abc"), ("BEARER  abc  ", "abc"), ('Bearer "abc"', "abc"), ("Bearer abc\n", "abc"),
    ("Basic abc", None), ("abc", None), (None, None), ("Bearer", None), ("Bearer ", None),
])
def test_parse_bearer(header, expect):
    assert parse_bearer(header) == expect


def test_contextvar_default_is_none():
    assert current_principal.get() is None
