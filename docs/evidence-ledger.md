# Evidence Ledger — Schema & Anti-Bloat Design

Status: proposed
Last updated: 2026-06-06
Companion to: [docs/implementation-plan-v2.md](docs/implementation-plan-v2.md) (§2.3)

---

## 0. The core tension

The evidence ledger has two jobs that pull in opposite directions:

- **Durability / audit / resume** wants to keep *everything* — every attempt, every
  log, every verdict — so a run is crash-safe, replayable, and auditable, and so the
  harness-improvement loop has a trace corpus.
- **Context health** wants to keep *almost nothing* in the agent's window — because
  **context rot is the most-cited long-horizon failure**, and a ledger that grows
  with every attempt and every change is the fastest way to rot a context.

The resolution is a **strict two-tier model**: the durable ledger lives on disk at
full fidelity and is *never* loaded wholesale; what an agent actually receives is a
small, capped, distilled **context payload** assembled per stage. The rule that makes
this work:

> An agent reads the **distilled outputs** of prior stages plus **pointers**. Raw
> logs, full diffs, and attempt history are pulled in **only on demand**, and only
> the specific excerpt needed (e.g. the failing assertion), never the whole file.

This is what keeps total context **flat across N changes** instead of growing O(N):
once a change is `DONE`, its full ledger is *sealed*, and everything downstream sees
only its ~1.5k-token `diff_summary.md`.

---

## 1. Two tiers

| | Tier 1 — Durable ledger (disk) | Tier 2 — Context payload (what the agent sees) |
|---|---|---|
| Fidelity | full | distilled / truncated |
| Lifetime | whole run + after | one stage |
| Size | unbounded | hard-capped per stage (§5) |
| Read by | resume, audit, AHE eval | the agentic coder, per stage |
| Example | `attempts.jsonl` (all attempts) | "attempt 4 of 4; last failure: `test_auth.py::test_expiry`" |

The harness (deterministic code) is the **only** thing that reads Tier 1 and decides
what becomes Tier 2. Agents never `cat` the ledger directory themselves — they
receive an assembled payload. (If an agent needs a raw excerpt, it requests it
through a tool that returns a bounded slice.)

---

## 2. Directory layout — ticket tier vs change tier

This answers **"where is the big plan written, and is the ledger per change?"**:
the ledger is **per change**, and the big plan is a **ticket-tier** artifact that
sits *above* the per-change ledgers.

Goals are **not** a ledger file (see §3.0): repo-level constraints live in the repo's
`CLAUDE.md`, ticket-level goals live on the ticket itself. And the big plan's open
questions are **not** a separate file either — they live as a section *inside*
`plan.md`, because a question is meaningless apart from the plan it's about.

```
<repo>/CLAUDE.md            # repo-level durable goals/constraints (auto-loaded)
<ticket>.json              # ticket-level goals + priority (a field on the ticket)

.coder/runs/<ticket_id>/
  ├─ plan.md               # ticket-tier: HIGH-LEVEL big plan + "Open questions &
  │                        #   decisions" section (questions → answers → woven in)
  ├─ changes.json          # ticket-tier: ordered changes + status + flags + soft_loc
  ├─ bootstrap.json        # ticket-tier: test/lint/typecheck commands
  ├─ final_review.json     # ticket-tier: whole-branch review verdict
  │
  └─ <change_id>/          # CHANGE-TIER ledger — one per atomic change
       ├─ inner_plan.md        # low-level plan for THIS change (if flagged)
       ├─ research_notes.md     # distilled findings from the inner research dive
       ├─ spec.md               # the DoD contract for this change
       ├─ dod.json              # structured done-conditions (test classes)
       ├─ test_red.log          # new tests failing BEFORE implementation
       ├─ test_green.log        # same tests passing AFTER
       ├─ lint.log
       ├─ typecheck.log
       ├─ attempts.jsonl        # one line per implement attempt
       ├─ remaining_delta.json  # the live loop variable
       ├─ review.json           # per-change independent review verdict
       ├─ reconcile_note.json   # if this change edited the outer plan + why
       ├─ diff_summary.md       # SEALED summary, written by the reviewer (§3)
       └─ record.json           # final status + pointers
```

- **Ticket tier** = the plan and everything that spans changes. Written by the outer
  graph (`big_plan`, `approve_plan`, `repo_bootstrap_check`, `final_review`,
  `replan`). Committed per change during the run (crash-safe), then **squashed at
  merge** so `main` stays clean (§6, decided).
- **Change tier** = everything scoped to one atomic change. Written by
  `implement_change` and its sub-sessions.

---

## 3. Evidence item catalog

For each item: **who produces it**, the **schema**, the **size cap**, **who reads
it**, and the **anti-bloat rule** that keeps it from rotting context.

### Ticket tier

#### `3.0` Goals — not a ledger file
Goals are **not** stored under `.coder/runs/`. They have two natural homes by
lifetime:
- **Repo-level constraints** (durable, high-level: "keep token cost bounded",
  "reliability over features", "this is a knowledge repo") → the repo's **`CLAUDE.md`**.
  Bonus: `claude -p` already auto-loads `CLAUDE.md`, so these reach every agent with
  zero plumbing.
- **Ticket-level goals + priority** (what *this* ticket is for, what's a non-goal) → a
  **field on the ticket** (`TicketContent`), authored when the ticket is written.
- **Read by:** `big_plan`, `inner_plan_and_research`, `review_change`,
  `final_review` — anywhere ambiguity must be resolved (`state.ticket` + the
  auto-loaded `CLAUDE.md`).
- **Why no file:** goals don't change per run, so a per-run artifact would be a stale
  copy; storing them with their owner (repo / ticket) keeps a single source of truth.

#### `plan.md` (the big plan + its open questions) — the canonical question record
- **Producer:** `big_plan`; the `## Open questions & decisions` section is answered
  at `approve_plan`, then the plan body is revised once to weave answers in.
- **Schema:** markdown — **high-level only**: the changes, their order, their intent.
  No file-level detail (that's deferred to `inner_plan.md`). Plus an `## Open
  questions & decisions` section: each blocking question, the human's answer written
  *beneath* it, and — once resolved — the change woven into the plan body. The
  section stays as the decision record.
- **Cap:** ≤ 1.5k tokens for the plan body; the Q&A section is transient and capped at
  ≤ 6 questions (more than that means the ticket is under-specified — surface *that*,
  don't enumerate 30).
- **Read by:** `big_plan` (self, on revise), `inner_plan_and_research` (relevant
  slice), `final_review`. The HITL gate reads the whole file; downstream stages read
  the *resolved* plan, not the raw Q&A.
- **Anti-bloat:** detail lives in per-change `inner_plan.md`, not here. For gate
  *routing* the harness keeps only a boolean in `AgentState` (`has_open_questions`) —
  it never has to parse the markdown to decide whether to interrupt.
- **The structured form is NOT a second file.** The UI needs the questions as clean
  JSON (with `options`: `pro`/`con`/`recommended`, per [docs/UI.md](docs/UI.md)); that
  lives in **`state.questions`** (checkpointed in SQLite) and reaches the UI via
  `interrupt({"questions": state.questions})` — ephemeral gate scaffolding, **not** a
  committed artifact. `plan.md` is canonical; the JSON is its projection for one
  consumer. This is the deliberate way to satisfy "questions in both JSON and the
  plan" without reintroducing two *persisted, editable* sources of truth.

#### `changes.json`
- **Producer:** `big_plan`; mutated by `reconcile_outer_plan` and `replan`
  (append/reorder PENDING only).
- **Schema:**
  ```json
  {
    "changes": [
      {
        "id": "c01",
        "title": "…",
        "intent": "one-line why",
        "soft_loc": 150,
        "needs_research": false,
        "needs_planning": false,
        "status": "pending|done|failed",
        "ledger": ".coder/runs/42/c01/"
      }
    ],
    "version": 3
  }
  ```
- **Cap:** the list itself; each entry ≤ ~60 tokens. No prose bodies here.
- **Read by:** `select_next_change`, `reconcile_outer_plan`, `replan`,
  `final_review`.
- **Anti-bloat:** entries are pointers + flags, not content; the change's substance
  lives in its `spec.md` / `diff_summary.md`.

#### `bootstrap.json`
- **Producer:** `repo_bootstrap_check`.
- **Schema:** `{ "test_cmd", "lint_cmd", "typecheck_cmd", "install_ok": true }`.
- **Cap:** trivially small.
- **Read by:** every gate in the implement loop.
- **Anti-bloat:** commands only, never their output.

#### `final_review.json`
- **Producer:** `final_review`.
- **Schema:** `{ "clean": bool, "bugs": [{ "where", "what", "severity", "evidence" }] }`.
- **Read by:** `replan` (to append fixes), the human (on surface).
- **Anti-bloat:** `evidence` per bug is a ≤ 200-token excerpt + pointer, not a dump.

### Change tier

#### `inner_plan.md`
- **Producer:** `inner_plan_and_research` (only if the change is flagged).
- **Schema:** markdown — the low-level approach for *this* change: files to touch,
  sequence, risks, what the research dive resolved.
- **Cap:** ≤ 2k tokens.
- **Read by:** `spec_and_tests`, `implement`, `reconcile_outer_plan`.
- **Anti-bloat:** scoped to one change; discarded from context once the change is
  `DONE` (sealed on disk).

#### `research_notes.md`
- **Producer:** `inner_plan_and_research` (the read-heavy dive; may fan out
  sub-agents).
- **Schema:** distilled findings + pointers (file/URL refs), not transcripts.
- **Cap:** ≤ 1.5k tokens (the §plan-2.6 "1–2k distilled summary" rule).
- **Read by:** `inner_plan_and_research` (self, to write `inner_plan.md`); not loaded
  by later stages unless cited.
- **Anti-bloat:** sub-agents return conclusions, never their raw exploration; the
  dive's full transcript is never persisted to context.

#### `spec.md` + `dod.json`
- **Producer:** `spec_and_tests` (the test-author session).
- **Schema (`dod.json`):**
  ```json
  {
    "new_tests": ["tests/test_x.py::test_a"],
    "affected_tests": ["tests/test_y.py"],
    "gates": ["lint", "typecheck", "full_suite"]
  }
  ```
- **Cap:** `spec.md` ≤ 1k tokens; `dod.json` small.
- **Read by:** `implement`, `review_change`.
- **Anti-bloat:** the spec is the contract, kept tight; it is *the* thing the
  implementer and reviewer share, so it must stay readable.

#### `test_red.log` / `test_green.log` / `lint.log` / `typecheck.log`
- **Producer:** the deterministic gates (`spec_and_tests` for red; `implement` for
  the rest).
- **Schema:** raw tool output (disk, Tier 1).
- **Cap (disk):** none. **Cap (context, Tier 2):**
  - **On failure:** only the failing tail — failing test ids + assertion lines, last
    ~50 lines, ≤ 500 tokens.
  - **On pass:** a single line — `PASS (37 tests, 4.1s)` — ≤ 20 tokens.
- **Read by:** `implement` (to fix the current failure), via `remaining_delta`.
- **Anti-bloat:** **the single biggest bloat source.** Never load a full log into
  context. Green = one line; red = the failing slice only.

#### `attempts.jsonl`
- **Producer:** `implement`, appended once per attempt.
- **Schema (one line):**
  ```json
  { "n": 4, "action": "fix expiry check", "delta_before": 3, "delta_after": 1,
    "delta_hash": "ab12…", "loc_changed": 38 }
  ```
- **Cap (disk):** unbounded. **Cap (context):** only the **last 1–2 attempts'**
  lines; older attempts collapse to `"attempts 1–3: see ledger; delta 5→3→1"`.
- **Read by:** `implement` (to avoid repeating a failed fix).
- **Anti-bloat:** the loop needs *recent* history, not all of it; old attempts are a
  count + hash, not full records.

#### `remaining_delta.json`
- **Producer:** `implement`, rewritten each attempt.
- **Schema:** the live loop variable (see plan §2.4): open items only +
  `previous_delta_hash` + `stagnation_count`.
- **Cap:** top-K open items; each `evidence` ≤ 200 tokens.
- **Read by:** `implement` (it *is* the loop), `review_change` indirectly.
- **Anti-bloat:** holds only what's *still wrong*, never the history of what was
  fixed — that's `attempts.jsonl`.

#### `review.json`
- **Producer:** `review_change` (the independent reviewer session).
- **Schema:** `{ "ok": bool, "bugs": [{ "where", "what", "severity", "evidence" }] }`.
- **Read by:** `implement` (bugs → `remaining_delta`); the human (on surface).
- **Anti-bloat:** structured + small; bugs reference diff locations, not whole files.

#### `reconcile_note.json`
- **Producer:** `reconcile_outer_plan` (only if this change edited the big plan).
- **Schema:** `{ "changed": "changes.json", "cause": "<final-review bug | discovered
  fact>", "diff": "appended c07; reordered c05↔c06" }`.
- **Read by:** the human (audit), `replan` (to avoid re-litigating).
- **Anti-bloat:** records the *cause* of a plan mutation, which is required anyway to
  contain scope drift (plan §3.6).

#### `diff_summary.md`  ← the load-bearing anti-bloat artifact
- **Producer:** the **independent reviewer** (`review_change`), on the final clean
  pass — *not* the warm implementer (§6, decided). The reviewer already has the final
  diff loaded and is independent of the author, so the summary it writes is both free
  (no extra call) and more honest than the implementer's self-report.
- **Schema:** markdown — what changed, why, the public surface touched, anything the
  next change must know. Written *for the next reader*, not as a log.
- **Cap:** ≤ 1.5k tokens. Hard.
- **Read by:** **every later change** (as context for what already happened) and
  `final_review`.
- **Anti-bloat:** **this is the mechanism that keeps context flat across changes.**
  Change 7 reads changes 1–6 as six ≤1.5k summaries, *not* as six full ledgers. A
  sealed change never re-enters context as raw material. Past ~30 sealed changes this
  set is itself compacted into a rolling digest (§5, rule 10).

#### `record.json`
- **Producer:** `mark DONE`.
- **Schema:** `{ "status", "attempts", "loc_changed", "ledger_pointers": {...} }`.
- **Read by:** resume, telemetry, AHE.
- **Anti-bloat:** pointers + counts, not content.

---

## 4. Stage-by-stage I/O — what the agentic coder actually reads

This is the operational answer to **"for each stage, what evidence is read?"** Every
"reads" column is the Tier-2 context payload (already distilled/capped), not the raw
ledger.

| Stage | Reads (context payload) | Writes (ledger) | Passes forward |
|---|---|---|---|
| `repo_bootstrap_check` | ticket, repo config | `bootstrap.json` | test/lint/typecheck cmds |
| `big_plan` | ticket body + ticket goals, `CLAUDE.md`, **live repo** | `plan.md` (incl. `## Open questions`), `changes.json`; `state.questions` (structured, for UI) | high-level plan + change list |
| `approve_plan` (HITL) | `state.questions` (clean JSON → UI), `plan.md` | answers written into `plan.md`; revised plan body | answered, woven plan |
| `select_next_change` | `changes.json` (status only) | — | current change's `dod` + flags |
| `inner_plan_and_research` | change entry, `plan.md` slice, ticket goals + `CLAUDE.md`, **live repo/web** | `inner_plan.md`, `research_notes.md` | `inner_plan.md` (≤2k) |
| `spec_and_tests` | change entry, `inner_plan.md` | `spec.md`, `dod.json`, tests, `test_red.log` | `spec.md` + `dod.json` + test paths |
| `implement` (loop) | `spec.md`, `dod.json`, `inner_plan.md`, test files, `remaining_delta.json`, `bootstrap.json`, **last 1–2 attempts** | diff, `*_green.log`, `lint.log`, `typecheck.log`, `attempts.jsonl`, `remaining_delta.json` | (within loop) `remaining_delta` only |
| `review_change` | **final diff of this change**, `spec.md`, `dod.json`, test-file diff, rubric | `review.json` + (on clean pass) `diff_summary.md` | bugs[] → delta, or ok + **`diff_summary.md` (≤1.5k)** |
| `reconcile_outer_plan` | `inner_plan.md`, `plan.md`, `changes.json` | updated `changes.json`, `reconcile_note.json` | updated change list |
| `mark DONE` | this change's `diff_summary.md` | `record.json` | sealed change |
| `final_review` | problem statement, ticket goals + `CLAUDE.md`, **per-change `diff_summary.md` ×N or rolling digest** (§5.10), `changes.json` | `final_review.json` | bugs[] → replan, or clean |
| `replan` | `final_review.json`, `changes.json` | updated `changes.json`, `replan_log` | updated change list |

**The two things to notice:**

1. `implement` never reads earlier changes' logs or attempts — only *this* change's
   spec, plan, and live delta. Earlier changes reach it (if at all) only as
   `diff_summary.md`.
2. `review_change` reads the **final diff**, not the attempt history — it judges the
   result, independent of how the implementer got there. This is what makes it a
   real second opinion rather than a continuation.

---

## 5. Anti-bloat mechanisms (the rules, collected)

1. **Two tiers, hard wall.** Disk = full; context = distilled. Agents never read the
   raw ledger directory; the harness assembles each payload.
2. **Logs: pass = 1 line, fail = failing slice.** Never load a full test/lint/type
   log. This is the dominant bloat source and the dominant fix.
3. **Seal-and-summarize.** A `DONE` change is sealed; downstream reads its ≤1.5k
   `diff_summary.md` (authored by the independent reviewer), never its ledger. Keeps
   context **flat across N changes**.
4. **Recent-window for attempts.** Only the last 1–2 attempts are verbatim; older =
   count + delta trajectory + hash.
5. **Delta holds only what's still wrong.** Fixed items leave the delta; history goes
   to `attempts.jsonl` (disk).
6. **Caps are forcing functions.** `plan.md` ≤1.5k keeps the big plan high-level;
   `inner_plan.md` ≤2k keeps detail just-in-time and per-change; ticket goals are kept
   short by living on the ticket, not in a growable file.
7. **Structured over prose.** Verdicts, deltas, and reviews are JSON with bounded
   `evidence` fields, not free text — cheaper to carry and to diff.
8. **Pointers over payloads.** `changes.json`, `record.json`, and bug `evidence`
   reference locations; the content is fetched on demand, bounded.
9. **Per-stage context budget.** Each stage gets a token budget for prior evidence
   (suggested: bootstrap ~0.1k, plan/spec ~3k, implement ~4k, review ~3k, final
   ~N×1.5k up to the digest threshold). Exceeding it triggers compaction, not silent
   truncation — and the drop is `log()`-ged (no silent caps).
10. **Rolling digest past ~30 changes.** `diff_summary.md` scales O(N), so beyond a
    threshold — **~30 sealed changes (≈45–50k tokens of summaries)** — the *oldest*
    summaries are compacted into a single `ticket_digest.md` (a higher-level "what
    happened in changes 1–20" rollup), while the most recent ~10 stay verbatim. This
    is a **rare safety valve**: most tickets are well under 30 changes; a ticket that
    isn't is a signal the *plan* was too coarse. The full summaries remain on disk;
    only the loaded context is digested. (Cheaper alternative, deferred: load only the
    *relevant* prior summaries via retrieval instead of all N — more plumbing, so the
    threshold-digest ships first.)

---

## 6. Decided (was: open questions)

- **Branch-commit cadence — DECIDED: squashed at merge.** Commit `.coder/runs/` per
  change *during* the run for crash-safe resume, then squash those commits when the
  ticket branch merges to `main`, so `main`'s history stays clean. (Open sub-point:
  whether the ledger is even kept on `main` post-merge or stripped as pure trace data
  — leaning strip/gitignore on `main`, keep on the branch.)
- **`diff_summary.md` authorship — DECIDED: the independent reviewer.** Honest (not a
  self-report) and free (the reviewer already holds the diff). See §3 / §5.3.
- **Large-N compaction — DECIDED: rolling digest at ~30 changes.** See §5.10.
- **Excerpt-on-demand tool — DECIDED to specify it.** The whole anti-bloat design
  *pushes* a distilled payload; this tool lets an agent *pull* a bounded raw slice
  when distillation dropped something it genuinely needs — e.g. the full traceback for
  one failing test, or lines 40–90 of `test_green.log`. Without it, the only way to
  recover an omitted detail is to dump the whole raw file, which defeats the point;
  with it, distillation can stay aggressive because nothing is truly lost. Proposed
  shape: `read_ledger_slice(change_id, artifact, {lines | test_id | bytes})` returning
  a hard-capped window (e.g. ≤ 100 lines / ≤ 2k tokens). It is the pressure-release
  valve that makes rules 2–6 safe.
