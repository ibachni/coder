"""The shared HITL gate core in general/nodes.py (docs/research/implementation-plan.md §0).

`approve_plan` is covered in test_approve_plan.py; here we prove the *reusable* pieces
work on a research-style doc (a brief.md), so `approve_brief`/`approve_watchlist` can be
built on them in R1/R3. The `interrupt()` lives in each caller's module, so these test
the post-interrupt half (`apply_gate_decision`) by passing the resume directly.
"""

from pathlib import Path

from classes import AgentState, Status
from nodes.general.nodes import apply_gate_decision, record_answers, render_questions_section

Q1 = {
    "question": "How deep should the report go?",
    "category": "scope",
    "why": "controls cost",
    "options": [{"label": "survey", "pro": "fast", "con": "shallow", "recommended": True}],
}


def _state(replans: int = 0) -> AgentState:
    return AgentState(status=Status.CONT, step=0, artifact={}, replans=replans)


class TestRenderQuestionsSection:
    def test_renders_headings_and_options(self) -> None:
        section = render_questions_section([Q1])
        assert "## Open questions & decisions" in section
        assert "### Q1: How deep should the report go?" in section
        assert "**survey** _(recommended)_" in section

    def test_empty_questions_note(self) -> None:
        assert "_None" in render_questions_section([])


class TestRecordAnswers:
    def test_records_beneath_heading(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("## Brief\n" + render_questions_section([Q1]))
        record_answers(brief, [{"id": "q1", "answer": "survey"}])
        assert "- _Answer:_ survey" in brief.read_text()

    def test_idempotent(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("## Brief\n" + render_questions_section([Q1]))
        record_answers(brief, [{"id": "q1", "answer": "survey"}])
        record_answers(brief, [{"id": "q1", "answer": "survey"}])
        assert brief.read_text().count("- _Answer:_ survey") == 1

    def test_missing_doc_is_noop(self, tmp_path: Path) -> None:
        record_answers(tmp_path / "nope.md", [{"id": "q1", "answer": "x"}])  # must not raise


class TestApplyGateDecision:
    def test_approved_clears_questions(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("## Brief\n" + render_questions_section([Q1]))
        state = _state()
        state.has_open_questions = True

        out = apply_gate_decision(state, {"approved": True}, doc_path=brief, max_replans=3)

        assert out.approval == {"approved": True}
        assert out.has_open_questions is False
        assert out.step == 1

    def test_records_answers_into_doc(self, tmp_path: Path) -> None:
        brief = tmp_path / "brief.md"
        brief.write_text("## Brief\n" + render_questions_section([Q1]))
        resume = {"approved": True, "answers": [{"id": "q1", "answer": "deep dive"}]}

        apply_gate_decision(_state(), resume, doc_path=brief, max_replans=3)

        assert "- _Answer:_ deep dive" in brief.read_text()

    def test_rejection_under_cap_increments_replans(self, tmp_path: Path) -> None:
        out = apply_gate_decision(
            _state(replans=1), {"approved": False}, doc_path=tmp_path / "brief.md", max_replans=3
        )
        assert out.status is Status.CONT
        assert out.replans == 2

    def test_rejection_at_cap_fails(self, tmp_path: Path) -> None:
        out = apply_gate_decision(
            _state(replans=3), {"approved": False}, doc_path=tmp_path / "brief.md", max_replans=3
        )
        assert out.status is Status.FAILURE
        assert out.replans == 3

    def test_non_dict_resume_is_rejection(self, tmp_path: Path) -> None:
        out = apply_gate_decision(_state(), None, doc_path=tmp_path / "brief.md", max_replans=3)
        assert out.approval == {"approved": False}
        assert out.replans == 1
