"""README stays honest about what is in the repo."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _local_targets(text: str) -> list[str]:
    links = re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", text)
    attrs = re.findall(r'(?:src|srcset|href)="(?!https?://|#|mailto:)([^"]+)"', text)
    return links + attrs


def test_every_local_image_and_link_exists():
    for name in ("README.md", "README.zh-TW.md"):
        targets = _local_targets((ROOT / name).read_text(encoding="utf-8"))
        assert any(t.startswith("docs/images/") for t in targets), name
        for t in targets:
            assert (ROOT / t.split("#")[0]).exists(), (name, t)


def test_positioning_sections_present():
    for heading in ("## When to use something else", "## Engines and licenses", "## Quick start", "## Connect your AI (MCP)"):
        assert heading in README, heading
    zh = (ROOT / "README.zh-TW.md").read_text(encoding="utf-8")
    assert 'href="README.zh-TW.md"' in README and 'href="README.md"' in zh
    for heading in ("## 快速開始", "## 什麼時候該用別的工具", "## 引擎與授權"):
        assert heading in zh, heading
    assert "AGPL-3.0" in README and "orchestrat" in README.lower()


def test_serve_section_passes_the_token_through_the_environment():
    """The token must not sit on a command line (process listings show argv)."""
    i = README.index("By default the server binds to `127.0.0.1`")
    section = README[i:i + 1500]
    assert "DOC4AI_TOKEN" in section and "process list" in section.lower()
    assert "--token <secret>" not in section


def test_links_in_docs_pages_resolve():
    for page in sorted((ROOT / "docs").glob("*.md")):
        for t in re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", page.read_text(encoding="utf-8")):
            assert (page.parent / t.split("#")[0]).exists(), (page.name, t)
