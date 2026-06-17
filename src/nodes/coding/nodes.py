import json
import subprocess
from pathlib import Path

from langgraph.types import interrupt

from nodes.helpers import parse_json_block, run_agent
from nodes.general.nodes import (
    apply_gate_decision,
    record_answers,
    render_questions_section as _render_questions_section,
)
from classes import AgentState, ChangeStatus, Status, WorkUnit
from helper.authTokenLoader import load_oauth_token
from helper.cleanSubscriptionEnv import clean_subscription_env
from ledger import _safe_segment, change_dir, plan_path, write_changes, write_plan
from prompt_loader import render

MAX_QUESTIONS = 6  # more blocking questions ⇒ the ticket is under-specified (§4)
MAX_REPLANS = 3  # bounded plan-rejection re-plans before we surface FAILURE (§3.5)


def big_plan(state: AgentState) -> AgentState:
    """Produce the high-level plan: one agent → plan.md + changes.json + questions.

    "Agents propose, the node commits" (v2 §2.2): the agent only emits a single JSON
    object; *this node* owns every file write and all validation. On any breach —
    non-zero exit, unparseable JSON, empty/duplicate/unsafe changes, bad soft_loc, or
    too many questions — it sets Status.FAILURE and writes nothing (don't half-apply a
    plan). See docs/coding/phase-1.md §3.3 / §4.
    """
    assert state.ticket is not None, "big_plan requires a ticket"
    assert state.repo_path is not None, "big_plan requires repo_path"
    assert state.ticket_id is not None, "big_plan requires ticket_id"
    repo, ticket_id = state.repo_path, state.ticket_id

    # Revise mode: a prior rejected plan + its feedback feed back into the prompt so
    # the agent returns a revised plan rather than starting blind (§3.3).
    prior_plan = feedback = None
    if state.approval and not state.approval.get("approved", False):
        feedback = state.approval.get("feedback")
        prior = plan_path(repo, ticket_id)
        if prior.exists():
            prior_plan = prior.read_text()

    prompt = render(
        "big_plan",
        ticket_title=state.ticket.content.title,
        ticket_repo=state.ticket.content.repo.value,
        ticket_body=state.ticket.content.body,
        ticket_goals=state.ticket.content.goals,
        repo_path=repo,
        prior_plan=prior_plan,
        feedback=feedback,
    )

    result = run_agent(prompt, repo)
    if result.returncode != 0:
        state.status = Status.FAILURE
        return state
    try:
        data = parse_json_block(result.stdout)
    except json.JSONDecodeError:
        state.status = Status.FAILURE
        return state
    if not _valid_plan(data):
        state.status = Status.FAILURE
        return state

    changes = data["changes"]
    questions = data.get("questions") or []

    units = [
        WorkUnit(
            id=c["id"],
            title=c["title"],
            intent=str(c.get("intent") or ""),
            soft_loc=None if c.get("soft_loc") is None else int(c["soft_loc"]),
            needs_research=bool(c.get("needs_research", False)),
            needs_planning=bool(c.get("needs_planning", False)),
            ledger_path=change_dir(repo, ticket_id, c["id"]),
        )
        for c in changes
    ]

    plan_md = data["plan_md"] + _render_questions_section(questions)
    state.plan_path = write_plan(repo, ticket_id, plan_md)
    write_changes(repo, ticket_id, units)
    state.units = units
    state.questions = questions
    state.has_open_questions = len(questions) > 0
    state.step += 1
    return state


def route_after_big_plan(state: AgentState) -> str:
    """A failed/empty plan goes to END; a valid one to the approval gate (§3.3/§8)."""
    return "end" if state.status is Status.FAILURE else "approve_plan"


def _valid_plan(data: object) -> bool:
    """Validate the agent's JSON object against the §4 contract (pure, no writes)."""
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("plan_md"), str) or not data["plan_md"].strip():
        return False
    return _valid_questions(data.get("questions") or []) and _valid_changes(data.get("changes"))


def _valid_questions(questions: object) -> bool:
    # Each question (and any option) must be a dict, else `_render_questions_section`
    # would crash on `.get(...)` instead of failing cleanly.
    if not isinstance(questions, list) or len(questions) > MAX_QUESTIONS:
        return False
    for q in questions:
        if not isinstance(q, dict):
            return False
        options = q.get("options")
        if options is not None and (
            not isinstance(options, list) or any(not isinstance(o, dict) for o in options)
        ):
            return False
    return True


def _valid_changes(changes: object) -> bool:
    if not isinstance(changes, list) or not changes:
        return False
    seen: set[str] = set()
    for c in changes:
        if not isinstance(c, dict):
            return False
        cid, title = c.get("id"), c.get("title")
        if not isinstance(cid, str) or not cid or not isinstance(title, str) or not title:
            return False
        try:
            _safe_segment(cid)  # ids become ledger path segments (§4)
        except ValueError:
            return False
        if cid in seen:
            return False
        seen.add(cid)
        if not _valid_soft_loc(c.get("soft_loc")):
            return False
    return True


def _valid_soft_loc(loc: object) -> bool:
    """A positive int, an integral float (agents often emit ``150.0``), or null (§4)."""
    if loc is None:
        return True
    if isinstance(loc, bool):  # bool is an int subclass — reject it explicitly
        return False
    if isinstance(loc, int):
        return loc > 0
    if isinstance(loc, float):
        return loc.is_integer() and loc > 0
    return False


def approve_plan(state: AgentState) -> AgentState:
    """The single HITL gate (§3.4).

    `interrupt()` with the plan + open questions; on resume, record the decision in
    `state.approval`, write any answers beneath their questions in plan.md (the durable
    record), and set the routing state. All mutation lives here — `route_after_approval`
    only reads it (§3.6). On rejection the re-plan budget is bounded by MAX_REPLANS;
    once exhausted, fail rather than thrash (§3.5).
    """
    assert state.repo_path is not None, "approve_plan requires repo_path"
    assert state.ticket_id is not None, "approve_plan requires ticket_id"
    repo, ticket_id = state.repo_path, state.ticket_id

    plan = plan_path(repo, ticket_id)
    payload = {
        "plan_md": plan.read_text() if plan.exists() else "",
        "changes": [
            {"id": u.id, "title": u.title, "intent": u.intent, "soft_loc": u.soft_loc}
            for u in state.units
        ],
        "questions": state.questions or [],
    }
    resume = interrupt(payload)
    return apply_gate_decision(state, resume, doc_path=plan, max_replans=MAX_REPLANS)


def route_after_approval(state: AgentState) -> str:
    """Pure router for the approval gate (§3.5); approve_plan owns the mutations."""
    if state.approval and state.approval.get("approved") is True:
        return "select_next_change"
    if state.status is Status.FAILURE:
        return "end"
    return "big_plan"


def _record_answers(repo: Path, ticket_id: str, answers: object) -> None:
    """Record answers into this ticket's plan.md (thin shim over the shared `record_answers`)."""
    record_answers(plan_path(repo, ticket_id), answers)


def select_next_change(state: AgentState) -> AgentState:
    """Pick the first PENDING work unit (or None) for the inner loop to consume (§3.6).

    The pick is a node, not the conditional, because routing functions must not mutate.
    """
    state.current_unit_id = next(
        (u.id for u in state.units if u.status is ChangeStatus.PENDING), None
    )
    state.step += 1
    return state


def route_change(state: AgentState) -> str:
    """Pure router (§3.6): a selected unit goes to the inner loop, else we're done."""
    return "implement_change" if state.current_unit_id is not None else "final_review"


def implement_change(state: AgentState) -> AgentState:
    """**Phase-1 STUB** (§3.7): mark the current unit DONE and persist changes.json.

    Phase 2 replaces this with the three-session inner loop; the outer-loop contract —
    consume one PENDING unit, leave it DONE, persist progress, return — stays identical.
    """
    assert state.repo_path is not None, "implement_change requires repo_path"
    assert state.ticket_id is not None, "implement_change requires ticket_id"
    assert state.current_unit_id is not None, "implement_change requires a selected unit"
    # Look the unit up rather than for/break: a no-op here would leave it PENDING and the
    # graph would re-select it forever, so a missing id must surface, not pass silently.
    unit = next((u for u in state.units if u.id == state.current_unit_id), None)
    assert unit is not None, f"implement_change: no unit with id {state.current_unit_id!r}"
    unit.status = ChangeStatus.DONE
    write_changes(state.repo_path, state.ticket_id, state.units)
    state.step += 1
    return state


def spec(state: AgentState) -> AgentState:
    state.step += 1
    return state


def write_tests(state: AgentState) -> AgentState:
    prompt = render("write_tests", ticket_id=state.ticket_id)
    result = subprocess.run(
        ["claude", "-p", prompt],
        env=clean_subscription_env(load_oauth_token()),
        cwd=state.repo_path,
        timeout=600,
        capture_output=True,
    )
    print(result)
    return state


def write_code(state: AgentState) -> AgentState:
    state.step += 1
    return state
