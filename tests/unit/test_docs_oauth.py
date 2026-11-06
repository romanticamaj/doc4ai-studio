"""README documents local MCP OAuth (plan 2026-11-05 Task 15): the token-free command, the limits, the threat model."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_oauth_section():
    readme = ROOT.joinpath("README.md").read_text(encoding="utf-8")
    assert "claude mcp add --transport http doc4ai http://127.0.0.1:3333/mcp" in readme
    assert "授權連線" in readme and "允許連線" in readme and "/mcp" in readme
    # R3: Claude Code only sends OAuth credentials to https or loopback token endpoints
    assert "Claude Code on another machine cannot use OAuth over plain http" in readme
    assert "Claude Desktop" in readme and "PATs keep working" in readme
    assert "[oauth] enabled = false" in readme
    sec = readme[readme.index("### Connect without a token"):]
    sec = sec[: sec.index("\n## ")]
    assert "local threat model" in sec.lower()
    # the threat model states what a web page can still do and what it cannot (no "zero capability" claim; 2026-11-06)
    assert "can still make the browser" in sec and "no open redirect" in sec and "5-second deadline" in sec
    assert "lock you out" in sec
