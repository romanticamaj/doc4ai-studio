"""Engine runner processes get a memory cap (verifier round, item 1b): over it the runner tree is killed and the
attempt fails as `engine` / `memory_limit`, so the pipeline falls back to the next engine."""
import json
import time

from aidoc.config import Limits


def test_memory_limit_defaults():
    from aidoc.engines.base import memory_limit_mb
    lim = Limits()
    assert lim.engine_memory_mb == 0 and lim.markitdown_memory_mb == 0
    gpu, md = memory_limit_mb("mineru", lim), memory_limit_mb("markitdown", lim)
    assert 1024 <= md <= 8192 and md < gpu
    assert memory_limit_mb("docling", Limits(engine_memory_mb=300)) == 300
    assert memory_limit_mb("markitdown", Limits(markitdown_memory_mb=200)) == 200


def test_runner_over_the_cap_is_killed_and_the_next_engine_runs(ctx, tmp_path):
    from aidoc.engines.registry import get_engines
    from aidoc.models import ConvertOptions
    from aidoc.names import file_sha256
    from aidoc.pipeline import run_task
    from tests.fakes.scenario import write_scenario
    write_scenario(ctx.config.root / "sc.json", default="ok",
                   rules=[{"match": {"engine": "markitdown"}, "behavior": "memory_hog"}])
    ctx.config.limits.markitdown_memory_mb = 150
    src = tmp_path / "doc.docx"
    import zipfile
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "cli")
    tid, _ = ctx.store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                                   str(ctx.config.output_root() / "doc"))
    t0 = time.time()
    run_task(ctx.store, tid, get_engines(ctx.config), ctx.config)
    task = ctx.store.get_task(tid)
    first = task["tried"][0]
    assert first["engine"] == "markitdown" and first["error_kind"] == "engine"
    assert first["error_msg"].startswith("memory_limit"), first
    assert time.time() - t0 < 60
    hog = [json.loads(x) for x in (ctx.config.root / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert hog and hog[0]["engine"] == "markitdown"
