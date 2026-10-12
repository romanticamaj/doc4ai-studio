import json
from pathlib import Path

from aidoc import paths
from aidoc import setup_engines as se
from aidoc.config import load_config


def test_plan_commands_mineru(tmp_root):
    cfg = load_config()
    cmds = se.plan_commands("mineru", cfg)
    assert cmds[0][:3] == ["uv", "sync", "--project"] and cmds[0][3].endswith("mineru")
    py = str(paths.venv_python("mineru"))
    # plan: the downloader is the mineru-kit console script ("mineru-kit(.exe)" on Windows) -> compare the stem
    assert cmds[1][:2] == [py, "-m"] and "mineru-kit" in " ".join(cmds[1]) or Path(cmds[1][0]).stem == "mineru-kit"
    assert "--tier" in cmds[1] and cmds[1][cmds[1].index("--tier") + 1] == "basic"


def test_plan_commands_docling(tmp_root):
    cmds = se.plan_commands("docling", load_config())
    dl = " ".join(cmds[1])
    assert "docling-tools" in dl and "models download" in dl and "easyocr" in dl
    assert "--easyocr-lang iso:zh-Hant" in dl and "--easyocr-lang en" in dl
    assert "--output-dir" in cmds[1] and cmds[1][cmds[1].index("--output-dir") + 1].endswith("docling")


def test_setup_writes_ready_on_success(tmp_root, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = json.dumps({"ok": True, "cuda": True, "arch_list": ["sm_120"], "sample_chars": 120})
            stderr = ""
        return R()
    (tmp_root / "envs" / "docling" / ".venv" / "Scripts").mkdir(parents=True)
    paths.venv_python("docling").parent.mkdir(parents=True, exist_ok=True)
    paths.venv_python("docling").write_text("")
    monkeypatch.setattr(se, "predownload_tiktoken", lambda d: None)
    logs = []
    assert se.setup("docling", load_config(), logs.append, run=fake_run) is True
    ready = json.loads(paths.ready_marker("docling").read_text())
    assert ready["engine"] == "docling" and ready["cuda"] is True and len(calls) == 3


def test_setup_fails_without_sm120(tmp_root, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = json.dumps({"ok": True, "cuda": True, "arch_list": ["sm_90"], "sample_chars": 120})
            stderr = ""
        return R()
    (tmp_root / "envs" / "mineru" / ".venv" / "Scripts").mkdir(parents=True)
    paths.venv_python("mineru").parent.mkdir(parents=True, exist_ok=True)
    paths.venv_python("mineru").write_text("")
    monkeypatch.setattr(se, "predownload_tiktoken", lambda d: None)
    logs = []
    assert se.setup("mineru", load_config(), logs.append, run=fake_run) is False
    assert not paths.ready_marker("mineru").exists() and any("sm_120" in line for line in logs)


def test_markitdown_needs_no_gpu(tmp_root, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = json.dumps({"ok": True, "cuda": None, "arch_list": [], "sample_chars": 80})
            stderr = ""
        return R()
    (tmp_root / "envs" / "markitdown" / ".venv" / "Scripts").mkdir(parents=True)
    paths.venv_python("markitdown").parent.mkdir(parents=True, exist_ok=True)
    paths.venv_python("markitdown").write_text("")
    monkeypatch.setattr(se, "predownload_tiktoken", lambda d: None)
    assert se.setup("markitdown", load_config(), lambda s: None, run=fake_run) is True


def test_setup_vlm_with_backend_none_refuses(tmp_root):
    from aidoc.config import AidocConfig
    logs = []
    assert not se.setup("vlm", AidocConfig(), logs.append, run=lambda *a, **k: None) and any("backend" in s for s in logs)


def test_setup_vlm_local_is_not_supported_yet(tmp_root):
    """Spike S1 rule 4 -> Task 8c: the local backend is not delivered; setup says so and points at the spike."""
    from aidoc.config import AidocConfig
    cfg = AidocConfig()
    cfg.vlm.backend = "local"
    logs = []
    assert not se.setup("vlm", cfg, logs.append, run=lambda *a, **k: None)
    assert any("not supported on this platform yet" in s and "2026-10-vlm-ocr.md" in s for s in logs)
    assert not paths.ready_marker("vlm").exists()


def test_setup_all_skips_an_unconfigured_vlm(tmp_root):
    from aidoc.config import AidocConfig
    seen = []
    se.setup("all", AidocConfig(), lambda s: None, run=lambda cmd, **kw: seen.append(cmd) or
             type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})())
    assert not any("vlm" in " ".join(map(str, c)) for c in seen)


def test_setup_vlm_openai_is_the_connection_check(tmp_root, monkeypatch):
    from aidoc.config import AidocConfig
    from tests.fakes.vlm_server import FakeVlmServer
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "sk-test")
    cfg = AidocConfig()
    cfg.vlm.backend, cfg.vlm.model, cfg.vlm.remote_enabled = "openai", "fake-vlm", True
    logs = []
    with FakeVlmServer({"default": "ok", "pages": {"1": "AIDOC VLM CHECK 繁體中文 2026"}, "require_key": "sk-test"}) as base:
        cfg.vlm.base_url = base
        assert se.setup("vlm", cfg, logs.append, run=lambda *a, **k: None)
    ready = json.loads(paths.ready_marker("vlm").read_text(encoding="utf-8"))
    assert ready["backend"] == "openai" and ready["model"] == "fake-vlm" and ready["endpoint_host"].startswith("127.0.0.1:")
    assert ready["checked_at"] and "sk-test" not in "\n".join(logs) + json.dumps(ready)
    with FakeVlmServer({"default": "unauthorized"}) as base:
        cfg.vlm.base_url = base
        logs.clear()
        assert not se.check_vlm(cfg, logs.append)
    assert any("401" in s for s in logs)
    with FakeVlmServer({"default": "ok", "pages": {"1": "something else entirely"}}) as base:
        cfg.vlm.base_url = base
        assert not se.check_vlm(cfg, logs.append)
