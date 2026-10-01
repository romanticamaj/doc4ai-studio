"""AIDOC_REQUIRE_MANUAL turns a missing real sample into a failure (spec 2026-10-01 §10.2)."""
import os
import subprocess
import sys
import textwrap

from aidoc import paths

ROOT = str(paths.project_root())


def _pytest(tmp_path, env_extra):
    t = tmp_path / "test_gate_probe.py"
    t.write_text(textwrap.dedent("""
        from tests.manual import manual_sample
        def test_x():
            manual_sample("ortho_p1-80.pdf")
    """), encoding="utf-8")
    env = {**os.environ, **env_extra, "PYTHONPATH": ROOT}
    return subprocess.run([sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:cacheprovider", "--rootdir", ROOT],
                          capture_output=True, text=True, env=env, cwd=ROOT, check=False)


def test_missing_sample_skips_normally_and_fails_in_acceptance(tmp_path):
    empty = {"AIDOC_MANUAL_DIR": str(tmp_path / "empty")}
    assert "1 skipped" in _pytest(tmp_path, {**empty, "AIDOC_REQUIRE_MANUAL": "0"}).stdout
    assert "1 failed" in _pytest(tmp_path, {**empty, "AIDOC_REQUIRE_MANUAL": "1"}).stdout


def test_unknown_sample_name_always_fails(tmp_path):
    import pytest

    from tests.manual import manual_sample
    with pytest.raises(pytest.fail.Exception):
        manual_sample("not_in_manifest.pdf")
