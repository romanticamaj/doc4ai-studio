"""Runtime effect of the [oauth] switches on existing grants (verifier INFO, 2026-11-06), and code consumption."""
import time

from aidoc.oauth import tokens as OT
from tests.oauth.conftest import (
    CLAUDE_CODE_CIMD,
    RES,
    authorize_code,
    exchange,
    mint_tokens,
    pkce,
    register_client,
)


def _cimd_grant(oauth_ctx, fake_cimd):
    from aidoc.oauth.clients import ClientRegistry
    fake_cimd.put(CLAUDE_CODE_CIMD["client_id"], CLAUDE_CODE_CIMD)
    ClientRegistry(oauth_ctx).resolve(CLAUDE_CODE_CIMD["client_id"], time.time())
    return mint_tokens(oauth_ctx, client_id=CLAUDE_CODE_CIMD["client_id"], redirect="http://localhost:3118/callback")


def test_allow_cimd_off_stops_refresh_and_access_of_cimd_grants(oauth_client, oauth_ctx, fake_cimd):
    m = _cimd_grant(oauth_ctx, fake_cimd)
    chain = oauth_ctx.extras["mcp"].verifier
    assert chain.verify(m.access_token, resource=RES).kind == "oauth"
    oauth_ctx.config.oauth.allow_cimd = False
    r = oauth_client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": m.refresh_token,
                                                "client_id": m.client_id})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert chain.verify(m.access_token, resource=RES).reason == "unknown_token"
    oauth_ctx.config.oauth.allow_cimd = True                             # switching back restores the connection
    assert chain.verify(m.access_token, resource=RES).kind == "oauth"
    r = oauth_client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": m.refresh_token,
                                                "client_id": m.client_id})
    assert r.status_code == 200


def test_allow_cimd_off_leaves_dcr_grants_alone(oauth_client, oauth_ctx):
    row = register_client(oauth_client)
    m = mint_tokens(oauth_ctx, client_id=row["client_id"])
    oauth_ctx.config.oauth.allow_cimd = False
    assert oauth_ctx.extras["mcp"].verifier.verify(m.access_token, resource=RES).kind == "oauth"
    r = oauth_client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": m.refresh_token,
                                                "client_id": m.client_id})
    assert r.status_code == 200


def test_no_disabled_at_column(oauth_ctx):
    """There is no per-client disable switch in this version (revocation is per grant); the reserved column is gone."""
    cols = {r[1] for r in oauth_ctx.store.con.execute("PRAGMA table_info(oauth_clients)")}
    assert "disabled_at" not in cols


def test_a_failed_code_exchange_burns_the_code(oauth_client, oauth_ctx):
    """Spec §5.3 (4): the code is consumed by the first exchange attempt whatever its outcome; a later attempt with the
    right verifier is invalid_grant (a stolen code tried with a wrong verifier first is dead for its owner too)."""
    row = register_client(oauth_client)
    code, v = authorize_code(oauth_client, row)
    bad = exchange(oauth_client, row, code, pkce()[0])
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    good = exchange(oauth_client, row, code, v)
    assert good.status_code == 400 and good.json()["error"] == "invalid_grant"
    assert oauth_ctx.store._q1("SELECT consumed_at FROM oauth_codes WHERE code_hash=?",
                               (OT.hash_(oauth_ctx.extras["mcp"].secret, code),))[0] is not None
