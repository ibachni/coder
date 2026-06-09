"""Tests for the Phase 0 data model additions in src/classes.py."""

from classes import (
    AgentState,
    ChangeStatus,
    Status,
    TicketContent,
    TicketPriority,
    TicketType,
    WorkUnit,
)


class TestChangeStatus:
    def test_values(self) -> None:
        assert ChangeStatus.PENDING.value == "pending"
        assert ChangeStatus.DONE.value == "done"
        assert ChangeStatus.FAILED.value == "failed"


class TestWorkUnit:
    def test_minimal_defaults(self) -> None:
        wu = WorkUnit(id="c01", title="add a field")
        assert wu.status is ChangeStatus.PENDING
        assert wu.intent == ""
        assert wu.dod == {}
        assert wu.soft_loc is None
        assert wu.needs_research is False
        assert wu.needs_planning is False
        assert wu.ledger_path is None
        assert wu.inner_plan_path is None

    def test_full_construction(self) -> None:
        wu = WorkUnit(
            id="c02",
            title="x",
            dod={"new_tests": ["t.py::a"]},
            status=ChangeStatus.DONE,
            soft_loc=120,
            needs_research=True,
            needs_planning=True,
        )
        assert wu.soft_loc == 120
        assert wu.needs_research is True
        assert wu.status is ChangeStatus.DONE
        assert wu.dod["new_tests"] == ["t.py::a"]

    def test_roundtrips_through_model_dump(self) -> None:
        wu = WorkUnit(id="c01", title="x", soft_loc=42)
        assert WorkUnit.model_validate(wu.model_dump()) == wu

    def test_default_dod_not_shared_between_instances(self) -> None:
        a = WorkUnit(id="a", title="a")
        b = WorkUnit(id="b", title="b")
        a.dod["k"] = 1
        assert b.dod == {}


class TestTicketContentGoals:
    def test_goals_defaults_to_none(self) -> None:
        content = TicketContent.model_validate(
            {"id": 1, "type": "coding", "priority": "4", "repo": "coder", "title": "t", "body": "b"}
        )
        assert content.goals is None

    def test_goals_accepted_when_present(self) -> None:
        content = TicketContent.model_validate(
            {
                "id": 1,
                "type": "coding",
                "priority": "4",
                "repo": "coder",
                "title": "t",
                "body": "b",
                "goals": "1. correctness 2. speed; non-goal: UI",
            }
        )
        assert content.goals is not None
        assert "correctness" in content.goals


class TestAgentStateFields:
    def test_new_fields_have_defaults(self) -> None:
        state = AgentState(status=Status.CONT, step=0, artifact={})
        assert state.units == []
        assert state.plan_path is None
        assert state.current_unit_id is None
        assert state.has_open_questions is False
        assert state.attempts == 0
        assert state.replans == 0
        assert state.autonomy == 1

    def test_units_carry_work_units(self) -> None:
        state = AgentState(
            status=Status.CONT,
            step=0,
            artifact={},
            units=[WorkUnit(id="c01", title="x")],
        )
        assert state.units[0].id == "c01"

    def test_units_default_not_shared_between_instances(self) -> None:
        a = AgentState(status=Status.CONT, step=0, artifact={})
        b = AgentState(status=Status.CONT, step=0, artifact={})
        a.units.append(WorkUnit(id="c01", title="x"))
        assert b.units == []

    def test_questions_accepts_nested_options(self) -> None:
        state = AgentState(
            status=Status.CONT,
            step=0,
            artifact={},
            questions=[
                {
                    "question": "q?",
                    "options": [{"label": "a", "pro": "p", "con": "c", "recommended": True}],
                }
            ],
        )
        assert state.questions is not None
        assert state.questions[0]["options"][0]["recommended"] is True

    def test_approval_defaults_to_none(self) -> None:
        state = AgentState(status=Status.CONT, step=0, artifact={})
        assert state.approval is None

    def test_approval_accepts_resume_payload(self) -> None:
        state = AgentState(
            status=Status.CONT,
            step=0,
            artifact={},
            approval={
                "approved": True,
                "answers": [{"id": "q1", "answer": "yes"}],
                "feedback": None,
            },
        )
        assert state.approval is not None
        assert state.approval["approved"] is True
        assert state.approval["answers"][0]["answer"] == "yes"

    def test_priority_still_coerces(self) -> None:
        # Guard that extending the model didn't disturb existing coercion.
        content = TicketContent.model_validate(
            {
                "id": 1,
                "type": "research",
                "priority": "3",
                "repo": "research",
                "title": "t",
                "body": "b",
            }
        )
        assert content.priority is TicketPriority.HIGH
        assert content.type is TicketType.RESEARCH
