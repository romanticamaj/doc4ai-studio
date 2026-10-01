import sqlite3

import pytest

from aidoc import pageindex as PI
from aidoc.store import Store

MD = ("Preamble line\n<!-- page: 1 -->\n# 第一章 機器學習\n\n機器學習 machine learning 概論。\n"
      "<!-- page: 2 -->\n## 2.1 Gradient descent\n\n梯度下降 gradient descent 的步驟如下。\n"
      "<!-- page: 3 -->\n\n參考文獻 references\n")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    yield s
    s.close()


def _doc(store, tmp_path, name, md, pages=3):
    out = tmp_path / "out" / name
    out.mkdir(parents=True)
    (out / f"{name}.md").write_text(md, encoding="utf-8")
    (out / f"{name}.json").write_text("{}", encoding="utf-8")
    did = store.upsert_document(sha256="s" + name, source_path=f"{name}.pdf", output_dir=str(out), engine="mineru",
                                quality={"score": 1, "level": "ok", "reasons": []}, pages=pages, lang="cht",
                                aidoc_version="0.1.0", status="ok")
    return store.get_document(did)


def test_pages_for_index_splits_on_markers_and_joins_preamble():
    pages = PI.pages_for_index(MD)
    assert [p for p, _ in pages] == [1, 2, 3]
    assert pages[0][1].startswith("Preamble line") and "機器學習" in pages[0][1]
    assert "gradient descent" in pages[1][1] and "references" in pages[2][1]
    assert PI.pages_for_index("no markers at all") == [(None, "no markers at all")]
    assert PI.pages_for_index("") == []


def test_tokenizer_is_trigram_when_supported(store):
    tok = store.page_index_tokenizer()
    assert tok in ("trigram", "unicode61")
    if sqlite3.sqlite_version_info >= (3, 34, 0):
        assert tok == "trigram"


def test_index_search_cjk_and_english(store, tmp_path):
    doc = _doc(store, tmp_path, "ml", MD)
    assert PI.index_document(store, doc) == 3
    assert store.page_index_doc_ids() == {doc["id"]}
    hits = PI.search_pages(store, "機器學習")
    assert hits and hits[0]["doc_id"] == doc["id"] and hits[0]["page"] == 1
    hits = PI.search_pages(store, "gradient")
    assert [h["page"] for h in hits] == [2]
    if store.page_index_tokenizer() == "trigram":
        assert [h["page"] for h in PI.search_pages(store, "escent")] == [2]        # substring match
    assert PI.search_pages(store, "nothing-here-xyz") == []


def test_reindex_replaces_rows(store, tmp_path):
    doc = _doc(store, tmp_path, "ml", MD)
    PI.index_document(store, doc)
    out = tmp_path / "out" / "ml"
    (out / "ml.md").write_text("<!-- page: 1 -->\nonly one page now\n", encoding="utf-8")
    assert PI.index_document(store, store.get_document(doc["id"])) == 1
    assert store._q1("SELECT COUNT(*) FROM page_index WHERE doc_id=?", (doc["id"],))[0] == 1
    assert PI.search_pages(store, "gradient") == []


def test_missing_markdown_indexes_nothing_and_clears(store, tmp_path):
    doc = _doc(store, tmp_path, "ml", MD)
    PI.index_document(store, doc)
    (tmp_path / "out" / "ml" / "ml.md").unlink()
    assert PI.index_document(store, store.get_document(doc["id"])) == 0
    assert store.page_index_doc_ids() == set()


@pytest.mark.parametrize("q", ['"', "*", "(", "-x", "NEAR", "AND", "a", "學習", "學", '"機器" OR', "機器 OR 學習", "   "])
def test_short_and_syntax_queries_never_error(store, tmp_path, q):
    doc = _doc(store, tmp_path, "ml", MD)
    PI.index_document(store, doc)
    hits = PI.search_pages(store, q)          # must not raise
    assert isinstance(hits, list)
    if q == "學習":                            # 2 chars: LIKE fallback still finds page 1
        assert hits and hits[0]["page"] == 1


def test_fts_query_quotes_terms():
    assert PI.fts_query('機器學習 "gradient" x') == '"機器學習" """gradient"""'
    assert PI.fts_query("ab") is None and PI.fts_query("") is None
    assert PI.fts_query("ab cde") == '"cde"'


def test_doc_ids_filter(store, tmp_path):
    a = _doc(store, tmp_path, "a", MD)
    b = _doc(store, tmp_path, "b", MD)
    PI.index_document(store, a)
    PI.index_document(store, b)
    hits = PI.search_pages(store, "gradient", doc_ids=[b["id"]])
    assert {h["doc_id"] for h in hits} == {b["id"]}


def test_snippet_window_and_ellipses():
    text = "x" * 400 + " gradient descent here " + "y" * 400
    s = PI.make_snippet(text, "gradient", width=100)
    assert "gradient" in s and len(s) <= 104 and s.startswith("…") and s.endswith("…")
    assert PI.make_snippet("short text", "zzz", width=50) == "short text"
    assert PI.make_snippet("機器學習 概論", "學習", width=50) == "機器學習 概論"


def test_backfill_indexes_only_unindexed(store, tmp_path, capsys):
    a = _doc(store, tmp_path, "a", MD)
    b = _doc(store, tmp_path, "b", MD)
    PI.index_document(store, a)
    assert PI.backfill_page_index(store, log=lambda s: None) == 1
    assert store.page_index_doc_ids() == {a["id"], b["id"]}
    assert PI.backfill_page_index(store, log=lambda s: None) == 0


def test_delete_documents_drops_index_rows(store, tmp_path):
    a = _doc(store, tmp_path, "a", MD)
    PI.index_document(store, a)
    store.set_document_status(a["id"], "orphaned")
    assert store.delete_documents("orphaned") == 1
    assert store.page_index_doc_ids() == set()
