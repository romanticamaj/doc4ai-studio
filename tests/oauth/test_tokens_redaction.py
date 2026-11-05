"""Token formats (spec 2026-11-05 §5.4) and redaction of every new secret kind."""
import json
import re

import pytest

from aidoc.mcp import tokens as PT
from aidoc.mcp.redact import redact_text, redact_value
from aidoc.oauth import tokens as T
from aidoc.server.logfilter import redact as log_redact

B62 = re.compile(r"^[0-9A-Za-z]+$")


@pytest.mark.parametrize("prefix", [T.AT_PREFIX, T.RT_PREFIX, T.CODE_PREFIX])
def test_format_and_checksum(prefix):
    raw = T.generate(prefix)
    body, crc = raw[len(prefix):].split("_")
    assert raw.startswith(prefix) and len(body) == 43 and len(crc) == 6 and B62.match(body) and B62.match(crc)
    assert T.is_well_formed(prefix, raw)
    assert not T.is_well_formed(prefix, raw[:-1] + ("A" if raw[-1] != "A" else "B"))      # checksum
    assert not T.is_well_formed(prefix, raw + "x") and not T.is_well_formed(prefix, raw.replace("_", "-", 2))
    others = {T.AT_PREFIX, T.RT_PREFIX, T.CODE_PREFIX} - {prefix}
    assert not any(T.is_well_formed(o, raw) for o in others)
    assert T.display_prefix(raw) == raw[: len(prefix) + 4] and T.prefix_seen(raw) == raw[: len(prefix) + 4]
    assert T.generate(prefix) != raw


def test_pat_is_not_an_oauth_token_and_client_ids():
    pat = PT.generate_token()
    assert not T.is_well_formed(T.AT_PREFIX, pat) and not T.is_well_formed(T.RT_PREFIX, pat)
    assert not T.is_well_formed(T.AT_PREFIX, None) and not T.is_well_formed(T.AT_PREFIX, 12)
    cid = T.generate_client_id()
    assert cid.startswith("doc4ai_cl_") and len(cid) == len("doc4ai_cl_") + 22 and B62.match(cid[len("doc4ai_cl_"):])
    assert T.prefix_seen(pat) == pat[:15] and T.prefix_seen("random") is None
    assert T.hash_(b"k" * 32, "x") == PT.token_hash(b"k" * 32, "x")


def test_redaction_new_prefixes():
    at, rt, code = T.generate(T.AT_PREFIX), T.generate(T.RT_PREFIX), T.generate(T.CODE_PREFIX)
    cid = T.generate_client_id()
    out = redact_text(f"Authorization: Bearer {at}")
    assert at[len(T.AT_PREFIX):] not in out and "<redacted" in out
    out = redact_text(f"saw {at} {rt} {code} {cid}")
    for raw, kind in ((at, "at"), (rt, "rt"), (code, "ac"), (cid, "cl")):
        assert raw not in out and f"doc4ai_{kind}_<redacted:{raw.split('_')[2][:4]}>" in out
    form = f"grant_type=authorization_code&code={code}&code_verifier=abcdefverifier123&redirect_uri=x"
    out = redact_text(form)
    assert code not in out and "abcdefverifier123" not in out and "code_verifier=<redacted>" in out and "redirect_uri=x" in out
    out = redact_text("grant_type=refresh_token&refresh_token=opaque-refresh-secret&client_secret=sss")
    assert "opaque-refresh-secret" not in out and "sss" not in out
    v = redact_value({"refresh_token": rt, "access_token": "opaque", "code_verifier": "vvv", "client_secret": "s",
                      "nested": [{"refresh_token": "x"}], "scope": "doc4ai:read"})
    s = json.dumps(v)
    assert rt not in s and "opaque" not in s and "vvv" not in s and v["scope"] == "doc4ai:read"
    assert v["access_token"] == "<redacted>" and v["nested"][0]["refresh_token"] == "<redacted>"
    # the MCP error payload's "code" (forbidden_scope, ...) is an error name, never a secret: it stays readable
    assert redact_value({"code": "forbidden_scope", "message": "m"}) == {"code": "forbidden_scope", "message": "m"}
    assert redact_value({"code": code})["code"].startswith("doc4ai_ac_<redacted:")
    # a bare display prefix (what the admin UI shows) stays readable
    assert redact_text("doc4ai_at_abcd and doc4ai_cl_wxyz") == "doc4ai_at_abcd and doc4ai_cl_wxyz"


def test_uvicorn_access_line_redacted():
    code = T.generate(T.CODE_PREFIX)
    line = f'127.0.0.1:5000 - "POST /oauth/token?code={code}&code_verifier=plainverifier HTTP/1.1" 400'
    out = log_redact(line)
    assert code not in out and "plainverifier" not in out and "/oauth/token?" in out and "HTTP/1.1" in out
    cb = f'"GET /callback?code={code}&state=st4te&iss=http%3A%2F%2F127.0.0.1%3A8765 HTTP/1.1"'
    out = log_redact(cb)
    assert code not in out and "state=st4te" in out
