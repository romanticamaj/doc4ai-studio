"""Constants pinned by the plan (2026-11-05 local MCP OAuth, Global Constraints)."""


def test_pinned_constants():
    from aidoc import store
    from aidoc.oauth import clients, endpoints, grants, tokens
    assert store.SCHEMA_VERSION == 8
    assert (tokens.AT_PREFIX, tokens.RT_PREFIX, tokens.CODE_PREFIX, tokens.CLIENT_PREFIX) == (
        "doc4ai_at_", "doc4ai_rt_", "doc4ai_ac_", "doc4ai_cl_")
    assert tokens.CLIENT_ID_LEN == 22
    assert clients.CIMD_MAX_BYTES == 65536 and clients.CIMD_TIMEOUT_S == 5 and clients.CIMD_FETCH_PER_MIN == 6
    assert clients.CIMD_STALE_OK_S == 7 * 86400 and clients.DCR_UNUSED_DAYS == 30
    assert clients.LOOPBACK_HOSTS == ("localhost", "127.0.0.1", "::1")
    assert grants.GRANT_REVOKED_KEEP_DAYS == 30 and grants.TOKEN_EXPIRED_KEEP_DAYS == 90
    assert grants.CODE_CONSUMED_KEEP_S == 3600
    assert endpoints.AUTHORIZE_GET_PER_MIN == 60
    assert endpoints.DEFAULT_CHALLENGE_SCOPE == "doc4ai:read doc4ai:convert"
