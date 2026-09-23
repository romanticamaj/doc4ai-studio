import json
import shutil

import psutil

from aidoc import procs
from tests.fakes.scenario import write_scenario


def _calls(root):
    return [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]


def test_batch_killed_mid_conversion_resumes(tmp_root, fixtures, batch_proc, wait_for, db):
    d = tmp_root / "in"; d.mkdir(); shutil.copy(fixtures / "big.pdf", d / "big.pdf"); out = tmp_root / "out"
    sc = write_scenario(tmp_root / "sc.json", rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}], slow_s=20)
    p = batch_proc(tmp_root, d, out, sc)
    store = db(tmp_root)
    wait_for(lambda: any(s["status"] == "done" for t in store.list_tasks() for s in store.list_segments(t["id"])), 60)
    wait_for(lambda: store.list_tasks()[0]["pid"], 10)
    task = store.list_tasks()[0]; runner_pid = task["pid"]; assert runner_pid
    procs.kill_tree(p.pid); p.wait(10)                          # the CLI dies (with its process tree)
    assert store.get_task(task["id"])["status"] == "converting"  # DB shows the interrupted state
    sc = write_scenario(tmp_root / "sc.json")                    # fast again
    p2 = batch_proc(tmp_root, d, out, sc); assert p2.wait(120) == 0
    assert not psutil.pid_exists(runner_pid) or not procs.is_aidoc_runner(runner_pid)
    t = store.get_task(task["id"]); assert t["status"] == "done"
    assert [c["pages"] for c in _calls(tmp_root)].count([1, 40]) == 1   # segment 0 not re-run
    assert (out / "big" / "big.md").exists()
    assert not (out / ".tmp").exists() or not any((out / ".tmp").iterdir())


def test_orphan_runner_of_killed_batch_is_killed_by_next_start(tmp_root, fixtures, batch_proc, wait_for, db):
    """Kill only the CLI (not its runner): the runner survives as an orphan and the next start kills it."""
    d = tmp_root / "in"; d.mkdir(); shutil.copy(fixtures / "big.pdf", d / "big.pdf"); out = tmp_root / "out"
    sc = write_scenario(tmp_root / "sc.json", rules=[{"match": {"segment_idx": 1}, "behavior": "slow_ok"}], slow_s=60)
    p = batch_proc(tmp_root, d, out, sc)
    store = db(tmp_root)
    wait_for(lambda: [41, 45] in [c["pages"] for c in _calls(tmp_root)], 60)
    runner_pid = store.list_tasks()[0]["pid"]
    assert runner_pid and procs.is_aidoc_runner(runner_pid)
    runner = psutil.Process(runner_pid)
    keep = {runner_pid, *(c.pid for c in runner.children(recursive=True))}
    ancestors = {a.pid for a in runner.parents()}
    cli = psutil.Process(p.pid)
    for v in [cli, *cli.children(recursive=True)]:               # the CLI (and a venv launcher), not the runner
        if v.pid not in keep and (v.pid in ancestors or v.pid == p.pid):
            v.kill()
    p.wait(10)
    assert psutil.pid_exists(runner_pid) and procs.is_aidoc_runner(runner_pid)
    sc = write_scenario(tmp_root / "sc.json")
    p2 = batch_proc(tmp_root, d, out, sc); assert p2.wait(120) == 0
    assert not psutil.pid_exists(runner_pid) or not procs.is_aidoc_runner(runner_pid)
    assert "killed orphan runner pid=" in (tmp_root / "batch1.err").read_text(encoding="utf-8")
    assert store.list_tasks()[0]["status"] == "done"
    assert [c["pages"] for c in _calls(tmp_root)].count([1, 40]) == 1
