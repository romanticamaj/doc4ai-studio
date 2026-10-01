import pytest

from aidoc.mcp import budget as B


def test_estimate_tokens_methods(monkeypatch):
    n, method = B.estimate_tokens("hello world " * 10)
    assert method in ("tiktoken", "bytes") and n > 0
    from aidoc import chunk

    def boom(text):
        raise chunk.TokenizerUnavailable("offline")
    monkeypatch.setattr(chunk, "count_tokens", boom)
    n2, m2 = B.estimate_tokens("héllo")                      # 6 utf-8 bytes → 2
    assert (n2, m2) == (2, "bytes")


def test_cut_to_budget_prefers_paragraph_boundary():
    text = "para one " * 30 + "\n\n" + "para two " * 30 + "\n\n" + "para three " * 30
    kept, nxt = B.cut_to_budget(text, max_tokens=120)
    assert kept.endswith(("para two", "para one"))
    assert nxt is not None and text[nxt:].lstrip("\n").startswith("para")
    assert B.estimate_tokens(kept)[0] <= 120
    rest, nxt2 = B.cut_to_budget(text, max_tokens=10_000, start=nxt)
    assert nxt2 is None and rest.strip().endswith("para three")


def test_cut_to_budget_never_returns_empty_for_nonempty_input():
    one_line = "x" * 50_000                                   # no boundary anywhere
    kept, nxt = B.cut_to_budget(one_line, max_tokens=100)
    assert kept and nxt is not None and nxt == len(kept)
    assert B.cut_to_budget("", max_tokens=5) == ("", None)


def test_cut_to_budget_fits():
    assert B.cut_to_budget("short\n\ntext", max_tokens=1000) == ("short\n\ntext", None)


def test_cursor_roundtrip_and_errors():
    c = B.encode_cursor({"k": "2026", "id": "abc"})
    assert "=" not in c and B.decode_cursor(c) == {"k": "2026", "id": "abc"}
    assert B.decode_cursor(None) is None and B.decode_cursor("") is None
    for bad in ("!!!", "eyJ4Ijo", B.encode_cursor({"k": 1})[:-2] + "zz", "W10"):   # garbage, truncated, tampered, not an object
        with pytest.raises(B.CursorError):
            B.decode_cursor(bad)
