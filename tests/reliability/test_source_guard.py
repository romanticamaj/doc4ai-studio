import json
import shutil

from aidoc.cli import main
from tests.fakes.scenario import fake_env, write_scenario


def _batch_with_mutation(tmp_root, fixtures, monkeypatch, mutate):
    import aidoc.batch
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    d = tmp_root / "in"; d.mkdir()
    shutil.copy(fixtures / "text.pdf", d / "a.pdf"); shutil.copy(fixtures / "sample.docx", d / "b.docx")
    real = aidoc.batch.run_task

    def run(store, tid, *a, **k):                    # the source changes after the job was created
        if store.get_task(tid)["source_path"].endswith("a.pdf"):
            mutate(d / "a.pdf")
        return real(store, tid, *a, **k)
    monkeypatch.setattr(aidoc.batch, "run_task", run)
    out = tmp_root / "out"
    assert main(["batch", str(d), "-o", str(out)]) == 2
    lines = (out / "_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["source"]: r for r in map(json.loads, lines)}


def test_source_changed_in_batch(tmp_root, fixtures, monkeypatch):
    rows = _batch_with_mutation(tmp_root, fixtures, monkeypatch, lambda p: shutil.copy(fixtures / "sample.docx", p))
    assert rows["a.pdf"]["error"].startswith("input: source_changed") and rows["b.docx"]["status"] == "done"


def test_source_missing_in_batch(tmp_root, fixtures, monkeypatch):
    rows = _batch_with_mutation(tmp_root, fixtures, monkeypatch, lambda p: p.unlink())
    assert rows["a.pdf"]["error"].startswith("input: source_missing") and rows["b.docx"]["status"] == "done"
