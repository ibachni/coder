"""Tests for the approve_plan HITL gate + route_after_approval (phase-1.md §3.4 / §3.5).

`interrupt` is monkeypatched to stand in for a UI resume (approved / rejected /
with-answers). plan.md is built with the real `_render_questions_section`, so the
answer-recording test exercises the exact heading format big_plan emits.
"""

from pathlib import Path

import pytest

import nodes.coding.nodes as cn
from classes import AgentState, Status, WorkUnit
from ledger import plan_path, write_changes, write_plan

Q1 = {
    "question": "Per-change or per-ticket budget?",
    "category": "ambiguity",
    "why": "it changes the data model",
    "options": [
        {"label": "per-change", "pro": "granular", "con": "more state", "recommended": True},
        {"label": "per-ticket", "pro": "simpler", "con": "coarse", "recommended": False},
    ],
}
Q2 = {"question": "Ship behind a flag?", "category": "scope", "why": "rollout risk"}


def _setup(tmp_path: Path, *, questions: list[dict], replans: int = 0) -> AgentState:
    """A repo with a plan.md (incl. the rendered questions section) + matching state."""
    plan_md = "## Plan\n\nDo the thing." + cn._render_questions_section(questions)
    write_plan(tmp_path, "42", plan_md)
    units = [
        WorkUnit(id="c01", title="first", intent="why one", soft_loc=10),
        WorkUnit(id="c02", title="second", intent="why two", soft_loc=20),
    ]
    write_changes(tmp_path, "42", units)
    return AgentState(
        status=Status.CONT,
        step=0,
        artifact={},
        ticket_id="42",
        repo_path=tmp_path,
        units=units,
        questions=questions,
        has_open_questions=bool(questions),
        replans=replans,
    )


def _stub_interrupt(monkeypatch: pytest.MonkeyPatch, resume: object) -> dict:
    """Replace interrupt with a stub that returns `resume` and records its payload."""
    captured: dict = {}

    def fake(payload: object) -> object:
        captured["payload"] = payload
        return resume

    monkeypatch.setattr(cn, "interrupt", fake)
    return captured


class TestApprovePlan:
    def test_approved_stores_approval_and_clears_questions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        state = _setup(tmp_path, questions=[Q1])
        _stub_interrupt(monkeypatch, {"approved": True})

        result = cn.approve_plan(state)

        assert result.approval == {"approved": True}
        assert result.has_open_questions is False
        assert result.status is Status.CONT
        assert result.replans == 0
        assert result.step == 1

    def test_interrupt_payload_shape(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        state = _setup(tmp_path, questions=[Q1])
        cap = _stub_interrupt(monkeypatch, {"approved": True})

        cn.approve_plan(state)

        payload = cap["payload"]
        assert "## Plan" in payload["plan_md"]
        assert "## Open questions & decisions" in payload["plan_md"]
        assert payload["changes"] == [
            {"id": "c01", "title": "first", "intent": "why one", "soft_loc": 10},
            {"id": "c02", "title": "second", "intent": "why two", "soft_loc": 20},
        ]
        assert payload["questions"] == [Q1]

    def test_rejected_under_cap_increments_replans(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        state = _setup(tmp_path, questions=[], replans=1)
        _stub_interrupt(monkeypatch, {"approved": False, "feedback": "use a different lib"})

        result = cn.approve_plan(state)

        assert result.status is Status.CONT
        assert result.replans == 2
        assert result.approval["feedback"] == "use a different lib"

    def test_rejected_at_cap_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        state = _setup(tmp_path, questions=[], replans=cn.MAX_REPLANS)
        _stub_interrupt(monkeypatch, {"approved": False, "feedback": "still wrong"})

        result = cn.approve_plan(state)

        assert result.status is Status.FAILURE
        assert result.replans == cn.MAX_REPLANS  # not pushed past the cap

    def test_answers_recorded_beneath_their_questions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        state = _setup(tmp_path, questions=[Q1, Q2])
        _stub_interrupt(
            monkeypatch,
            {
                "approved": True,
                "answers": [
                    {"id": "q1", "answer": "per-change"},
                    {"id": "q2", "answer": "yes, behind a flag"},
                ],
            },
        )

        cn.approve_plan(state)

        text = plan_path(tmp_path, "42").read_text()
        assert "- _Answer:_ per-change" in text
        assert "- _Answer:_ yes, behind a flag" in text
        # Each answer sits beneath its own question, in order.
        assert text.index("### Q1:") < text.index("per-change") < text.index("### Q2:")
        assert text.index("### Q2:") < text.index("yes, behind a flag")

    def test_record_answers_is_idempotent(self, tmp_path: Path) -> None:
        # A re-entry (crash between write and checkpoint, §8) must not duplicate answers.
        _setup(tmp_path, questions=[Q1, Q2])
        answers = [{"id": "q1", "answer": "per-change"}]
        cn._record_answers(tmp_path, "42", answers)
        cn._record_answers(tmp_path, "42", answers)
        text = plan_path(tmp_path, "42").read_text()
        assert text.count("- _Answer:_ per-change") == 1

    def test_record_answers_collapses_multiline(self, tmp_path: Path) -> None:
        # A multi-line answer can't break the single-line record or inject a heading.
        _setup(tmp_path, questions=[Q1])
        cn._record_answers(
            tmp_path, "42", [{"id": "q1", "answer": "line one\n### Q9: fake\nline two"}]
        )
        text = plan_path(tmp_path, "42").read_text()
        answer_lines = [ln for ln in text.split("\n") if ln.startswith("- _Answer:_")]
        assert answer_lines == ["- _Answer:_ line one ### Q9: fake line two"]
        # The fake heading stayed embedded in the answer line — no standalone Q9 heading.
        assert not any(ln.startswith("### Q9") for ln in text.split("\n"))

    def test_non_dict_resume_treated_as_rejection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        state = _setup(tmp_path, questions=[], replans=0)
        _stub_interrupt(monkeypatch, None)  # malformed resume from the UI

        result = cn.approve_plan(state)

        assert result.approval == {"approved": False}
        assert result.replans == 1  # falls into the bounded re-plan path


def _routing_state(approval: dict | None, status: Status = Status.CONT) -> AgentState:
    return AgentState(status=status, step=0, artifact={}, approval=approval)


class TestRouteAfterApproval:
    def test_approved_routes_to_select(self) -> None:
        assert cn.route_after_approval(_routing_state({"approved": True})) == "select_next_change"

    def test_rejected_routes_to_big_plan(self) -> None:
        assert cn.route_after_approval(_routing_state({"approved": False})) == "big_plan"

    def test_failure_routes_to_end(self) -> None:
        state = _routing_state({"approved": False}, status=Status.FAILURE)
        assert cn.route_after_approval(state) == "end"
