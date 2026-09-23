import json

from aidoc.output import (
    OutputWriter,
    build_sidecar,
    choose_output_dir,
    list_output_dirs,
    lookup_cached,
    read_sidecar,
)
from aidoc.store import Store


def test_choose_dir_plain_and_collision(tmp_root):
    s = Store(tmp_root / "data" / "aidoc.db")
    out = tmp_root / "out"
    assert choose_output_dir(s, out, "report", "a" * 64) == out / "report"
    s.upsert_document(sha256="a" * 64, source_path="report.pdf", output_dir=str(out / "report"), engine="x", quality={},
                      pages=1, lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=None,
                      work_copy_expires_at=None)
    assert choose_output_dir(s, out, "report", "a" * 64) == out / "report"
    assert choose_output_dir(s, out, "report", "b" * 64) == out / "report-bbbbbbbb"


def test_choose_dir_collision_from_disk_only(tmp_root):
    s = Store(tmp_root / "data" / "aidoc.db")
    out = tmp_root / "out"
    (out / "report").mkdir()
    (out / "report" / "report.json").write_text(json.dumps({"sha256": "c" * 64}))
    assert choose_output_dir(s, out, "report", "d" * 64) == out / "report-dddddddd"


def test_writer_finalize(tmp_root, tmp_path):
    img = tmp_path / "i.png"
    img.write_bytes(b"x")
    w = OutputWriter(tmp_root / "out", "task1", "doc")
    w.write("# hi\n![](assets/p1_1.png)\n", [(img, "p1_1.png")], {"sha256": "e" * 64, "source": "doc.pdf"})
    assert (w.tmp_dir / "doc.md").exists() and (w.tmp_dir / "assets" / "p1_1.png").exists()
    w.finalize(tmp_root / "out" / "doc")
    assert (tmp_root / "out" / "doc" / "doc.json").exists() and not w.tmp_dir.exists()
    assert read_sidecar(tmp_root / "out" / "doc")["sha256"] == "e" * 64


def test_lookup_cached(tmp_root):
    s = Store(tmp_root / "data" / "aidoc.db")
    d = tmp_root / "out" / "doc"
    d.mkdir(parents=True)
    assert lookup_cached(s, "f" * 64, d) is None
    (d / "doc.json").write_text(json.dumps({"sha256": "f" * 64, "quality": {"level": "ok"}}))
    assert lookup_cached(s, "f" * 64, d)["sha256"] == "f" * 64          # disk fallback
    s.upsert_document(sha256="f" * 64, source_path="x", output_dir=str(d), engine="x", quality={"level": "ok"}, pages=1,
                      lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=None, work_copy_expires_at=None)
    assert lookup_cached(s, "f" * 64, d)["engine"] == "x"
    (d / "doc.json").unlink()
    assert lookup_cached(s, "f" * 64, d) is None                         # DB row without output on disk is not a hit


def test_sidecar_keys():
    sc = build_sidecar(source="a.pdf", sha256="x", pages=3, engine="mineru", tried=[], segments=1, probe={}, quality={},
                       lang="cht", elapsed_s=1.0)
    assert list(sc) == ["source", "sha256", "pages", "engine", "tried", "segments", "probe", "quality", "lang",
                        "elapsed_s", "aidoc_version"]


def test_list_output_dirs_ignores(tmp_root):
    out = tmp_root / "out"
    for n in [".tmp", ".trash", "a", "b"]:
        (out / n).mkdir()
    (out / "_manifest.jsonl").write_text("")
    (out / "chunks.jsonl").write_text("")
    assert [p.name for p in list_output_dirs(out)] == ["a", "b"]
