## Summary
- Flow Enginnering: autonomous system operate under deterministic constraints
- Software development lifecycle (SDLC)
- Goal: hardcode the invariants, gates, and side effects, and delegate the cognition
- Formalizing control flow, execution safety and verification rules
- Deterministic harness architecture
    - Two level loop
    - Outer orchestrator 
    - Inner implementation: Spec -> execute -> verify
    -> no changes left -> debugging -> commit & merge
- Human in the loop for the big plan
- Core requirements: 
    - Standardized CI/CD pipelines, security gates, linting standards
    - Up-to-date context
    - Agent execution in secure sandboxing; use Firecracker microVM engines (E2B); persistent workspaces like Daytona with Kata containers
    - Spec-first multi-agent architecture: Highly structured, unambigious specifications. 
        - planner
        - implementer
        - reviewer 
        - security agents
    Coordinated through standardized protocols like MCP. 


# Claude

- Pivotal components:
    - Bounded atomic units of work
    - execution grounded verification: tests, ling, typecheck
    - aggressive context management:
        - compaction 
        - sub-agents
        - fresh contexts per unit
    - plan-level human gating

- Risks
    - context rot
    - scope drift
    - loop thrashing
    - reward hacking

## Insights
- Scaffolds
    - ReAct (Reasoning + Acting): Alternating between thinking and going in a tight cycle
    - generate-test-repair
    - plan-execute
        - Danger: does not handle mid-task requirement changes; bad at iterative re-scoping. Plan quality caps everything downstream as well as ambiguous requirements. 
        - 
    - multi-attempt-retry
    - tree search
- Reads tasks parallelize well
- Write tasks create coordination problem -> favor single-agent or strict hierarchy
- Seperate test writing from implementation; however test-based gates are demonstrably gameable
- Best of n + seperate verifier/selector beats single rollout: use a seperate verifier!

## Approaches
- Autonomous single-agent loop (ReAct / "gather context → act → verify → repeat")
    - prone to context rot and self-reinforcing errors on long runs. 
- Structured pipeline without LLM control flow (Agentless)
    - localization -> repair -> patch validation pipeline
    - Brittle on multi-step/cross-file tasks needing incremental interations; early localizaiton errors are fatal; not suitable for long-running tasks
- Plan then execute with human gate
    - plan quality caps everything downstream
    - ambiguous requirements are dangerous
- Multi-agent hierachies (planner / worker / judge)
    - coordination is dominant failure mode (locking, stranded work, nobody owns hard problems); write-write conflicts; hard to debug
- architect / editor split
    - two-hop information loss
- Spec driven development
    - Specifications are first-class, executable artificats
    - constitution -> specify -> plan -> tasks -> implement. Each producing a md artificat the next phase reads
    - Spec Kit is agent agnostic, large adoption
    - Cons: Heavy overheads (sledgehammer to crack a nut) on small bugs; best for greenfield, awkward for brownfield
- Test-drive / verification gated
    - Plan -> Red -> Green -> Refactor 
    - Seperating test-writing from implementation 
    - Cons: gameable: write weak tests, memorize inputs
- Best of n / tree search / verifier selection (inference-time scaling)
    - Generate multiple candidate solutions and select with a verifier / cricic / judge 
- Background Async cloud agents
    - Best only when well scoped, verifiable tasks
    - "Junior at execution" 
    - Iterative / ambigious tasks fail

Cross cutting:
- Context management: 
    - compaction
    - structured note-taking
        - returning distilled summaries (1-2k tokens)
        - context rot is most cited long-horizon failure.
    - sub-agent architecture

Recommendations
- Make per-change node crash-safe
    - Wrap side-effects as idempotent tasks and persist intermediate change-state to the branch
    - Harden the verifier beyond tests
        - (a) held-out tests: tests the implmenter never sees (SpecBranch) and "Are Solved Issues Really Solved" 
        - (b) non-test review dimension:
            - style / spec conformance critic
            - track the gap between visible-suite and held-out suite; if it widens, you have reward hacking
    - consider adding best-of-n
    - stay-single threaded for the write path. Adopt sub agents for read-heavy work. 
    - Per cycle judge: decides continue/reset and codex's "done-when" routinel cap replans per change and require measurable progress to continue, else escalate to the human. 

# ChatGPT PRo
- Recommendation
    - Two -level harness
    - per-change warm coding sessions
    - test-first hard gates
    - idependent verifier
    - durable plan artifacts
    - trace/eval driven harness improvement


## Changes
- Add the following:
    - Audit/evidence ledger per change: save test output, lint/typecheck output, verifier JSON, diff summary, retry reason and remaining delta
    - 
- Harness improvement loop after baseline runs:
    - hold base model fixed, evolving harness components
        - tools, middleware, skills, subagents, memory and prompts
- Stronger sandbox / session separation plan before unattended merge
    - warns against letting generated code run in the same environment as credentials!

## Important:
- bounded work units
- explicit state artificats
- fresh-context handoffs
- verifiable gates

Dangers:
- do too much at once -> run of of context, later prematurely declare work done. 
    - Fix: initializer/planner
    - Feature list with pass/fail state
    - one-feature-at-a-time implementation
    - git commits
    - progress files
    - explicit end-to-end testing

## Changes suggested:
1. Evidence artifcats
```
.coder/runs/<ticket>/<change_id>/
  spec.md
  dod.json
  test_red.log
  test_green.log
  lint.log
  typecheck.log
  verifier.json
  diff_summary.md
  attempts.jsonl
  record.json
```

2. Make remaining delta the loops primary variable
```json
{
  "remaining_delta": [
    {"kind": "test_failure", "source": "pytest", "evidence": "..."},
    {"kind": "verifier_bug", "source": "verify_change", "evidence": "..."},
    {"kind": "type_error", "source": "pyright", "evidence": "..."}
  ],
  "previous_delta_hash": "...",
  "stagnation_count": 0
}
```
- stop when gates pass, max attempts is hit or delta stops shrinking (or human review is needed)

3. Split tests into "new red tests", "affected tests" and "full confidence suite"
- include in DoD: 
```
new_tests_red_before_implementation
new_tests_green_after_implementation
affected_existing_tests_green
lint_green
typecheck_green
full_suite_greend
```
Test-driven agentic development: TDAD
AHE = Agentic Harness Engineering

4. Add verifier rubrics as shared versioned files
```
.coder/rubrics/
  change_verifier.md
  final_review.md
  security_review.md
  test_quality_review.md
  ui_e2e_review.md
```

5. Single-writer semantics for code changes
Do not have multiple agents mutate the same working tree unless they are isolated by worktree/branch

Suggested approach:

```
START
  → pick_up_ticket
  → open_branch
  → repo_bootstrap_check
       hard gate: dependencies installed, tests command known, lint/typecheck known
  → big_plan
       writes .coder/plan.md + .coder/changes.json
  → approve_plan
       only HITL gate by default
  → select_next_change
       pending → implement_change
       none    → final_review

implement_change(change):
  1. context_brief:
       collect relevant files, affected tests, prior artifacts
  2. spec_and_tests in warm session:
       write spec + tests
       hard gate: new/changed tests exist
       hard gate: new tests fail for the expected reason
  3. execute loop:
       implement smallest change
       run targeted tests
       run affected existing tests
       run lint/typecheck
       save evidence
       independent verifier judges diff vs DoD
       if fail: convert failures to remaining_delta and continue
  4. mark done:
       commit or checkpoint diff
       update changes.json status + evidence pointers

final_review:
  independent one-shot or multi-review fan-out
  hard gate: no unresolved bugs vs original ticket
  if bugs:
       bounded replan can append/reorder PENDING only
  if clean:
       commit_push → merge/PR
```

Consider:
- test-quality review
- affected-existing-test selection: Checks must represent the desired behavior. 
- Strict schema for agents evaluating their own work
- Replan can cause scope drift: every replan must cite which final-review bug or failed gate caused the plan mutation
- 



# Other Topics

- Sandoboxing
    - Daytona, E2B, Modal, Cloudflare Sandbox, Github codespaces?
- OpenSource Engineering Agents
    - OpenHands vs SWE-Agent
- Harness Engineering
    - Test-time scaling
Language Server Protocol (LSP)
    - Gives access to intelligent programming features like auto-complete, error checking and go-to-definitions
SWE World
    - Attempts to bridge the gap from dev to real-world application
    - The SWT Transition Model is a model that acts as an emulator, simulating step-by-step terminal execution feedback, standard output and errors, without spinning up physical containers
    - Reward Model (SWR) -> reads proposed patch and outputs a structured simulated unit-test report

## Ideas
- Create a goal hierarchy for agents to understands the context


