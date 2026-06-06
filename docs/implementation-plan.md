# Implementation Plan: Agentic Coding Workflow

Status: proposed
Last updated: 2026-06-06

## Why I'm building this

This repo automates two kinds of autonomous agent work, both run inside **strong,
deterministic harnesses** so they can finish real tasks unattended — not just
produce a first draft and stall.

1. **Agentic coding.** Drive a coding task end to end, including complex ones,
   without losing the thread. The pattern is: create an overall plan first, then
   break it into smaller steps. When a step needs more information, do the
   research first; implement the step; and update the overall plan if what was
   learned changes it. Repeat — research-as-needed → implement → reconcile the
   plan — until the whole plan is done. The harness owns the control flow and the
   gates so the agent can keep going through long, multi-step work.

2. **Research agents.** Run agents that investigate a given question or topic, in
   the same kind of controlled, well-harnessed environment. The goal is
   repeatable, bounded research runs rather than open-ended chat.

The common thread is the guiding principle below: **the harness is hardcoded and
deterministic; the cognition is delegated.** Strong harnesses are what let these
agents take on harder, longer tasks and actually finish them. The rest of this
document specifies the coding workflow; the research-agent track will follow the
same harness philosophy.

## Goal

Turn the current linear LangGraph pipeline into a **two-level loop** that runs a
coding ticket end to end:

1. Create a big overall plan.
2. Break it into small atomic changes.
3. Define specs & tests as the Definition of Done (DoD) per change.
4. Execute each change — done when the DoD is met and tests are green.
5. Review for bugs (separate agent).
6. Solve bugs.
7. Update the overall plan and upcoming atomic plans when needed.

## Guiding principle

> **Hardcode the invariants, gates, and side effects. Delegate the cognition.**

The deterministic LangGraph shell owns control flow, human gates, side effects
(branch/commit/merge), and resumability. Open-ended thinking (planning,
spec-writing, coding, reviewing) is delegated to autonomous agent cores. Every
"done" decision is a **hard predicate**, never a step count.

## Design decisions (resolved)

| Decision | Choice | Consequence |
|---|---|---|
| Agent runtime | **Hybrid** | Persistent Claude Agent SDK session *inside* the per-change node; one-shot `claude -p` for outer stages (`big_plan`, `final_review`). |
| Human-in-the-loop | **Approve the big plan only** | High autonomy elsewhere. The verifier + hard gates carry the weight. `replan` runs unattended, so it must be tightly bounded. |
| Atomic-change execution | **Sequential** | Changes share a working tree and usually depend on each other. Sequential + per-change checkpoint = clean resume. |

### Key structural insight

A persistent session per change means the inner loop is **plain Python inside one
LangGraph node**, not LangGraph sub-nodes. Agent SDK sessions are not
checkpoint-serializable, so they must not span nodes. The warm session is a local
variable inside the `implement_change` node, driving spec→execute→fix itself with
the hard gates as ordinary code between turns. LangGraph stays at the outer level:
orchestration, the plan-approval interrupt, and per-change checkpointing.

## Architecture

### Outer graph — orchestrator (plan-approval is the only gate)

```
START
 → pick_up_ticket
 → open_branch
 → big_plan            claude -p one-shot → plan.md + changes.json
 → approve_plan        interrupt()  ← the single HITL gate
 → select_next_change  deterministic
        │ pending  → implement_change → (loop) select_next_change
        │ none     → final_review
 → final_review        claude -p one-shot, branch diff vs problem statement
        │ clean → commit_push → merge
        │ bugs  → replan ──→ select_next_change   (bounded, unattended)
```

### Inner change — one node, one warm session

```
implement_change(change):
  open persistent ClaudeSession(cwd=repo)
    1. spec + failing tests
       ⟨hard gate: tests exist AND currently RED⟩
    2. loop up to MAX_RETRIES:
         execute
         ⟨hard gate: tests GREEN + lint + typecheck⟩  (fail → fix, continue)
         verify (SEPARATE one-shot agent, diff vs DoD)
           ok   → mark change DONE, return
           bugs → fix, continue
    3. retries exhausted → Status.FAILURE (surface)
```

## Done predicates (explicit, no step counting)

```
change done = tests_green ∧ lint_clean ∧ typecheck_clean ∧ verifier == "satisfies_dod"
ticket done = (∀ changes: status == DONE) ∧ final_review == "no_bugs vs problem_statement"
```

`AgentState.step` remains telemetry only.

## Data model — `src/classes.py`

```python
class ChangeStatus(Enum):
    PENDING = "pending"
    DONE = "done"

class Change(BaseModel):
    id: str
    title: str
    dod: str                      # human-readable spec / Definition of Done
    test_files: list[str] = []
    status: ChangeStatus = ChangeStatus.PENDING

class AgentState(BaseModel):
    status: Status
    step: int                     # telemetry only
    ticket_id: Optional[str] = None
    ticket: Optional[Ticket] = None
    repo_path: Optional[Path] = None
    plan_path: Optional[Path] = None        # plan.md, committed on the branch
    changes: list[Change] = []
    current_change_id: Optional[str] = None
    attempts: int = 0
    replans: int = 0                         # bounds unattended replanning
```

The plan is a **document, not state**: `plan.md` (big plan) + `changes.json`
(ordered atomic changes) live on the branch. "Update the plan" is a file edit +
diff — versioned and auditable. `AgentState` only holds pointers.

## Hard gates (deterministic, run before any agent)

```python
def run_tests(repo, files) -> bool:        # exit 0 == green
def lint_clean(repo) -> bool:
def typecheck_clean(repo) -> bool:

def tests_are_red(repo, files) -> bool:    # cheap, high-value:
    return not run_tests(repo, files)      # new tests MUST fail before execute
```

Rationale for the RED check: a test that was never red proves nothing. Asserting
new tests fail first catches the common failure where the agent writes trivially
passing tests. Hard checks run before the verifier so we never spend tokens
judging code that doesn't compile.

## `replan` runs unattended — bound it three ways

Because replans are not gated by a human, `replan` is the only autonomous
control-flow actor with no human check. Constrain it:

1. **Append/reorder PENDING changes only** — it cannot touch `DONE` changes.
2. **`state.replans` cap** (e.g. ≤ 2) — exceeding it → `Status.FAILURE`, surface
   rather than thrash.
3. **`log()` every plan mutation** — silent scope drift is the failure mode with
   no human gate; make every `changes.json` edit a visible diff.

## File-by-file changes

| File | Change |
|---|---|
| `src/classes.py` | Add `ChangeStatus`, `Change`; extend `AgentState` (above). |
| `src/nodes.py` | Replace linear edges with the outer graph; register new nodes. |
| `src/nodes/general/nodes.py` | Add `big_plan`, `approve_plan`, `select_next_change`, `final_review`, `replan`. Keep `pick_up_ticket`, `open_branch`. Retire stub `review`. |
| `src/nodes/coding/nodes.py` | Replace stubs with `implement_change` (persistent session) + `verify_change` (separate one-shot). |
| `src/nodes/helpers.py` | Add `ClaudeSession` wrapper (Agent SDK), hard-gate helpers, `has_pending_change`, `review_clean`. |
| `src/nodes/serde` / checkpointer | Register `Change`, `ChangeStatus` in the SqliteSaver `allowed_msgpack_modules`. |
| `src/prompts/` | New/updated templates: `spec_and_tests.j2`, `execute.j2`, `verify.j2`, `final_review.j2`, `big_plan.j2`. |

## Prompts (DoD = spec + tests)

- `big_plan.j2` — problem statement → `plan.md` + ordered `changes.json`; each
  change carries a `dod` and intended `test_files`.
- `spec_and_tests.j2` — given a change's DoD, write the spec and **failing** tests.
- `execute.j2` — implement until the DoD is met and tests are green.
- `verify.j2` — given the diff + the DoD, return structured `{ok, bugs[]}`.
  Fresh agent, never the one that wrote the code.
- `final_review.j2` — branch diff vs the problem statement → `{clean, bugs[]}`.

## Runtime split (hybrid)

- **Persistent session** (Agent SDK): `implement_change` — `spec_and_tests →
  execute → fix` share warm context within one change; reset between changes.
  Good for tokens and focus; each atomic change is a small bounded unit.
- **One-shot `claude -p`**: `big_plan`, `verify_change`, `final_review`.

This also fixes the current cold-context-per-node cost: today each `claude -p`
re-explores the repo from zero ([nodes/general/nodes.py](../src/nodes/general/nodes.py),
[nodes/coding/nodes.py](../src/nodes/coding/nodes.py)).

## Phased roadmap

1. **Data + gates** — `Change`/`ChangeStatus`/`AgentState`; hard-gate helpers;
   checkpointer serde registration. No behavior change yet.
2. **Big plan + approval** — `big_plan` writes `plan.md`/`changes.json`;
   `approve_plan` interrupt; `select_next_change` routing.
3. **Inner loop** — `ClaudeSession` wrapper; `implement_change`; `verify_change`;
   RED-test gate; capped fix loop wired to `MAX_RETRIES`.
4. **Review + replan** — `final_review`; bounded `replan`; `commit_push`/`merge`.
5. **Polish** — `log()` on plan mutations; surface `Status.FAILURE` paths;
   telemetry on `step`/`attempts`/`replans`.

## Open questions

- `ClaudeSession` shape over the Agent SDK (lifecycle, context compaction policy,
  how `send()` returns tool/turn results).
- Where `plan.md` / `changes.json` live on the branch (e.g. `.coder/`), and
  whether they are committed per change or only at merge.
- Whether `verify` and `final_review` should share a rubric definition.
