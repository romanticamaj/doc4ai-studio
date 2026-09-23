import json
import shutil
import threading
import time

import pytest

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    sc = write_scenario(tmp_root / "sc.json"); fake_env(monkeypatch, sc)
    cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")

    def make(name="big.pdf", **opt):
        src = tmp_root / name; shutil.copy(fixtures / name, src)
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt); job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                   str(tmp_root / "out" / src.stem))
        return tid
    return {"cfg": cfg, "store": store, "sc": sc, "make": make, "root": tmp_root, "engines": lambda: get_engines(cfg)}


def calls(root):
    return [json.loads(l) for l in (root / "calls.jsonl").read_text().splitlines()]


def test_big_pdf_two_segments_one_runner(env):
    tid = env["make"]()
    events = []
    assert run_task(env["store"], tid, env["engines"](), env["cfg"],
                    emit=lambda k, p: events.append((k, p))) == TaskStatus.done
    segs = env["store"].list_segments(tid)
    assert [(s["page_start"], s["page_end"], s["status"]) for s in segs] == [(1, 40, "done"), (41, 45, "done")]
    sc = json.loads((env["root"] / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["pages"] == 45
    md = (env["root"] / "out" / "big" / "big.md").read_text(encoding="utf-8")
    assert "<!-- page: 41 -->" in md and "<!-- page: 45 -->" in md and md.count("<!-- page: ") == 45
    assert (env["root"] / "out" / "big" / "assets" / "p45_1.png").exists()
    assert [c["pages"] for c in calls(env["root"])] == [[1, 40], [41, 45]]
    seg_events = [p for k, p in events if k == "segment.updated"]
    assert {p["task_id"] for p in seg_events} == {tid} and [p["status"] for p in seg_events][-1] == "done"
    prog = [p["progress"] for k, p in events if k == "task.updated" and "progress" in p]
    assert prog[-1] == {"pages_done": 45, "pages_total": 45}


def test_quick_check_fail_fast_switches_engine(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling", "segment_idx": 0}, "behavior": "low"}])
    tid = env["make"]()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and "quick check" in (t["tried"][0]["error_msg"] or "")
    c = calls(env["root"])
    assert [(x["engine"], x["pages"]) for x in c] == [("docling", [1, 40]), ("mineru", [1, 40]), ("mineru", [41, 45])]


def test_resume_skips_done_segments(env):
    write_scenario(env["sc"], rules=[{"match": {"segment_idx": 1, "attempt": 1}, "behavior": "crash"}])
    tid = env["make"]()
    st = run_task(env["store"], tid, env["engines"](), env["cfg"])      # crash -> transient retry (Task 5) resumes seg 1
    assert st == TaskStatus.done
    c = calls(env["root"])
    assert [x["pages"] for x in c] == [[1, 40], [41, 45], [41, 45]]


def test_cancel_keeps_done_segments(env):
    write_scenario(env["sc"], rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}], slow_s=30)
    tid = env["make"](); ev = threading.Event()

    def watch():
        while env["store"].list_segments(tid) == [] or env["store"].list_segments(tid)[0]["status"] != "done":
            time.sleep(0.05)
        ev.set()
    threading.Thread(target=watch, daemon=True).start()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"], cancel=ev) == TaskStatus.cancelled
    segs = env["store"].list_segments(tid)
    assert segs[0]["status"] == "done" and segs[1]["status"] in ("queued", "converting")
    assert not (env["root"] / "out" / "big").exists()


def test_rerun_after_cancel_resumes_same_engine(env):
    write_scenario(env["sc"], rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}], slow_s=30)
    tid = env["make"](); ev = threading.Event()

    def watch():                     # cancel once segment 0 is done and the slow segment-1 call has started
        def started():
            segs = env["store"].list_segments(tid)
            return bool(segs) and segs[0]["status"] == "done" and [41, 45] in [c["pages"] for c in calls(env["root"])]
        while not started():
            time.sleep(0.05)
        ev.set()
    threading.Thread(target=watch, daemon=True).start()
    assert run_task(env["store"], tid, env["engines"](), env["cfg"], cancel=ev) == TaskStatus.cancelled
    write_scenario(env["sc"])
    env["store"].update_task(tid, status="queued")
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    assert [x["pages"] for x in calls(env["root"])] == [[1, 40], [41, 45], [41, 45]]
    md = (env["root"] / "out" / "big" / "big.md").read_text(encoding="utf-8")
    assert md.count("<!-- page: ") == 45


def test_last_engine_quick_check_does_not_fail_fast(env):
    """No fallback left: finish the document and let the whole-document check decide (best-of keeps a low result)."""
    write_scenario(env["sc"], rules=[{"match": {"segment_idx": 0}, "behavior": "low"}])
    tid = env["make"](engine="mineru")
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.low
    assert [x["pages"] for x in calls(env["root"])] == [[1, 40], [41, 45]]


def test_single_segment_pdf_is_not_split(env):
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, env["engines"](), env["cfg"]) == TaskStatus.done
    c = calls(env["root"])
    assert len(c) == 1 and c[0]["pages"] is None and c[0]["source"].startswith("src")
