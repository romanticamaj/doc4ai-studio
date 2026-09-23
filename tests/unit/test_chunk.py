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
