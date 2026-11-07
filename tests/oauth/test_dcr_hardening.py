"""/oauth/register hardening (verifier F2): no cross-site or non-JSON registrations, bounded stored metadata, and a quota
that junk registrations cannot use to lock a real client out (the oldest unused registration gives way)."""
import json
import time

from doc4ai.oauth import clients
from doc4ai.oauth.clients import ClientRegistry
from tests.oauth.conftest import ISSUER, mint_tokens

BODY = {"client_name": "Cursor", "redirect_uris": ["http://localhost:8787/callback"], "token_endpoint_auth_method": "none"}


def test_cross_site_and_non_json_refused_without_using_the_quota(oauth_client, oauth_ctx):
    raw = json.dumps(BODY)
    refused = [
        ({"Content-Type": "text/plain"}, 400),                                            # a form / no-cors body
        ({"Content-Type": "application/x-www-form-urlencoded"}, 400),
        ({"Content-Type": "application/json", "Origin": "https://evil.example"}, 403),
        ({"Content-Type": "application/json", "Origin": "null"}, 403),
        ({"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "application/json", "Sec-Fetch-Site": "same-site"}, 403),
        ({"Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, 403),
    ]
    for _ in range(5):
        for headers, status in refused:
            r = oauth_client.post("/oauth/register", content=raw, headers=headers)
            assert r.status_code == status, (headers, r.text)
    assert r.json()["error"] in ("invalid_client_metadata", "cross_origin")
    assert [c for c in oauth_ctx.store.list_oauth_clients() if c["kind"] == "dcr"] == []
    for headers in ({"Content-Type": "application/json"}, {"Content-Type": "application/json; charset=utf-8", "Origin": ISSUER,
                                                            "Sec-Fetch-Site": "same-origin"},
                    {"Content-Type": "application/json", "Sec-Fetch-Site": "none"}):
        assert oauth_client.post("/oauth/register", content=raw, headers=headers).status_code == 201


def test_stored_metadata_is_bounded(oauth_client, oauth_ctx):
    big = oauth_client.post("/oauth/register", json={**BODY, "pad": "A" * 20000})
    assert big.status_code == 400 and big.json()["error"] == "invalid_client_metadata"
    r = oauth_client.post("/oauth/register", json={**BODY, "extra_vendor_field": "x" * 100, "software_id": "s"})
    assert r.status_code == 201
    stored = oauth_ctx.store.get_oauth_client(r.json()["client_id"])["metadata"]
    assert "extra_vendor_field" not in stored and stored["software_id"] == "s" and stored["client_name"] == "Cursor"
    assert len(json.dumps(stored)) <= clients.DCR_METADATA_MAX


def test_junk_registrations_cannot_lock_out_a_real_client(oauth_client, oauth_ctx):
    """The verifier's P2: 21 junk registrations used to answer the next real one with 429 for an hour."""
    limit = oauth_ctx.config.oauth.dcr_per_hour_per_ip
    junk = [oauth_client.post("/oauth/register", json={**BODY, "client_name": f"junk{i}"}).json()["client_id"] for i in range(limit + 5)]
    real = oauth_client.post("/oauth/register", json=BODY)
    assert real.status_code == 201
    left = {c["client_id"] for c in oauth_ctx.store.list_oauth_clients() if c["kind"] == "dcr"}
    assert real.json()["client_id"] in left and len(left) == limit                    # the oldest unused gave way
    assert junk[0] not in left and junk[-1] in left


def test_a_used_client_is_never_pruned(oauth_client, oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    now = time.time()
    first = reg.register_dcr(dict(BODY), "1.1.1.1", now)["client_id"]
    mint_tokens(oauth_ctx, client_id=first)                                           # it got a grant
    for i in range(oauth_ctx.config.oauth.dcr_per_hour_per_ip + 3):
        reg.register_dcr(dict(BODY), "1.1.1.1", now + i)
    assert oauth_ctx.store.get_oauth_client(first) is not None


def test_global_unused_cap(oauth_ctx, monkeypatch):
    monkeypatch.setattr(clients, "DCR_UNUSED_MAX", 5)
    reg = ClientRegistry(oauth_ctx)
    now = time.time()
    ids = [reg.register_dcr(dict(BODY), f"10.0.0.{i}", now + i)["client_id"] for i in range(8)]
    left = [c["client_id"] for c in oauth_ctx.store.list_oauth_clients() if c["kind"] == "dcr"]
    assert len(left) == 5 and set(left) == set(ids[-5:])


def test_flood_ceiling_still_answers_429(oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    oauth_ctx.config.oauth.dcr_per_hour_per_ip = 2
    now = time.time()
    ceiling = 2 * clients.DCR_ATTEMPT_FACTOR
    for i in range(ceiling):
        reg.register_dcr(dict(BODY), "3.3.3.3", now + i)
    try:
        reg.register_dcr(dict(BODY), "3.3.3.3", now + ceiling)
        raise AssertionError("no 429")
    except clients.ClientError as e:
        assert e.code == "rate_limited" and e.retry_after > 0
    reg.register_dcr(dict(BODY), "3.3.3.3", now + 3601)                                # a new hour
