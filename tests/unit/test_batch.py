import json
import shutil
from aidoc.batch import collect_inputs, run_batch, manifest_rows
from aidoc.cli import main
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions
from aidoc.store import Store
from tests.fakes.scenario import write_scenario, fake_env


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
