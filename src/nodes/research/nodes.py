"""Research workflow — the `new` mode (docs/research/implementation-plan.md §4, R1).

The `new`-mode pipeline: classify → frame_brief → approve_brief → research_agent →
save_report. A single reasoning agent does the investigation with Firecrawl tools and
*returns* `{report_md, sources}`; the node writes the files (the agent has no write
tools, decision §0.7). "Agents propose, the node commits": every agent emits JSON; the
node owns all validation and file writes — on any breach it sets Status.FAILURE and
writes nothing. R2/R3 add the `continuous`/`discover` modes alongside this one.
"""

import json
import re

from langgraph.types import interrupt

import research_io
from classes import AgentState, ResearchMode, Status
from nodes.general.nodes import apply_gate_decision, render_questions_section
from nodes.helpers import agent_text, parse_json_block, run_agent, slugify
from prompt_loader import render
from research_config import DISALLOWED_TOOLS, run_research_agent

MAX_QUESTIONS = 6  # more blocking questions ⇒ the question is under-specified
MAX_REPLANS = 3  # bounded brief-rejection re-frames before we surface FAILURE


def _slug(state: AgentState) -> str:
    """Stable, collision-free output-folder name: ``<ticket-id>-<title-slug>``.

    The id prefix keeps two distinct tickets that share a title in separate
    ``research/<slug>/`` folders, while staying stable across runs of the same ticket —
    which the continuous mode (R2) relies on to find a prior report.
    """
    assert state.ticket is not None, "research nodes require a ticket"
    return f"{state.ticket.content.id}-{slugify(state.ticket.content.title)}"


def classify_research_type(state: AgentState) -> AgentState:
    """Pick the research mode and establish the output-folder identity.

    R1 implements only the `new` mode; R2/R3 add the real detection here (recurring +
    prior report → `continuous`; a "find me sites to follow" ask → `discover`). Setting
    the three paths up front means every downstream node — and a resume — shares one
    folder. `plan_path` is the gated doc (brief.md), reusing the field coding uses for
    plan.md.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)
    state.research_mode = ResearchMode.NEW
    state.plan_path = research_io.brief_path(repo, slug)
    state.report_path = research_io.report_path(repo, slug)
    state.watchlist_path = research_io.watchlist_path(repo, slug)
    state.step += 1
    return state


def frame_brief(state: AgentState) -> AgentState:
    """Frame the question into brief.md (sub-questions + done-when) and surface questions.

    Mirrors coding's `big_plan`: the agent proposes a single JSON object; this node writes
    brief.md (brief + the rendered questions section) and sets the gate state. On a prior
    rejection it feeds the rejected brief + feedback back in so the agent revises rather
    than starting blind.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)

    prior_brief = feedback = None
    if state.approval and not state.approval.get("approved", False):
        feedback = state.approval.get("feedback")
        prior_brief = research_io.read_brief(repo, slug)

    prompt = render(
        "research/frame_brief",
        ticket_title=state.ticket.content.title,
        ticket_body=state.ticket.content.body,
        ticket_goals=state.ticket.content.goals,
        max_questions=MAX_QUESTIONS,
        prior_brief=prior_brief,
        feedback=feedback,
    )

    # Framing is read-only — deny the write/exec built-ins so the agent can't mutate the
    # knowledge repo (invariant §5.7), regardless of that repo's own settings.
    result = run_agent(prompt, repo, disallowed_tools=DISALLOWED_TOOLS)
    if result.returncode != 0:
        state.status = Status.FAILURE
        return state
    try:
        data = parse_json_block(result.stdout)
    except json.JSONDecodeError:
        state.status = Status.FAILURE
        return state
    if not _valid_brief(data):
        state.status = Status.FAILURE
        return state

    questions = data.get("questions") or []
    brief_md = data["brief_md"] + render_questions_section(questions)
    state.plan_path = research_io.write_brief(repo, slug, brief_md)
    state.questions = questions
    state.has_open_questions = len(questions) > 0
    state.step += 1
    return state


def route_after_frame_brief(state: AgentState) -> str:
    """A failed frame goes to END; a valid one to the approval gate."""
    return "end" if state.status is Status.FAILURE else "approve_brief"


def _valid_brief(data: object) -> bool:
    """Validate the agent's brief JSON (pure, no writes)."""
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("brief_md"), str) or not data["brief_md"].strip():
        return False
    questions = data.get("questions") or []
    if not isinstance(questions, list) or len(questions) > MAX_QUESTIONS:
        return False
    return all(isinstance(q, dict) for q in questions)


def approve_brief(state: AgentState) -> AgentState:
    """The single HITL gate for research — approve the brief before the expensive dive.

    Same shape as coding's `approve_plan`: `interrupt()` lives here (so a test can
    monkeypatch it on this module), then the shared `apply_gate_decision` records answers
    into brief.md and sets approval / bounded re-frame / FAILURE.
    """
    assert state.plan_path is not None, "approve_brief requires a framed brief (plan_path)"
    brief = state.plan_path
    payload = {
        "brief_md": brief.read_text() if brief.exists() else "",
        "questions": state.questions or [],
    }
    resume = interrupt(payload)
    return apply_gate_decision(state, resume, doc_path=brief, max_replans=MAX_REPLANS)


def route_after_brief(state: AgentState) -> str:
    """Pure router for the brief gate; approve_brief owns the mutations."""
    if state.approval and state.approval.get("approved") is True:
        return "research_agent"
    if state.status is Status.FAILURE:
        return "end"
    return "frame_brief"


def research_agent(state: AgentState) -> AgentState:
    """Single reasoning agent: investigate the approved brief with Firecrawl, return the report.

    The agent returns `{report_md, sources}` and writes no files (decision §0.7); we stash
    the parsed result on `state.artifact` for `save_report`. Two robustness measures, both
    learned from real runs:

    - **Don't throw away the work on a format hiccup.** On a long, tool-heavy run the model
      often drifts from the strict JSON and just writes the report as markdown. Rather than
      fail, `_parse_report` falls back to treating the final message as the report and
      harvesting cited URLs as sources.
    - **Surface, don't spin.** Every failure path records *why* on `state.artifact["error"]`
      (plus a stdout/stderr head) so a FAILURE is debuggable instead of silent.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)
    brief = research_io.read_brief(repo, slug) or ""

    prompt = render("research/research_agent", ticket_title=state.ticket.content.title, brief=brief)

    result = run_research_agent(prompt, repo, ResearchMode.NEW)
    # Parse the envelope regardless of exit code: claude sometimes exits non-zero while
    # still emitting a usable result envelope, and 1–3 min of investigation shouldn't be
    # thrown away over a wrapper-level error. agent_text raises on an is_error envelope
    # (surfacing its subtype) or on stdout that isn't an envelope at all.
    try:
        text = agent_text(result.stdout)
    except RuntimeError as e:
        return _fail(
            state, f"agent run failed (exit {result.returncode}): {e}",
            stdout=result.stdout, stderr=result.stderr,
        )

    report_md, sources = _parse_report(text)
    if not report_md.strip():
        return _fail(
            state, f"agent returned an empty report (exit {result.returncode})",
            stdout=result.stdout, stderr=result.stderr,
        )

    state.artifact = {"report_md": report_md, "sources": sources}
    state.step += 1
    return state


def route_after_research_agent(state: AgentState) -> str:
    """A failed dive goes to END; a valid one to persistence."""
    return "end" if state.status is Status.FAILURE else "save_report"


def _parse_report(text: str) -> tuple[str, list[dict]]:
    """Extract (report_md, sources) from the agent's final message.

    Prefer the `{report_md, sources}` JSON contract; if the model returned prose instead
    (common on long runs), treat the whole message as the report and pull its cited URLs.
    """
    try:
        data = parse_json_block(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("report_md"), str):
        sources = data["sources"] if isinstance(data.get("sources"), list) else []
        return data["report_md"], sources
    return text, [{"url": u} for u in _cited_urls(text)]


def _cited_urls(text: str) -> list[str]:
    """Distinct http(s) URLs in the text, in order, with trailing punctuation trimmed."""
    seen: set[str] = set()
    urls: list[str] = []
    for raw in re.findall(r"https?://[^\s)\]\"'<>]+", text):
        url = raw.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _fail(state: AgentState, reason: str, *, stdout: str = "", stderr: str = "") -> AgentState:
    """Record why the dive failed on `state.artifact` and surface FAILURE (no silent spin)."""
    art: dict = {"error": reason}
    if stdout:
        art["output_head"] = stdout[:2000]
    if stderr:
        art["stderr_tail"] = stderr[-2000:]
    state.artifact = art
    state.status = Status.FAILURE
    return state


def save_report(state: AgentState) -> AgentState:
    """Persist the agent's report + sources to research/<slug>/ (the node writes, not the agent)."""
    assert state.repo_path is not None, "save_report requires repo_path"
    repo, slug = state.repo_path, _slug(state)
    art = state.artifact or {}
    report_md = art.get("report_md")
    if not report_md:
        state.status = Status.FAILURE
        return state
    research_io.write_report(repo, slug, report_md)
    research_io.write_sources(repo, slug, art.get("sources") or [])
    state.step += 1
    return state
