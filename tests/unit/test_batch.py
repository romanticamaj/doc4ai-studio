import json
import shutil

from aidoc.batch import collect_inputs, manifest_rows, run_batch
from aidoc.cli import main
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def setup_inputs(tmp_root, fixtures, names):
    d = tmp_root / "in"
    (d / "sub").mkdir(parents=True)
    (d / ".hidden").mkdir()
    for i, n in enumerate(names):
        shutil.copy(fixtures / n, (d / "sub" if i % 2 else d) / n)
    (d / ".hidden" / "x.pdf").write_bytes(b"x")
    return d


def test_collect_inputs(tmp_root, fixtures):
    d = setup_inputs(tmp_root, fixtures, ["text.pdf", "sample.docx"])
    assert [p.name for p in collect_inputs(d)] == ["sample.docx", "text.pdf"]


def test_batch_continues_after_failure_and_writes_manifest(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = setup_inputs(tmp_root, fixtures, ["text.pdf", "bad.exe", "sample.docx", "corrupt.pdf"])
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    out = tmp_root / "out"
    job = run_batch(store, cfg, get_engines(cfg), collect_inputs(d), ConvertOptions(output_dir=out))
    rows = {r["source"]: r for r in manifest_rows(store, job)}
    assert rows["bad.exe"]["status"] == "failed" and rows["bad.exe"]["error"].startswith("input:")
    assert rows["text.pdf"]["status"] == "done" and rows["sample.docx"]["level"] == "ok"
    lines = (out / "_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and all(json.loads(line)["source"] for line in lines)
    assert store.get_job(job)["status"] == "done"
    store.close()


def test_batch_cache_then_force(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = setup_inputs(tmp_root, fixtures, ["text.pdf"])
    out = tmp_root / "out"
    assert main(["batch", str(d), "-o", str(out)]) == 0
    assert main(["batch", str(d), "-o", str(out)]) == 0
    store = Store(tmp_root / "data" / "aidoc.db")
    # index A14: a terminal (sha256, output_dir) task row is replaced by the re-run's row, so the newest job owns it
    jobs = store.list_jobs()
    assert len(jobs) == 2 and store.list_tasks(jobs[0]["id"])[0]["status"] == "skipped"
    assert store.list_tasks(jobs[1]["id"]) == []
    assert main(["batch", str(d), "-o", str(out), "--force"]) == 0
    assert store.list_tasks(store.list_jobs()[0]["id"])[0]["status"] == "done"
    store.close()


def test_batch_exit_code_on_failure(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = setup_inputs(tmp_root, fixtures, ["bad.exe"])
    assert main(["batch", str(d), "-o", str(tmp_root / "out")]) == 2


def test_same_stem_two_sources_in_manifest(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = tmp_root / "in"
    d.mkdir()
    shutil.copy(fixtures / "text.pdf", d / "report.pdf")
    shutil.copy(fixtures / "sample.docx", d / "report.docx")
    assert main(["batch", str(d), "-o", str(tmp_root / "out")]) == 0
    lines = [json.loads(line) for line in (tmp_root / "out" / "_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {line["source"] for line in lines} == {"report.pdf", "report.docx"} and all(line["status"] == "done" for line in lines)
    assert (tmp_root / "out" / "report").exists() and any(p.name.startswith("report-") for p in (tmp_root / "out").iterdir())


def test_manifest_source_relative_to_input_dir(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = setup_inputs(tmp_root, fixtures, ["text.pdf", "bad.exe"])
    assert main(["batch", str(d), "-o", str(tmp_root / "out")]) == 2
    lines = [json.loads(line) for line in (tmp_root / "out" / "_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {line["source"] for line in lines} == {"text.pdf", "sub/bad.exe"}


def test_identical_duplicates_both_in_manifest(tmp_root, fixtures, monkeypatch):
    """Same content under two names/folders: converted once, the second is recorded as a duplicate."""
    sc = write_scenario(tmp_root / "sc.json")
    fake_env(monkeypatch, sc)
    d = tmp_root / "in"
    (d / "a").mkdir(parents=True)
    (d / "b").mkdir()
    shutil.copy(fixtures / "text.pdf", d / "a" / "report.pdf")
    shutil.copy(fixtures / "text.pdf", d / "b" / "report.pdf")
    assert main(["batch", str(d), "-o", str(tmp_root / "out")]) == 0
    lines = {json.loads(x)["source"]: json.loads(x)
             for x in (tmp_root / "out" / "_manifest.jsonl").read_text(encoding="utf-8").splitlines()}
    assert set(lines) == {"a/report.pdf", "b/report.pdf"}
    assert lines["a/report.pdf"]["status"] == "done"
    assert lines["b/report.pdf"]["status"] == "skipped" and lines["b/report.pdf"]["duplicate_of"] == "a/report.pdf"
    calls = (tmp_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1                                  # converted once


def test_rerun_reuses_unfinished_row_for_sanitised_stem(tmp_root, fixtures, monkeypatch):
    """The resume key (sha256, output_dir) must use the final (sanitised) dir, so a re-run reuses the row (A14)."""
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = tmp_root / "in"
    d.mkdir()
    src = d / "CON.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    out = tmp_root / "out"
    from aidoc.names import file_sha256
    j1 = store.create_job(ConvertOptions(output_dir=out), "cli")
    tid, _ = store.create_task(j1, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                               str(out / "CON_"))
    store.update_task(tid, status="converting")            # e.g. the previous process died mid-conversion
    j2 = run_batch(store, cfg, get_engines(cfg), collect_inputs(d), ConvertOptions(output_dir=out))
    assert [t["id"] for t in store.list_tasks(j2)] == [tid] and store.get_task(tid)["status"] == "done"
    assert (out / "CON_" / "CON_.md").exists()
    store.close()


def test_batch_unreadable_and_vanished_inputs_do_not_abort(tmp_root, fixtures, monkeypatch, capsys):
    """P1 verifier 1/10: an I/O error on one input fails that input only; the batch goes on and exits 2."""
    import aidoc.names
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = tmp_root / "in"; d.mkdir()
    for n in ("a_locked.docx", "b_ok.docx"):
        shutil.copy(fixtures / "sample.docx", d / n)
    real = aidoc.names.file_sha256

    def sha(p):
        if "a_locked" in str(p):
            raise PermissionError(13, "being used by another process")
        return real(p)
    monkeypatch.setattr(aidoc.names, "file_sha256", sha)
    out = tmp_root / "out"
    assert main(["batch", str(d), "-o", str(out)]) == 2
    rows = {r["source"]: r for r in map(json.loads, (out / "_manifest.jsonl").read_text(encoding="utf-8").splitlines())}
    assert rows["a_locked.docx"]["status"] == "failed" and rows["a_locked.docx"]["error"].startswith("input: source_unreadable")
    assert rows["b_ok.docx"]["status"] == "done"
    store = Store(tmp_root / "data" / "aidoc.db")
    assert store.list_jobs()[0]["status"] == "done"
    # vanished between listing and hashing
    cfg = load_config(); opts = ConvertOptions(output_dir=tmp_root / "out2")
    job = run_batch(store, cfg, get_engines(cfg), [d / "gone.docx", d / "b_ok.docx"], opts, input_root=d)
    st = {r["source"]: r for r in manifest_rows(store, job, d)}
    assert st["gone.docx"]["status"] == "failed" and "source_missing" in st["gone.docx"]["error"]
    assert st["b_ok.docx"]["status"] == "done" and store.get_job(job)["status"] == "done"


def test_batch_job_never_left_queued_when_collection_crashes(tmp_root, fixtures, monkeypatch):
    import pytest

    import aidoc.batch
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = tmp_root / "in"; d.mkdir(); shutil.copy(fixtures / "sample.docx", d / "a.docx")
    monkeypatch.setattr(aidoc.batch, "planned_output_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    store = Store(tmp_root / "data" / "aidoc.db"); cfg = load_config()
    with pytest.raises(RuntimeError):
        run_batch(store, cfg, get_engines(cfg), [d / "a.docx"], ConvertOptions(output_dir=tmp_root / "out"))
    assert store.list_jobs()[0]["status"] == "done"
