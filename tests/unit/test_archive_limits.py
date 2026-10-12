"""ZIP-based inputs (Office, EPUB, ODF, zip) are checked before any engine runs (verifier item 1a)."""
import json
import zipfile

import pytest

from aidoc.config import Limits


def bomb_docx(path, *, mb=50, entries=1):
    """A valid-looking .docx whose document.xml inflates from a few KB to `mb` MB of zeros."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        chunk = b"\0" * (1024 * 1024)
        with z.open("word/document.xml", "w", force_zip64=True) as f:
            for _ in range(mb):
                f.write(chunk)
        for i in range(entries - 1):
            z.writestr(f"word/media/e{i}.bin", b"x")
    return path


def test_limits_defaults():
    lim = Limits()
    assert (lim.archive_max_uncompressed_mb, lim.archive_max_entries, lim.archive_max_entry_mb, lim.archive_max_ratio) == \
        (512, 10000, 256, 200)


def test_ratio_bomb_is_refused(tmp_path):
    from aidoc.archive import check_archive
    p = bomb_docx(tmp_path / "bomb.docx", mb=50)
    problem = check_archive(p, Limits())
    assert problem and problem.startswith("archive_too_large") and "ratio" in problem


def test_total_entry_and_count_limits(tmp_path):
    from aidoc.archive import check_archive
    p = tmp_path / "big.docx"
    import os
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("word/document.xml", os.urandom(3 * 1024 * 1024))     # incompressible: ratio ~1
    assert check_archive(p, Limits()) is None
    assert "total" in check_archive(p, Limits(archive_max_uncompressed_mb=2))
    assert "entry" in check_archive(p, Limits(archive_max_entry_mb=2))
    many = bomb_docx(tmp_path / "many.docx", mb=0, entries=30)
    assert "entries" in check_archive(many, Limits(archive_max_entries=10))


def test_non_zip_and_broken_zip_are_left_to_the_engines(tmp_path):
    from aidoc.archive import check_archive
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.docx").write_bytes(b"PK\x03\x04garbage")
    assert check_archive(tmp_path / "a.pdf", Limits()) is None
    assert check_archive(tmp_path / "b.docx", Limits()) is None


@pytest.mark.parametrize("ext", [".docx", ".xlsx", ".pptx", ".epub", ".odt", ".zip"])
def test_every_zip_based_type_is_checked(tmp_path, ext):
    from aidoc.archive import check_archive
    p = bomb_docx(tmp_path / f"bomb{ext}", mb=30)
    assert check_archive(p, Limits()) is not None


def test_pipeline_refuses_the_bomb_before_any_engine_runs(ctx, tmp_path):
    """The web upload, CLI, MCP and compat entry points all reach run_task: one check covers them."""
    from aidoc.models import ConvertOptions
    from aidoc.pipeline import run_task
    src = bomb_docx(tmp_path / "bomb.docx", mb=50)
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "cli")
    from aidoc.names import file_sha256
    tid, _ = ctx.store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                                   str(ctx.config.output_root() / "bomb"))
    from aidoc.engines.registry import get_engines
    status = run_task(ctx.store, tid, get_engines(ctx.config), ctx.config)
    task = ctx.store.get_task(tid)
    assert status.value == "failed" and task["error_kind"] == "input" and task["error_msg"].startswith("archive_too_large")
    log = ctx.config.root / "calls.jsonl"
    assert not log.exists() or not [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
