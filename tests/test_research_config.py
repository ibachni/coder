"""Firecrawl tool wiring (src/research_config.py) — encodes decisions §0.9 / §0.10.

The allowlist is the safety boundary for the research agent, so these lock the two
rules that matter: crawl is never exposed, and map is discover-only.
"""

from pathlib import Path

import pytest

import nodes.helpers as helpers
import research_config as rc
from classes import ResearchMode


class TestAllowlist:
    def test_every_mode_has_an_allowlist(self) -> None:
        assert set(rc.ALLOWED_TOOLS) == set(ResearchMode)

    def test_crawl_is_never_exposed(self) -> None:
        # NB: match the exact tool, not substring "crawl" — it's inside "fire-crawl".
        for tools in rc.ALLOWED_TOOLS.values():
            assert not any(t.endswith("firecrawl_crawl") for t in tools)

    def test_map_is_discover_only(self) -> None:
        assert rc.MAP in rc.allowed_tools_for(ResearchMode.DISCOVER)
        assert rc.MAP not in rc.allowed_tools_for(ResearchMode.NEW)
        assert rc.MAP not in rc.allowed_tools_for(ResearchMode.CONTINUOUS)

    def test_search_and_scrape_everywhere(self) -> None:
        for mode in ResearchMode:
            assert rc.SEARCH in rc.allowed_tools_for(mode)
            assert rc.SCRAPE in rc.allowed_tools_for(mode)

    def test_tool_names_are_mcp_namespaced(self) -> None:
        assert rc.SCRAPE == "mcp__firecrawl__firecrawl_scrape"


class TestDisallowed:
    def test_denies_write_and_exec_builtins(self) -> None:
        for tool in ("Write", "Edit", "Bash"):
            assert tool in rc.DISALLOWED_TOOLS

    def test_does_not_disallow_firecrawl(self) -> None:
        assert not any("firecrawl" in t for t in rc.DISALLOWED_TOOLS)


class TestRunResearchAgent:
    def test_wires_mcp_allowlist_disallow_and_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured: dict = {}

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

            class R:
                stdout = "{}"

            return R()

        monkeypatch.setattr(helpers.subprocess, "run", fake_run)
        rc.run_research_agent("dig in", tmp_path, ResearchMode.DISCOVER)

        cmd = captured["cmd"]
        assert cmd[:3] == ["claude", "-p", "dig in"]
        assert cmd[cmd.index("--mcp-config") + 1] == str(tmp_path / rc.MCP_CONFIG_FILENAME)
        assert rc.MAP in cmd  # discover gets map
        assert "--disallowedTools" in cmd and "Write" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert captured["kwargs"]["timeout"] == rc.RESEARCH_AGENT_TIMEOUT
        assert captured["kwargs"]["cwd"] == tmp_path
