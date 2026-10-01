"""[mcp] lists edited by hand in aidoc.toml are validated at load with the settings-API rules (S-phase finding 6).

Policy: a bad `allowed_hosts` entry or a `local_path_roots` directory that does not exist is ignored with a startup
warning; a `local_path_roots` entry that is a network (UNC / \\\\?\\) or relative path, or a list that is not a list
of strings, refuses to start — those would widen what convert_path can reach."""
import pytest

from aidoc import paths
from aidoc.cli import main
from aidoc.config import ConfigError, load_config


def _write(tmp_root, body: str):
    (tmp_root / "aidoc.toml").write_text(body, encoding="utf-8")


def _toml_list(items):
    return "[" + ", ".join("'" + i + "'" for i in items) + "]"              # literal strings: backslashes as-is


def test_bad_entries_are_dropped_with_warnings(tmp_root):
    docs = tmp_root / "docs"
    docs.mkdir()
    _write(tmp_root, "[mcp]\nallowed_hosts = " + _toml_list(["box.tail1.ts.net", "bad host!", "", "h:*"]) + "\n"
           "local_path_roots = " + _toml_list([str(docs), str(tmp_root / "gone")]) + "\n")
    cfg = load_config()
    assert cfg.mcp.allowed_hosts == ["box.tail1.ts.net", "h:*"]
    assert cfg.mcp.local_path_roots == [str(docs)]
    joined = "\n".join(cfg.warnings)
    assert len(cfg.warnings) == 3 and "'bad host!'" in joined and str(tmp_root / "gone") in joined and "ignored" in joined


@pytest.mark.parametrize("roots", [["\\\\nas\\share\\docs"], ["//nas/share/docs"], ["\\\\?\\C:\\docs"], ["relative\\docs"]])
def test_network_or_relative_roots_refuse_to_load(tmp_root, roots):
    _write(tmp_root, "[mcp]\nlocal_path_roots = " + _toml_list(roots) + "\n")
    with pytest.raises(ConfigError) as e:
        load_config()
    assert "mcp.local_path_roots" in str(e.value) and roots[0] in str(e.value)


@pytest.mark.parametrize("body", ["[mcp]\nlocal_path_roots = 'C:/docs'\n", "[mcp]\nallowed_hosts = [1, 2]\n"])
def test_lists_must_be_lists_of_strings(tmp_root, body):
    _write(tmp_root, body)
    with pytest.raises(ConfigError):
        load_config()


def test_serve_prints_warnings_and_refuses_unsafe_roots(tmp_root, monkeypatch, capsys):
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: None)
    _write(tmp_root, "[mcp]\nallowed_hosts = ['bad host!']\n")
    assert main(["serve"]) == 0
    assert "warning: mcp.allowed_hosts entry 'bad host!'" in capsys.readouterr().err
    _write(tmp_root, "[mcp]\nlocal_path_roots = ['\\\\nas\\share']\n")
    assert main(["serve"]) == 6
    err = capsys.readouterr().err
    assert "aidoc.toml" in err and "local_path_roots" in err
    assert not (paths.data_dir() / "aidoc.lock").exists()


def test_status_reports_config_warnings(client, ctx):
    ctx.config.warnings = ["mcp.allowed_hosts entry 'x y' is not a host[:port|:*]; ignored"]
    assert client.get("/api/mcp/status").json()["config_warnings"] == ctx.config.warnings


def test_other_commands_report_unsafe_config_without_a_traceback(tmp_root, capsys):
    _write(tmp_root, "[mcp]\nlocal_path_roots = ['docs']\n")
    (tmp_root / "x.pdf").write_bytes(b"%PDF-1.4")
    assert main(["convert", str(tmp_root / "x.pdf")]) == 6
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "local_path_roots" in err and "Traceback" not in err
