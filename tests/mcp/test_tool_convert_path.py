import json
import os
import shutil
import sys

import pytest

from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.tools_convert import check_local_path
from tests.mcp.conftest import mcp_call


def _err(res):
    assert res.is_error is True, res.structured_content
    return res.structured_content or json.loads(res.content[0].text)


@pytest.fixture
def roots(tmp_path, fixtures):
    allowed = tmp_path / "allowed"
    (allowed / "sub").mkdir(parents=True)
    shutil.copy(fixtures / "text.pdf", allowed / "sub" / "text.pdf")
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(fixtures / "text.pdf", outside / "secret.pdf")
    return allowed, outside


def test_check_local_path_accepts_inside_and_case_variants(roots):
    allowed, _ = roots
    p = check_local_path(str(allowed / "sub" / "text.pdf"), [str(allowed)])
    assert p == (allowed / "sub" / "text.pdf").resolve()
    if sys.platform == "win32":
        assert check_local_path(str(allowed / "sub" / "TEXT.PDF").upper(), [str(allowed).lower()]) == p


@pytest.mark.parametrize("make", [
    lambda a, o: str(o / "secret.pdf"),                                  # outside
    lambda a, o: str(a / "sub" / ".." / ".." / "outside" / "secret.pdf"),  # .. escape
    lambda a, o: "\\\\nas\\share\\x.pdf", lambda a, o: "//nas/share/x.pdf",   # UNC
    lambda a, o: "\\\\?\\" + str(a / "sub" / "text.pdf"),               # \\?\ prefix
    lambda a, o: "sub/text.pdf", lambda a, o: "",                        # relative / empty
    lambda a, o: str(a / "sub" / "text.pdf") + "\x00",
])
def test_check_local_path_rejects(roots, make):
    allowed, outside = roots
    with pytest.raises(ToolFailure) as e:
        check_local_path(make(allowed, outside), [str(allowed)])
    assert e.value.code == "path_not_allowed" and "allowed" not in e.value.message.lower().replace("not allowed", "")


def test_check_local_path_missing_and_directory(roots):
    allowed, _ = roots
    for p in (allowed / "sub" / "nope.pdf", allowed / "sub"):
        with pytest.raises(ToolFailure) as e:
            check_local_path(str(p), [str(allowed)])
        assert e.value.code == "path_not_found"


def test_symlink_pointing_outside_is_rejected(roots):
    allowed, outside = roots
    link = allowed / "sub" / "link.pdf"
    try:
        os.symlink(outside / "secret.pdf", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    with pytest.raises(ToolFailure) as e:
        check_local_path(str(link), [str(allowed)])
    assert e.value.code == "path_not_allowed"


def test_convert_path_tool(mcp_server, mcp_env, roots):
    allowed, outside = roots
    ctx = mcp_env.ctx
    raw, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert:local"))
    e = _err(mcp_call(mcp_server, raw, "convert_path", {"path": str(allowed / "sub" / "text.pdf")}))
    assert e["code"] == "tool_disabled"                                  # no roots configured yet
    ctx.config.mcp.local_path_roots = [str(allowed)]
    e = _err(mcp_call(mcp_server, raw, "convert_path", {"path": str(outside / "secret.pdf")}))
    assert e["code"] == "path_not_allowed"
    out = mcp_call(mcp_server, raw, "convert_path", {"path": str(allowed / "sub" / "text.pdf")}).structured_content
    assert out["job_id"] and out["status"] == "queued"
    assert ctx.store.get_job(out["job_id"])["origin"] == "mcp"
    task = ctx.store.list_tasks(out["job_id"])[0]
    assert task["source_path"] == str((allowed / "sub" / "text.pdf").resolve())
    ctx.queue.process_next()
    cached = mcp_call(mcp_server, raw, "convert_path", {"path": str(allowed / "sub" / "text.pdf")}).structured_content
    assert cached["status"] == "cached" and cached["doc_id"]
    ctx.config.mcp.local_path_roots = []
    raw2, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    ctx.config.mcp.local_path_roots = [str(allowed)]
    assert _err(mcp_call(mcp_server, raw2, "convert_path", {"path": str(allowed / "sub" / "text.pdf")}))["code"] == "forbidden_scope"
    ctx.config.mcp.local_path_roots = []


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")
def test_junction_pointing_outside_is_rejected(roots):
    """Junctions need no privilege on Windows (unlike symlinks), so this is the realistic escape there."""
    import _winapi
    allowed, outside = roots
    _winapi.CreateJunction(str(outside), str(allowed / "jn"))
    with pytest.raises(ToolFailure) as e:
        check_local_path(str(allowed / "jn" / "secret.pdf"), [str(allowed)])
    assert e.value.code == "path_not_allowed"
