# Research

The research stage handles **two kinds of question**, which split right after
intake (mirrors the two boards in [UI.md](../UI.md) — one-off vs recurring):

| Kind | Board | Goal | Question load | Output |
|---|---|---|---|---|
| **New question** | one-off | Answer it from scratch with a deep, cited report | **Heavy** — ask the user a lot up front | New report folder |
| **Continuous question** | recurring | Surface *new* insights — via open web search **and** by scraping a saved watchlist of sites | **None / light** — brief already exists | Append delta to the existing folder |
| **Discover sources** | one-off (setup) | Find sites/blogs worth monitoring for a question | Light | `watchlist.jsonl` — consumed by the continuous branch |

All three reuse the same harness spine. They diverge only in the framing/question
phase, the input sources, and how the output folder is written. The output target is
the knowledge repo (`Repo.RESEARCH`); findings are committed and merged like code.

> **v1 vs v2 scope.** In **v1**, a **single reasoning agent** does the research with
> its tools (Firecrawl search + scrape): it gathers sources, reasons against the
> brief, and writes the report itself. The richer pipeline — parallel `investigate`
> fan-out, adversarial `verify_claims`, single-writer `synthesize`, and the
> `coverage_critic` + `replan` loop — is the **v2** target and is marked *(v2)* in the
> steps below. See [docs/research/implementation-plan.md](../research/implementation-plan.md).

**The watchlist links two branches.** Discover-sources (C) *writes* `watchlist.jsonl`;
the continuous branch (B) *reads and re-scrapes* it every run. So the lifecycle of a
monitored question is: run C once to seed the watchlist → run B on a schedule to
harvest deltas.

---

## 0. Intake (shared)

1. **pick_up_ticket** — load the question + any prior context.
2. **assert_research** — confirm this is a research ticket (not coding).
3. **classify_research_type** — `new` | `continuous` | `discover`.
   - `discover` if the ask is "find me sites to follow for X" (seeds a watchlist).
   - `continuous` if the ticket is recurring **and** a prior report folder exists
     for it (it may also carry a `watchlist.jsonl`).
   - otherwise `new`.
4. **open_branch** — branch in the knowledge repo.

---

## A. New question (one-off) — ask a lot, then research deep

The whole point of this branch is to **front-load questions** so the deep
research runs against a sharp, calibrated brief. Plan quality caps everything
downstream, so we spend the questions here.

### A1. Frame the brief
5. **frame_brief** — `claude -p` → `brief.md` + `questions.json`.
   - Decompose the question into **disjoint sub-questions**.
   - Per sub-question: depth, coverage criteria, and an explicit **`done-when`**.
   - Calibrate to the user's stated knowledge level (don't over-explain basics,
     don't under-support novel claims).
   - Set the **autonomy** + **token/round budget** inputs (see Knobs below).

### A2. Question loop (the heavy part)
6. **surface_questions** — generate scoping/depth questions. Each with **pro / con
   per option and a recommended default**, per [UI.md](../UI.md).
7. **get_user_answer** — push questions to the UI; wait for answers.
8. **implement_user_answer** — fold answers back into `brief.md` + the question.
9. **decide_if_additional_questions** — if the updated brief has open unknowns,
   loop to step 6. Stop when the brief is sharp (or the autonomy knob says
   "decide the rest yourself").
10. **approve_brief** — **the single HITL gate.** User approves scope, depth, and
    budget before any expensive investigation runs.

### A3. Research
11. **research_agent** *(v1)* — a single reasoning agent works the approved brief end
    to end with its tools: searches broadly, reads/scrapes sources, reasons against
    each sub-question's `done-when`, and writes `report.md` with **inline citations
    for the sources it read** + `sources.jsonl`. Coverage and citation quality are the
    agent's own responsibility in this one pass.
    > **v2 (deferred):** replace the single agent with parallel **investigate**
    > (one sub-agent per sub-question → distilled 1–2k summaries) → adversarial
    > **verify_claims** (re-fetch each citation, confirm support, refute) →
    > single-writer **synthesize** → **coverage_critic** + bounded **replan_research**
    > / loop-until-dry. Tracks the claim-to-grounded-claim gap.

### A4. Save + ship
12. **save_report** — write the output folder (layout below).
13. **commit_push → merge** into the knowledge repo.

---

## B. Continuous question (recurring) — surface new insights

The brief already exists from prior runs, so **skip the question loop**. The job
is to find what changed since last time and append only the delta — not re-answer
the whole question. New content comes from **two input sources**, either or both of
which may be active: a **saved watchlist** of sites (scraped every run) and **open
web search**.

### B1. Load prior state
5. **load_prior_report** — read the existing report folder for this ticket:
   `report.md`, `sources.jsonl`, `watchlist.jsonl` (if present), and `last_run.json`
   (timestamp + seen-source/content hashes).
6. **refresh_brief** *(light)* — re-confirm the sub-questions still match intent.
   Only surface a question to the user if the scope genuinely drifted; otherwise
   proceed autonomously.

### B2. Gather what's new
7. **research_agent** *(v1, update mode)* — a single reasoning agent gathers new
   content from two inputs and reports only the delta:
   - **Watchlist** *(if `watchlist.jsonl` exists)* — scrape each entry per its `scope`
     (single-page fetch, or a subpath crawl), sequentially. Hash the content vs the
     entry's `last_content_hash`; unchanged → skip; changed → extract the new content
     and record which entry it came from, updating `last_scraped_at` +
     `last_content_hash`. A persistently dead/blocked URL → flag `status: stale` and
     `log()` it; never silently drop a watched site.
   - **Open web** — searches scoped to **new insights since `last_run`** (recency
     filters, "what changed", sources not already in `sources.jsonl`). Dedup against
     seen hashes.
   - Keep only genuinely new/changed findings; cite each. If nothing new survives
     dedup (watchlist unchanged *and* no new search hits), record a **"no new insights
     this run"** note and stop (a valid, logged outcome).
   > **v2 (deferred):** parallelize the watchlist scrape + open search (one agent per
   > entry/angle) and gate findings through adversarial **verify_claims** before
   > append — a "new" insight only counts once its citation resolves and supports it.

### B3. Append + ship
8. **append_insights** — prepend a dated **"Insights — <date>"** section to
   `report.md` (cite which watched site or search surfaced each item), append new
   rows to `sources.jsonl`, persist the updated `watchlist.jsonl`, and update
   `last_run.json`.
9. **commit_push → merge**.

> Continuous runs are the natural fit for a cron/loop trigger (see
> [ideas.md](../ideas.md) — "Cron jobs … wrapped around a standard git workflow").

---

## C. Discover sources — build the watchlist a continuous question scrapes

Given a question, find the sites/blogs worth following and emit `watchlist.jsonl`
in exactly the format branch B consumes. This is the **setup step** that turns a
question into a monitored, continuous one.

### C1. Frame
5. **frame_brief** *(light)* — restate the question and what kind of source is
   relevant (research blogs? vendor docs? news? a specific author?). Surface a
   question to the user only if the source intent is ambiguous.

### C2. Find candidate sites
6. **discover_sites** *(v1: single agent)* — one reasoning agent sweeps for sites via
   several angles with its tools (web search, domain enumeration / site-mapping,
   following links from a known seed). For each candidate it records: URL, kind, why
   it's relevant, and a freshness signal (does it actually publish new content?). It
   then ranks by relevance + freshness + authority (prefer primary sources), drops
   dupes/dead domains, and keeps the top N (N is a knob); `log()` what was cut so the
   cap isn't silent.
   > **v2 (deferred):** parallelize the sweep (one agent per angle) and score with an
   > independent ranker.

### C3. Confirm + write the watchlist
7. **approve_watchlist** — **HITL gate**: show the ranked candidates with pro/con +
   recommended, per [UI.md](../UI.md); the user keeps/drops/adds entries. (High
   autonomy may auto-accept the top N.)
8. **write_watchlist** — emit `watchlist.jsonl` (one row per site) into the
   question's report folder, with `last_scraped_at`/`last_content_hash` empty so the
   first continuous run treats everything as new. If the folder doesn't exist yet,
   also scaffold an empty `report.md` + `brief.md` so the ticket flips to
   continuous-ready.
9. **commit_push → merge**.

> A **new question (A)** can optionally end by invoking C to set up monitoring —
> "answer it now, then keep watching these sources." Likewise, a continuous run can
> re-invoke C's `discover_sites` to *grow* the watchlist when coverage looks thin.

---

## Output folder (md format)

Both branches write the **research output** as a folder in the knowledge repo:

```
research/<ticket-slug>/
  report.md          # the synthesized, cited answer (continuous: dated insight sections)
  brief.md           # framed question + sub-questions + done-when criteria
  sources.jsonl      # provenance of cited claims: {url, claim_ids, fetched_at, supports}
  watchlist.jsonl    # sites to scrape every continuous run (written by C, read by B)
  last_run.json      # continuous only: {ran_at} — the recency cutoff; dedup is via sources.jsonl
```

`sources.jsonl` vs `watchlist.jsonl` are **not** the same: `sources.jsonl` records
where cited claims came from (output/provenance); `watchlist.jsonl` is the *input*
list of sites to re-scrape. One watched site can produce many sources over time.

`watchlist.jsonl` — one row per site:

```
{url, kind, why, scope: "single-page" | "crawl-subpath" | "rss",
 added_at, last_scraped_at, last_content_hash, status: "active" | "stale"}
```

The per-run **evidence ledger** (search logs, verifier JSON, refutation attempts,
`remaining_delta`) lives separately under `.coder/runs/<ticket>/` per
[implementation-plan-v2.md](../implementation-plan-v2.md) §2.3 — keep raw process
artifacts out of the published `report.md` to avoid context rot.

`report.md` rules: every claim carries a citation; record per-claim confidence and
note dissenting sources; lead with the answer, not the search trail.

---

## Done predicate

v1 (single-agent) predicates — the harness checks these, not the agent:

```
new question done = brief approved ∧ report.md written (addresses the brief) ∧ committed
continuous done   = every watchlist entry scraped (or flagged stale)
                    ∧ ( new findings appended  ∨  "no new insights" logged )
discover done     = watchlist.jsonl written & approved ∧ ≥1 active entry
```

> **v2** strengthens "report written" into the grounded predicate:
> `(∀ sub_questions: answered ∧ every_claim_grounded ∧ verifier == "claims_supported")
> ∧ coverage_critic == "complete vs question"`.

---

## Knobs

- **autonomy** — how far the agent decides questions itself vs. surfacing them.
  High autonomy shrinks the New-question loop (A2) toward the Continuous path.
- **token/round budget** — bounds investigate fan-out width and max replan rounds;
  a per-ticket input gated at brief approval, never a silent constant.
- **depth calibration** — write `report.md` to the user's knowledge level.
- **watchlist size (N)** — max sites discover-sources (C) keeps; bounds per-run
  scrape cost for the continuous branch (B).

## Open questions
- How to decide a stage is "done" beyond the hard predicates above —
  is `coverage_critic == "complete"` trustworthy without a held-out check?
- Continuous: how to weight primary vs. secondary sources, and an allow-list of
  trusted domains?
- Change detection: a raw content hash flips on every dynamic/boilerplate change —
  do we hash extracted main-content only, or rely on a scrape tool's built-in
  change/monitor diff?
- Token reduction — one warm session across the New-question loop vs. fresh
  context per phase?
