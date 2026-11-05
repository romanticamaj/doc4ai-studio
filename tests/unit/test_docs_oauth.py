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
    assert "local threat model" in sec.lower() and "Origin" in sec


def test_index_amendments():
    index = ROOT.joinpath("docs/superpowers/plans/2026-10-05-mcp-distribution-index.md").read_text(encoding="utf-8")
    assert "2026-11-05-local-mcp-oauth" in index and "schema v8" in index and "grant id" in index
