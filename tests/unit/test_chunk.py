import json

from aidoc.chunk import chunk_markdown, chunk_output_dir, count_tokens


def words(t):
    return len(t.split())   # deterministic counter for tests


MD = """<!-- page: 1 -->
# Title
intro para one two three
## Sec A
para a1 word word word
<!-- page: 2 -->
para a2 word word word word word
| h1 | h2 |
| --- | --- |
| 1 | 2 |
## Sec B
$$
x = 1
$$
para b
"""


def test_heading_paths_and_pages():
    cs = chunk_markdown(MD, "doc.pdf", max_tokens=8, counter=words)
    assert cs[0].heading_path == ["Title"] and cs[0].page_start == 1 and cs[0].text == "intro para one two three"
    a = [c for c in cs if c.heading_path == ["Title", "Sec A"]]
    assert a[0].page_start == 1 and a[-1].page_end == 2
    assert any("| h1 | h2 |" in c.text for c in a)
    b = [c for c in cs if c.heading_path == ["Title", "Sec B"]]
    assert b[0].text.startswith("$$") and b[0].page_start == 2


def test_table_never_split_and_oversized():
    table = "| a | b |\n| --- | --- |\n" + "\n".join(f"| {i} | {i} |" for i in range(50))
    cs = chunk_markdown("# T\n" + table, "d", max_tokens=10, counter=words)
    assert len(cs) == 1 and cs[0].oversized and cs[0].text == table


def test_no_pages_and_no_headings():
    cs = chunk_markdown("just text\n\nmore text", "d", max_tokens=100, counter=words)
    assert cs[0].page_start is None and cs[0].heading_path == [] and cs[0].to_json().get("oversized") is None
    assert cs[0].text == "just text\n\nmore text"
    assert chunk_markdown("", "d", counter=words) == [] and chunk_markdown("<!-- page: 3 -->\n", "d", counter=words) == []


def test_ids_and_json():
    cs = chunk_markdown(MD, "doc.pdf", max_tokens=8, counter=words)
    assert cs[0].id == "doc#0000" and cs[1].id == "doc#0001"
    assert set(cs[0].to_json()) == {"id", "source", "heading_path", "page_start", "page_end", "text"}


def test_fences_html_tables_and_heading_levels():
    md = ("# A\n## B\n### C\ntext c\n## D\n```python\nx = 1\n\ny = 2\n```\n"
          "<table><tr><td>1</td></tr>\n\n<tr><td>2</td></tr></table>\n# E\nlast")
    cs = chunk_markdown(md, "d", max_tokens=1000, counter=words)
    paths = [c.heading_path for c in cs]
    assert paths == [["A", "B", "C"], ["A", "D"], ["E"]]
    assert "```python\nx = 1\n\ny = 2\n```" in cs[1].text and "<tr><td>2</td></tr></table>" in cs[1].text


def test_long_paragraph_is_its_own_oversized_chunk():
    cs = chunk_markdown("short one\n\n" + "w " * 50 + "\n\nshort two", "d", max_tokens=10, counter=words)
    assert [c.oversized for c in cs] == [False, True, False]


def test_chunk_output_dir(tmp_path):
    d = tmp_path / "a"; d.mkdir(); (d / "a.md").write_text(MD, encoding="utf-8")
    (d / "a.json").write_text(json.dumps({"source": "a.pdf"}))
    (tmp_path / ".tmp").mkdir()
    (tmp_path / "noout").mkdir()                                       # dir without markdown is skipped
    p = chunk_output_dir(tmp_path, max_tokens=800, counter=words)
    lines = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert p.name == "chunks.jsonl" and lines[0]["source"] == "a.pdf" and not (tmp_path / ".chunks.jsonl.tmp").exists()
    assert lines[0]["id"] == "a#0000"


def test_real_tokenizer_counts():
    assert count_tokens("hello world") == 2


def test_cli_chunk(tmp_path, capsys):
    from aidoc.cli import main
    d = tmp_path / "a"; d.mkdir(); (d / "a.md").write_text(MD, encoding="utf-8")
    (d / "a.json").write_text(json.dumps({"source": "a.pdf"}))
    assert main(["chunk", str(tmp_path), "--max-tokens", "600"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote ") and "chunks to" in out and (tmp_path / "chunks.jsonl").is_file()
    rows = [json.loads(line) for line in (tmp_path / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and all({"heading_path", "page_start", "page_end"} <= set(r) for r in rows)
    assert main(["chunk", str(tmp_path / "missing")]) == 1
    assert main(["chunk", str(tmp_path), "--doc", "nope"]) == 1


def test_chunk_single_doc_keeps_other_documents(tmp_path):
    """Final review (M3, re-graded): `--doc X` refreshes X's chunks and keeps everybody else's."""
    for name in ("a", "b"):
        d = tmp_path / name; d.mkdir(); (d / f"{name}.md").write_text(MD, encoding="utf-8")
        (d / f"{name}.json").write_text(json.dumps({"source": f"{name}.pdf"}))
    p = chunk_output_dir(tmp_path, max_tokens=8, counter=words)
    before = p.read_text(encoding="utf-8").splitlines()
    (tmp_path / "a" / "a.md").write_text("# New\nonly one chunk now", encoding="utf-8")
    chunk_output_dir(tmp_path, max_tokens=8, only="a", counter=words)
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows if r["id"].startswith("a#")] == ["a#0000"]
    assert [r for r in rows if r["id"].startswith("b#")] == [json.loads(x) for x in before if x.startswith('{"id": "b#')]


# ---- P2 verifier minors (3, 4)

def test_headings_only_document_is_not_dropped():
    cs = chunk_markdown("<!-- page: 1 -->\n# A\n## B\n## C\n", "h.pdf", max_tokens=50, counter=words)
    assert [c.heading_path for c in cs] == [["A", "B"], ["A", "C"]]
    assert [c.text for c in cs] == ["## B", "## C"] and cs[0].page_start == 1


def test_parent_heading_with_children_gets_no_empty_chunk():
    cs = chunk_markdown("# A\n## B\ntext b\n", "h.md", max_tokens=50, counter=words)
    assert [(c.heading_path, c.text) for c in cs] == [(["A", "B"], "text b")]


def test_unclosed_fence_stops_at_next_heading():
    md = "# One\n```python\nx = 1\n# Two\ntext two\n"
    cs = chunk_markdown(md, "f.md", max_tokens=50, counter=words)
    assert cs[-1].heading_path == ["Two"] and cs[-1].text == "text two"
    assert "x = 1" in cs[0].text


def test_unclosed_math_stops_at_blank_line_or_heading():
    md = "# One\n$$\nx = 1\n\npara after\n# Two\ntext two\n"
    cs = chunk_markdown(md, "m.md", max_tokens=3, counter=words)
    assert any(c.text == "para after" for c in cs) and cs[-1].heading_path == ["Two"]


def test_text_before_first_marker_is_not_given_the_next_page():
    cs = chunk_markdown("preface words\n<!-- page: 3 -->\nbody three\n", "p.pdf", max_tokens=50, counter=words)
    assert (cs[0].text, cs[0].page_start, cs[0].page_end) == ("preface words", None, None)
    assert (cs[1].page_start, cs[1].page_end) == (3, 3)
    cs = chunk_markdown("page one text\n<!-- page: 2 -->\nbody two\n", "p.pdf", max_tokens=50, counter=words)
    assert (cs[0].text, cs[0].page_start) == ("page one text", 1)       # marker for page 1 absent


def test_offline_tokenizer_gives_clear_error(tmp_root, monkeypatch, capsys):
    import aidoc.chunk as ch
    from aidoc.cli import main

    def boom(name):
        raise ConnectionError("ProxyError: cannot reach openaipublic.blob.core.windows.net")
    monkeypatch.setattr(ch, "_encoder", None)
    import tiktoken
    monkeypatch.setattr(tiktoken, "get_encoding", boom)
    d = tmp_root / "out" / "doc"
    d.mkdir(parents=True)
    (d / "doc.md").write_text("# T\ntext\n", encoding="utf-8")
    assert main(["chunk", str(tmp_root / "out")]) == 1
    err = capsys.readouterr().err
    assert "aidoc setup" in err and "Traceback" not in err
