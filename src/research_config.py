"""Firecrawl tool wiring for the research agent (docs/research/implementation-plan.md §0).

The agent reaches Firecrawl through an MCP server named ``firecrawl``, declared in the
RESEARCH repo's committed ``.mcp.json`` (see docs/research/firecrawl-mcp-setup.md). The
node passes an explicit per-mode allowlist to `run_agent` — we never bypass permissions
(decision §0.9), and `firecrawl_crawl` is deliberately excluded (recursive / the cost
footgun; held until it's behind a budget, decision §0.10).
"""

from pathlib import Path
from subprocess import CompletedProcess

from classes import ResearchMode
from nodes.helpers import run_agent

MCP_CONFIG_FILENAME = ".mcp.json"  # lives in the RESEARCH repo root
FIRECRAWL_SERVER = "firecrawl"

# Research runs (search + scrape + reason + write, or a multi-site continuous sweep) far
# exceed the coding default; bound generously rather than let the node hang forever.
RESEARCH_AGENT_TIMEOUT = 1800


def _tool(name: str) -> str:
    """Claude Code namespaces MCP tools as ``mcp__<server>__<tool>``."""
    return f"mcp__{FIRECRAWL_SERVER}__{name}"


SEARCH = _tool("firecrawl_search")
SCRAPE = _tool("firecrawl_scrape")
MAP = _tool("firecrawl_map")

# Per-mode tool surface (decision §0.10). `new`/`continuous` only search + scrape;
# `discover` also enumerates a domain via map. No mode gets crawl.
ALLOWED_TOOLS: dict[ResearchMode, list[str]] = {
    ResearchMode.NEW: [SEARCH, SCRAPE],
    ResearchMode.CONTINUOUS: [SEARCH, SCRAPE],
    ResearchMode.DISCOVER: [SEARCH, SCRAPE, MAP],
}

# Belt-and-suspenders: deny the file-mutating / exec built-ins outright, so the agent
# can't write even if the RESEARCH repo's own settings would allow it. `--disallowedTools`
# takes precedence over any allow. The node writes every file (invariant §5.7).
# (Only names this CLI knows — an unknown one like "MultiEdit" just emits a noisy warning.)
DISALLOWED_TOOLS: list[str] = ["Write", "Edit", "NotebookEdit", "Bash"]


def allowed_tools_for(mode: ResearchMode) -> list[str]:
    """The `--allowedTools` allowlist for a research mode."""
    try:
        return ALLOWED_TOOLS[mode]
    except KeyError as e:
        raise ValueError(f"no research tool allowlist for mode {mode!r}") from e


def run_research_agent(
    prompt: str, repo: Path, mode: ResearchMode, *, timeout: int = RESEARCH_AGENT_TIMEOUT
) -> CompletedProcess:
    """Spawn the research agent for `mode` — the single place the wiring decisions live.

    Bundles the RESEARCH repo's committed `.mcp.json`, the per-mode read-only Firecrawl
    allowlist, the denied write/exec built-ins, and the JSON result envelope, so every
    research node calls one function instead of repeating (and drifting on) the flags.
    Returns the `CompletedProcess`; pair `.stdout` with `helpers.agent_text`.
    """
    return run_agent(
        prompt,
        repo,
        timeout=timeout,
        mcp_config=repo / MCP_CONFIG_FILENAME,
        allowed_tools=allowed_tools_for(mode),
        disallowed_tools=DISALLOWED_TOOLS,
        output_format="json",
    )
