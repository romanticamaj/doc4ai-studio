"""Which library document may answer a compat request from the cache (verifier item 2): the output-affecting
options must match (engine forced or auto, language, MinerU tier); an auto request takes the best auto result."""
import io
import time

from tests.compat.test_sync import PDF

DATA = PDF + b"\n% cache-rules\n"


def _post(env, raw, name, **form):
    return env.client.post("/v1/convert/file", files={"files": (name, io.BytesIO(DATA), "application/pdf")},
                           data=form, headers={"X-Api-Key": raw}).json()


def test_auto_request_never_gets_a_forced_engine_result(workers):
    """The verifier's repro: a.pdf (auto), g.pdf (forced markitdown, newer), k.pdf (auto) -> a's document."""
    env = workers
    raw, _ = env.issue()
    a = _post(env, raw, "a.pdf")["document"]["doc4ai"]
    assert a["engine"] != "markitdown"
    g = _post(env, raw, "g.pdf", doc4ai_engine="markitdown")["document"]["doc4ai"]
    assert g["engine"] == "markitdown" and g["cached"] is False and g["doc_id"] != a["doc_id"]
    k = _post(env, raw, "k.pdf")["document"]["doc4ai"]
    assert k["cached"] is True and k["doc_id"] == a["doc_id"]
    again = _post(env, raw, "x.pdf", doc4ai_engine="markitdown")["document"]["doc4ai"]
    assert again["cached"] is True and again["doc_id"] == g["doc_id"]


def test_another_language_is_not_a_cache_hit(workers):
    env = workers
    raw, _ = env.issue()
    first = _post(env, raw, "l.pdf")["document"]["doc4ai"]
    assert first["lang"] == "cht"
    en = _post(env, raw, "l.pdf", doc4ai_lang="en")["document"]["doc4ai"]
    assert en["cached"] is False and en["lang"] == "en"
    assert _post(env, raw, "l2.pdf", ocr_lang="en")["document"]["doc4ai"]["cached"] is True


def test_the_best_auto_result_wins_over_the_newest(ctx, make_doc):
    """Ranking: quality level (ok > warn > low), then score, then the newest."""
    from aidoc.compat.jobs import find_cached
    from aidoc.compat.options import parse_options
    sha = "c" * 64
    good = make_doc("good", pages=1, status="ok", quality={"score": 0.9, "level": "ok", "reasons": [], "pages": []}, sha=sha)
    time.sleep(0.01)
    make_doc("worse", pages=1, status="warn", quality={"score": 0.95, "level": "warn", "reasons": [], "pages": []}, sha=sha)
    for d in ctx.store.documents_by_sha(sha):                   # the sidecars must name the content for a valid hit
        import json
        from pathlib import Path
        out = Path(d["output_dir"])
        sc = json.loads((out / f"{out.name}.json").read_text(encoding="utf-8"))
        (out / f"{out.name}.json").write_text(json.dumps({**sc, "sha256": sha}), encoding="utf-8")
    opts = parse_options({}, ctx.config, multipart=False)
    hit = find_cached(ctx, sha, ctx.config.output_root() / "new", opts)
    assert hit is not None and hit["id"] == good["id"]


def test_a_running_conversion_with_other_options_is_not_attached_to(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    raw, _ = env.issue()
    env.client.post("/v1/convert/file/async", files={"files": ("f.pdf", io.BytesIO(DATA))},
                    data={"doc4ai_engine": "markitdown"}, headers={"X-Api-Key": raw})
    body = _post(env, raw, "auto.pdf")["document"]["doc4ai"]
    assert body["attached"] is False and body["engine"] != "markitdown"
