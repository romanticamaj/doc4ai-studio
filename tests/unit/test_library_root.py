"""The Doc4AI folder (spec 2026-10-29 §3): resolution, validation, warnings (plan Task 1)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aidoc.config import ConfigError, default_library_root, load_config, root_dir_problem, root_dir_writable

pytestmark = pytest.mark.library_root_env


def _default() -> Path:
    return Path("C:\\doc4ai") if sys.platform == "win32" else Path("~/doc4ai").expanduser()


def test_unset_root_is_the_platform_default(tmp_root, monkeypatch):
    """User decision 2026-10-29: no Doc4AI folder set = C:\\doc4ai (Windows) / ~/doc4ai, never the repo's out/."""
    monkeypatch.delenv("AIDOC_LIBRARY_ROOT", raising=False)
    (tmp_root / "aidoc.toml").write_text('[general]\nlang = "cht"\n', encoding="utf-8")
    existed = _default().exists()
    cfg = load_config()
    assert default_library_root() == _default()
    assert cfg.library_root() == _default()
    assert cfg.output_root() == _default() / "out" and cfg.source_root() == _default() / "source"
    assert not cfg.root_configured() and not cfg.root_from_env()
    assert cfg.warnings == []
    assert _default().exists() == existed                    # loading never creates it


def test_legacy_output_dir_is_ignored_with_one_migration_note(tmp_root, tmp_path, monkeypatch):
    monkeypatch.delenv("AIDOC_LIBRARY_ROOT", raising=False)
    lib = tmp_path / "lib"
    (tmp_root / "aidoc.toml").write_text(f"[general]\nroot_dir = '{lib}'\noutput_dir = \"out\"\n",
                                         encoding="utf-8")
    cfg = load_config()
    assert cfg.general.root_dir == str(lib)
    assert cfg.output_root() == lib / "out" and cfg.source_root() == lib / "source" and cfg.library_root() == lib
    assert cfg.output_root() != tmp_root / "out"             # never the repo's out/
    assert cfg.root_configured() and not cfg.root_from_env()
    notes = [w for w in cfg.warnings if "general.output_dir" in w]
    assert len(notes) == 1 and "no longer used" in notes[0] and str(lib / "out") in notes[0]
    assert not lib.exists()                                  # loading never creates the folder


def test_env_overrides_root_dir(tmp_root, tmp_path, monkeypatch):
    lib, env = tmp_path / "lib", tmp_path / "envlib"
    (tmp_root / "aidoc.toml").write_text(f'[general]\nroot_dir = "{lib.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(env))
    cfg = load_config()
    assert cfg.root_from_env() and cfg.root_configured()
    assert cfg.library_root() == env and cfg.output_root() == env / "out" and cfg.source_root() == env / "source"


@pytest.mark.parametrize("value, needle", [
    (r"relative\dir", "絕對路徑"), (r"\\server\share\x", "本機"), (r"\\?\C:\x", "本機"),
    ("C:\\", "整個磁碟"), (r"<data_dir>\lib", "內部資料夾"), ("C:\\" + "a" * 200, "200")])
def test_root_dir_problem(value, needle, tmp_root):
    data = tmp_root / "data"
    value = value.replace("<data_dir>", str(data))
    problem = root_dir_problem(value, data_dir=data, envs_dir=tmp_root / "envs")
    assert problem is not None and needle in problem


def test_root_dir_problem_accepts_empty_and_a_plain_folder(tmp_root, tmp_path):
    kw = {"data_dir": tmp_root / "data", "envs_dir": tmp_root / "envs"}
    assert root_dir_problem("", **kw) is None
    assert root_dir_problem(str(tmp_path / "lib"), **kw) is None
    # the data dir inside root/out is refused as well (V4, second half)
    assert "內部資料夾" in root_dir_problem(str(tmp_path / "x"), data_dir=tmp_path / "x" / "out" / "data",
                                            envs_dir=tmp_root / "envs")


def test_invalid_root_dir_in_toml_refuses_to_start(tmp_root, monkeypatch):
    monkeypatch.delenv("AIDOC_LIBRARY_ROOT", raising=False)
    (tmp_root / "aidoc.toml").write_text('[general]\nroot_dir = "relative/dir"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="root_dir"):
        load_config()


def test_invalid_env_root_refuses_to_start(tmp_root, monkeypatch):
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", "relative/dir")
    with pytest.raises(ConfigError, match="AIDOC_LIBRARY_ROOT"):
        load_config()


def test_root_dir_writable_creates_out_and_source(tmp_path):
    lib = tmp_path / "a" / "lib"
    assert root_dir_writable(lib, create=True) is None
    assert (lib / "out").is_dir() and (lib / "source").is_dir()
    assert not list(lib.rglob(".aidoc-probe-*"))


def test_root_dir_writable_check_creates_nothing(tmp_path):
    lib = tmp_path / "new" / "lib"
    assert root_dir_writable(lib, create=False) is None
    assert not (tmp_path / "new").exists() and not list(tmp_path.glob(".aidoc-probe-*"))
    f = tmp_path / "file"
    f.write_text("x", encoding="utf-8")
    assert "無法寫入" in root_dir_writable(f / "lib", create=False)


def test_root_is_normalized(tmp_root, tmp_path, monkeypatch):
    """Verifier P4: `lib\\x\\..` and `lib` are one folder, so planning and ownership checks see one path."""
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(lib) + "\\x\\..")
    cfg = load_config()
    assert cfg.library_root() == lib and cfg.source_root() == lib / "source"
