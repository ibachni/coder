"""Tests for the big_plan node (phase-1.md §3.3 / §4).

`run_agent` is monkeypatched so no real `claude` is spawned; the canned stdout
exercises the node's parse → validate → write → state path (and the real big_plan.j2
template + ledger I/O). Failure cases assert nothing is half-applied.
"""

import json
import subprocess
from pathlib import Path

import pytest

import nodes.coding.nodes as cn
from classes import (
    AgentState,
    ChangeStatus,
    Repo,
    Status,
    Ticket,
    TicketContent,
    TicketPriority,
    TicketType,
)
from ledger import changes_path, load_changes, plan_path


def _ticket(tmp_path: Path, *, body: str = "do the thing", goals: str | None = None) -> Ticket:
    content = TicketContent(
        id=42,
        type=TicketType.CODING,
        priority=TicketPriority.HIGH,
        repo=Repo.CODER,
        title="add a retry budget",
        body=body,
        goals=goals,
    )
    return Ticket(content=content, path=tmp_path / "ticket.md")


def _state(tmp_path: Path, **kw: object) -> AgentState:
    return AgentState(
        status=Status.CONT,
        step=0,
        artifact={},
        ticket_id="42",
        ticket=_ticket(tmp_path),
        repo_path=tmp_path,
        **kw,
    )


def _agent_returning(stdout: str, returncode: int = 0):
    """Build a fake run_agent that records the prompt it was handed."""
    calls: dict = {}

    def fake(prompt: str, repo: Path, *, timeout: int = 600) -> subprocess.CompletedProcess:
        calls["prompt"] = prompt
        calls["repo"] = repo
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    return fake, calls


# A valid two-change plan with one question, wrapped in a ```json fence (so the test
# also exercises parse_json_block's fence handling end-to-end).
VALID_PAYLOAD = {
    "plan_md": "## Plan\n\nAdd a retry budget, then consume it in the loop.",
    "changes": [
        {
            "id": "c01",
            "title": "add retry budget to config",
            "intent": "bound retries",
            "soft_loc": 30,
        },
        {
            "id": "c02",
            "title": "consume the budget in the loop",
            "intent": "stop infinite retry",
            "soft_loc": 80,
            "needs_planning": True,
        },
    ],
    "questions": [
        {
            "question": "Should the budget be per-ticket or per-change?",
            "category": "ambiguity",
            "why": "wrong scope changes the data model",
            "options": [
                {
                    "label": "per-change",
                    "pro": "granular",
                    "con": "more state",
                    "recommended": True,
                },
                {"label": "per-ticket", "pro": "simpler", "con": "coarse", "recommended": False},
            ],
        }
    ],
}


def _fenced(payload: dict) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


class TestBigPlanHappyPath:
    def test_writes_plan_changes_and_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake, _ = _agent_returning(_fenced(VALID_PAYLOAD))
        monkeypatch.setattr(cn, "run_agent", fake)

        state = cn.big_plan(_state(tmp_path))

        # plan.md = agent prose + the rendered questions section.
        plan_text = plan_path(tmp_path, "42").read_text()
        assert "## Plan" in plan_text
        assert "## Open questions & decisions" in plan_text
        assert "### Q1: Should the budget be per-ticket or per-change?" in plan_text
        assert "_(recommended)_" in plan_text
        assert state.plan_path == plan_path(tmp_path, "42")

        # changes.json round-trips into PENDING units with ledger paths.
        units = load_changes(tmp_path, "42")
        assert [u.id for u in units] == ["c01", "c02"]
        assert all(u.status is ChangeStatus.PENDING for u in units)
        assert units[0].intent == "bound retries"  # persisted for the approval gate (§3.4)
        assert units[1].needs_planning is True
        assert units[0].ledger_path == tmp_path / ".coder" / "runs" / "42" / "c01"

        # state mirrors what was written.
        assert [u.id for u in state.units] == ["c01", "c02"]
        assert state.questions is not None and len(state.questions) == 1
        assert state.has_open_questions is True
        assert state.status is Status.CONT
        assert state.step == 1

    def test_zero_questions_still_writes_and_flags_clean(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        payload = {**VALID_PAYLOAD, "questions": []}
        fake, _ = _agent_returning(_fenced(payload))
        monkeypatch.setattr(cn, "run_agent", fake)

        state = cn.big_plan(_state(tmp_path))

        assert state.has_open_questions is False
        assert state.questions == []
        plan_text = plan_path(tmp_path, "42").read_text()
        assert "_None — the ticket is workable as written._" in plan_text


class TestBigPlanFailures:
    def _assert_fails_without_writing(self, tmp_path: Path, state: AgentState) -> None:
        result = cn.big_plan(state)
        assert result.status is Status.FAILURE
        assert not plan_path(tmp_path, "42").exists()
        assert not changes_path(tmp_path, "42").exists()
        assert result.units == []

    def test_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake, _ = _agent_returning("", returncode=1)
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_unparseable_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake, _ = _agent_returning("I could not produce a plan, sorry.")
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_empty_changes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake, _ = _agent_returning(_fenced({**VALID_PAYLOAD, "changes": []}))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_duplicate_ids(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        dup = {
            **VALID_PAYLOAD,
            "changes": [
                {"id": "c01", "title": "a", "soft_loc": 10},
                {"id": "c01", "title": "b", "soft_loc": 10},
            ],
        }
        fake, _ = _agent_returning(_fenced(dup))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_unsafe_id(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad = {**VALID_PAYLOAD, "changes": [{"id": "../escape", "title": "a", "soft_loc": 10}]}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_nonpositive_soft_loc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad = {**VALID_PAYLOAD, "changes": [{"id": "c01", "title": "a", "soft_loc": 0}]}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_too_many_questions(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        q = {"question": "q?", "category": "scope", "why": "w"}
        bad = {**VALID_PAYLOAD, "questions": [q] * 7}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_non_dict_question_fails_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A bare-string question would crash the markdown renderer on `.get(...)`;
        # validation must turn it into FAILURE instead (no crash, no writes).
        bad = {**VALID_PAYLOAD, "questions": ["just a string, not an object"]}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_non_dict_option_fails_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        q = {"question": "q?", "category": "scope", "why": "w", "options": ["not an object"]}
        bad = {**VALID_PAYLOAD, "questions": [q]}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_non_list_changes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad = {**VALID_PAYLOAD, "changes": {"id": "c01", "title": "a"}}  # dict, not a list
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_missing_plan_md(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bad = {"changes": VALID_PAYLOAD["changes"], "questions": []}  # no plan_md
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_non_integral_float_soft_loc_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bad = {**VALID_PAYLOAD, "changes": [{"id": "c01", "title": "a", "soft_loc": 150.5}]}
        fake, _ = _agent_returning(_fenced(bad))
        monkeypatch.setattr(cn, "run_agent", fake)
        self._assert_fails_without_writing(tmp_path, _state(tmp_path))

    def test_null_soft_loc_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ok = {**VALID_PAYLOAD, "changes": [{"id": "c01", "title": "a", "soft_loc": None}]}
        fake, _ = _agent_returning(_fenced(ok))
        monkeypatch.setattr(cn, "run_agent", fake)
        state = cn.big_plan(_state(tmp_path))
        assert state.status is Status.CONT
        assert load_changes(tmp_path, "42")[0].soft_loc is None

    def test_integral_float_soft_loc_is_coerced_to_int(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Agents often emit `150.0`; accept it and store a plain int.
        ok = {**VALID_PAYLOAD, "changes": [{"id": "c01", "title": "a", "soft_loc": 150.0}]}
        fake, _ = _agent_returning(_fenced(ok))
        monkeypatch.setattr(cn, "run_agent", fake)
        state = cn.big_plan(_state(tmp_path))
        assert state.status is Status.CONT
        stored = load_changes(tmp_path, "42")[0].soft_loc
        assert stored == 150 and isinstance(stored, int)


class TestBigPlanReviseMode:
    def test_prior_plan_and_feedback_reach_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # First plan on disk (what a prior big_plan run would have written).
        from ledger import write_plan

        write_plan(tmp_path, "42", "## Plan\n\nThe original, rejected approach.")

        revised = {
            "plan_md": "## Plan\n\nThe revised approach.",
            "changes": [{"id": "c01", "title": "do it differently", "soft_loc": 20}],
            "questions": [],
        }
        fake, calls = _agent_returning(_fenced(revised))
        monkeypatch.setattr(cn, "run_agent", fake)

        state = _state(tmp_path, approval={"approved": False, "feedback": "use a different lib"})
        result = cn.big_plan(state)

        # The prompt the agent received carried the prior plan + the reviewer feedback.
        assert "The original, rejected approach." in calls["prompt"]
        assert "use a different lib" in calls["prompt"]
        # The pending plan was overwritten with the revision.
        assert result.status is Status.CONT
        assert "The revised approach." in plan_path(tmp_path, "42").read_text()
        assert "The original, rejected approach." not in plan_path(tmp_path, "42").read_text()

    def test_first_run_prompt_has_no_prior_plan_block(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake, calls = _agent_returning(_fenced(VALID_PAYLOAD))
        monkeypatch.setattr(cn, "run_agent", fake)
        cn.big_plan(_state(tmp_path))
        assert "prior plan.md" not in calls["prompt"]
