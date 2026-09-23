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
