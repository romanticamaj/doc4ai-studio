import json
from pathlib import Path

import pytest

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import options_key, run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario

VLM_ON = '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nremote_enabled = true\nremote_daily_page_budget = %d\nremote_max_pages_per_doc = %d\n'


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    def make(cfg_body="", budget=500, cap=200):
        (tmp_root / "aidoc.toml").write_text(cfg_body or VLM_ON % (budget, cap), encoding="utf-8")
        monkeypatch.setenv("AIDOC_CONFIG", str(tmp_root / "aidoc.toml"))
        monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
        sc = write_scenario(tmp_root / "sc.json"); fake_env(monkeypatch, sc)
        cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")
        def task(name, **opt):
            src = tmp_root / name; src.write_bytes((fixtures / name).read_bytes())
            opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
            job = store.create_job(opts, "cli")
            tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                       str(tmp_root / "out" / Path(src).stem))
            return tid
        return {"cfg": cfg, "store": store, "sc": sc, "task": task, "root": tmp_root}
    return make


def test_all_three_low_falls_through_to_vlm(env):
    e = env()
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf")
    logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.done
    t = e["store"].get_task(tid)
    assert t["engine"] == "vlm" and [a["engine"] for a in t["tried"]] == ["docling", "mineru", "markitdown", "vlm"]
    sc = json.loads((e["root"] / "out" / "text" / "text.json").read_text(encoding="utf-8"))
    assert sc["vlm"]["backend"] == "openai" and sc["vlm"]["pages_sent"] == 3 and sc["vlm"]["role"] == "primary"
    assert any(line.startswith("vlm(openai): sending 3 page image(s) to 127.0.0.1:9") for line in logs)
    assert e["store"].vlm_usage_today("openai", "127.0.0.1:9")["pages"] == 3


def test_page_cap_and_budget_gate(env):
    e = env(budget=500, cap=2)
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf")                                   # 3 pages > cap 2
    logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(page_cap)" in line for line in logs) and e["store"].get_task(tid)["engine"] != "vlm"
    e2 = env(budget=2, cap=200)
    e2["store"].add_vlm_usage(__import__("time").strftime("%Y-%m-%d"), "openai", "127.0.0.1:9", 2, 2, 0, 0)
    write_scenario(e2["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid2 = e2["task"]("text.pdf", retry_low=True); logs2 = []          # the first run left a `low` document
    assert run_task(e2["store"], tid2, get_engines(e2["cfg"]), e2["cfg"], emit=lambda k, p: logs2.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(budget)" in line for line in logs2)


def test_no_vlm_remote_option_disables_it_for_one_run(env):
    e = env()
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf", vlm_remote=False); logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(disabled_remote)" in line for line in logs)


def test_options_key_includes_vlm_backend_and_model(env):
    e = env()
    k1 = options_key(ConvertOptions(output_dir=Path("o")), e["cfg"])
    e["cfg"].vlm.model = "other"
    assert options_key(ConvertOptions(output_dir=Path("o")), e["cfg"]) != k1
    assert "vlm_remote" not in k1


def test_repair_by_vlm_writes_role_repair_block(env):
    e = env()
    # docling main engine, garbled page 1; docling's own repair (pypdfium) and mineru are sticky -> vlm repairs
    write_scenario(e["sc"], default="ok", garbled_pages=[1],
                   rules=[{"match": {"engine": "docling"}, "behavior": "garbled_sticky"},
                          {"match": {"engine": "mineru"}, "behavior": "garbled_sticky"}])
    tid = e["task"]("broken_tounicode.pdf", engine="docling")
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"]) == TaskStatus.done
    sc = json.loads((e["root"] / "out" / "broken_tounicode" / "broken_tounicode.json").read_text(encoding="utf-8"))
    rep = [p for p in sc["quality"]["pages"] if p.get("repaired_by")]
    assert rep and all(p["repaired_by"].startswith("vlm:openai:") for p in rep)
    assert sc["engine"] == "docling" and sc["vlm"]["role"] == "repair" and sc["vlm"]["pages_sent"] == len(rep)


def test_cli_no_vlm_remote_and_engine_choice(tmp_root):
    from aidoc.cli import build_parser, options_from_args
    cfg = load_config()
    a = build_parser().parse_args(["convert", "x.pdf", "--no-vlm-remote", "--engine", "vlm"])
    o = options_from_args(a, cfg)
    assert o.vlm_remote is False and o.engine == "vlm"
    assert options_from_args(build_parser().parse_args(["convert", "x.pdf"]), cfg).vlm_remote is None


def test_api_job_carries_vlm_remote(client, ctx, tmp_root, fixtures):
    src = tmp_root / "text.pdf"
    src.write_bytes((fixtures / "text.pdf").read_bytes())
    r = client.post("/api/jobs", json={"inputs": [{"path": str(src)}], "vlm_remote": False, "engine": "vlm"})
    assert r.status_code == 201 and r.json()["job"]["options"]["vlm_remote"] is False


def test_maintenance_prunes_vlm_usage(ctx):
    from aidoc.server.maintenance import Maintenance
    ctx.store.add_vlm_usage("2020-01-01", "openai", "h", 1, 1, 0, 0)
    assert Maintenance(ctx).run_once()["vlm_usage_pruned"] == 1


def test_vlm_attempts_carry_their_label_and_others_stay_unchanged(env):
    e = env()
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf")
    run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"])
    tried = e["store"].get_task(tid)["tried"]
    assert tried[-1]["label"] == "vlm:openai:m" and all("label" not in a for a in tried[:-1])
