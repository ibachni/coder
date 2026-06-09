"""Tests for select_next_change / route_change / the implement_change stub (§3.6 / §3.7).

These nodes need no agent — they iterate the in-memory `state.units` and (for
implement_change) persist progress to changes.json in a temp repo.
"""

from pathlib import Path

import pytest

import nodes.coding.nodes as cn
from classes import AgentState, ChangeStatus, Status, WorkUnit
from ledger import load_changes


def _state(tmp_path: Path, units: list[WorkUnit]) -> AgentState:
    return AgentState(
        status=Status.CONT,
        step=0,
        artifact={},
        ticket_id="42",
        repo_path=tmp_path,
        units=units,
    )


class TestSelectNextChange:
    def test_picks_first_pending(self, tmp_path: Path) -> None:
        state = _state(
            tmp_path,
            [
                WorkUnit(id="c01", title="a", status=ChangeStatus.DONE),
                WorkUnit(id="c02", title="b"),
                WorkUnit(id="c03", title="c"),
            ],
        )
        result = cn.select_next_change(state)
        assert result.current_unit_id == "c02"

    def test_none_when_all_done(self, tmp_path: Path) -> None:
        state = _state(tmp_path, [WorkUnit(id="c01", title="a", status=ChangeStatus.DONE)])
        result = cn.select_next_change(state)
        assert result.current_unit_id is None

    def test_none_when_no_units(self, tmp_path: Path) -> None:
        result = cn.select_next_change(_state(tmp_path, []))
        assert result.current_unit_id is None


class TestRouteChange:
    def test_routes_to_implement_when_selected(self, tmp_path: Path) -> None:
        state = _state(tmp_path, [WorkUnit(id="c01", title="a")])
        state.current_unit_id = "c01"
        assert cn.route_change(state) == "implement_change"

    def test_routes_to_final_review_when_none(self, tmp_path: Path) -> None:
        state = _state(tmp_path, [])
        state.current_unit_id = None
        assert cn.route_change(state) == "final_review"


class TestImplementChangeStub:
    def test_marks_current_done_and_persists(self, tmp_path: Path) -> None:
        state = _state(tmp_path, [WorkUnit(id="c01", title="a"), WorkUnit(id="c02", title="b")])
        state.current_unit_id = "c01"
        before = state.step

        result = cn.implement_change(state)

        # In-memory: only the selected unit flips to DONE.
        assert next(u for u in result.units if u.id == "c01").status is ChangeStatus.DONE
        assert next(u for u in result.units if u.id == "c02").status is ChangeStatus.PENDING
        assert result.step == before + 1
        # Persisted so a resume sees progress.
        loaded = load_changes(tmp_path, "42")
        assert next(u for u in loaded if u.id == "c01").status is ChangeStatus.DONE
        assert next(u for u in loaded if u.id == "c02").status is ChangeStatus.PENDING

    def test_raises_when_selected_unit_missing(self, tmp_path: Path) -> None:
        # A current_unit_id with no matching unit must surface, not silently no-op
        # (which would leave it PENDING and re-select forever in the graph).
        state = _state(tmp_path, [WorkUnit(id="c01", title="a")])
        state.current_unit_id = "c99"
        with pytest.raises(AssertionError):
            cn.implement_change(state)


class TestOuterLoop:
    def test_drains_all_changes_then_routes_to_final_review(self, tmp_path: Path) -> None:
        # The select → route → implement loop the graph will run (§2): every change
        # ends DONE and the empty queue falls through to final_review.
        state = _state(
            tmp_path,
            [
                WorkUnit(id="c01", title="a"),
                WorkUnit(id="c02", title="b"),
                WorkUnit(id="c03", title="c"),
            ],
        )
        routes = []
        for _ in range(10):  # bounded so a routing bug can't loop forever
            state = cn.select_next_change(state)
            route = cn.route_change(state)
            routes.append(route)
            if route == "final_review":
                break
            state = cn.implement_change(state)

        assert routes == ["implement_change"] * 3 + ["final_review"]
        assert all(u.status is ChangeStatus.DONE for u in state.units)
        assert all(u.status is ChangeStatus.DONE for u in load_changes(tmp_path, "42"))
