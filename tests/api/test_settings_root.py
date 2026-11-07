"""The Doc4AI folder over the API: PUT /settings general.root_dir, POST /settings/root-check, GET /system
(spec 2026-10-29 §3.4-§3.5, §10.1; plan Task 7, A11)."""
from __future__ import annotations

import difflib
import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.library_root_env           # these tests manage DOC4AI_LIBRARY_ROOT themselves

TOML = '# my settings\n[general]\n# results\nlang = "cht"  # keep me\nroot_dir = "{lib0}"\n'


@pytest.fixture
def api(client_factory, tmp_path, monkeypatch):
    monkeypatch.delenv("DOC4AI_LIBRARY_ROOT", raising=False)
    lib0 = tmp_path / "lib0"
    return client_factory(TOML.format(lib0=lib0.as_posix()))


def _added_removed(before: str, after: str) -> tuple[list[str], list[str]]:
    diff = list(difflib.ndiff(before.splitlines(), after.splitlines()))
    return [d[2:] for d in diff if d.startswith("+ ")], [d[2:] for d in diff if d.startswith("- ")]


@pytest.mark.parametrize("value, needle", [
    ("rel\\dir", "絕對路徑"), ("\\\\srv\\s\\x", "本機"), ("C:\\", "整個磁碟"), ("<data>\\x", "內部資料夾"),
    ("C:\\" + "a" * 201, "200"), ("\\\\?\\C:\\x", "本機")])
def test_put_root_dir_rejects(api, tmp_root, value, needle):
    value = value.replace("<data>", str(tmp_root / "data"))
    r = api.put("/api/settings", json={"general": {"root_dir": value}})
    assert r.status_code == 422 and needle in r.json()["detail"]                         # A11: 6 cases


def test_put_root_dir_saves_one_line(api, tmp_root, tmp_path):
    before = (tmp_root / "doc4ai.toml").read_text(encoding="utf-8")
    lib = tmp_path / "lib"
    r = api.put("/api/settings", json={"general": {"root_dir": str(lib)}})
    after = (tmp_root / "doc4ai.toml").read_text(encoding="utf-8")
    assert r.status_code == 200 and (lib / "out").is_dir() and (lib / "source").is_dir()
    assert not list(lib.rglob(".doc4ai-probe-*"))
    added, removed = _added_removed(before, after)
    assert added == [f"root_dir = {json.dumps(str(lib))}"] and len(removed) == 1 and removed[0].startswith("root_dir")
    assert "# my settings" in after and "# keep me" in after


def test_unwritable_root_is_refused(api, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    r = api.put("/api/settings", json={"general": {"root_dir": str(blocker / "lib")}})
    assert r.status_code == 422 and "無法寫入這個資料夾" in r.json()["detail"]


def test_root_dir_needs_local_path_rights(client_factory, tmp_path, monkeypatch):
    monkeypatch.delenv("DOC4AI_LIBRARY_ROOT", raising=False)
    api = client_factory(TOML.format(lib0=(tmp_path / "lib0").as_posix()))
    from doc4ai.server.app import create_app
    with TestClient(create_app(api.ctx), client=("10.0.0.5", 50000), base_url="http://127.0.0.1:8765") as remote:
        r = remote.put("/api/settings", json={"general": {"root_dir": str(tmp_path / "x")}})
        assert r.status_code == 403 and r.json()["error"] == "local_path_forbidden"
        r = remote.post("/api/settings/root-check", json={"root_dir": str(tmp_path / "x")})
        assert r.status_code == 403
        assert remote.put("/api/settings", json={"general": {"lang": "en"}}).status_code == 200   # others still fine


def test_root_dir_env_override_409(api, monkeypatch, tmp_path):
    monkeypatch.setenv("DOC4AI_LIBRARY_ROOT", str(tmp_path / "env"))
    r = api.put("/api/settings", json={"general": {"root_dir": str(tmp_path / "x")}})
    assert r.status_code == 409 and r.json()["error"] == "root_env_override"
    s = api.get("/api/system").json()
    assert s["root_from_env"] is True and s["root_dir"] == str(tmp_path / "env")


def test_root_check_creates_nothing(api, tmp_path):
    r = api.post("/api/settings/root-check", json={"root_dir": str(tmp_path / "new" / "lib")}).json()
    assert r["ok"] is True and r["problem"] is None and r["exists"] is False
    assert r["output_dir"] == str(tmp_path / "new" / "lib" / "out")
    assert r["source_dir"] == str(tmp_path / "new" / "lib" / "source") and r["free_bytes"] > 0
    assert not (tmp_path / "new").exists() and not list(tmp_path.glob(".doc4ai-probe-*"))
    bad = api.post("/api/settings/root-check", json={"root_dir": "relative\\x"}).json()
    assert bad["ok"] is False and bad["problem"] == "必須是本機的絕對路徑"


def test_hot_apply(api, tmp_path):
    lib = tmp_path / "lib"
    assert api.put("/api/settings", json={"general": {"root_dir": str(lib)}}).status_code == 200
    s = api.get("/api/system").json()
    assert s["source_dir"] == str(lib / "source") and s["output_dir"] == str(lib / "out")
    assert s["root_dir"] == str(lib) and s["root_configured"] is True and s["root_from_env"] is False
    assert s["source_disk_free"] > 0 and s["root_default"]
    assert api.ctx.config.output_root() == lib / "out"                          # the next job uses it


def test_output_dir_is_no_longer_exposed(api):
    g = api.get("/api/settings").json()["settings"]["general"]
    assert "output_dir" not in g and "root_dir" in g
    assert api.put("/api/settings", json={"general": {"output_dir": "out"}}).status_code == 200   # echo: ignored
    r = api.put("/api/settings", json={"general": {"output_dir": "D:\\elsewhere"}})
    assert r.status_code == 422 and "root_dir" in r.json()["detail"]


def test_root_check_never_claims_a_missing_folder_writable(api, tmp_path, monkeypatch):
    """Verifier V6: os.access only reads the read-only attribute on Windows (C:/Program Files/x looked writable).
    A folder that does not exist yet is reported as unchecked; the real probe happens on save."""
    import os as _os
    monkeypatch.setattr(_os, "access", lambda *a, **k: True)          # what Windows says for C:/Program Files
    r = api.post("/api/settings/root-check", json={"root_dir": str(tmp_path / "new" / "lib")}).json()
    assert r["ok"] is True and r["writable"] is None and r["exists"] is False
    assert r["nearest_existing"] == str(tmp_path)
    assert "儲存時會建立並檢查寫入權限" in r["note"]
    existing = api.post("/api/settings/root-check", json={"root_dir": str(tmp_path)}).json()
    assert existing["ok"] is True and existing["writable"] is True and existing["note"] is None
