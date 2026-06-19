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
from datetime import datetime, timezone

from langgraph.types import interrupt

import research_io
from classes import AgentState, ResearchMode, Status, WatchEntry
from nodes.general.nodes import apply_gate_decision, render_questions_section
from nodes.helpers import agent_text, parse_json_block, run_agent, slugify
from prompt_loader import render
from research_config import DISALLOWED_TOOLS, run_research_agent

MAX_QUESTIONS = 6  # more blocking questions ⇒ the question is under-specified
MAX_REPLANS = 3  # bounded brief-rejection re-frames before we surface FAILURE
MAX_KNOWN_URLS = 50  # cap the dedup hint fed to the continuous agent so the prompt stays bounded
MAX_WATCHLIST = 15  # cap the sites `discover` proposes for the watchlist


def _slug(state: AgentState) -> str:
    """Stable, collision-free output-folder name: ``<ticket-id>-<title-slug>``.

    The id prefix keeps two distinct tickets that share a title in separate
    ``research/<slug>/`` folders, while staying stable across runs of the same ticket —
    which the continuous mode (R2) relies on to find a prior report.
    """
    assert state.ticket is not None, "research nodes require a ticket"
    return f"{state.ticket.content.id}-{slugify(state.ticket.content.title)}"


def classify_research_type(state: AgentState) -> AgentState:
    """Pick the research mode (from the ticket) and establish the output-folder identity.

    The mode is declared on the ticket — `new` (default) | `continuous` | `discover`
    (research plan §0, R2 decision). With stable ticket ids, the `<id>-<slug>` folder is
    stable across runs, so a recurring (`continuous`) ticket re-uses its prior report.
    `plan_path` is the gated doc (brief.md), reusing the field coding uses for plan.md.
    `discover` (R3) routes as `new` until that mode lands.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)
    state.research_mode = state.ticket.content.research_mode or ResearchMode.NEW
    state.plan_path = research_io.brief_path(repo, slug)
    state.report_path = research_io.report_path(repo, slug)
    state.watchlist_path = research_io.watchlist_path(repo, slug)
    state.step += 1
    return state


def route_after_classify(state: AgentState) -> str:
    """Route by research mode: `continuous` updates, `discover` builds a watchlist, else `new`."""
    if state.research_mode is ResearchMode.CONTINUOUS:
        return "continuous"
    if state.research_mode is ResearchMode.DISCOVER:
        return "discover"
    return "new"


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


# === Continuous mode (R2) ========================================================
#
# A recurring question: load the prior report's state, ask one agent for what's NEW
# since the last run (scraping the watchlist + searching), then append only the delta.
# "No new insights" is a valid terminal — we still record the run. Content-hash skipping
# (WatchEntry.last_content_hash) and a light brief re-frame are deferred (plan §8).


def _now() -> str:
    """Today's date (UTC, ISO) for dated insight sections + last_run. Monkeypatched in tests."""
    return datetime.now(timezone.utc).date().isoformat()


def load_prior_report(state: AgentState) -> AgentState:
    """Load the prior report's state for the update agent (watchlist, known sources, last run).

    `known_urls` is the dedup hint handed to the agent; it's capped to the most-recent
    `MAX_KNOWN_URLS` so a long-lived continuous ticket can't grow the prompt without
    bound. Older sources may re-surface — an accepted trade, logged rather than silent.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)
    all_urls = [s["url"] for s in research_io.read_sources(repo, slug) if s.get("url")]
    if len(all_urls) > MAX_KNOWN_URLS:
        print(f"load_prior_report: {len(all_urls)} known sources; feeding the most recent {MAX_KNOWN_URLS}.")
    state.artifact = {
        "watchlist": [e.model_dump(mode="json") for e in research_io.read_watchlist(repo, slug)],
        "known_urls": all_urls[-MAX_KNOWN_URLS:],
        "last_run": research_io.read_last_run(repo, slug).get("ran_at"),
    }
    state.step += 1
    return state


def gather_updates(state: AgentState) -> AgentState:
    """Single agent: scrape the watchlist + search for what's NEW since the last run.

    Returns `{insights_md, sources, stale_urls}` — `insights_md` empty means "nothing new"
    (a valid outcome, handled by `append_insights`). Same robustness as `research_agent`:
    tolerate a non-zero exit with a usable envelope, fall back to prose, and record why on
    failure. Adds its result to `state.artifact` (preserving the prior context).
    """
    assert state.ticket is not None and state.repo_path is not None
    repo = state.repo_path
    art = dict(state.artifact or {})

    prompt = render(
        "research/gather_updates",
        ticket_title=state.ticket.content.title,
        brief=state.ticket.content.body,
        last_run=art.get("last_run"),
        watchlist=art.get("watchlist") or [],
        known_urls=art.get("known_urls") or [],
    )

    result = run_research_agent(prompt, repo, ResearchMode.CONTINUOUS)
    try:
        text = agent_text(result.stdout)
    except RuntimeError as e:
        return _fail(
            state, f"update agent failed (exit {result.returncode}): {e}",
            stdout=result.stdout, stderr=result.stderr,
        )

    insights_md, sources, stale_urls = _parse_updates(text)
    art.update({"insights_md": insights_md, "new_sources": sources, "stale_urls": stale_urls})
    state.artifact = art
    state.step += 1
    return state


def route_after_gather(state: AgentState) -> str:
    """A failed update goes to END; otherwise persist (even an empty delta records the run)."""
    return "end" if state.status is Status.FAILURE else "append_insights"


def _parse_updates(text: str) -> tuple[str, list[dict], list[str]]:
    """Extract (insights_md, sources, stale_urls); fall back to prose like `_parse_report`."""
    try:
        data = parse_json_block(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("insights_md"), str):
        sources = data["sources"] if isinstance(data.get("sources"), list) else []
        stale = data["stale_urls"] if isinstance(data.get("stale_urls"), list) else []
        return data["insights_md"], sources, [str(u) for u in stale]
    return text, [{"url": u} for u in _cited_urls(text)], []


def append_insights(state: AgentState) -> AgentState:
    """Persist the delta: prepend a dated insights section, append new sources, refresh
    the watchlist (stale flags + last_scraped_at) and last_run. An empty delta is logged
    and only the bookkeeping is updated — a valid "no new insights this run" terminal.
    """
    assert state.repo_path is not None, "append_insights requires repo_path"
    repo, slug = state.repo_path, _slug(state)
    art = state.artifact or {}
    insights = (art.get("insights_md") or "").strip()
    new_sources = art.get("new_sources") or []
    stale_urls = set(art.get("stale_urls") or [])
    today = _now()

    if insights:
        prior = research_io.read_report(repo, slug) or ""
        research_io.write_report(repo, slug, f"## Insights — {today}\n\n{insights}\n\n{prior}")
        existing = {s.get("url") for s in research_io.read_sources(repo, slug)}
        fresh = [s for s in new_sources if s.get("url") and s["url"] not in existing]
        if fresh:
            research_io.append_sources(repo, slug, fresh)
    else:
        print(f"append_insights: no new insights for {slug} this run ({today}).")

    # Refresh the watchlist: mark unreachable sites stale, stamp the scrape time.
    watchlist = research_io.read_watchlist(repo, slug)
    for entry in watchlist:
        entry.last_scraped_at = today
        if entry.url in stale_urls:
            entry.status = "stale"
    if watchlist:
        research_io.write_watchlist(repo, slug, watchlist)

    # Dedup memory lives in sources.jsonl (read back as known_urls); last_run only
    # records *when* the question was last refreshed — the recency cutoff for next time.
    research_io.write_last_run(repo, slug, {"ran_at": today})
    state.step += 1
    return state


# === Discover mode (R3) ==========================================================
#
# Given a question, find the sites worth following and emit watchlist.jsonl — the input
# the `continuous` mode (R2) re-scrapes. One agent finds + scores candidates; a HITL gate
# keeps/drops/adds; the node writes the watchlist (empty scrape state so the first
# continuous run treats everything as new) and scaffolds the folder. The light brief
# re-frame from the runbook is folded into the discover prompt for v1.


def discover_sites(state: AgentState) -> AgentState:
    """One agent finds + ranks candidate sites to monitor for the question (search/scrape/map).

    Returns a list of `{url, kind, why, scope}` (the node, not the agent, writes files).
    Same robustness as the other agents: tolerant exit, prose→URL fallback, recorded
    failures. On a prior rejection, the reviewer's feedback is fed back in.
    """
    assert state.ticket is not None and state.repo_path is not None
    repo = state.repo_path

    feedback = None
    if state.approval and not state.approval.get("approved", False):
        feedback = state.approval.get("feedback")

    prompt = render(
        "research/discover_sites",
        ticket_title=state.ticket.content.title,
        question=state.ticket.content.body,
        max_sites=MAX_WATCHLIST,
        feedback=feedback,
    )

    result = run_research_agent(prompt, repo, ResearchMode.DISCOVER)
    try:
        text = agent_text(result.stdout)
    except RuntimeError as e:
        return _fail(
            state, f"discover agent failed (exit {result.returncode}): {e}",
            stdout=result.stdout, stderr=result.stderr,
        )

    candidates = _parse_sites(text)
    if not candidates:
        return _fail(state, "discover found no candidate sites", stdout=result.stdout)

    if len(candidates) > MAX_WATCHLIST:
        print(f"discover_sites: {len(candidates)} candidates; keeping the top {MAX_WATCHLIST}.")
    art = dict(state.artifact or {})
    art["candidates"] = candidates[:MAX_WATCHLIST]
    state.artifact = art
    state.step += 1
    return state


def route_after_discover(state: AgentState) -> str:
    """A failed discovery goes to END; a candidate list to the approval gate."""
    return "end" if state.status is Status.FAILURE else "approve_watchlist"


def _parse_sites(text: str) -> list[dict]:
    """Extract candidate sites, deduped by URL; fall back to bare URLs if the JSON is off."""
    try:
        data = parse_json_block(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("sites"), list):
        out: list[dict] = []
        seen: set[str] = set()
        for s in data["sites"]:
            if isinstance(s, dict) and s.get("url") and s["url"] not in seen:
                seen.add(s["url"])
                out.append(
                    {
                        "url": s["url"],
                        "kind": s.get("kind", ""),
                        "why": s.get("why", ""),
                        "scope": s.get("scope", "single-page"),
                    }
                )
        return out
    return [{"url": u} for u in _cited_urls(text)]  # _cited_urls already dedups


def approve_watchlist(state: AgentState) -> AgentState:
    """HITL gate: the user approves / edits / rejects the proposed watchlist.

    `interrupt()` lives here (monkeypatchable). Resume `{approved, entries?, feedback?}`:
    on approval the final list is `entries` (the user's keep/drop/add) or the candidates
    as-is; on rejection, a bounded re-discovery (then FAILURE). Mirrors the brief gate.
    """
    art = dict(state.artifact or {})
    candidates = art.get("candidates") or []
    resume = interrupt({"candidates": candidates})
    state.approval = resume if isinstance(resume, dict) else {"approved": False}

    if state.approval.get("approved") is True:
        entries = state.approval.get("entries")
        art["watchlist"] = entries if isinstance(entries, list) else candidates
        state.artifact = art
    elif state.replans < MAX_REPLANS:
        state.replans += 1
    else:
        state.status = Status.FAILURE

    state.step += 1
    return state


def route_after_watchlist_approval(state: AgentState) -> str:
    """Approved → write; rejected → re-discover (bounded); exhausted → END."""
    if state.approval and state.approval.get("approved") is True:
        return "write_watchlist"
    if state.status is Status.FAILURE:
        return "end"
    return "discover_sites"


def write_watchlist(state: AgentState) -> AgentState:
    """Persist watchlist.jsonl and scaffold the folder continuous-ready.

    Merges by URL with any existing watchlist: a re-discovered site KEEPS its prior scrape
    state (`last_scraped_at`/`last_content_hash`) and is un-stale'd; a genuinely new site
    is added with empty scrape state (only `added_at`), so the first continuous run treats
    it as new; sites dropped from the approved list are removed. Scaffolds an empty
    report.md/brief.md so the same ticket re-run as `continuous` has a folder to update.

    NB: the folder is keyed `<id>-<title-slug>`, so the discover→continuous handoff needs
    the ticket's title to stay stable across the mode flip (R2 decision, plan §8).
    """
    assert state.ticket is not None and state.repo_path is not None
    repo, slug = state.repo_path, _slug(state)
    art = state.artifact or {}
    raw = art.get("watchlist") or art.get("candidates") or []
    today = _now()

    prior = {e.url: e for e in research_io.read_watchlist(repo, slug)}
    entries: list[WatchEntry] = []
    seen: set[str] = set()
    for e in raw:
        if not (isinstance(e, dict) and e.get("url")) or e["url"] in seen:
            continue
        url = e["url"]
        seen.add(url)
        if url in prior:
            kept = prior[url]
            kept.status = "active"  # re-proposed ⇒ verified alive again
            entries.append(kept)  # preserve last_scraped_at / last_content_hash
        else:
            entries.append(
                WatchEntry(
                    url=url,
                    kind=e.get("kind", ""),
                    why=e.get("why", ""),
                    scope=e.get("scope", "single-page"),
                    added_at=today,
                )
            )
    if not entries:
        return _fail(state, "no valid watchlist entries to write")

    research_io.write_watchlist(repo, slug, entries)
    if research_io.read_report(repo, slug) is None:
        research_io.write_report(
            repo, slug, f"# {state.ticket.content.title}\n\n_Monitoring set up {today}; no findings yet._\n"
        )
    if research_io.read_brief(repo, slug) is None:
        research_io.write_brief(repo, slug, f"## Brief\n\n{state.ticket.content.body}\n")
    state.step += 1
    return state


def route_after_write_watchlist(state: AgentState) -> str:
    """A failed write goes to END; otherwise land the watchlist."""
    return "end" if state.status is Status.FAILURE else "commit_push"
