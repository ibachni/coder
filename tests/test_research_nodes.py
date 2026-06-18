"""Research `new`-mode nodes (src/nodes/research/nodes.py, R1).

The agent spawns are mocked (no real `claude`): `frame_brief` via `rn.run_agent`,
`research_agent` via `rn.run_research_agent`, the gate via `rn.interrupt`. These assert
the "agents propose, the node commits" contract — valid JSON is written to the output
folder; any breach sets Status.FAILURE and writes nothing.
"""

import json
import subprocess
from pathlib import Path

import pytest

import nodes.research.nodes as rn
import research_io
from classes import (
    AgentState,
    ResearchMode,
    Status,
    Ticket,
    TicketContent,
    TicketPriority,
    TicketType,
)

TITLE = "Future of AI agents"
SLUG = "9-future-of-ai-agents"  # _slug = "<ticket-id>-<title-slug>"

BRIEF = {
    "brief_md": "## Brief\n\nWhat's next for agents?\n\n### Sub-questions\n- Trends — done-when: 3 sources",
    "questions": [],
}
REPORT = {"report_md": "# Agents\n\nThey are improving [src](https://a.example).", "sources": [{"url": "https://a.example"}]}


def _ticket() -> Ticket:
    return Ticket(
        content=TicketContent(
            id=9,
            type=TicketType.RESEARCH,
            priority=TicketPriority.HIGH,
            repo=__import__("classes").Repo.RESEARCH,
            title=TITLE,
            body="Where are AI agents heading?",
        ),
        path=Path("/tmp/t"),
    )


def _state(tmp_path: Path, **kw) -> AgentState:
    kw.setdefault("status", Status.CONT)
    kw.setdefault("artifact", {})
    return AgentState(step=0, ticket_id="9", ticket=_ticket(), repo_path=tmp_path, **kw)


def _completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


def _envelope(body: object, *, is_error: bool = False) -> str:
    """A `--output-format json` envelope whose `result` is the agent's final text."""
    result = body if isinstance(body, str) else json.dumps(body)
    return json.dumps({"is_error": is_error, "result": result})


class TestClassify:
    def test_sets_new_mode_and_paths(self, tmp_path: Path) -> None:
        out = rn.classify_research_type(_state(tmp_path))
        assert out.research_mode is ResearchMode.NEW
        assert out.plan_path == research_io.brief_path(tmp_path, SLUG)
        assert out.report_path == research_io.report_path(tmp_path, SLUG)
        assert out.watchlist_path == research_io.watchlist_path(tmp_path, SLUG)


class TestFrameBrief:
    def test_writes_brief_and_sets_gate_state(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "run_agent", lambda *a, **k: _completed(json.dumps(BRIEF)))
        out = rn.frame_brief(_state(tmp_path))
        assert out.status is Status.CONT
        assert "## Brief" in (research_io.read_brief(tmp_path, SLUG) or "")
        assert out.has_open_questions is False
        assert out.plan_path == research_io.brief_path(tmp_path, SLUG)

    def test_surfaced_questions_set_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        brief = {**BRIEF, "questions": [{"question": "How deep?", "category": "scope", "why": "cost"}]}
        monkeypatch.setattr(rn, "run_agent", lambda *a, **k: _completed(json.dumps(brief)))
        out = rn.frame_brief(_state(tmp_path))
        assert out.has_open_questions is True
        assert "## Open questions & decisions" in (research_io.read_brief(tmp_path, SLUG) or "")

    def test_nonzero_exit_fails_without_writing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "run_agent", lambda *a, **k: _completed("", code=1))
        out = rn.frame_brief(_state(tmp_path))
        assert out.status is Status.FAILURE
        assert research_io.read_brief(tmp_path, SLUG) is None

    def test_bad_json_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "run_agent", lambda *a, **k: _completed("not json"))
        assert rn.frame_brief(_state(tmp_path)).status is Status.FAILURE

    def test_empty_brief_md_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "run_agent", lambda *a, **k: _completed(json.dumps({"brief_md": "  ", "questions": []})))
        assert rn.frame_brief(_state(tmp_path)).status is Status.FAILURE

    def test_framing_denies_write_tools(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Framing is read-only: the agent must not be able to mutate the knowledge repo.
        captured: dict = {}

        def fake_run(*a, **k) -> subprocess.CompletedProcess:
            captured.update(k)
            return _completed(json.dumps(BRIEF))

        monkeypatch.setattr(rn, "run_agent", fake_run)
        rn.frame_brief(_state(tmp_path))
        assert {"Write", "Edit", "Bash"} <= set(captured.get("disallowed_tools") or [])

    def test_revise_mode_feeds_prior_brief_and_feedback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        research_io.write_brief(tmp_path, SLUG, "## Brief\n\nthe earlier brief")
        captured: dict = {}

        def fake_run(prompt, repo, **k) -> subprocess.CompletedProcess:
            captured["prompt"] = prompt
            return _completed(json.dumps(BRIEF))

        monkeypatch.setattr(rn, "run_agent", fake_run)
        rn.frame_brief(_state(tmp_path, approval={"approved": False, "feedback": "go deeper"}))
        assert "the earlier brief" in captured["prompt"]
        assert "go deeper" in captured["prompt"]

    def test_route(self, tmp_path: Path) -> None:
        assert rn.route_after_frame_brief(_state(tmp_path)) == "approve_brief"
        assert rn.route_after_frame_brief(_state(tmp_path, status=Status.FAILURE)) == "end"


class TestApproveBrief:
    def _framed(self, tmp_path: Path, questions: list[dict] | None = None) -> AgentState:
        questions = questions or []
        brief_md = "## Brief\n" + rn.render_questions_section(questions)
        path = research_io.write_brief(tmp_path, SLUG, brief_md)
        return _state(tmp_path, plan_path=path, questions=questions, has_open_questions=bool(questions))

    def test_approved_routes_to_agent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "interrupt", lambda payload: {"approved": True})
        out = rn.approve_brief(self._framed(tmp_path))
        assert out.approval == {"approved": True}
        assert rn.route_after_brief(out) == "research_agent"

    def test_answers_recorded_into_brief(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        q = [{"question": "How deep?", "category": "scope", "why": "cost"}]
        monkeypatch.setattr(
            rn, "interrupt", lambda payload: {"approved": True, "answers": [{"id": "q1", "answer": "deep dive"}]}
        )
        rn.approve_brief(self._framed(tmp_path, q))
        assert "- _Answer:_ deep dive" in (research_io.read_brief(tmp_path, SLUG) or "")

    def test_rejected_routes_back_to_frame(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "interrupt", lambda payload: {"approved": False, "feedback": "narrower"})
        out = rn.approve_brief(self._framed(tmp_path))
        assert out.replans == 1
        assert rn.route_after_brief(out) == "frame_brief"


class TestResearchAgent:
    def _approved(self, tmp_path: Path) -> AgentState:
        research_io.write_brief(tmp_path, SLUG, "## Brief\n\n- Trends — done-when: 3 sources")
        return _state(tmp_path, approval={"approved": True})

    def test_stashes_parsed_report(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(rn, "run_research_agent", lambda *a, **k: _completed(_envelope(REPORT)))
        out = rn.research_agent(self._approved(tmp_path))
        assert out.status is Status.CONT
        assert out.artifact["report_md"].startswith("# Agents")
        assert out.artifact["sources"] == [{"url": "https://a.example"}]
        assert rn.route_after_research_agent(out) == "save_report"

    def test_prose_fallback_recovers_report(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Long runs sometimes drift from JSON and return markdown — don't lose the work.
        prose = "# Findings\n\nAgents advance fast, see https://x.example/post and https://y.example."
        monkeypatch.setattr(rn, "run_research_agent", lambda *a, **k: _completed(_envelope(prose)))
        out = rn.research_agent(self._approved(tmp_path))
        assert out.status is Status.CONT
        assert out.artifact["report_md"] == prose
        urls = {s["url"] for s in out.artifact["sources"]}
        assert urls == {"https://x.example/post", "https://y.example"}

    def test_agent_error_envelope_fails_with_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            rn, "run_research_agent", lambda *a, **k: _completed(_envelope("boom", is_error=True))
        )
        out = rn.research_agent(self._approved(tmp_path))
        assert out.status is Status.FAILURE
        assert "boom" in out.artifact["error"]  # the reason is surfaced, not swallowed
        assert rn.route_after_research_agent(out) == "end"

    def test_nonzero_exit_without_envelope_fails_with_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(rn, "run_research_agent", lambda *a, **k: _completed("", code=1))
        out = rn.research_agent(self._approved(tmp_path))
        assert out.status is Status.FAILURE
        assert "exit 1" in out.artifact["error"]

    def test_tolerates_nonzero_exit_with_good_envelope(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # claude can exit non-zero yet still emit a usable result — don't discard the work.
        monkeypatch.setattr(
            rn, "run_research_agent", lambda *a, **k: _completed(_envelope(REPORT), code=1)
        )
        out = rn.research_agent(self._approved(tmp_path))
        assert out.status is Status.CONT
        assert out.artifact["report_md"].startswith("# Agents")

    def test_empty_report_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            rn, "run_research_agent", lambda *a, **k: _completed(_envelope({"report_md": " ", "sources": []}))
        )
        assert rn.research_agent(self._approved(tmp_path)).status is Status.FAILURE


class TestSaveReport:
    def test_writes_report_and_sources(self, tmp_path: Path) -> None:
        state = _state(tmp_path, artifact={"report_md": "# R\n\nbody", "sources": [{"url": "https://a"}]})
        out = rn.save_report(state)
        assert out.status is Status.CONT
        assert research_io.read_report(tmp_path, SLUG) == "# R\n\nbody"
        assert research_io.read_sources(tmp_path, SLUG) == [{"url": "https://a"}]

    def test_missing_report_fails(self, tmp_path: Path) -> None:
        assert rn.save_report(_state(tmp_path, artifact={})).status is Status.FAILURE
