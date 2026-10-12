"""VLM engine end to end with fake engines and a fake OpenAI-compatible endpoint (VLM plan Task 12, spec §11.3).

Tests marked ⚑ run the REAL vlm_runner.py against tests/fakes/vlm_server.py (AIDOC_FAKE_ENGINES_EXCEPT=vlm);
the others use the fake runner's `vlm` engine."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import psutil
import pymupdf

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario
from tests.fakes.vlm_server import FakeVlmServer

KEY = "sk-test-reliability-key"
LOW3 = [{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")]
STICKY = [{"match": {"engine": n}, "behavior": "garbled_sticky"} for n in ("docling", "mineru")]


def _config(root: Path, base_url: str = "http://127.0.0.1:9/v1", **vlm) -> None:
    vals = {"backend": "openai", "base_url": base_url, "model": "fake-vlm", "remote_enabled": True, **vlm}
    lines = ["[vlm]"] + [f"{k} = {json.dumps(v)}" for k, v in vals.items()]
    (root / "aidoc.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class Env:
    def __init__(self, root: Path, monkeypatch, fixtures: Path):
        self.root, self.mp, self.fixtures = root, monkeypatch, fixtures
        monkeypatch.setenv("AIDOC_VLM_API_KEY", KEY)
        self.sc = root / "sc.json"
        self.store = Store(root / "data" / "aidoc.db")
        self.logs: list[str] = []

    def scenario(self, **kw) -> None:
        fake_env(self.mp, write_scenario(self.sc, **kw))

    def real_vlm(self) -> None:
        self.mp.setenv("AIDOC_FAKE_ENGINES_EXCEPT", "vlm")

    def task(self, src: Path, **opt) -> str:
        opts = ConvertOptions(output_dir=self.root / "out", **opt)
        job = self.store.create_job(opts, "cli")
        tid, _ = self.store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime,
                                        opts.lang, str(self.root / "out" / src.stem))
        return tid

    def run(self, tid: str, cancel=None) -> TaskStatus:
        cfg = load_config()
        return run_task(self.store, tid, get_engines(cfg), cfg, cancel=cancel,
                        emit=lambda kind, p: self.logs.append(p.get("line", "")) if kind == "task.log" else None)

    def sidecar(self, stem: str) -> dict:
        return json.loads((self.root / "out" / stem / f"{stem}.json").read_text(encoding="utf-8"))

    def copy(self, name: str, as_name: str | None = None) -> Path:
        dst = self.root / (as_name or name)
        dst.write_bytes((self.fixtures / name).read_bytes())
        return dst


def _pdf(dst: Path, fixtures: Path, broken: int, text_pages: int) -> Path:
    """`broken` copies of broken_tounicode.pdf page 1 (a broken-font page) then `text_pages` pages of text.pdf."""
    d = pymupdf.open()
    b = pymupdf.open(fixtures / "broken_tounicode.pdf")
    t = pymupdf.open(fixtures / "text.pdf")
    for _ in range(broken):
        d.insert_pdf(b, from_page=0, to_page=0)
    for i in range(text_pages):
        d.insert_pdf(t, from_page=i % t.page_count, to_page=i % t.page_count)
    d.save(dst)
    return dst


def _runner_procs() -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any(str(a).replace("\\", "/").endswith("/engines/runner/vlm_runner.py") for a in (p.info["cmdline"] or [])):
                out.append(p)
        except (psutil.Error, TypeError):
            pass
    return out


# 1
def test_three_low_engines_fall_through_to_vlm(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    _config(tmp_root)
    e.scenario(rules=LOW3)
    tid = e.task(e.copy("text.pdf"))
    assert e.run(tid) == TaskStatus.done
    t = e.store.get_task(tid)
    assert t["engine"] == "vlm" and [a["engine"] for a in t["tried"]] == ["docling", "mineru", "markitdown", "vlm"]
    assert e.sidecar("text")["vlm"]["role"] == "primary"


# 2
def test_vlm_repairs_what_docling_and_mineru_cannot(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    _config(tmp_root)
    e.scenario(default="ok", garbled_pages=[1], rules=STICKY)
    tid = e.task(e.copy("broken_tounicode.pdf"), engine="docling")
    assert e.run(tid) == TaskStatus.done
    q = e.store.get_task(tid)["quality"]
    assert q["pages_unrepaired"] == 0 and [p["repaired_by"] for p in q["pages"]] == ["vlm:openai:fake-vlm"]
    assert any(line.startswith("vlm(openai): sending 1 page image(s)") for line in e.logs)


# 3 ⚑
def test_remote_disabled_sends_nothing(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    e.real_vlm()
    calls = tmp_root / "vlm_calls.jsonl"
    with FakeVlmServer({"default": "ok", "call_log": str(calls)}) as base:
        _config(tmp_root, base, remote_enabled=False)
        e.scenario(default="ok", garbled_pages=[1], rules=STICKY)
        tid = e.task(_pdf(tmp_root / "b10.pdf", fixtures, broken=1, text_pages=9), engine="docling")
        assert e.run(tid) == TaskStatus.done
    q = e.store.get_task(tid)["quality"]
    assert q["level"] == "warn" and q["pages_unrepaired"] == 1
    assert not calls.exists() or calls.read_text(encoding="utf-8").strip() == ""
    assert not any("vlm(openai)" in line for line in e.logs)


# 4
def test_page_cap_and_daily_budget(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    _config(tmp_root, remote_daily_page_budget=5, remote_max_pages_per_doc=10)
    e.scenario(rules=LOW3)
    tid = e.task(e.copy("span_margin.pdf"))                         # 45 pages > the 10-page cap
    assert e.run(tid) == TaskStatus.low
    assert any("vlm(page_cap)" in line for line in e.logs) and e.store.get_task(tid)["engine"] != "vlm"
    # per-page repair is not capped per document, only by the daily budget: 3 pages fit into 5, 3 more do not
    e.scenario(default="ok", garbled_pages=[1, 2, 3], rules=STICKY)
    a = e.task(_pdf(tmp_root / "r1.pdf", fixtures, broken=3, text_pages=12), engine="docling")
    assert e.run(a) == TaskStatus.done
    assert all(p["repaired_by"].startswith("vlm:") for p in e.store.get_task(a)["quality"]["pages"])
    assert e.store.vlm_usage_today("openai", "127.0.0.1:9")["pages"] == 3
    e.logs.clear()
    b = e.task(_pdf(tmp_root / "r2.pdf", fixtures, broken=3, text_pages=13), engine="docling")
    assert e.run(b) == TaskStatus.done
    assert any("repair: vlm skipped (budget)" in line for line in e.logs)
    assert e.store.get_task(b)["quality"]["pages_unrepaired"] == 3
    assert e.store.vlm_usage_today("openai", "127.0.0.1:9")["pages"] == 3


# 5 ⚑
def test_hanging_endpoint_times_out_and_cancel_kills_the_runner(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    e.real_vlm()
    e.scenario()
    with FakeVlmServer({"default": "hang"}) as base:
        _config(tmp_root, base, request_timeout_s=10)
        tid = e.task(e.copy("text.pdf"), engine="vlm", timeout_s=3)
        t0 = time.time()
        st = e.run(tid)
        assert st in (TaskStatus.failed, TaskStatus.low) and time.time() - t0 < 90
        tried = e.store.get_task(tid)["tried"]
        assert tried and all(a["error_kind"] == "transient" for a in tried) and tried[0]["label"] == "vlm:openai:fake-vlm"
        assert not _runner_procs()
        # cancel while a request hangs: the runner tree goes, the task is cancelled
        tid2 = e.task(e.copy("text.pdf", "text2.pdf"), engine="vlm", timeout_s=600)
        cancel = threading.Event()
        threading.Timer(3.0, cancel.set).start()
        assert e.run(tid2, cancel=cancel) == TaskStatus.cancelled
    time.sleep(0.5)
    assert not _runner_procs()


def _ten_text_pages(dst: Path, fixtures: Path) -> Path:
    return _pdf(dst, fixtures, broken=0, text_pages=10)


# 6 ⚑ and 8 ⚑
def test_refusal_and_truncation_are_failed_pages_and_the_key_is_never_written(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    e.real_vlm()
    e.scenario()
    rules = [{"match": {"page": 1}, "behavior": "refuse"}, {"match": {"page": 2}, "behavior": "length"}]
    with FakeVlmServer({"default": "ok", "require_key": KEY, "rules": rules, "with_table": True}) as base:
        _config(tmp_root, base)
        tid = e.task(_ten_text_pages(tmp_root / "ten.pdf", fixtures), engine="vlm")
        assert e.run(tid) == TaskStatus.done
    side = e.sidecar("ten")
    q = side["quality"]
    failed = {p["page"]: p for p in q["pages"] if "page_conversion_failed" in p["reasons"]}
    assert set(failed) == {1, 2} and q["level"] == "warn"
    assert side["vlm"]["refused_pages"] == [1] and side["vlm"]["truncated_pages"] == [2]
    assert side["vlm"]["usage"]["requests"] >= 11                  # 10 pages + the truncation retry
    # 8: key hygiene — nothing under data/, the output dir or the task log carries the key
    hay = "\n".join(e.logs)
    for root in (tmp_root / "data", tmp_root / "out"):
        for f in root.rglob("*"):
            if f.is_file() and f.suffix not in (".db", ".db-wal", ".db-shm"):
                hay += f.read_bytes().decode("utf-8", "ignore")
    assert KEY not in hay
    con = e.store.con
    rows = con.execute("SELECT payload_json FROM events").fetchall() + con.execute("SELECT tried_json FROM tasks").fetchall()
    assert all(KEY not in str(r[0]) for r in rows)


# 7
def test_resume_after_interrupt_redoes_segments_when_the_model_changed(tmp_root, monkeypatch, fixtures):
    e = Env(tmp_root, monkeypatch, fixtures)
    _config(tmp_root)
    e.scenario(slow_s=60, rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}])
    src = e.copy("big.pdf")                                         # 45 pages = two segments
    tid = e.task(src, engine="vlm")
    cancel = threading.Event()

    def stop_after_segment_0():
        deadline = time.time() + 60
        while time.time() < deadline:
            segs = e.store.list_segments(tid)
            if segs and segs[0]["status"] == "done":
                cancel.set()
                return
            time.sleep(0.1)
    threading.Thread(target=stop_after_segment_0, daemon=True).start()
    assert e.run(tid, cancel=cancel) == TaskStatus.cancelled
    assert e.store.list_segments(tid)[0]["status"] == "done"
    text = (tmp_root / "aidoc.toml").read_text(encoding="utf-8").replace('model = "fake-vlm"', 'model = "other-vlm"')
    (tmp_root / "aidoc.toml").write_text(text, encoding="utf-8")
    e.scenario()
    e.logs.clear()
    calls_before = len((tmp_root / "calls.jsonl").read_text(encoding="utf-8").splitlines())
    assert e.run(tid) == TaskStatus.done
    assert any("options changed since the done segments were made" in line for line in e.logs)
    calls = [json.loads(x) for x in (tmp_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()[calls_before:]]
    assert sorted(c["pages"] for c in calls if c["engine"] == "vlm") == [[1, 40], [41, 45]]
    assert e.sidecar("big")["vlm"]["model"] == "other-vlm"
