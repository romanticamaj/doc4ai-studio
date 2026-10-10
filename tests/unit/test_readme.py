"""README stays honest about what is in the repo."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_every_local_image_and_link_exists():
    targets = re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", README)
    assert any(t.startswith("docs/superpowers/screenshots/") for t in targets)
    for t in targets:
        assert (ROOT / t.split("#")[0]).exists(), t


def test_positioning_sections_present():
    for heading in ("## When to use something else", "## Engines and licenses", "## Quick start", "## MCP", "## 繁體中文"):
        assert heading in README, heading
    assert "AGPL-3.0" in README and "orchestrat" in README.lower()
