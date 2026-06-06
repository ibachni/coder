# Coding Workflow — High-Level Implementation Plan

Status: proposed
Last updated: 2026-06-06

**Scope:** the build roadmap for the *agentic coding* workflow only. This is the
"how we ship it" view. The "why / tradeoffs" live in
[docs/implementation-plan-v2.md](docs/implementation-plan-v2.md) §3 (Workflow A); the
evidence schema + anti-bloat rules live in
[docs/evidence-ledger.md](docs/evidence-ledger.md). This doc stays high-level and points
there for detail rather than repeating it.

---

## 1. What we're building

A **two-level loop** that drives one coding ticket end to end inside a deterministic
LangGraph harness:

- **Outer loop** (orchestrator): plan → approve → run each change → review → merge.
- **Inner loop** (one change): research-if-needed → write tests → implement → review →
  reconcile the plan.

The harness owns control flow, the single human gate, side effects (branch/commit/
merge), and resumability. The cognition (planning, coding, reviewing) is delegated to
agent sessions. Every "done" is a **hard predicate**, never a step count.

---

## 2. Target architecture at a glance

**Outer graph** (plan approval is the only human gate):

```
pick_up_ticket → open_branch → repo_bootstrap_check → big_plan → approve_plan
  → select_next_change ─(pending)→ implement_change ─┐
        ▲                                            │
        └────────────────────────────────────────────┘
  select_next_change ─(none)→ final_review ─(clean)→ commit_push → merge
                                          └─(bugs)→ replan → select_next_change
```

**Inner change** — three sessions + two deterministic steps (detail: v2 §3.3):

```
0 inner_plan_and_research  [cold, if flagged]   → inner_plan.md
1 spec_and_tests           [cold]               → spec + RED tests   (hard gate: RED)
2 implement                [warm]  delta-driven  → green gates        (hard gates)
3 review_change            [cold]               → {ok|bugs} + seals diff_summary.md
4 reconcile_outer_plan     [bounded]            → updates changes.json if plan was wrong
5 mark DONE                                     → commit + record.json
```

---

## 3. Current state → target

What exists today (mostly linear + stubs) and where it goes:

| Today ([src/](src/)) | Target |
|---|---|
| Linear edges in [nodes/__init__.py](src/nodes/__init__.py) | Outer graph above; branch at `select_next_change` |
| `spec` / `write_tests` / `write_code` stubs ([nodes/coding/nodes.py](src/nodes/coding/nodes.py)) | `big_plan` + `implement_change` (orchestrates the 3 sessions) |
| `review` / `commit_push` / `merge` stubs ([nodes/general/nodes.py](src/nodes/general/nodes.py)) | `final_review`, real `commit_push`/`merge` (squash ledger at merge) |
| `surface_questions` + `get_user_answer` (interrupt) | Reused by `approve_plan` (questions live in `plan.md` + `state.questions`) |
| `AgentState` flat fields ([classes.py](src/classes.py)) | + `WorkUnit`/`ChangeStatus`, `plan_path`, `has_open_questions`, `replans`, `autonomy`; `TicketContent.goals` |
| One-shot `claude -p` per node | Hybrid: warm `ClaudeSession` for `implement`; cold one-shots elsewhere |

---

## 4. Build phases

Each phase is independently shippable and leaves the graph runnable. Coding-specific
expansion of v2 §8.

### Phase 0 — Foundations: data, gates, ledger (no routing change) ✅ DONE
- **Model:** `ChangeStatus`, `WorkUnit` (with `soft_loc`, `needs_research`,
  `needs_planning`); extend `AgentState`; add `TicketContent.goals`. → [src/classes.py](src/classes.py).
- **Serializer:** allow-list factored into [src/serde_config.py](src/serde_config.py)
  (adds `ChangeStatus`/`WorkUnit`/`TicketContent`/`AgentState`); wired into
  [src/nodes/__init__.py](src/nodes/__init__.py) with an env-overridable, dir-creating
  `CODER_STATE_DB` so the package imports cleanly in tests/CI.
- **Hard gates:** `run_tests`, `tests_are_red`, `lint_clean`, `typecheck_clean`,
  `loc_changed` → [src/gates.py](src/gates.py) (dedicated module, not `helpers.py`).
- **`repo_bootstrap_check`** node in [src/nodes/general/nodes.py](src/nodes/general/nodes.py)
  (defined, **not yet wired**) + detection brain in [src/bootstrap.py](src/bootstrap.py);
  writes `bootstrap.json`; hard-fails to `Status.FAILURE` if undetectable.
- **Ledger I/O:** paths + write/read + bounded `read_ledger_slice` pull valve →
  [src/ledger.py](src/ledger.py) (per evidence-ledger.md).
- **Done-when (all verified by `make check`, 70 new tests):** new types round-trip
  through the configured serializer; gates return correct pass/fail; bootstrap detects
  commands (and fails closed); ledger write→read round-trips; graph still compiles.

### Phase 1 — Outer loop: plan + approval
- **`big_plan`:** high-level `plan.md` + ordered `changes.json`; chunk by `soft_loc`;
  set `needs_*` flags; surface blocking questions into `plan.md`'s `## Open questions`
  section **and** `state.questions` (structured, with pro/con/recommended options);
  set `has_open_questions`.
- **`approve_plan`:** the single HITL gate — `interrupt({"questions": state.questions})`;
  write answers into `plan.md`; bounded single re-plan if answers change scope; freeze.
- **`select_next_change`:** deterministic routing (pending → `implement_change`,
  none → `final_review`).
- **`commit_push` / `merge`:** real implementations; squash `.coder/runs/` at merge.
- **Done-when:** a ticket yields an approved, high-level plan; the gate renders
  questions to the UI and folds answers back; changes are selected in order.

### Phase 2 — Inner change: the three sessions
- **`ClaudeSession`** wrapper (warm, Agent SDK) + a cold one-shot helper, in
  [nodes/helpers.py](src/nodes/helpers.py).
- **`implement_change`** orchestrates steps 0–5 (§2): `inner_plan_and_research`
  (flagged only) → cold `spec_and_tests` with the RED gate → warm `implement` loop →
  cold `review_change` (which also writes the sealed `diff_summary.md`) →
  `reconcile_outer_plan` → `mark DONE`.
- **`remaining_delta`** as the loop variable; stop on empty delta / `MAX_RETRIES` /
  stagnation / needs-human — never on step count.
- **Done-when:** a single change runs RED → GREEN → reviewed → sealed with evidence on
  disk; the delta shrinks each attempt and the stop predicates fire correctly.

### Phase 3 — Review + bounded replan
- **`final_review`:** branch diff vs the problem statement + goals → `{clean|bugs}`.
- **`replan`:** bounded (append/reorder PENDING only, shared `replans` cap,
  cite-cause, `log()` every mutation). Inner reconciliation (Phase 2 step 4) and this
  outer replan share the same bounds.
- **Failure surfacing:** stagnation / cap breaches → `Status.FAILURE` to the human.
- **Done-when:** an end-to-end ticket completes and merges; review bugs route through
  bounded replan; caps surface instead of thrashing.

### Phase 4 — Hardening (later)
- `test_quality_review` rubric; visible-vs-held-out test-gap metric (reward-hacking
  detector); rolling `diff_summary` digest past ~30 changes; sandbox graduation before
  unattended merge; AHE trace corpus from the ledger.

---

## 5. Invariants to preserve (don't regress these)

1. **Hard gates run before any judging agent** — never spend tokens reviewing code
   that doesn't compile or has no failing-first tests.
2. **New tests must be RED before implementation** — a test that was never red proves
   nothing.
3. **Single-writer on code** — one warm session mutates the tree; tests and review are
   independent cold sessions.
4. **`plan.md` is canonical** — `state.questions` is a transient UI projection, not a
   competing source of truth.
5. **Stop on the delta, not the clock** — `AgentState.step` is telemetry only.
6. **Every plan mutation cites its cause** — the guard against scope drift, since
   replan is unattended.
7. **The ledger is two-tier** — disk = full; context = distilled. Sealed changes reach
   later stages only as their ≤1.5k `diff_summary.md`.

---

## 6. Component → file checklist

| Component | File |
|---|---|
| Model, state, work-unit types | [src/classes.py](src/classes.py) |
| Checkpointer serializer allow-list | [src/serde_config.py](src/serde_config.py) |
| Graph wiring + DB path | [src/nodes/__init__.py](src/nodes/__init__.py) |
| Hard gates | [src/gates.py](src/gates.py) |
| Bootstrap command detection | [src/bootstrap.py](src/bootstrap.py) |
| Ledger I/O (`read_ledger_slice`, paths) | [src/ledger.py](src/ledger.py) |
| `big_plan`, `implement_change` (3 sessions) | [src/nodes/coding/nodes.py](src/nodes/coding/nodes.py) |
| `repo_bootstrap_check`, `select_next_change`, `final_review`, `replan`, `commit_push`, `merge` | [src/nodes/general/nodes.py](src/nodes/general/nodes.py) |
| `ClaudeSession`, cold-session helper (Phase 2) | [src/nodes/helpers.py](src/nodes/helpers.py) |
| Prompts: `big_plan`, `inner_plan`, `spec_and_tests`, `execute`, `review_change`, `final_review` | [src/prompts/](src/prompts/) |
| Verifier rubrics | `.coder/rubrics/` (new) |

---

## 7. Sequencing notes

- **Phase 0 is the unblocker** — gates and the ledger are dependencies of every later
  phase, so land them first even though they change no behavior.
- **Phases 1 and 2 can overlap** — the outer loop and a single `implement_change` are
  loosely coupled through `changes.json`; one person can build the outer graph while
  another builds the inner sessions, integrating at `select_next_change`.
- **Defer Phase 4** until a baseline end-to-end ticket runs green — the hardening work
  (held-out tests, digests, sandbox) only pays once there's a working loop to measure.
