# Implementation Plan v2 — Two Harnessed Workflows: Coding & Research

Status: proposed
Last updated: 2026-06-06
Supersedes: scope of [docs/implementation-plan.md](docs/implementation-plan.md) (v1, coding-only)
Source of best practices: [docs/nicolas_summary.md](docs/nicolas_summary.md)

---

## 0. TL;DR

This repo runs **autonomous agents inside a deterministic harness** so they can
finish real, multi-step work unattended instead of producing a first draft and
stalling. v2 specifies **two workflows** that share one harness spine but diverge
exactly where the work is different:

| | **Coding workflow** | **Research workflow** |
|---|---|---|
| Unit of work | one atomic code change | one bounded sub-question |
| Dominant operation | **write** (mutate a tree) | **read** (gather + distill) |
| Concurrency model | **single-writer, sequential** | **parallel sub-agent fan-out** |
| Ground truth | **executable** (tests/lint/types) | **evidential** (citations, primary sources) |
| Verifier | runs the diff against the DoD | adversarially refutes each claim |
| Output target | the code repo (`Repo.CODER`) | the knowledge repo (`Repo.RESEARCH`) |
| "Done" signal | gates green ∧ verifier passes | coverage met ∧ claims grounded ∧ critic clean |

The guiding principle is the same for both: **hardcode the invariants, gates, and
side effects; delegate the cognition.** Everywhere a non-obvious design choice
exists, this document states the alternatives, the choice, *why*, and the risk it
buys — because the wrong default in a harness is what turns "autonomous" into
"silently wrong."

---

## 1. Guiding principle

> **Hardcode the invariants, gates, and side effects. Delegate the cognition.**

The deterministic shell (today: a LangGraph `StateGraph` with a `SqliteSaver`
checkpointer, see [src/nodes/__init__.py](src/nodes/__init__.py)) owns:

- **control flow** — what runs next is code, never a model's choice;
- **human gates** — the one place a person must approve;
- **side effects** — branch / commit / merge, made idempotent and crash-safe;
- **resumability** — every "done" decision is a **hard predicate**, never a step count.

Open-ended thinking — planning, spec-writing, coding, searching, synthesizing — is
delegated to agent cores (`claude -p` one-shots and persistent Agent SDK sessions).

**Why this split, and not "one big agent that decides everything":** the summary's
four named failure modes — **context rot, scope drift, loop thrashing, reward
hacking** — are all failures of an agent left to govern its *own* control flow over
a long horizon. Moving control flow, gating, and the stop condition into
deterministic code is what removes the surface those failures need. The agent stays
smart; the harness stays in charge.

---

## 2. The shared harness spine (cross-cutting)

Both workflows are built from the same eight primitives. Each is stated with its
**tradeoff** because each one is a place where a reasonable engineer would pick
differently and pay for it later.

### 2.1 Bounded atomic units of work

Work is decomposed into the smallest units that can be independently specified,
executed, and verified: a **change** (coding) or a **sub-question** (research).

- **Alternatives:** (a) one agent does the whole ticket in one context; (b) fixed
  N-step decomposition regardless of ticket size.
- **Why bounded units:** the summary's headline danger is *"do too much at once →
  run out of context → prematurely declare work done."* Small units keep each
  context window relevant, make `git`/file checkpoints meaningful, and let a failed
  unit be retried without re-running the whole ticket.
- **Risk it buys:** decomposition becomes the new single point of failure (plan
  quality caps everything downstream). **Mitigation:** the plan/brief is a gated,
  versioned artifact (§2.8) and `replan` can only refine *pending* units (§3.6).

### 2.2 Explicit state artifacts (plan-as-document)

The plan is a **document on the branch**, not in-memory state. Coding writes
`plan.md` + `changes.json`; research writes `brief.md` + `questions.json`. The
`AgentState` holds only *pointers* to these files.

These are **ticket-tier** artifacts: they live at `.coder/runs/<ticket>/` *above*
the per-change ledgers (§2.3). So the answer to "where is the big plan written?" is:
the big plan is ticket-scoped; each atomic change gets its own ledger subdirectory
beneath it. (Goals are not here — they live on the ticket and in `CLAUDE.md`, §2.10.)

- **Why:** "update the plan" becomes a file edit + a reviewable `git` diff —
  versioned, auditable, and immune to checkpoint-serialization limits. It also
  survives compaction and fresh-context handoffs (§2.6).
- **Tradeoff:** a second source of truth (files vs. state) must be kept in sync.
  **Mitigation:** the harness is the *only* writer of these files at node
  boundaries; agents propose, the node commits.

### 2.3 Evidence ledger per unit

Each ticket carries **ticket-tier** artifacts (`plan.md`, `changes.json`,
`bootstrap.json`, `final_review.json`); each unit writes its own **change-tier**
ledger beneath it. Goals are *not* a ledger file — they live on the ticket and in the
repo's `CLAUDE.md` (§2.10); the big plan's open questions are *not* a file either —
they're a section inside `plan.md` (§3.2):

```
.coder/runs/<ticket>/
  plan.md  changes.json  bootstrap.json  final_review.json             # ticket-tier
  └─ <unit_id>/                                                        # change-tier
       spec.md  dod.json  inner_plan.md  test_red.log  test_green.log
       lint.log  typecheck.log  attempts.jsonl  remaining_delta.json
       review.json  reconcile_note.json  diff_summary.md  record.json
```

- **Why a ledger and not just logs:** it is the substrate for three other things —
  resumability, the harness-improvement loop (§9 here), and **reward-hacking
  detection** (track the gap between the visible suite and the held-out suite; if it
  widens, the implementer is gaming the gate).
- **The bloat risk is real and is designed against, not hoped against.** A ledger
  that grows with every attempt and every change is the fastest way to rot a context.
  The defense is a **strict two-tier model**: the durable ledger lives on disk at
  full fidelity and is *never* loaded wholesale; each stage receives a small, capped,
  **distilled** payload (pointers + summaries, failing-log slices only, sealed
  changes represented by a ≤1.5k `diff_summary.md`). This keeps total context **flat
  across N changes** instead of O(N). The full schema, per-item size caps, and the
  stage-by-stage "what does the coder read" matrix live in their own doc:
  **[docs/evidence-ledger.md](docs/evidence-ledger.md)**.
- **Tradeoff:** disk + write overhead per unit, and a schema to maintain.
  **Mitigation:** the ledger doubles as the resume journal, so it is not pure
  overhead; the schema is small and versioned alongside the rubrics.

### 2.4 `remaining_delta` is the loop's primary variable

The inner loop does **not** count steps. It tracks what is *still wrong*:

```json
{
  "remaining_delta": [
    {"kind": "test_failure",  "source": "pytest",        "evidence": "..."},
    {"kind": "type_error",    "source": "pyright",       "evidence": "..."},
    {"kind": "verifier_bug",  "source": "verify",        "evidence": "..."},
    {"kind": "unverified_claim", "source": "fact_check", "evidence": "..."},
    {"kind": "coverage_gap",  "source": "critic",        "evidence": "..."}
  ],
  "previous_delta_hash": "…",
  "stagnation_count": 0
}
```

A unit **stops** when one of these is true:
1. `remaining_delta == []` (gates pass / claims grounded) → **success**;
2. `attempts >= MAX_RETRIES` → **surface to human**;
3. delta stops shrinking (`stagnation_count` exceeds a cap, detected via
   `previous_delta_hash`) → **surface**, do not thrash;
4. the unit needs a human decision → **surface**.

- **Why delta-driven instead of step-driven:** step counts terminate either too
  early (work unfinished) or too late (loop thrashing). A monotonically shrinking
  delta is the only honest progress signal, and stagnation is the honest
  "I'm stuck" signal — both are the antidotes to **loop thrashing**.
- **Tradeoff:** "did the delta shrink?" must be computable, which forces structured
  verifier/gate output. **Mitigation:** that structure is required anyway (§2.5).

### 2.5 Hard gates and a separate verifier

Two layers of checking, always in this order:

1. **Hard gates — deterministic, cheap, run *before* any judging agent.** Coding:
   tests green, lint clean, typecheck clean — and, critically, **new tests RED
   before implementation** (a test that was never red proves nothing). Research:
   every claim in the summary carries at least one resolvable citation; no orphan
   claims.
2. **Independent verifier — a *separate* agent that never wrote the artifact.**
   Coding: judges the diff against the DoD. Research: *adversarially tries to
   refute* each claim from its cited sources.

- **Why separate verifier (best-of-n insight):** "best-of-n + a separate
  verifier/selector beats a single rollout." The agent that wrote the code/claim is
  the worst judge of it. Independence is the whole point.
- **Why gates before verifier:** never spend judge tokens on something that doesn't
  compile or has no citations.
- **The reward-hacking caveat, stated plainly:** *test-based gates are demonstrably
  gameable* (weak tests, memorized inputs). Mitigations baked in: (a) **held-out
  checks** the implementer never sees; (b) a **non-test review dimension** (spec/
  style conformance) via versioned rubrics; (c) the **visible-vs-held-out gap
  metric** in the ledger. Research's analog of "gaming the test" is *citing a source
  that doesn't actually support the claim* — caught by the refutation verifier.

Rubrics are **shared, versioned files**, not prompt-inlined:

```
.coder/rubrics/
  change_verifier.md      final_review.md       security_review.md
  test_quality_review.md  ui_e2e_review.md
  claim_verifier.md       coverage_critic.md     source_quality_review.md
```

### 2.6 Context management

The most-cited long-horizon failure is **context rot**. Three defenses:

- **Fresh context per unit** — a new context for each change/sub-question; warm
  context *within* a unit only.
- **Sub-agents for read-heavy work** — fan out exploration, return only conclusions.
- **Distilled note-taking** — each sub-agent returns a **1–2k-token** summary, never
  its raw transcript; compaction kicks in past a threshold.

- **Tradeoff:** distillation can drop the one detail synthesis later needs.
  **Mitigation:** the raw evidence (sources.jsonl, full diffs) stays in the ledger,
  so the synthesizer can re-open ground truth rather than trust a lossy summary —
  this is how we avoid the **two-hop information loss** of a naive architect/editor
  split.

### 2.7 Single-writer semantics

> Do not let multiple agents mutate the same working tree unless they are isolated
> by worktree/branch.

- **Why:** the summary's clearest verdict — *reads parallelize well; writes create a
  coordination problem* (write-write conflicts, stranded work, "nobody owns the hard
  problem"). The coding write-path is therefore **single-threaded**. Research *reads*
  fan out, but research **synthesis** (a write) collapses back to a single agent.
- **Tradeoff:** sequential writes are slower than parallel. **Mitigation:** that's
  the right trade — a fast wrong merge is worse than a slow correct one; where true
  write-parallelism is needed later, isolate by `git worktree`.

### 2.8 Human-in-the-loop: one gate, plus an autonomy knob

The **only** default human gate is **plan/brief approval**. Everything downstream —
execute, verify, replan — runs unattended, carried by the gates and the verifier.

- **Why one gate:** plan quality caps everything downstream and ambiguous
  requirements are the dangerous input; gating exactly there gives the highest
  leverage per interruption. More gates would erode autonomy without proportional
  safety.
- **The `autonomy` knob (from the runbooks):** a per-ticket setting governs how many
  surfaced questions the agent resolves itself vs. escalates. Low autonomy → asks
  more (good for ambiguous/high-stakes); high autonomy → decides more (good for
  well-scoped work). **Tradeoff:** higher autonomy trades fewer interruptions for
  more risk of guessing wrong — so it is a per-ticket dial, not a global default.
- **Bounded unattended replan:** because `replan` has *no* human gate, it is the
  most dangerous control-flow actor. It is constrained three ways (see §3.6).

### 2.9 Sandbox and session separation

> Never let generated code (or fetched web content) run in the same environment as
> your credentials.

- **Coding:** the warm execution session runs in the repo, but the *merge* path must
  be gated and ideally the execution sandboxed (Firecracker microVM / E2B for
  ephemeral, Daytona+Kata for persistent workspaces). **Tradeoff:** strong isolation
  costs setup latency and infra; for a single-user local tool we start with
  process-level separation and a dirty-tree refusal (already in `open_branch`,
  [src/nodes/general/nodes.py:39](src/nodes/general/nodes.py#L39)) and graduate to a
  microVM before any unattended merge.
- **Research:** the investigation agents touch the open web. Treat fetched content as
  untrusted input — it can carry prompt-injection. **Mitigation:** web-only tools,
  no credentials in that context, and the fact-check verifier never *acts* on
  fetched instructions, only evaluates claims.

### 2.10 Goal hierarchy (ambiguity resolution) — two levels, no `goals.md`

Agents need a goal hierarchy to resolve ambiguity, but it is **not** a per-run file.
Goals split by *lifetime* into two homes, each its natural owner:

- **Repo-level constraints** (durable, high-level — "keep token cost bounded",
  "reliability over features", "this is a knowledge repo") → the repo's **`CLAUDE.md`**.
  These rarely change per ticket, so a per-run copy would just be a stale duplicate.
  Bonus: `claude -p` already auto-loads `CLAUDE.md`, so these constraints reach every
  agent with **zero plumbing** (the repo's `CLAUDE.md` is currently empty —
  [CLAUDE.md](CLAUDE.md) — and is the natural place to seed them).
- **Ticket-level goals + priority** (what *this* ticket is for, and its non-goals) →
  a **field on the ticket** (`TicketContent`, §7.1), authored when the ticket is
  written. No separate artifact, no template-seeding step.

This is the resolved form of the summary's idea (*"create a goal hierarchy for agents
to understand the context"*), and it is a deliberate **simplification** of the first
v2 draft, which over-engineered it into a `goals.md` per ticket.

- **What it's for:** ambiguity is the normal state of a ticket. When an agent hits an
  ambiguous choice — in `big_plan`, inner planning, or review — it has three options:
  guess, ask the human, or **decide from the goals**. The hierarchy makes the third
  safe: a choice that serves the higher-priority goal and violates no non-goal (repo
  *or* ticket) is a *defensible, logged* decision, not a guess.
- **How it composes with the autonomy knob (§2.8):** the two are a pair — goals supply
  the *criterion* for resolving ambiguity; `autonomy` sets the *threshold* for acting
  on it. High autonomy + clear goals → resolve most ambiguity, surface little. Low
  autonomy, or two high-priority goals in **conflict** → surface to the human.
  "Goals conflict" is a clean, checkable escalation trigger.
- **Where it's read:** `big_plan`, `inner_plan_and_research`, `review_change`, and
  `final_review` — via `state.ticket` (ticket goals) plus the auto-loaded `CLAUDE.md`
  (repo constraints).
- **Verdict on the idea:** worth doing, and now genuinely cheap — it converts "the
  agent guessed" (unauditable) into "the agent chose goal #1 over goal #3" (logged,
  reviewable), with **no new files**.
- **Tradeoff:** vague goals ("build good software") give no signal and the agent
  falls back to asking. **Mitigation:** keep both levels short and concrete; a goal
  set that can't resolve a real ambiguity is a bug in the ticket/repo config, not the
  agent.

---

## 3. Workflow A — Agentic Coding

This is the refinement of v1 ([docs/implementation-plan.md](docs/implementation-plan.md)),
absorbing the summary's ledger, delta-loop, test-splitting, and rubric additions.

### 3.1 Scaffold choice

We use **high-level plan → per change (inner-plan/research → tests → execute →
review) → final review**, i.e. a *two-level loop*: an outer **plan-execute**
orchestrator wrapping an inner **generate-test-repair** loop, with the test-author
and the reviewer split out as independent cold sessions (§3.3).

| Scaffold | Why not as the whole thing |
|---|---|
| Autonomous single-agent ReAct | Prone to context rot and self-reinforcing errors on long runs. *Used* — but only **inside** one bounded change, never across the ticket. |
| Structured pipeline, no LLM control flow (Agentless) | Brittle on multi-step/cross-file work; early localization errors are fatal. Good lesson (determinism) but too rigid for incremental work. |
| Plan-then-execute | Plan quality caps everything; bad at mid-task re-scoping. We keep it but add **bounded replan** to handle discovered work. |
| Multi-agent hierarchy (planner/worker/judge) | Coordination is the dominant failure mode for the **write** path. We keep planner/verifier *roles* but **single-writer** on code. |
| Architect/editor split | Two-hop information loss. Avoided by giving the verifier the real diff, not a summary. |

**Net:** the scaffold is chosen per-altitude — plan-execute outside, ReAct +
generate-test-repair inside — because no single scaffold survives a long, multi-file
ticket.

### 3.2 Outer graph (orchestrator) — plan approval is the only gate

```
START
 → pick_up_ticket          repo = resolve_repo(ticket.repo) = CODER
                            goals come from the ticket field + repo CLAUDE.md (§2.10)
 → open_branch
 → repo_bootstrap_check    HARD GATE: test/lint/typecheck cmds known (fail if not)
                            (install state is recorded as a hint, not gated on — a
                             missing dep surfaces for real when a gate first runs)
 → big_plan                claude -p → plan.md (HIGH-LEVEL) + changes.json
                            • chunks work into changes with a soft LoC target (§3.7)
                            • flags per change: needs_research / needs_planning (§3.3)
                            • surfaces questions it cannot resolve from ticket+repo+goals
                              → structured into state.questions (UI payload, w/ options)
                              → AND rendered into plan.md's "## Open questions" section
                            • sets has_open_questions (state, for routing)
 → approve_plan            interrupt({"questions": state.questions})  ← single HITL gate
                            UI renders the clean JSON (pro/con/recommended, chat);
                            human answers + approves; answers are written beneath each
                            question in plan.md, then the plan body is revised once to
                            weave them in (bounded: big_plan revises ONCE, then frozen)
 → select_next_change      deterministic
        │ pending → implement_change → (loop) select_next_change
        │ none    → final_review
 → final_review            claude -p, branch diff vs problem statement + goals
        │ clean → commit_push → merge/PR
        │ bugs  → replan ──→ select_next_change   (bounded, unattended)
```

**`big_plan` surfaces questions in two representations (request 1).** A ticket is
usually ambiguous, and the planner will hit choices it *cannot* resolve from the
ticket body, the repo, or the goals (§2.10). Rather than guess, it produces the
questions **once, as structured objects**, and uses them two ways — with a strict rule
about which is canonical, so we don't reintroduce a two-sources-of-truth sync problem:

- **Structured JSON — the transient UI view.** The questions go into `state.questions`
  (already in [src/classes.py:58](src/classes.py#L58)) and reach the UI via
  `interrupt({"questions": state.questions})` — exactly what `get_user_answer` already
  does ([src/nodes/general/nodes.py:137](src/nodes/general/nodes.py#L137)). The schema
  carries `options` with `pro` / `con` / `recommended` so the Linear-style board can
  render answer choices and a chat, per [docs/UI.md](docs/UI.md). This view is
  **ephemeral gate scaffolding** — checkpointed in state, but not a committed artifact.
- **`plan.md` `## Open questions` section — the durable record.** The same questions
  are rendered here; the human's answers are written *beneath each one*, then the plan
  body is revised once to weave them in. **`plan.md` is canonical**; the JSON is a
  projection of it for one consumer (the UI), not a competing file.

The harness keeps only a `has_open_questions` boolean for routing — it never parses
the markdown to decide whether to interrupt. This reuses the `surface_questions` /
`interrupt()` machinery already in
[src/nodes/general/nodes.py:88](src/nodes/general/nodes.py#L88); `surface_questions.j2`
just gains an `options` field on each question.

**The big plan stays high-level (request 2).** `big_plan` decides *what* the changes
are, *in what order*, *how big* (soft LoC, §3.7), and *which need a research or
planning dive first* (`needs_research` / `needs_planning`). It deliberately does
**not** pre-plan the internals of each change — that detail is produced just-in-time
inside the change (§3.3 step 0), so the plan neither rots nor over-commits to detail
the first change will invalidate. The ≤1.5k-token cap on `plan.md`
([docs/evidence-ledger.md](docs/evidence-ledger.md)) is the forcing function.

`repo_bootstrap_check` earns its place: the summary warns the implementer must *know
the test/lint/typecheck commands* before it can be gated on them. Discovering this
once, up front, is a hard gate; failing it surfaces to the human rather than letting
the inner loop flail.

### 3.3 Inner change — three sessions, not one

v1 (and the first draft of v2) ran spec→tests→execute in a **single warm session**.
v2 splits the inner change into **three separate sessions** plus two deterministic
steps. Two pressures force the split, and both come straight from the summary:

- **Test-writing must be separate from implementation (answer to Q4, part 1).** A
  single session that writes both tests *and* code will quietly shape the tests to
  the code it already intends to write — the textbook reward-hack. Making the test
  author a *different* session turns the tests into an independent contract the
  implementer must satisfy, not a rationalization of what it built.
- **The per-change check must be an independent reader (answer to Q4, part 2).** See
  the dedicated note below — there *is* a per-change review; it just has to be cold.

```
implement_change(change):

  0. inner_plan_and_research        [fresh session — only if flagged]
       if change.needs_research or change.needs_planning:
         research the unknowns (read-heavy; may fan out sub-agents)
         write inner_plan.md  (the LOW-level plan for THIS change)
       ⟨the big plan stays high-level; detail is produced HERE, just-in-time⟩

  1. spec_and_tests                 [fresh session — the test author]
       write spec.md (the DoD contract) + failing tests
       ⟨HARD GATE: new/changed tests exist⟩
       ⟨HARD GATE: new tests are RED for the expected reason⟩

  2. implement                      [warm session — the ONLY warm context]
       loop ≤ MAX_RETRIES, delta-driven:
         implement the smallest change toward green (respect soft LoC, §3.7)
         run targeted → affected existing tests → lint → typecheck
         append evidence; remaining_delta := failing gates
         delta empty → break
       ⟨implementer must not weaken tests; any test-file edit is flagged
        and handed to review in step 3⟩

  3. review_change                  [fresh session — independent reviewer]
       reads the FINAL diff + spec.md + dod.json + the test-file diff + rubric
       returns {ok, bugs[]}
         bugs → re-open step 2 with bugs added to remaining_delta
         ok   → also write diff_summary.md (≤1.5k) — the reviewer seals the change
       ⟨the brief per-change review checkpoint — separate, NOT warm. The reviewer
        authors the sealed summary: honest (not a self-report) and free (it already
        holds the diff)⟩

  4. reconcile_outer_plan           [deterministic + bounded agent edit]
       if inner work proved the big plan inaccurate:
         update changes.json (append/reorder PENDING only) + cite cause + log()
       ⟨inner replan happens HERE, as the last step, so the outer plan is
        consistent before the next change starts — request 4⟩

  5. mark DONE
       commit/checkpoint diff; write record.json; set change.status = DONE
       (diff_summary.md was written by the reviewer in step 3)
```

**What is warm and what is cold, and why.** The warm `ClaudeSession` is now scoped to
**step 2 only** — the implement loop, where sharing context across fix-attempts
genuinely pays. Steps 0, 1, and 3 are deliberately **fresh/cold**: the research dive
(step 0) carries a large read-context that would pollute the implement loop;
test-writing (step 1) and review (step 3) must be *independent* of the implementer,
and independence is the whole point. As in v1, Agent SDK sessions are not
checkpoint-serializable, so each session is a local variable inside the
`implement_change` node; LangGraph stays at the outer level. **Tradeoff:** the splits
cost tokens (each fresh session re-establishes context) — but they buy an independent
test contract and an independent review, which is exactly where the summary says the
spend is worth it.

**Just-in-time inner planning / research (request 2).** If `big_plan` flagged the
change `needs_research` or `needs_planning`, step 0 runs first: a dedicated session
resolves the unknowns and writes `inner_plan.md`. This is how the high-level big plan
and detailed execution coexist — the big plan says *what*; the inner plan, produced
only when needed and only for the current change, says *how*.

#### Why a per-change review, and why it is cold (answer to Q4, part 2)

There are **two** independent-reader checks, at two altitudes, and they are not the
same thing:

| | `review_change` (per change) | `final_review` (whole ticket) |
|---|---|---|
| Scope | this change's diff vs its DoD + spec | branch diff vs the **problem statement** + goals |
| Catches | local correctness, security, test quality | cross-change interactions, integration, scope-vs-ticket |
| When | once, after the cheap gates go green | once, after all changes are DONE |
| Session | **fresh / cold** | **fresh / cold** |

So the plan does **not** have "only a final review" — it has a brief, cold per-change
review *and* a whole-ticket review, because they catch different failure classes. The
key design choice (and the reason it's worth stating) is that `review_change` reads
the **final diff**, not the implementer's attempt history — it judges the *result*,
blind to how the implementer got there. That blindness is what makes it a genuine
second opinion rather than a continuation of the same reasoning. Within the loop, the
cheap gates (tests/lint/types) drive iteration; the expensive cold review runs *once*
when those gates are green, so it stays brief and doesn't burn tokens judging
non-compiling code.

### 3.4 Test splitting (the DoD, made precise)

Tests are split into three classes, all recorded in `dod.json`:

```
new_tests_red_before_implementation      # proves the test is real
new_tests_green_after_implementation     # proves the change works
affected_existing_tests_green            # proves no regression in the blast radius
lint_green
typecheck_green
full_suite_green                         # confidence pass before merge
```

- **Why split, not "run everything":** the full suite is slow and noisy; the
  *affected* set is the fast inner-loop signal; the full suite is the merge gate.
  Splitting keeps the loop fast without sacrificing final confidence.
- **`affected_existing_tests` is itself a judgment call:** the summary flags that
  *checks must represent the desired behavior* — so a **test-quality review** rubric
  (`test_quality_review.md`) guards against trivially-passing or over-fitted tests.

### 3.5 Done predicates (no step counting)

```
change done = tests_green ∧ lint_clean ∧ typecheck_clean ∧ review_change == "ok"
ticket done = (∀ changes: status == DONE) ∧ final_review == "no_bugs vs problem_statement"
```

`AgentState.step` remains telemetry only.

### 3.6 Replan — two entry points, same bounds

Replanning happens at **two** altitudes, and request 4 is specifically about the
inner one:

- **Inner reconciliation** — `reconcile_outer_plan`, the *last step inside*
  `implement_change` (§3.3 step 4). Triggered when executing a change reveals the big
  plan was inaccurate (a change is bigger than thought, a dependency was missed, an
  assumed file doesn't exist). The fix-up to *this* change happens in the warm loop;
  but because that discovery often invalidates *other* pending changes, the change
  reconciles the **outer** plan as its final act — editing `changes.json` so the next
  change starts from a correct plan. Doing it here, not in a separate node, is what
  keeps the big plan consistent without a mid-stream human gate.
- **Outer replan** — the `replan` node, after `final_review` finds whole-branch bugs.

Both obey the **same three bounds**, because an ungated plan mutation is the scope-
drift failure mode:

1. **Append/reorder PENDING changes only** — neither can touch `DONE` changes.
2. **`state.replans` cap (≤ 2)** shared across both entry points — exceeding it →
   `Status.FAILURE`, surface rather than thrash.
3. **Every mutation cites its cause** — each `changes.json` edit names the discovered
   fact (inner) or the `final_review` bug (outer) that justified it, recorded in
   `reconcile_note.json` and `log()`-ged as a visible diff.

### 3.7 Soft size limit per change (request 3)

`big_plan` chunks larger pieces of work into changes that each target a **soft LoC
goal** (`change.soft_loc`, e.g. ~150 LoC). It is a *budget the planner aims for*, not
a gate:

- **Why soft, not hard:** a *hard* LoC gate is itself gameable and perverse — it
  incentivizes artificial splits mid-logic, or padding/golfing to hit a number. The
  goal is bounded, reviewable changes; a strict line-count enforces the letter and
  misses the intent.
- **Where it bites:** the planner sizes changes against `soft_loc` up front; the
  implementer (§3.3 step 2) tracks `loc_changed` and, on material overflow, either
  flags it for `review_change` or triggers inner reconciliation (§3.6) to **split**
  the change into a new pending change. Overflow is a *signal that the unit was
  mis-scoped*, surfaced — never silently absorbed.
- **Tradeoff:** too small a budget fragments coherent changes and multiplies
  session/overhead cost; too large reintroduces the "do too much at once" failure.
  `soft_loc` is therefore a per-change planner estimate, not a global constant, so it
  can flex with the nature of the change (a mechanical rename vs. new logic).

---

## 4. Workflow B — Research

Research follows the *same spine* but inverts the concurrency model (reads
parallelize) and replaces executable ground truth with **evidential** ground truth.
Its output target is the knowledge repo (`Repo.RESEARCH`) — research findings are
committed and merged like code, so the whole `open_branch → … → commit_push → merge`
machinery is reused. The current research node is a stub
([src/nodes/research/nodes.py](src/nodes/research/nodes.py)); this section specifies
its replacement.

### 4.1 Scaffold choice

We use **brief → parallel investigate → adversarial verify → single-writer
synthesize → coverage critic**. This is a *multi-modal fan-out* (read) feeding a
*single-writer* (synthesis), with a *completeness critic* loop.

| Approach | Fit for research |
|---|---|
| Open-ended chat / single ReAct agent | The default, and the thing to beat. Context rot kills long investigations; one agent searches one way and is blind to the rest. |
| Parallel sub-agent fan-out (one per sub-question) | **Chosen for the read phase.** Reads parallelize well; each agent explores a disjoint slice and returns a distilled summary. |
| Single-writer synthesis | **Chosen for the write phase.** Merging findings is a write; multiple writers would re-introduce the coordination problem and incoherent narrative. |
| Background async cloud agent | Only safe for well-scoped, verifiable tasks; "junior at execution," fails on iterative/ambiguous work — which most research is. Not the default. |

### 4.2 Research graph

```
START
 → pick_up_ticket          (existing)  → repo = resolve_repo(ticket.repo) = RESEARCH
 → open_branch             (existing, in the knowledge repo)
 → frame_brief             claude -p → brief.md + questions.json
                           (decompose into disjoint sub-questions; mark depth;
                            define coverage criteria + "done-when" per sub-question)
 → surface_questions       (existing) + approve_brief  ← THE single HITL gate
        (scope/depth calibration; the autonomy knob lives here)
 → investigate             PARALLEL sub-agents, one per sub-question
                           each returns: distilled summary (1–2k) + sources.jsonl
 → verify_claims           adversarial fact-check: try to REFUTE each claim from
                           its cited sources; resolve every citation URL
 → synthesize              SINGLE writer merges distilled summaries + raw ledger
                           → report.md on the branch
 → coverage_critic         "what's missing?" vs the original question
        │ gaps  → replan_research ──→ investigate   (bounded: append sub-questions)
        │ clean → commit_push → merge
```

### 4.3 The unit of work and its DoD

A **sub-question** is the research atomic unit. Its `dod.json` is not tests but
**coverage + grounding** criteria:

```
sub_question_answered            # the distilled summary addresses it
every_claim_has_citation         # no orphan claims
citations_resolve                # each URL fetched and reachable
citations_support_claim          # verifier confirms the source actually says it
confidence_recorded              # per-claim confidence + dissenting sources noted
```

### 4.4 Verification without executable ground truth

This is the hardest divergence from coding and deserves the most explicit reasoning.
Coding has objective-but-gameable tests; research has **no compiler**. So
verification leans on three independent, *softer* signals — and we make their
softness explicit rather than pretend otherwise:

1. **Citation grounding (hard gate):** every claim resolves to a source, and the
   source is re-fetched (`get_url_content` / fetch) and checked to actually support
   the claim — not just exist. Catches hallucinated and misattributed citations.
2. **Adversarial refutation (verifier):** a separate agent is prompted to *refute*
   each claim, defaulting to "unsupported" when uncertain. For high-stakes claims,
   use **perspective-diverse verification** (e.g. a primary-source lens, a
   recency/staleness lens, a methodology lens) rather than N identical checkers —
   diversity catches failure modes redundancy can't.
3. **Coverage critic (final review):** asks "what modality wasn't searched, what
   claim is unverified, what source went unread?" Its findings become the next
   `investigate` round.

- **Reward-hacking analog:** in coding we track the visible-vs-held-out *test* gap;
  in research we track the **claim-to-grounded-claim gap** — if the report makes more
  claims than the verifier can ground, that gap is the research equivalent of a gamed
  test suite, and it surfaces to the human.

### 4.5 Done predicate and bounded replan

```
sub_question done = answered ∧ every_claim_grounded ∧ verifier == "claims_supported"
research done     = (∀ sub_questions: DONE) ∧ coverage_critic == "complete vs question"
```

`replan_research` mirrors coding's bounded replan: it may **append sub-questions
only**, is capped (`state.replans`), and every appended sub-question must cite the
coverage gap that justified it. Plus a research-specific stop: **loop-until-dry** —
if K consecutive `investigate` rounds surface nothing new, declare the tail empty and
stop (and `log()` what scope was intentionally dropped — **no silent caps**).

### 4.6 Cost and depth control (research-specific)

Research balloons more easily than coding because "more sources" always *feels*
useful. Three explicit brakes:

- **`done-when` per sub-question** in the brief — the coverage bar is defined *before*
  investigation, not rationalized after.
- **Token/round budget** — fan-out width and max rounds are bounded; exceeding →
  surface.
- **Depth calibration knob** — the brief is written to the user's stated knowledge
  level (the runbook's "calibrate to my knowledge level" TODO), so it neither
  over-explains basics nor under-supports novel claims.

**Tradeoff:** tighter budgets risk shallow coverage; looser budgets risk runaway
cost and context fragmentation. The budget is therefore a per-ticket input, gated at
brief approval, not a silent constant.

---

## 5. Where the two workflows diverge — and why

| Dimension | Coding | Research | Underlying reason |
|---|---|---|---|
| Concurrency | single-writer, sequential changes | parallel investigate, single synthesis | reads parallelize; writes coordinate |
| Ground truth | executable tests/lint/types | citations to primary sources | no compiler for prose |
| Verifier job | does the diff satisfy the DoD? | can I refute this claim? | objective vs. evidential checking |
| Gameable how | weak/over-fit tests | unsupported/misattributed citations | both need an independent skeptic |
| Stop signal | gates green ∧ verifier ok | coverage met ∧ claims grounded ∧ dry | delta must be *computable* for both |
| Primary risk | regression, scope creep | hallucinated sources, context rot | different blast radius |
| HITL gate | approve `plan.md` | approve `brief.md` | plan/brief quality caps everything |

**The deep symmetry:** both are *the same loop* — bound the unit, define a
done-predicate, execute, verify independently, drive the `remaining_delta` to empty,
escalate on stagnation, commit to a repo. Only the *kind of evidence* changes. That
symmetry is why they share one harness and one `AgentState`, and why the research
node can reuse `open_branch`, the checkpointer, the ledger, and the
commit/merge tail unchanged.

---

## 6. Consolidated tradeoff catalog

The decisions a future maintainer is most likely to second-guess, each with the
alternative we rejected and the cost we accepted.

| # | Decision | Chosen | Rejected alternative | Why / cost accepted |
|---|---|---|---|---|
| 1 | Control flow | deterministic harness | agent-governed flow | removes the surface for context rot / drift / thrash; cost: more harness code |
| 2 | HITL gates | plan/brief approval only | gate every stage | highest leverage per interruption; cost: trust the verifier downstream |
| 3 | Coding concurrency | sequential single-writer | parallel workers | avoids write-write conflicts; cost: slower |
| 4 | Research read phase | parallel fan-out | single agent | beats context rot, broader coverage; cost: dedup + distillation needed |
| 5 | Research write phase | single synthesizer | parallel writers | coherent narrative, no coordination; cost: a bottleneck stage |
| 6 | Stop condition | `remaining_delta` + stagnation | step/iteration count | honest progress signal; cost: structured verifier output required |
| 7 | Verification | separate verifier + held-out + rubric | self-review / tests only | best-of-n insight; tests are gameable; cost: extra agent calls |
| 8 | Replan | bounded, append-only, cite-cause | free re-planning | contains scope drift; cost: some legitimate re-scopes get surfaced |
| 9 | Plan representation | document on branch | in-memory state | auditable, survives compaction; cost: two sources of truth to sync |
| 10 | Inner-change sessions | 3 sessions (cold test-author, warm implement, cold review) | one warm session for all of it | independent tests + independent review; cost: re-establishing context per session |
| 11 | Per-change review | cold review *per change* + cold *final* review | final review only | catches local vs integration bugs separately; cost: one extra cold call per change |
| 12 | Inner planning | high-level big plan + just-in-time inner plan per change | fully-detailed big plan up front | plan can't rot/over-commit; cost: a planning session inside flagged changes |
| 13 | Change sizing | soft LoC budget | hard LoC gate / no limit | bounded reviewable units without gaming; cost: planner must estimate size |
| 14 | Ambiguity | goals on ticket + repo `CLAUDE.md`, with autonomy knob | a `goals.md` per run / always ask / always guess | defensible, logged decisions, no new files; cost: author goals once per ticket/repo |
| 15 | Sandbox | graduate: process-sep → microVM before unattended merge | full microVM day one / none | matches single-user risk now, hardens before autonomy; cost: a later migration |
| 16 | Improvement | hold model fixed, evolve harness (AHE) | fine-tune the model | cheaper, faster iteration, attributable wins; cost: requires trace/eval discipline |

---

## 7. Data model & code changes (grounded in the repo)

### 7.1 `src/classes.py`

Extend the existing `AgentState`
([src/classes.py:51](src/classes.py#L51)) to serve both tracks. The `Repo` enum
([src/classes.py:32](src/classes.py#L32)) already routes coding → `CODER`,
research → `RESEARCH`; keep that as the workflow selector alongside `TicketType`.
Add a **`goals`** field to `TicketContent` ([src/classes.py:37](src/classes.py#L37))
to carry the ticket-level goal hierarchy + non-goals (§2.10); repo-level constraints
stay in the repo's `CLAUDE.md` (no model change needed — `claude -p` auto-loads it).

```python
class TicketContent(BaseModel):
    id: int
    type: TicketType
    priority: TicketPriority
    repo: Repo
    title: str
    body: str
    goals: Optional[str] = None     # ticket-level goal hierarchy + non-goals (§2.10)


class WorkUnitStatus(Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"

class WorkUnit(BaseModel):          # one base type for both tracks
    id: str
    title: str
    dod: dict                       # coding: test classes | research: coverage criteria
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    ledger_path: Optional[Path] = None
    # coding-specific (set by big_plan; see §3.2/§3.3/§3.7)
    soft_loc: Optional[int] = None          # soft size budget for this change
    needs_research: bool = False            # → run inner_plan_and_research first
    needs_planning: bool = False            # → write inner_plan.md first
    inner_plan_path: Optional[Path] = None  # pointer to the low-level plan, if any

class AgentState(BaseModel):
    status: Status
    step: int                       # telemetry only
    artifact: Mapping[str, Any]
    ticket_id: Optional[str] = None
    ticket: Optional[Ticket] = None
    repo_path: Optional[Path] = None
    plan_path: Optional[Path] = None         # plan.md (coding) / brief.md (research)
    has_open_questions: bool = False         # set by big_plan; gates approve_plan (§3.2)
    units: list[WorkUnit] = []               # changes or sub-questions
    current_unit_id: Optional[str] = None
    attempts: int = 0
    replans: int = 0                         # shared cap across inner + outer replan
    autonomy: int = 1                        # the autonomy knob (0=ask more … 3=decide)
    questions: Optional[list[dict]] = None   # structured UI payload (q + options:
                                             # label/pro/con/recommended); §3.2
    answers: Optional[str] = None            # gate response; woven into plan.md
    complexity: Optional[TicketComplexity] = None
```

`questions` is the **transient UI projection** of `plan.md`'s `## Open questions`
section — checkpointed so the board can render it, but `plan.md` stays canonical
(§3.2). It widens from `list[dict[str, str]]` to `list[dict]` to hold the nested
`options` list.

Register `WorkUnit`, `WorkUnitStatus` in the `SqliteSaver`
`allowed_msgpack_modules` ([src/nodes/__init__.py:38](src/nodes/__init__.py#L38)) —
the checkpointer silently can't resume types it doesn't know.

### 7.2 Nodes

| File | Change |
|---|---|
| [src/nodes/__init__.py](src/nodes/__init__.py) | Replace the linear edges with the two outer graphs; branch on `assert_coding` at `select_next_*`, not before `spec`. |
| [src/nodes/general/nodes.py](src/nodes/general/nodes.py) | Add `repo_bootstrap_check`, `select_next_unit`, `final_review`/`coverage_critic`, `replan`; keep `open_branch`; reuse `surface_questions`/`get_user_answer` for `approve_plan` (answering the questions inside `plan.md`); flesh out the stub `review`/`commit_push`/`merge`. No `goals.md` seeding — goals live on the ticket + `CLAUDE.md` (§2.10). |
| [src/nodes/coding/nodes.py](src/nodes/coding/nodes.py) | Replace `spec`/`write_tests`/`write_code` stubs with `big_plan` (high-level; writes questions into `plan.md` + sets `has_open_questions`; soft-LoC chunking; `needs_*` flags) and `implement_change`. The node orchestrates the three sessions of §3.3 — `inner_plan_and_research`, `spec_and_tests` (cold), the warm `implement` loop, `review_change` (cold, also seals `diff_summary.md`) — plus `reconcile_outer_plan` as the last step. |
| [src/nodes/research/nodes.py](src/nodes/research/nodes.py) | Replace the stub with `frame_brief`, `investigate` (parallel fan-out), `verify_claims`, `synthesize`, `coverage_critic`. |
| [src/nodes/helpers.py](src/nodes/helpers.py) | Add `ClaudeSession` wrapper (Agent SDK), a cold-session helper for the test-author and reviewer, hard-gate helpers (`run_tests`, `lint_clean`, `typecheck_clean`, `tests_are_red`), `loc_changed`, ledger I/O (two-tier payload assembly per [docs/evidence-ledger.md](docs/evidence-ledger.md)), citation-resolution helper, `has_pending_unit`. |

### 7.3 Prompts and rubrics

Current prompts ([src/prompts/](src/prompts/)) are a starting set; v2 adds:

- Coding: `big_plan.j2` (high-level, surfaces questions into `plan.md`, soft-LoC
  chunking), `inner_plan.j2` (the just-in-time low-level plan + research dive),
  `spec_and_tests.j2` (merging today's `spec.j2` + `write_tests.j2`), `execute.j2`,
  `review_change.j2` (the cold per-change reviewer, which also writes the sealed
  `diff_summary.md`), `final_review.j2`. Repo goals seed the repo's `CLAUDE.md` (§2.10).
- Research: `frame_brief.j2`, `investigate.j2`, `verify_claims.j2`,
  `synthesize.j2`, `coverage_critic.j2`.
- Rubrics under `.coder/rubrics/` (§2.5), versioned and referenced by file, so the
  reviewer's standard is auditable and improvable without touching code.

---

## 8. Phased roadmap

Each phase is independently shippable and leaves the graph runnable.

1. **Spine — data + ledger + gates.** `WorkUnit`/`WorkUnitStatus`, `TicketContent.goals`,
   extended `AgentState`, checkpointer registration; two-tier ledger I/O (the
   §evidence-ledger payload assembler); coding hard-gate helpers;
   `repo_bootstrap_check`. *No behavior change to routing yet.*
2. **Coding outer loop.** `big_plan` → high-level `plan.md` (with its `## Open
   questions` section) + `changes.json`, soft-LoC chunking, `needs_*` flags;
   `approve_plan` resolves questions in-plan + approves; `select_next_unit` routing;
   `commit_push`/`merge` (ledger squashed at merge).
3. **Coding inner change.** `ClaudeSession` + cold-session helper; the three-session
   `implement_change` — `inner_plan_and_research` (flagged), cold `spec_and_tests`
   with RED-gate, warm delta-driven `implement`, cold `review_change`; capped loop.
4. **Coding reconciliation + replan.** `reconcile_outer_plan` (inner replan, last
   step of the change); `final_review`; bounded `replan` (shared cap, cite-cause);
   surface `Status.FAILURE` paths.
5. **Research workflow.** `frame_brief`/`approve_brief`; parallel `investigate`;
   `verify_claims` (citation grounding + adversarial); single-writer `synthesize`;
   `coverage_critic`; bounded `replan_research` + loop-until-dry.
6. **Harness-improvement loop (AHE).** Hold the base model fixed; use the evidence
   ledger as a trace/eval corpus; iterate tools/prompts/rubrics/sub-agents and
   measure against held-out checks. Add the audit metrics: visible-vs-held-out test
   gap (coding) and claim-to-grounded-claim gap (research).

---

## 9. Named risks and their mitigations

The four failure modes from the summary, mapped to the specific harness feature that
contains each:

| Risk | What it looks like | Contained by |
|---|---|---|
| **Context rot** | quality degrades over a long run | fresh context per unit; sub-agent fan-out; 1–2k distilled summaries; compaction (§2.6) |
| **Scope drift** | the agent quietly does more/less than asked | append-only, cite-cause replan; plan-as-document diffs; one HITL gate on the plan (§2.8, §3.6) |
| **Loop thrashing** | retries without progress | `remaining_delta` + `stagnation_count`; `MAX_RETRIES`; surface-don't-spin (§2.4) |
| **Reward hacking** | passes the gate without doing the work | separate verifier; held-out checks; non-test rubric; visible-vs-held-out / claim-grounding gap metrics (§2.5, §4.4) |

Plus the cross-cutting one the summary flags hardest: **"do too much at once → run
out of context → prematurely declare done."** Contained by the entire bounded-unit +
hard-predicate design — the agent is never *allowed* to declare done; a deterministic
predicate does.

---

## 10. Open questions

- **`ClaudeSession` shape** over the Agent SDK — lifecycle, compaction policy, and
  how `send()` surfaces tool/turn results inside the warm loop.
- **Ledger commit cadence** — *decided: squashed at merge* (per-change commits during
  the run for crash-safety, squashed into `main` at merge). Open sub-point: strip the
  ledger from `main` entirely vs. keep it. See [docs/evidence-ledger.md](docs/evidence-ledger.md) §6.
- **Ticket `goals` field format** — free text, or a light schema (priority-ranked
  list + non-goals) the harness can parse to detect "goals conflict" escalations
  mechanically (§2.10)?
- **`soft_loc` default and overflow threshold** — what multiple of `soft_loc`
  triggers an inner-reconciliation split vs. just a flag to `review_change` (§3.7)?
- **Inner vs outer replan boundary** — when an inner change reconciles the outer
  plan (§3.6), how much may it reorder before it should instead fail and surface?
- **Shared rubric for `review_change` vs `final_review`** (coding) and
  `verify_claims` vs `coverage_critic` (research) — one rubric with scope flags, or
  separate files?
- **Research source trust policy** — allow-list of domains? how to weight primary vs.
  secondary sources in the confidence score?
- **When does research warrant `git worktree` parallelism** for the synthesis of
  *independent* report sections, vs. staying strictly single-writer?
- **Sandbox graduation trigger** — what concrete capability (e.g. enabling
  unattended merge) flips us from process-separation to microVM isolation?
```
