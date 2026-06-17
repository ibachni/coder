# Research Workflow — High-Level Implementation Plan

Status: proposed
Last updated: 2026-06-16

**Scope:** the build roadmap for the *agentic research* workflow only — the "how we
ship it" view. The step-level behavior lives in
[docs/runbooks/research.md](../runbooks/research.md); the "why / tradeoffs" live in
[docs/implementation-plan-v2.md](../implementation-plan-v2.md) §4 (Workflow B); the
evidence-ledger schema + anti-bloat rules live in
[docs/evidence-ledger.md](../evidence-ledger.md). This doc stays high-level and points
there for detail rather than repeating it.

This plan **expands** plan-v2 §4 with the two modes added after it was written
(continuous + discover) and the **watchlist** artifact they share.

---

## 0. Decisions locked

These were open in plan-v2 §4 / the runbook; now settled:

1. **Research ships first** — before the coding inner loop (reverses the plan-v2 §8
   ordering). It's read-heavy, more independent, and has the most detailed spec.
2. **Web + scrape = Firecrawl** — exposed to the research agent as an MCP server
   (search + scrape tools). (The runbook stays tool-agnostic; this plan picks the
   concrete tool.)
3. **Three modes, one workflow** — `new` (one-off report), `continuous` (recurring
   delta surfacing + watchlist scrape), `discover` (build a watchlist).
4. **The watchlist is its own artifact** — *not* a `WorkUnit`. Persisted as
   `watchlist.jsonl` (a list of `WatchEntry`) and iterated outside the unit machinery.
5. **v1 = a single reasoning agent does the research with the tools provided.** No
   parallel fan-out, no separate verification stage. The rigor layer — parallel
   `investigate`, adversarial `verify_claims`, the `coverage_critic` + `replan` loop —
   is **deferred to v2** (§4.5). v1 trusts one capable agent with good tools and a
   tight brief; v2 adds the skeptic machinery once the spine is proven.

### R0 integration decisions

6. **Auth = `CLAUDE_CODE_OAUTH_TOKEN` from the env** (subscription). `load_oauth_token`
   now reads it; when unset, `claude` falls back to the ambient login (the env var is
   left unset rather than blanked). *(Done — [src/helper/authTokenLoader.py](../../src/helper/authTokenLoader.py).)*
7. **The agent returns text; the node writes files** (chosen). `research_agent` emits a
   JSON body `{report_md, sources: [...]}`; the node writes `report.md` / `sources.jsonl`.
   So the agent needs **no Write/Edit/Bash** — only Firecrawl read tools. Upholds the
   "agents propose, the node commits" invariant.
8. **MCP wiring** — a **committed `.mcp.json`** in the RESEARCH repo declares the
   Firecrawl stdio server with `${FIRECRAWL_API_KEY}` interpolation (no secret in git);
   the key lives in the harness `.env`. `run_agent` gains optional
   `mcp_config` / `allowed_tools` / `output_format` kwargs (defaults keep the coding
   path byte-for-byte unchanged) and, when set, passes
   `--mcp-config … --strict-mcp-config --allowedTools … --output-format json`.
9. **Permissions = tight allowlist, never bypass.** Only the exposed Firecrawl tools are
   allowed; default-deny everything else, even for unattended/cron runs. Safe precisely
   because of decision 7 (the agent writes nothing).
10. **Tool surface (v1):** `firecrawl_search` + `firecrawl_scrape` for `new`/`continuous`;
    add `firecrawl_map` for `discover`. **Hold `firecrawl_crawl`** (recursive, the cost
    footgun) until it's behind a budget. Pin the server version (`firecrawl-mcp@<pinned>`,
    captured from the R0 spike) for deterministic cold starts; pre-install globally for cron.

---

## 1. What we're building

One research workflow that drives a research ticket end to end inside the existing
deterministic LangGraph harness, branching by **mode** right after intake:

- **`new`** — frame the question (asking the user up front), then a **single reasoning
  agent** investigates and writes a cited report.
- **`continuous`** — the agent surfaces only what changed since the last run, from two
  inputs: a saved **watchlist** of sites (re-scraped each run) and open web search.
- **`discover`** — the agent finds sites worth following for a question and emits the
  `watchlist.jsonl` that `continuous` consumes.

The harness keeps owning control flow, the single human gate, side effects
(branch/commit/merge into the knowledge repo `Repo.RESEARCH`), and resumability.
**Cognition is delegated to one agent per run** — a headless `claude -p` session with
the Firecrawl tools available, which searches, reads, reasons, and writes the report
itself. Every "done" is still a **hard predicate** owned by the harness (the brief is
approved; the report exists; it was committed) — what v1 drops is the *internal*
verification loop, not the harness's outer guarantees.

---

## 2. Target architecture at a glance

**v1 — shared spine + mode branch** (brief/watchlist approval is the only human gate;
`research_agent` is one reasoning agent with Firecrawl tools):

```
pick_up_ticket → open_branch → repo_bootstrap_check → classify_research_type
        │
        ├─ new        → frame_brief → approve_brief → research_agent → save_report → commit_push → merge
        │
        ├─ continuous → load_prior → research_agent(scrape watchlist + search for new)
        │                 ─(new)→ append_insights → commit_push → merge
        │                 └─(none)→ log "no new insights" → END
        │
        └─ discover   → frame_brief(light) → research_agent(find + score sites)
                          → approve_watchlist → write_watchlist → commit_push → merge
```

`research_agent` is prompted to use its tools to gather sources, reason against the
brief's `done-when` criteria, and **return** `{report_md, sources}` with **inline
citations for the sources it actually read** — the node writes the files (decision 7),
the agent writes nothing. Coverage and citation quality are the agent's own
responsibility in v1 (a single well-instructed pass), not a separate gate.

**v2 (deferred) — the rigor layer** expands `research_agent` into:

```
investigate*   parallel fan-out, one agent per sub-question  → distilled summaries
verify_claims  re-fetch each citation + confirm support; adversarial refutation
synthesize     single writer merges summaries → report.md
coverage_critic "what's missing?" ─(gaps)→ replan_research → investigate*  (loop-until-dry)
                                            (* = parallel)
```

---

## 3. Current state → target (v1)

| Today ([src/](../../src/)) | Target (v1) |
|---|---|
| `research` is a no-op stub ([nodes/research/nodes.py](../../src/nodes/research/nodes.py)) | `frame_brief`, `research_agent`, `save_report` + mode nodes (`load_prior`, `append_insights`, `discover`-side `score`/`write_watchlist`) |
| Graph path `research → review → commit` ([nodes/__init__.py](../../src/nodes/__init__.py#L102)) | Mode subgraphs above; `classify_research_type` router |
| `src/prompts/research/` empty | `frame_brief`, `research_agent` (one strong prompt, mode-parameterized), `discover_sites` |
| `approve_plan` lives in coding ([coding/nodes.py:181](../../src/nodes/coding/nodes.py#L181)) | Generalized to `general`, reused as `approve_brief` / `approve_watchlist` |
| `run_agent` = one `claude -p` ([helpers.py:18](../../src/nodes/helpers.py#L18)) | One agent per run, **extended with optional `mcp_config`/`allowed_tools`/`output_format`** (coding path unchanged). Parallel `run_agents` stays a v2 concern. |
| No web access | Firecrawl MCP via a committed `.mcp.json` in the RESEARCH repo (`${FIRECRAWL_API_KEY}` interpolation) |
| `WorkUnit` only ([classes.py:58](../../src/classes.py#L58)) | + `WatchEntry` artifact; `AgentState` gains `research_mode`, `report_path`, `watchlist_path` |

---

## 4. Build phases

Each phase is independently shippable and leaves the graph runnable.

### Phase R0 — Spine: tools, artifact, output folder (the unblocker)
- **Auth** — `load_oauth_token` reads `CLAUDE_CODE_OAUTH_TOKEN` (ambient-login fallback). *(done)*
- **Firecrawl MCP** declared in the RESEARCH repo's committed `.mcp.json` (stdio
  `npx firecrawl-mcp@<pinned>`, `${FIRECRAWL_API_KEY}` interpolation). Extend `run_agent`
  with optional `mcp_config` / `allowed_tools` / `output_format` kwargs (coding path
  unchanged). Reachability proven by [notebooks/firecrawl_mcp_reachability.ipynb](../../notebooks/firecrawl_mcp_reachability.ipynb).
- **`WatchEntry`** pydantic model + serde allow-list registration; `AgentState` fields
  `research_mode` / `report_path` / `watchlist_path`.
- **Research output-folder I/O** (`report.md` / `brief.md` / `sources.jsonl` /
  `watchlist.jsonl` / `last_run.json`), kept separate from the `.coder/runs` ledger.
- **Generalize the HITL gate** — lift `approve_plan` to `general` so `approve_brief`
  and `approve_watchlist` reuse it.
- **Permissions** — pass an explicit `--allowedTools` allowlist (never
  `bypassPermissions`); the set is per-mode (R1/R2: `firecrawl_search` +
  `firecrawl_scrape`; R3 adds `firecrawl_map`; `firecrawl_crawl` stays out until budgeted).
- **Done-when:** the research agent can search+scrape via Firecrawl through `claude -p`
  (notebook green); `WatchEntry` round-trips through the checkpointer; the output folder
  writes/reads; graph still compiles.

### Phase R1 — `new` mode end-to-end (the spine proof)
- **`frame_brief`** → `brief.md` + sub-questions + per-question `done-when`; surface
  blocking questions into `brief.md` + `state.questions`.
- **`approve_brief`** — the single HITL gate (scope/depth/budget; autonomy knob).
- **`research_agent`** — one reasoning agent works the approved brief end to end with
  its tools (`firecrawl_search` + `firecrawl_scrape`) and **returns** `{report_md, sources}`
  (writes no files itself).
- **`save_report`** — the node writes `report.md` / `sources.jsonl` to the output
  folder; **`commit_push` / `merge`**.
- **Wire the graph:** replace the stub path; `classify_research_type` with only `new` live.
- **Done-when:** a new question produces an approved brief and a cited report in its
  folder, and merges; the report addresses the brief's `done-when` criteria.

### Phase R2 — `continuous` mode + watchlist scrape
- **`load_prior_report`** (report/sources/watchlist/last_run) → **`research_agent`** in
  *update mode*: it scrapes each watchlist entry (sequentially, with its tools),
  hashes content vs `last_content_hash` to skip unchanged, runs recency-scoped open
  search, and reports only genuinely new findings; flag dead sites `stale`, never drop
  silently → **`append_insights`** (dated section; update watchlist + last_run). "No new
  insights" is a valid, logged terminal.
- **Done-when:** a recurring ticket scrapes its watchlist, appends only new findings,
  and is safe to run on a cron/loop trigger.

### Phase R3 — `discover` mode (builds the watchlist)
- **`frame_brief`** (light) → **`research_agent`** in *discover mode*: find candidate
  sites with its tools (`firecrawl_search`, `firecrawl_scrape`, **+`firecrawl_map`** to
  enumerate a domain), score by relevance + freshness + authority, return a ranked
  list (log cuts) → **`approve_watchlist`** (HITL) → **`write_watchlist`** (seed empty
  scrape state so the first continuous run treats all as new; scaffold the folder).
- **Done-when:** a question yields an approved `watchlist.jsonl` that R2 consumes
  unchanged; a `new` run can optionally chain into discover to set up monitoring.

### Phase R4 — Deferred to v2: the rigor layer
Build only after a baseline v1 run is trusted. This is the `investigate`/`verify_claims`
machinery explained in [docs/runbooks/research.md](../runbooks/research.md) and plan-v2 §4.4:
- **`run_agents`** parallel fan-out helper + **parallel `investigate`** (one agent per
  sub-question, distilled 1–2k summaries).
- **`verify_claims`** — `web.py` deterministic Firecrawl-API fetch + a support check
  (re-fetch each citation, confirm it supports the claim) and **adversarial
  refutation** (a separate skeptic agent), perspective-diverse for high-stakes claims.
- **`coverage_critic`** + bounded **`replan_research`** (append-only, cite-cause) +
  **loop-until-dry**.
- **`synthesize`** as a distinct single-writer stage (once investigate fans out).
- **Metrics:** the **claim-to-grounded-claim gap** (research's reward-hacking
  detector); source-trust policy; main-content hashing for change detection.

---

## 5. Invariants to preserve (don't regress these)

1. **The harness owns "done", not the agent** — even with one agent doing the work,
   the agent never declares completion; the node checks the predicate (brief approved,
   report written, committed).
2. **`brief.md` is canonical** — `state.questions` is a transient UI projection (same
   contract as coding's `plan.md`).
3. **The report cites its sources** — v1 relies on the agent to cite the sources it
   read (best-effort, soft); v2 upgrades this to a deterministic grounding *gate*.
   Keep claims and citations together so v2 can verify them later.
4. **Watchlist ≠ sources** — `watchlist.jsonl` is the *input* list of sites to
   re-scrape; `sources.jsonl` is the *provenance* of cited claims. Keep them distinct.
5. **No silent caps** — every dropped candidate / stale site is `log()`ed.
6. **Published report ≠ ledger** — `report.md` is the clean artifact; raw search/scrape
   traces stay in `.coder/runs/`.
7. **The research agent gets read-only tools only** — an explicit Firecrawl allowlist
   (search/scrape, +map for discover), never `bypassPermissions`, and no
   Write/Edit/Bash. The node writes every file (decision 7), which is exactly what makes
   the tight allowlist sufficient — including for unattended/cron runs.

---

## 6. Component → file checklist (v1)

| Component | File |
|---|---|
| `WatchEntry` artifact; `AgentState` research fields (`research_mode`, `report_path`, `watchlist_path`) | [src/classes.py](../../src/classes.py) |
| Checkpointer serializer allow-list (+ `WatchEntry`) | [src/serde_config.py](../../src/serde_config.py) |
| Graph wiring + `classify_research_type` routing | [src/nodes/__init__.py](../../src/nodes/__init__.py) |
| `frame_brief`, `research_agent` (mode-parameterized), `save_report`, `load_prior_report`, `append_insights`, `score_sites`, `write_watchlist` | [src/nodes/research/nodes.py](../../src/nodes/research/nodes.py) |
| Generalized `approve_brief`/`approve_watchlist`, `classify_research_type`, `commit_push`/`merge` reuse | [src/nodes/general/nodes.py](../../src/nodes/general/nodes.py) |
| Research output-folder I/O (report/brief/sources/watchlist/last_run) | [src/ledger.py](../../src/ledger.py) or `src/research_io.py` (new) |
| Firecrawl MCP server (stdio `firecrawl-mcp@<pinned>`, `${FIRECRAWL_API_KEY}`) | `.mcp.json` in the RESEARCH repo (new) |
| `run_agent` `mcp_config`/`allowed_tools`/`output_format` kwargs | [src/nodes/helpers.py](../../src/nodes/helpers.py) |
| Prompts: `frame_brief`, `research_agent`, `discover_sites` | [src/prompts/research/](../../src/prompts/research/) |
| **v2:** `run_agents`, `web.py`, verify/coverage prompts + rubrics | helpers / `.coder/rubrics/` |

---

## 7. Sequencing notes

- **Phase R0 is the unblocker** — Firecrawl access through `claude -p` and the watchlist
  artifact are dependencies of every mode; prove the Firecrawl-through-`claude -p`
  spike first.
- **R1 proves the spine** — build the `new` mode end-to-end before R2/R3; `research_agent`
  is shared (mode-parameterized) by all three.
- **R2 then R3 by data dependency** — R2 (continuous) *reads* the watchlist; R3
  (discover) *writes* it. Hand-author a small `watchlist.jsonl` to test R2 before R3 exists.
- **Defer R4 (v2 rigor)** until a baseline v1 ticket runs and you can see *where* the
  single agent is weak — that evidence tells you whether to add the verifier, the
  fan-out, or the coverage loop first, rather than building all three speculatively.

---

## 8. Open questions

- **`frame_brief` depth in v1** — how much decomposition does a single-agent run need?
  Maybe just a sharp question + `done-when`, with the agent self-organizing the rest.
- **Research-agent timeout & progress** — a scrape+reason+write run far exceeds the
  600s `run_agent` default (continuous multi-site scrapes more so). Pick a research
  timeout and whether to stream progress.
- **Structured-output contract** — the agent must emit a parseable
  `{report_md, sources}`; reuse `parse_json_block` on the `--output-format json`
  result body, and decide the failure handling if it doesn't.
- **Sandbox for unattended web access** — the agent gets live web egress (and runs on a
  cron in continuous mode); the plan-v2 sandbox-graduation question applies before that.
- **Does `continuous` ever block on a user question** when scope drifts, or always
  proceed autonomously and flag? (Runbook default: proceed, flag on genuine drift.)
- **Watchlist size N** and per-run scrape budget — fixed, or a per-ticket gated input?
- **Change detection** — raw content hash flips on boilerplate; hash extracted
  main-content only, or rely on a scrape-tool diff? (Relevant from R2.)
- **(v2) Citation grounding mechanism** — deterministic `web.fetch` + agent
  support-check vs. fully agent-driven.
- **(v2) Source trust policy** — domain allow-list? primary vs. secondary weighting?
- **(v2) Worktree parallelism** for synthesizing independent report sections.
