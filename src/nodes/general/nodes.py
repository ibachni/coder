import json
import os
import re
import subprocess
from pathlib import Path
from langgraph.types import interrupt

from nodes.helpers import parse_json_block, run_agent, slugify
from classes import AgentState, Status
from helper.repoPaths import resolve_repo
from prompt_loader import render
from tools.get_ticket import get_open_ticket, get_ticket
from classes import TicketType
from bootstrap import BootstrapError, detect_commands
from ledger import ticket_dir, write_json

### Initial steps


def pick_up_ticket(state: AgentState) -> AgentState:
    """
    Picks up the next ticket with high prio. Calls a simple tools which does that.
    """
    if state.ticket_id:
        ticket = get_ticket(int(state.ticket_id))
    else:
        ticket = get_open_ticket()
    state.ticket = ticket
    state.repo_path = resolve_repo(state.ticket.content.repo)
    state.step += 1
    return state


def assert_coding(state: AgentState) -> bool:
    assert state.ticket is not None
    if state.ticket.content.type == TicketType.CODING:
        return True
    else:
        return False


def open_branch(state: AgentState) -> AgentState:
    """
    Mimic a developer's flow: refuse a dirty tree, pull main, then switch to
    (or create) the ticket branch off the latest main.
    """
    assert state.ticket is not None, "open_branch requires a ticket to be set"
    assert state.repo_path is not None, "open_branch requires repo_path to be set"

    branch = f"ticket_{state.ticket_id}/{slugify(state.ticket.content.title)}"
    cwd = state.repo_path

    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        if dirty.stdout.strip():
            raise RuntimeError(f"Working tree is dirty in {cwd}; aborting open_branch.")

        subprocess.run(
            ["git", "checkout", "main"], cwd=cwd, capture_output=True, text=True, check=True
        )
        subprocess.run(
            ["git", "pull", "--ff-only"], cwd=cwd, capture_output=True, text=True, check=True
        )

        exists = (
            subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                cwd=cwd,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        checkout_cmd = ["git", "checkout", branch] if exists else ["git", "checkout", "-b", branch]
        subprocess.run(checkout_cmd, cwd=cwd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"git command failed: {' '.join(e.cmd)}")
        print(f"stderr: {e.stderr}")
        raise

    state.step += 1
    return state


def repo_bootstrap_check(state: AgentState) -> AgentState:
    """Discover the repo's test/lint/typecheck commands and persist them.

    Hard gate: if we cannot determine how to build/test the repo, fail fast and
    surface to the human rather than letting the inner loop flail on unknown
    commands. Writes the detected commands to the ticket-tier `bootstrap.json`.
    """
    assert state.repo_path is not None, "repo_bootstrap_check requires repo_path"
    assert state.ticket_id is not None, "repo_bootstrap_check requires ticket_id"

    try:
        config = detect_commands(state.repo_path)
    except BootstrapError:
        state.status = Status.FAILURE
        return state

    bootstrap_path = ticket_dir(state.repo_path, state.ticket_id) / "bootstrap.json"
    write_json(bootstrap_path, config.model_dump())
    state.step += 1
    return state


def route_after_open_branch(state: AgentState) -> str:
    """Split by ticket type right after branching (pure; no mutation).

    Coding goes through the bootstrap gate (test/lint/typecheck detection); research
    skips it — a knowledge repo has no test commands, so `detect_commands` would
    hard-fail it — and enters the research graph directly (plan-v2 §4.2)."""
    return "coding" if assert_coding(state) else "research"


def route_after_bootstrap(state: AgentState) -> str:
    """Coding-only after the bootstrap gate (§3.1/§3.2): fail fast to END, else plan."""
    return "end" if state.status is Status.FAILURE else "big_plan"


def surface_questions(state: AgentState) -> AgentState:
    """
    Usually the text body given is not specific enough.
    In this step, the goal is to surface any questions.
    The questions are attached to the state as a list of strings.
    -> Human in the loop
    """
    assert state.ticket is not None
    assert state.repo_path is not None
    prompt = render(
        "surface_questions",
        ticket_title=state.ticket.content.title,
        ticket_repo=state.ticket.content.repo,
        ticket_body=state.ticket.content.body,
        repo_path=state.repo_path,
    )
    result = run_agent(prompt, state.repo_path)
    if result.returncode != 0:
        state.status = Status.FAILURE
        return state
    try:
        state.questions = parse_json_block(result.stdout)
    except json.JSONDecodeError:
        state.status = Status.FAILURE
        return state
    state.step += 1
    return state


def get_user_answer(state: AgentState) -> AgentState:
    if not state.questions:
        state.step += 1
        return state
    answers = interrupt({"questions": state.questions})
    state.answers = answers
    state.step += 1
    return state


### HITL gate (shared)
#
# The approval gate is one pattern reused by both tracks: coding's `approve_plan`
# (over plan.md) and research's `approve_brief`/`approve_watchlist` (over brief.md).
# The reusable pieces live here so a question's durable record format is defined once.
# The `interrupt()` itself stays in each caller's module so a test can monkeypatch it
# there; the caller passes the resume value to `apply_gate_decision`.


def render_questions_section(questions: list[dict]) -> str:
    """Render the durable `## Open questions & decisions` section appended to a gated doc.

    Each question gets a stable `### Q{n}:` heading so the gate can append the chosen
    answer beneath it (via `record_answers`) as the durable record.
    """
    parts = ["\n\n## Open questions & decisions\n"]
    if not questions:
        parts.append("\n_None — the ticket is workable as written._\n")
        return "".join(parts)
    for i, q in enumerate(questions, 1):
        parts.append(f"\n### Q{i}: {str(q.get('question', '')).strip()}\n")
        if q.get("category"):
            parts.append(f"- _Category:_ {q['category']}\n")
        if q.get("why"):
            parts.append(f"- _Why it matters:_ {q['why']}\n")
        for opt in q.get("options") or []:
            rec = " _(recommended)_" if opt.get("recommended") else ""
            parts.append(
                f"- **{opt.get('label', '')}**{rec} — pro: {opt.get('pro', '')}; "
                f"con: {opt.get('con', '')}\n"
            )
    return "".join(parts)


def _answer_index(qid: object) -> int | None:
    """Map a resume answer id (e.g. "q1") to its 1-based question number."""
    if not isinstance(qid, str):
        return None
    m = re.search(r"\d+", qid)
    return int(m.group()) if m else None


def record_answers(doc_path: Path, answers: object) -> None:
    """Append each answer beneath its `### Q{n}:` heading in the gated doc (durable record).

    Idempotent: a question that already carries an `- _Answer:_` line is left untouched,
    so a re-entry of the gate (e.g. a crash between this write and the checkpoint) can't
    duplicate answers. Answer text is collapsed to a single line so a multi-line reply
    can't break the markdown record or smuggle in a fake `### Q` heading.
    """
    if not doc_path.exists() or not isinstance(answers, list):
        return
    by_index: dict[int, str] = {}
    for a in answers:
        if not isinstance(a, dict):
            continue
        n = _answer_index(a.get("id"))
        raw = a.get("answer")
        ans = " ".join(str(raw).split()) if raw is not None else ""
        if n is not None and ans:
            by_index[n] = ans
    if not by_index:
        return
    lines = doc_path.read_text().split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        m = re.match(r"^### Q(\d+):", line)
        if not m:
            continue
        n = int(m.group(1))
        already_answered = i + 1 < len(lines) and lines[i + 1].startswith("- _Answer:_")
        if n in by_index and not already_answered:
            out.append(f"- _Answer:_ {by_index[n]}")
    doc_path.write_text("\n".join(out))


def apply_gate_decision(
    state: AgentState, resume: object, *, doc_path: Path, max_replans: int
) -> AgentState:
    """Apply a HITL gate resume to the state — the generic half of an approval node.

    The caller has already run `interrupt(payload)` (in its own module, so it's
    monkeypatchable) and passes the `resume` here. On approval: clear
    `has_open_questions` and record any answers into `doc_path`. On rejection: a bounded
    re-plan (increment `replans`) until `max_replans`, then `Status.FAILURE` rather than
    thrash. A non-dict resume (malformed UI payload) is treated as a rejection.
    """
    state.approval = resume if isinstance(resume, dict) else {"approved": False}

    answers = state.approval.get("answers")
    if answers:
        record_answers(doc_path, answers)

    if state.approval.get("approved") is True:
        state.has_open_questions = False
    elif state.replans < max_replans:
        state.replans += 1
    else:
        state.status = Status.FAILURE

    state.step += 1
    return state


### Last steps


def final_review(state: AgentState) -> AgentState:
    """**Phase-1 STUB** (§3.8): pass-through to commit_push. Phase 3 replaces this with
    the real whole-branch review + bounded replan."""
    state.step += 1
    return state


def _has_origin(cwd: Path) -> bool:
    """Whether an `origin` remote is configured — push and PR creation both need one (§8)."""
    remotes = subprocess.run(
        ["git", "remote"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.split()
    return "origin" in remotes


def commit_push(state: AgentState) -> AgentState:
    """Commit the ticket branch and push it to origin if a remote exists (§3.9).

    Stages everything, commits `ticket <id>: <title>` (skipping cleanly when the tree
    is already clean), then pushes the current branch to `origin` — but only if a
    remote is configured. A repo with no remote commits locally and skips the push
    rather than failing (§8). Git failures propagate, matching `open_branch`.
    """
    assert state.ticket is not None, "commit_push requires a ticket"
    assert state.repo_path is not None, "commit_push requires repo_path"
    assert state.ticket_id is not None, "commit_push requires ticket_id"
    cwd = state.repo_path
    message = f"ticket {state.ticket_id}: {state.ticket.content.title}"

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)

    try:
        git("add", "-A")
        # `git diff --cached --quiet` exits non-zero iff something is staged.
        if git("diff", "--cached", "--quiet", check=False).returncode != 0:
            git("commit", "-m", message)

        if _has_origin(cwd):
            branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            git("push", "-u", "origin", branch)
    except subprocess.CalledProcessError as e:
        print(f"git command failed: {' '.join(e.cmd)}")
        print(f"stderr: {e.stderr}")
        raise

    state.step += 1
    return state


def _pr_body(state: AgentState) -> str:
    """PR body, pointing reviewers at the right artifacts for the ticket type.

    A research ticket never writes `.coder/runs/` (it skips bootstrap and writes to
    `research/<slug>/`), so a coding-style ledger link would be dead.
    """
    assert state.ticket is not None, "_pr_body requires a ticket"
    if state.ticket.content.type is TicketType.RESEARCH:
        artifacts = "Research outputs under `research/` (report.md, sources.jsonl)."
    else:
        artifacts = f"Plan and per-change ledger: `.coder/runs/{state.ticket_id}/`."
    return f"Automated changes for ticket {state.ticket_id}.\n\n{artifacts}"


def merge(state: AgentState) -> AgentState:
    """Land the ticket branch (§3.10).

    Default (`CODER_MERGE_MODE` unset/`pr`): open a PR with `gh pr create` — generated
    code shouldn't merge to `main` unattended until the Phase-3 verifier + review exist
    (decision §7.1). Set `CODER_MERGE_MODE=squash` for the eventual autonomous path:
    squash-merge the per-change commits into one clean `main` commit.
    """
    assert state.ticket is not None, "merge requires a ticket"
    assert state.repo_path is not None, "merge requires repo_path"
    assert state.ticket_id is not None, "merge requires ticket_id"
    cwd = state.repo_path
    title = f"ticket {state.ticket_id}: {state.ticket.content.title}"

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

    try:
        if os.environ.get("CODER_MERGE_MODE", "pr") == "squash":
            branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            git("checkout", "main")
            git("merge", "--squash", branch)
            git("commit", "-m", title)
        elif _has_origin(cwd):
            subprocess.run(
                ["gh", "pr", "create", "--title", title, "--body", _pr_body(state)],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            # Can't open a PR without a remote; the branch is committed locally (§8).
            print(
                f"merge: no 'origin' remote — ticket {state.ticket_id} branch committed "
                "locally, skipping PR (open one manually or set CODER_MERGE_MODE=squash)."
            )
    except subprocess.CalledProcessError as e:
        print(f"merge command failed: {' '.join(e.cmd)}")
        print(f"stderr: {e.stderr}")
        raise

    state.step += 1
    return state
