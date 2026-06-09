# Phase 1 — Outer Loop: Plan + Approval (detailed)

Status: proposed
Last updated: 2026-06-06
Parent: [docs/coding/implementation-plan.md](docs/coding/implementation-plan.md) (Phase 1)
Builds on: Phase 0 (data model, gates, ledger, bootstrap — all merged)

---

## 1. Goal & scope

Turn the current linear stub pipeline into the **outer orchestrator loop**: pick up a
coding ticket, produce a high-level plan, gate it past the human once, then iterate
changes to completion and merge. Phase 1 delivers the *real* outer loop and leaves the
inner change as a temporary stub so the whole graph is **runnable end-to-end**.

**In scope (real):**
- Wire the Phase-0 `repo_bootstrap_check` node into the graph.
- `big_plan` — high-level `plan.md` + ordered `changes.json` + surfaced questions.
- `approve_plan` — the single HITL gate; bounded re-plan on rejection.
- `select_next_change` — deterministic routing over pending changes.
- `commit_push` / `merge` — real git side effects (ledger squashed at merge).
- Graph rewiring for the coding path.

**Out of scope (temporary stubs this phase, real later):**
- `implement_change` → **stub** that marks the current change `DONE` (Phase 2 builds the
  three-session inner loop).
- `final_review` → **stub** pass-through (Phase 3 builds the real whole-branch review +
  bounded `replan`).

**Unchanged:** the research path stays on its existing stub; Phase 5 rebuilds it.

---

## 2. Target graph

```
START
 → pick_up_ticket            (existing)
 → open_branch               (existing)
 → repo_bootstrap_check      (Phase 0 node — NOW WIRED; FAILURE → END)
 → route_by_type             coding → big_plan ;  research → research (existing stub)
 → big_plan                  agent → plan.md + changes.json + state.questions
 → approve_plan              interrupt(...)  ← THE single HITL gate
 → route_after_approval      approved              → select_next_change
                             rejected, replans<cap → big_plan  (revise w/ feedback)
                             rejected, replans≥cap → END (Status.FAILURE)
 → select_next_change        sets current_unit_id = first PENDING (or None)
 → route_change              pending → implement_change ;  none → final_review
 → implement_change          [STUB: mark current change DONE] → select_next_change
 → final_review              [STUB: pass-through] → commit_push
 → commit_push               commit + push the ticket branch
 → merge                     squash-merge / open PR → END
```

### Changes vs the current graph ([src/nodes/__init__.py](src/nodes/__init__.py))
- `open_branch → surface_questions` becomes `open_branch → repo_bootstrap_check →
  route_by_type`.
- `assert_coding` is **repurposed** as `route_by_type` (same bool function, new
  position: `repo_bootstrap_check`'s conditional edge).
- The coding stubs `surface_questions`, `get_user_answer`, `spec`, `write_tests`,
  `write_code`, `review` are **unwired** from the coding path. `get_user_answer` is
  already orphaned today; `surface_questions`/`get_user_answer` are superseded by
  `big_plan`/`approve_plan`. Leave the functions in place (research may reuse the
  question machinery in Phase 5); delete the coding-only stubs in Phase 2.
- Add an explicit `END` edge after `merge` (currently missing).

---

## 3. Node contracts

Each node is `(AgentState) -> AgentState`. Conditional edges are pure
`(AgentState) -> <route key>` (no mutation).

### 3.1 `repo_bootstrap_check` (wire the Phase-0 node)
Already implemented in [src/nodes/general/nodes.py](src/nodes/general/nodes.py). Phase 1
only **wires** it and adds its failure edge.
- On `BootstrapError` it sets `Status.FAILURE`; the graph must route `FAILURE → END`
  (add a conditional after this node, or a shared `route_or_fail`).

### 3.2 `route_by_type` (conditional)
- Reuse `assert_coding(state) -> bool` ([general/nodes.py:31](src/nodes/general/nodes.py#L31)).
- `True → "big_plan"`, `False → "research"`.

### 3.3 `big_plan`  (new — [src/nodes/coding/nodes.py](src/nodes/coding/nodes.py))
**Pre:** `state.ticket`, `state.repo_path`, `bootstrap.json` present.
**Does:** runs one `claude -p` agent that explores the repo and emits a single strict
JSON object (schema in §4). The **node owns all file writes** ("agents propose, the node
commits", v2 §2.2):
1. Run agent → parse stdout JSON `{plan_md, changes, questions}`.
2. Write `plan.md` (= `plan_md` + a rendered `## Open questions & decisions` section).
3. Write `changes.json` = `{"changes": [...], "version": N}` via the ledger.
4. Build `state.units` (one `WorkUnit` per change, `status=PENDING`,
   `ledger_path = change_dir(repo, ticket, id)`).
5. Set `state.plan_path`, `state.questions` (structured UI payload),
   `state.has_open_questions = len(questions) > 0`.

**Revise mode:** if `plan.md` already exists *and* `state.approval` carries feedback
(rejection) — see §3.4 — the prompt includes the prior `plan.md` + the feedback and the
agent returns a revised plan. Re-runs overwrite the pending plan/changes (nothing is
`DONE` yet at plan time, so there is nothing to preserve in Phase 1).

**Failure:** subprocess non-zero, JSON parse failure, empty/invalid `changes`, or
duplicate ids → `Status.FAILURE`.

**Routing:** always → `approve_plan` (single outgoing edge).

### 3.4 `approve_plan`  (new — the single HITL gate)
Reuses the `interrupt()` mechanism already used by `get_user_answer`
([general/nodes.py:137](src/nodes/general/nodes.py#L137)).

**Interrupt payload (to UI):**
```json
{ "plan_md": "<contents of plan.md>",
  "changes": [{"id","title","intent","soft_loc"}],
  "questions": [ <state.questions, with options pro/con/recommended> ] }
```
**Resume payload (from UI):**
```json
{ "approved": true,
  "answers": [{"id":"q1","answer":"<chosen label or free text>"}],
  "feedback": "optional free-text when rejecting" }
```
**Does:**
- Store the resume payload in `state.approval` (new field, §6).
- If `answers` present: append each answer **beneath its question** in `plan.md`'s
  `## Open questions & decisions` section (deterministic edit — the durable record).
- Does *not* itself branch; `route_after_approval` decides.

> **Phase-1 simplification (flagged):** answers are *recorded* beneath their questions,
> not re-woven into the plan body by a second agent pass. The implementer (Phase 2)
> reads the resolved `plan.md`, which includes the answered questions, so no information
> is lost. A "weave answers into the body" revise pass is an optional later refinement.

### 3.5 `route_after_approval` (conditional)
- `state.approval["approved"] is True` → `"select_next_change"` (clear
  `has_open_questions`).
- rejected **and** `state.replans < MAX_REPLANS` → increment `state.replans`,
  → `"big_plan"` (revise; feedback already in `state.approval`).
- rejected **and** `state.replans ≥ MAX_REPLANS` → set `Status.FAILURE` → `END`.

`MAX_REPLANS` reuses the bound from [src/nodes/__init__.py](src/nodes/__init__.py)
(`MAX_RETRIES = 3`) or a dedicated constant.

### 3.6 `select_next_change`  (new — node) + `route_change` (conditional)
- **Node:** set `state.current_unit_id` = id of the first `WorkUnit` with
  `status == PENDING`, else `None`. (Routing functions must not mutate, so the pick is a
  node.)
- **Conditional `route_change`:** `current_unit_id is not None → "implement_change"`,
  else `"final_review"`.

### 3.7 `implement_change`  (**Phase-1 STUB**)
- Mark the unit named by `state.current_unit_id` as `DONE` in `state.units`, persist
  `changes.json` (so resume sees progress), `step += 1`. → `select_next_change`.
- Phase 2 replaces this entire node with the three-session inner loop; the outer-loop
  contract (consume one PENDING change, leave it DONE, return) stays identical.

### 3.8 `final_review`  (**Phase-1 STUB**)
- Pass-through (`step += 1`) → `commit_push`. Phase 3 replaces with the real review +
  bounded `replan`.

### 3.9 `commit_push`  (new — real)
- `git add -A` in `repo_path`; commit `f"ticket {id}: {title}"`; push the ticket branch
  to `origin` **if a remote exists** (detect via `git remote`; skip cleanly if none).
- Uses the existing `subprocess` + branch conventions from `open_branch`
  ([general/nodes.py:39](src/nodes/general/nodes.py#L39)).

### 3.10 `merge`  (new — real, with a decision; see §7)
- **Recommended Phase-1 default: open a PR** (`gh pr create`) rather than auto-merge to
  `main` — unattended merge should wait until the verifier + review (Phase 3) give
  confidence (per the summary's "don't let generated code merge unattended" caution).
- The eventual autonomous path: `git checkout main && git merge --squash <branch> &&
  git commit` (squashes the per-change ledger commits into one clean `main` commit, per
  evidence-ledger §6). Implement behind a config flag; default to PR.
- → `END`.

---

## 4. `big_plan` agent I/O contract

The agent (one `claude -p`, default read tools) explores the repo and emits **only** a
JSON object as its final message. The node strips ```json fences and parses (reuse the
logic already in `surface_questions`, extracted to a helper — §5).

```json
{
  "plan_md": "## Plan\n<high-level prose: what changes, in what order, why. NO file-level detail>",
  "changes": [
    { "id": "c01", "title": "short imperative title",
      "intent": "one line: why this change exists",
      "soft_loc": 150,            // soft size budget; null if genuinely unknown
      "needs_research": false,    // → Phase-2 inner research dive
      "needs_planning": false }   // → Phase-2 inner planning session
  ],
  "questions": [                  // [] when the ticket is workable as written
    { "question": "phrased for yes/no or option pick",
      "category": "ambiguity | missing_context | scope",
      "why": "what I'd build wrong without an answer",
      "options": [ {"label": "...", "pro": "...", "con": "...", "recommended": true} ] }
  ]
}
```

**Node validation (→ `Status.FAILURE` on breach):** `changes` non-empty; `id`s unique
and slug-safe (they become ledger path segments — see Phase-0 `_safe_segment`);
`soft_loc` is a positive int or null; `questions` ≤ 6 (more ⇒ the ticket is
under-specified — surface *that*). The agent is told to **bias toward zero questions**
and resolve what it can from the ticket `goals` + repo + `CLAUDE.md` (§2.10).

---

## 5. Helpers to extract / add

To avoid duplicating the `surface_questions` subprocess+parse logic across nodes:

- **`run_agent(prompt, repo, *, timeout=600) -> subprocess.CompletedProcess`** — the
  `claude -p` + `clean_subscription_env(oauth_token)` call. → [src/nodes/helpers.py](src/nodes/helpers.py).
- **`parse_json_block(stdout) -> Any`** — fence-strip + locate `{...}`/`[...]` + `json.loads`
  (lift from [general/nodes.py:115-128](src/nodes/general/nodes.py#L115)).
- **Ledger (extend [src/ledger.py](src/ledger.py)):** `write_plan(repo, ticket, plan_md)`,
  `write_changes(repo, ticket, units)`, `load_changes(repo, ticket)` — `changes.json` ⇄
  `list[WorkUnit]`. `big_plan` and the `implement_change` stub both write `changes.json`,
  so this belongs in one place.

---

## 6. Data-model change ([src/classes.py](src/classes.py))

Add one transient field to `AgentState` for the approval-gate routing:

```python
approval: Optional[dict] = None   # HITL resume payload {approved, answers, feedback}; §3.4
```

It is a plain `dict` (like `questions`), so **no new `allowed_msgpack_modules` entry** is
needed. `AgentState` is already registered (Phase 0). No other model changes.

---

## 7. Decisions & tradeoffs

| # | Decision | Recommended | Alternative | Why |
|---|---|---|---|---|
| 1 | `merge` behavior | **open a PR** (`gh pr create`) | auto squash-merge to `main` | unattended merge is unsafe until Phase-3 review exists; PR keeps a human in the loop for the *actual* merge while the loop is still young |
| 2 | `big_plan` output | **agent returns one JSON; node writes files** | agent writes `plan.md`/`changes.json` itself | single parse + validation point; node controls paths; matches "agents propose, the node commits" |
| 3 | Answer handling | **record beneath questions (deterministic)** | second agent pass to weave into body | bounded + deterministic for Phase 1; no info lost; weave can be added later |
| 4 | Gate when 0 questions | **still gate (approve the plan)** | skip the gate when no questions | plan approval is *the* leverage point; a clean plan still deserves one look |
| 5 | Keep graph runnable | **stub `implement_change`/`final_review`** | build inner loop now | preserves "each phase ships a runnable graph"; stubs share the real contract |

---

## 8. Edge cases & failure modes

- **No remote** in the target repo → `commit_push` commits locally, skips push (don't fail).
- **Dirty tree** before `big_plan` → already guarded by `open_branch`'s dirty-tree refusal.
- **Agent emits prose around the JSON** → `parse_json_block` strips fences / locates the
  object; unrecoverable → `Status.FAILURE` (don't half-apply a plan).
- **`changes` empty or ids collide** → `Status.FAILURE` (a planless plan is a bug).
- **Rejection loop** → bounded by `MAX_REPLANS` → `Status.FAILURE` (surface, don't thrash).
- **Resume mid-gate** → `approve_plan` re-issues the `interrupt`; `plan.md`/`changes.json`
  are already on disk so nothing is recomputed (crash-safe, per Phase-0 ledger).
- **`FAILURE` from any node** → must reach `END` (add `route_or_fail` after the
  side-effecting nodes).

---

## 9. Test plan

Unit tests (monkeypatch `subprocess.run` and `interrupt`; reuse the `tmp_path` git-repo
fixture pattern from `test_gates.py`):

- **big_plan** — canned agent JSON → asserts `plan.md` + `changes.json` written,
  `state.units`/`questions`/`has_open_questions`/`plan_path` set; bad JSON / empty
  changes / dup ids → `Status.FAILURE`; revise mode includes the prior plan.
- **approve_plan** — monkeypatch `interrupt` to return approved / rejected / with-answers;
  assert answers written into `plan.md`, `state.approval` set.
- **route_after_approval** — approved → select; rejected under cap → big_plan + replans++;
  rejected at cap → FAILURE.
- **select_next_change / route_change** — picks first PENDING; routes none → final_review.
- **implement_change stub** — marks current unit DONE + persists `changes.json`.
- **commit_push** — temp git repo: asserts a commit exists on the branch; no-remote path
  skips push cleanly.
- **merge** — monkeypatch `gh`/`git`: asserts the PR (or squash-merge) command is invoked.
- **End-to-end (graph)** — with mocked agent + `interrupt`, invoke the compiled graph for
  a coding ticket and assert it reaches `merge`/`END` with all changes `DONE` (stub).

`make check` stays green throughout.

---

## 10. Done-when

1. A coding ticket flows `pick_up_ticket → … → merge`, producing a committed high-level
   `plan.md` + `changes.json` on the ticket branch (research tickets still take the old
   stub path).
2. `big_plan` writes a valid high-level plan, populates `state.units`, and surfaces
   blocking questions to the gate in both forms (`plan.md` section + `state.questions`).
3. `approve_plan` gates once: approval proceeds; rejection drives bounded re-planning;
   the cap surfaces `Status.FAILURE`.
4. `select_next_change` iterates changes in order; the stub marks each `DONE`; an empty
   queue routes to `final_review`.
5. `commit_push`/`merge` perform their git side effects (PR by default).
6. `make check` green; the new unit + graph tests pass.

---

## 11. Open questions (confirm before building)

1. **Merge vs PR** (decision #1) — PR by default, or go straight to autonomous
   squash-merge to `main`?
2. **Push target** — do the real target repos (`backtester_v1`, `research`) have remotes
   we should push to, or stay local until merge?
3. **Plan approval UX** — is the `interrupt` payload shape in §3.4 what the Linear-style
   UI ([docs/UI.md](docs/UI.md)) expects, or should `changes` carry more (e.g. per-change
   `soft_loc`/flags) for the board?
4. **`MAX_REPLANS`** — reuse `MAX_RETRIES = 3`, or a separate, smaller plan-rejection cap?

---

## 12. Task sequence

1. **1.0 Helpers** — `run_agent`, `parse_json_block`, ledger `write_plan`/`write_changes`/
   `load_changes`; `AgentState.approval`.
2. **1.1 big_plan** — node + `big_plan.j2` + validation + tests.
3. **1.2 approve_plan** — node + `route_after_approval` + tests.
4. **1.3 select_next_change** — node + `route_change` + `implement_change` stub + tests.
5. **1.4 commit_push + merge** — real git side effects + tests.
6. **1.5 Rewire graph** — wire `repo_bootstrap_check`, `route_by_type`, new edges,
   `final_review` stub, `END`; end-to-end graph test.
