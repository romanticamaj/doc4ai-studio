import pytest

from aidoc.mcp import docs as D
from aidoc.mcp.errors import ToolFailure
from tests.mcp.conftest import page_md


def test_load_doc_view(ctx, make_doc):
    doc = make_doc("book", pages=3)
    v = D.load_doc(ctx, doc["id"])
    assert v.title == "book" and v.source_name == "book.pdf" and v.pages == 3 and v.has_pages
    assert v.page_text(2).lstrip().startswith("## Chapter 2") and v.page_text(9) is None
    assert v.page_block(9) == "<!-- page: 9 -->\n<!-- no text for this page -->\n"
    assert v.pre.startswith("# Title")
    assert D.load_doc(ctx, doc["id"]) is v                      # cached
    (v.md_path).write_text(page_md(1), encoding="utf-8")
    assert D.load_doc(ctx, doc["id"]) is not v                  # mtime/size changed → reloaded


def test_load_doc_errors(ctx, make_doc):
    with pytest.raises(ToolFailure) as e:
        D.load_doc(ctx, "nope")
    assert e.value.code == "document_not_found"
    doc = make_doc("gone", pages=1)
    (D.load_doc(ctx, doc["id"]).md_path).unlink()
    with pytest.raises(ToolFailure) as e:
        D.load_doc(ctx, doc["id"])
    assert e.value.code == "output_missing"
    orphan = make_doc("orph", pages=1, status="orphaned")
    with pytest.raises(ToolFailure) as e:
        D.load_doc(ctx, orphan["id"])
    assert e.value.code == "document_not_found"


def test_rewrite_assets_and_resources():
    md = "![fig](assets/p1_1.png) text <img src=\"assets/p2_1.jpg\" alt=x> [not](other/file.png) ![abs](https://x/y.png)"
    out = D.rewrite_assets(md, "d1")
    assert "](doc4ai://documents/d1/assets/p1_1.png)" in out and 'src="doc4ai://documents/d1/assets/p2_1.jpg"' in out
    assert "](other/file.png)" in out and "https://x/y.png" in out
    assert D.resources_for("d1") == ["doc4ai://documents/d1", "doc4ai://documents/d1/metadata",
                                     "doc4ai://documents/d1/pages/{range}", "doc4ai://documents/d1/assets/{name}"]


def test_page_warnings_and_flagged_count():
    row = {"quality": {"pages": [{"page": 3, "reasons": ["broken_text_layer"], "repaired_by": "docling"},
                                 {"page": 7, "reasons": ["garbage", "page_map_missing"]}], "pages_unrepaired": 1}}
    w = D.page_warnings(row, 1, 5)
    assert [x.model_dump() for x in w] == [{"page": 3, "reason": "broken_text_layer", "reasons": ["broken_text_layer"], "repaired": True}]
    assert [x.page for x in D.page_warnings(row)] == [3, 7]
    assert D.flagged_count(row) == 2 and D.flagged_count({"quality": {}}) == 0


def test_stale_job_detects_live_reconvert(ctx, make_doc):
    from aidoc.models import ConvertOptions
    doc = make_doc("book", pages=2)
    assert D.stale_job(ctx, doc) is None
    job = ctx.store.create_job(ConvertOptions(output_dir=ctx.config.output_root()), "web")
    tid, _ = ctx.store.create_task(job, doc["source_path"], doc["sha256"], 1, 1.0, "cht", doc["output_dir"])
    assert D.stale_job(ctx, doc) == job
    ctx.store.update_task(tid, status="done")
    assert D.stale_job(ctx, doc) is None


def test_estimate_and_chunk_caches_survive_concurrent_eviction(ctx, make_doc):
    """Review finding: the caches were evicted without a lock from to_thread workers (KeyError / RuntimeError)."""
    import threading
    views = [D.load_doc(ctx, make_doc(f"v{i}", pages=1, md=page_md(1, body=f"text {i}"))["id"]) for i in range(40)]
    errors = []

    def hammer(k):
        try:
            for r in range(300):
                v = views[(k * 7 + r) % len(views)]
                v.mtime_ns += 1                        # a new cache identity every time: constant eviction
                D.token_ranges(v)
                D.chunk_cache(v, 800 + (r % 3) * 100)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
