import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

BENCH = Path(__file__).resolve().parents[2] / "bench"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """The small corpus generated fresh (fonts/texts from the sha-pinned cache; downloaded when
    AIDOC_BENCH_FETCH=1). Missing sources skip normally and fail in acceptance mode (AIDOC_REQUIRE_MANUAL=1)."""
    sys.path.insert(0, str(BENCH.parent))
    from bench.corpus import sources
    missing = [k for k, s in sources.SOURCES.items() if not (sources.CACHE / s["file"]).is_file()]
    if missing and not os.environ.get("AIDOC_BENCH_FETCH"):
        msg = f"bench sources not cached: {missing}; set AIDOC_BENCH_FETCH=1 to download"
        pytest.fail(msg) if os.environ.get("AIDOC_REQUIRE_MANUAL") == "1" else pytest.skip(msg)
    out = tmp_path_factory.mktemp("corpus")
    r = subprocess.run([sys.executable, str(BENCH / "corpus" / "make_synthetic.py"), "--small", "--out", str(out)]
                       + ([] if os.environ.get("AIDOC_BENCH_FETCH") else ["--offline"]),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert r.returncode == 0, r.stderr[-3000:]
    return out / "small"


def test_small_corpus_is_deterministic_and_matches_committed_gt(corpus):
    committed = sorted((BENCH / "corpus" / "small").glob("*/gt.json"))
    assert len(committed) == 12
    for gt_path in committed:
        want = json.loads(gt_path.read_text(encoding="utf-8"))
        fresh = json.loads((corpus / gt_path.parent.name / "gt.json").read_text(encoding="utf-8"))
        assert fresh == want, gt_path.parent.name
        pdf = corpus / gt_path.parent.name / (gt_path.parent.name + ".pdf")
        if want["kind"] == "verify-negative":
            assert not pdf.exists()
            continue
        with pymupdf.open(pdf) as d:
            assert d.page_count == want["pages"]
            if want["kind"] == "scan":
                assert all(len(p.get_text().strip()) == 0 for p in d)          # image-only
            else:
                fonts = " ".join(f[3] for p in d for f in p.get_fonts())
                assert "Noto" in fonts and "msjh" not in fonts.lower()       # OFL font embedded, never msjh


def test_text_layer_matches_ground_truth(corpus):
    from aidoc.pagemap import squash
    from bench.metrics import ned
    gt = json.loads((corpus / "syn-text-zh" / "gt.json").read_text(encoding="utf-8"))
    with pymupdf.open(corpus / "syn-text-zh" / "syn-text-zh.pdf") as d:
        for n, page in enumerate(d, 1):
            layer = page.get_text()
            body = layer.split("\n", 2)[2]                       # running header + page number first
            assert ned(gt["page_text"][str(n)], body) > 0.99, n
            assert squash(gt["page_text"][str(n)])[:50] in squash(layer)


def test_broken_variant_has_glyph_id_layer_on_defect_pages(corpus):
    from aidoc.probe import probe_file
    for v in "abd":
        name = f"syn-broken-tounicode-{v}"
        gt = json.loads((corpus / name / "gt.json").read_text(encoding="utf-8"))
        pr = probe_file(corpus / name / f"{name}.pdf")
        assert set(pr.broken_font_pages) == set(gt["defects"]["broken_text_layer_pages"]) and pr.broken_fonts, v
    gt_c = json.loads((corpus / "syn-broken-tounicode-c" / "gt.json").read_text(encoding="utf-8"))
    pr_c = probe_file(corpus / "syn-broken-tounicode-c" / "syn-broken-tounicode-c.pdf")
    assert gt_c["defects"]["broken_text_layer_pages"] == [] and pr_c.broken_font_pages == [1, 2, 3]   # footer only


def test_tables_and_formulas_have_ground_truth(corpus):
    t = json.loads((corpus / "syn-tables" / "gt.json").read_text(encoding="utf-8"))
    tables = [x for v in t["tables"].values() for x in v]
    assert tables and all(len({len(r) for r in x["cells"]}) == 1 for x in tables)
    assert any(x["spans"] for x in tables) and any(x["spans_pages"] for x in tables)
    f = json.loads((corpus / "syn-formula" / "gt.json").read_text(encoding="utf-8"))
    assert sum(len(v) for v in f["formulas"].values()) >= 2


def test_every_gt_has_a_license_line():
    for gt_path in (BENCH / "corpus").rglob("gt.json"):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        assert gt["license"] and "Public domain" in gt["license"] and "OFL" in gt["license"], gt_path


def test_no_font_files_are_committed():
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=BENCH.parent, check=True)
    assert not [f for f in r.stdout.splitlines() if f.lower().endswith((".ttf", ".otf", ".ttc"))]
