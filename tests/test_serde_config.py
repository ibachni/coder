"""The checkpointer must be able to rehydrate the new types after a resume.

A type that lands in AgentState but isn't in `ALLOWED_MSGPACK_MODULES` will fail
to deserialize on resume, so we round-trip the new types through the configured
serializer directly.
"""

from classes import AgentState, ChangeStatus, Status, WorkUnit
from serde_config import ALLOWED_MSGPACK_MODULES, make_serializer


def _roundtrip(obj: object) -> object:
    serde = make_serializer()
    return serde.loads_typed(serde.dumps_typed(obj))


class TestAllowList:
    def test_new_types_are_registered(self) -> None:
        assert ("classes", "WorkUnit") in ALLOWED_MSGPACK_MODULES
        assert ("classes", "ChangeStatus") in ALLOWED_MSGPACK_MODULES


class TestRoundTrip:
    def test_change_status(self) -> None:
        assert _roundtrip(ChangeStatus.DONE) == ChangeStatus.DONE

    def test_work_unit(self) -> None:
        wu = WorkUnit(id="c01", title="x", soft_loc=120, needs_research=True)
        assert _roundtrip(wu) == wu

    def test_agent_state_with_units(self) -> None:
        state = AgentState(
            status=Status.CONT,
            step=2,
            artifact={},
            units=[
                WorkUnit(id="c01", title="a", status=ChangeStatus.DONE),
                WorkUnit(id="c02", title="b"),
            ],
            has_open_questions=True,
            replans=1,
        )
        restored = _roundtrip(state)
        assert restored == state
