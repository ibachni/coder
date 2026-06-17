# Firecrawl MCP setup (RESEARCH repo)

The research agent (a headless `claude -p` run, cwd = the RESEARCH repo) reaches
Firecrawl through an MCP server named **`firecrawl`**, declared in a committed
**`.mcp.json`** at the RESEARCH repo root. The harness passes
`--mcp-config <repo>/.mcp.json --strict-mcp-config` plus a per-mode `--allowedTools`
allowlist (see [src/research_config.py](../../src/research_config.py)).

Reachability was proven by [notebooks/firecrawl_mcp_reachability.ipynb](../../notebooks/firecrawl_mcp_reachability.ipynb).

## 1. Drop this into the RESEARCH repo root as `.mcp.json`

```json
{
  "mcpServers": {
    "firecrawl": {
      "command": "npx",
      "args": ["-y", "firecrawl-mcp@<PINNED_VERSION>"],
      "env": { "FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}" }
    }
  }
}
```

- **Pin `<PINNED_VERSION>`** to the exact `firecrawl-mcp` version the notebook resolved
  (decision §0.10) — `npx firecrawl-mcp --version` — so a cron run can't be broken by an
  upstream release. For unattended/continuous runs, `npm i -g firecrawl-mcp@<ver>` once to
  skip the per-cold-start download.
- **`${FIRECRAWL_API_KEY}`** is interpolated from the harness environment, so **no secret
  is committed**. The key lives in the harness `.env` (`run_agent` passes the env through;
  `ANTHROPIC_API_KEY` is stripped so the agent uses subscription auth).

## 2. Verify

From the RESEARCH repo:

```
FIRECRAWL_API_KEY=... claude -p \
  --mcp-config .mcp.json --strict-mcp-config \
  --allowedTools mcp__firecrawl__firecrawl_scrape \
  --output-format json \
  "Use the firecrawl scrape tool on https://example.com and reply with the title."
```

A non-error JSON envelope whose run included an `mcp__firecrawl__*` tool call confirms
the wiring. The harness does the same call via
[run_agent](../../src/nodes/helpers.py) with `mcp_config` / `allowed_tools` /
`output_format` set.

## Notes

- Tool surface is per mode: `new`/`continuous` get `firecrawl_search` + `firecrawl_scrape`;
  `discover` adds `firecrawl_map`. `firecrawl_crawl` is intentionally **not** exposed.
- The agent has **no Write/Edit/Bash** — it returns `{report_md, sources}` and the node
  writes the files ([src/research_io.py](../../src/research_io.py)). That no-file-write
  surface is what makes the tight allowlist sufficient, including for cron runs.
