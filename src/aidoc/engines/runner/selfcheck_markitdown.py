"""Self-check for the markitdown env. STDLIB + markitdown only. Prints one JSON line."""
from __future__ import annotations
import json
from pathlib import Path


def main() -> None:
    from importlib.metadata import version
    from markitdown import MarkItDown
    sample = Path(__file__).parent / "samples" / "selfcheck.docx"
    md = MarkItDown(enable_plugins=False).convert(str(sample)).markdown
    chars = len("".join(md.split()))
    print(json.dumps({"ok": chars > 0, "cuda": None, "arch_list": [], "sample_chars": chars,
                      "version": version("markitdown")}))


if __name__ == "__main__":
    main()
