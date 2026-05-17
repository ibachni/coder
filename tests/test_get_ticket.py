"""Tests for src/tools/get_ticket.py."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools import get_ticket as gt
from tools.get_ticket import (
    Repo,
    Ticket,
    TicketContent,
    TicketPriority,
    TicketType,
    _get_ticket_priority,
    _parse_ticket,
    _resolve_id,
    get_open_ticket,
    get_ticket,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tickets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint module-level TICKETS_DIR at an empty tmp dir for hermetic tests."""
    d = tmp_path / "open"
    d.mkdir()
    monkeypatch.setattr(gt, "TICKETS_DIR", d)
    return d


def _write_ticket(
    dir_path: Path,
    *,
    id: int,
    ticket_type: str = "research",
    priority: str | int = "3",
    repo: str = "research",
    title: str = "title",
    body: str = "body",
) -> Path:
    path = dir_path / f"ticket_{id:04d}.json"
    payload = {
        "id": id,
        "type": ticket_type,
        "priority": priority,
        "repo": repo,
        "title": title,
        "body": body,
    }
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# _resolve_id
# ---------------------------------------------------------------------------


class TestResolveId:
    def test_pads_to_four_digits(self, tickets_dir: Path) -> None:
        path = _resolve_id(1)
        assert path.name == "ticket_0001.json"

    def test_pads_two_digit_id(self, tickets_dir: Path) -> None:
        assert _resolve_id(42).name == "ticket_0042.json"

    def test_four_digit_id_unchanged(self, tickets_dir: Path) -> None:
        assert _resolve_id(9999).name == "ticket_9999.json"

    def test_id_exceeding_four_digits_not_truncated(self, tickets_dir: Path) -> None:
        # zero-pad format does not truncate; width is a minimum
        assert _resolve_id(10000).name == "ticket_10000.json"

    def test_id_zero(self, tickets_dir: Path) -> None:
        assert _resolve_id(0).name == "ticket_0000.json"

    def test_path_is_under_tickets_dir(self, tickets_dir: Path) -> None:
        path = _resolve_id(7)
        assert path.parent == tickets_dir

    def test_string_id_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="Wrong id type"):
            _resolve_id("1")  # type: ignore[arg-type]

    def test_float_id_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            _resolve_id(1.0)  # type: ignore[arg-type]

    def test_none_id_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            _resolve_id(None)  # type: ignore[arg-type]

    def test_bool_id_rejected_by_strict_type_check(self) -> None:
        # bool is a subclass of int, but the check uses `type(id) is not int`
        # (not isinstance), so bool values are rejected.
        with pytest.raises(TypeError):
            _resolve_id(True)  # type: ignore[arg-type]

    def test_intenum_id_rejected_by_strict_type_check(self) -> None:
        # IntEnum is also a subclass of int but `type(x) is int` is False.
        with pytest.raises(TypeError):
            _resolve_id(TicketPriority.HIGH)  # type: ignore[arg-type]

    def test_negative_id_pads_with_sign(self, tickets_dir: Path) -> None:
        # f"{-1:04d}" → "-001"; sign consumes a pad slot. Locks in current
        # (somewhat surprising) behavior — there is no input validation for
        # negative ids.
        path = _resolve_id(-1)
        assert path.name == "ticket_-001.json"
        assert path.parent == tickets_dir

    def test_returns_path_even_when_file_missing(self, tickets_dir: Path) -> None:
        # _resolve_id is a pure path builder; it never touches the filesystem.
        path = _resolve_id(12345)
        assert path.parent == tickets_dir
        assert not path.exists()
        assert path.name == "ticket_12345.json"


# ---------------------------------------------------------------------------
# _parse_ticket
# ---------------------------------------------------------------------------


class TestParseTicket:
    def test_returns_ticket_with_content_and_path(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, title="hello", body="world")
        ticket = _parse_ticket(path)

        assert isinstance(ticket, Ticket)
        assert ticket.path == path
        assert ticket.content.id == 1
        assert ticket.content.title == "hello"
        assert ticket.content.body == "world"

    def test_priority_string_coerced_to_intenum(self, tickets_dir: Path) -> None:
        # JSON has "priority": "3"; pydantic coerces to TicketPriority.HIGH
        path = _write_ticket(tickets_dir, id=1, priority="3")
        ticket = _parse_ticket(path)
        assert ticket.content.priority is TicketPriority.HIGH

    def test_priority_int_accepted(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority=4)
        ticket = _parse_ticket(path)
        assert ticket.content.priority is TicketPriority.HIGHEST

    def test_type_field_parsed(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, ticket_type="coding")
        ticket = _parse_ticket(path)
        assert ticket.content.type is TicketType.CODING

    def test_repo_field_parsed(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, repo="coder")
        ticket = _parse_ticket(path)
        assert ticket.content.repo is Repo.CODER

    def test_invalid_type_value_raises_validation_error(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, ticket_type="bogus")
        with pytest.raises(ValidationError):
            _parse_ticket(path)

    def test_invalid_repo_value_raises_validation_error(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, repo="bogus")
        with pytest.raises(ValidationError):
            _parse_ticket(path)

    def test_invalid_priority_raises_validation_error(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority="99")
        with pytest.raises(ValidationError):
            _parse_ticket(path)

    def test_missing_field_raises_validation_error(self, tickets_dir: Path) -> None:
        path = tickets_dir / "ticket_0001.json"
        path.write_text(json.dumps({"id": 1, "type": "research", "priority": "3"}))
        with pytest.raises(ValidationError):
            _parse_ticket(path)

    def test_malformed_json_raises(self, tickets_dir: Path) -> None:
        path = tickets_dir / "ticket_0001.json"
        path.write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            _parse_ticket(path)

    def test_missing_file_raises_file_not_found(self, tickets_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _parse_ticket(tickets_dir / "does_not_exist.json")

    def test_empty_file_raises_json_decode_error(self, tickets_dir: Path) -> None:
        path = tickets_dir / "ticket_0001.json"
        path.write_text("")
        with pytest.raises(json.JSONDecodeError):
            _parse_ticket(path)

    def test_json_array_root_raises_validation_error(self, tickets_dir: Path) -> None:
        # Top-level JSON must be an object; an array fails pydantic validation.
        path = tickets_dir / "ticket_0001.json"
        path.write_text("[1, 2, 3]")
        with pytest.raises(ValidationError):
            _parse_ticket(path)

    def test_unknown_extra_fields_ignored(self, tickets_dir: Path) -> None:
        # Pydantic v2 defaults to extra="ignore"; lock in current behavior.
        path = tickets_dir / "ticket_0001.json"
        path.write_text(
            json.dumps(
                {
                    "id": 1,
                    "type": "research",
                    "priority": "3",
                    "repo": "research",
                    "title": "t",
                    "body": "b",
                    "unexpected_field": "surprise",
                }
            )
        )
        ticket = _parse_ticket(path)
        assert ticket.content.id == 1
        assert not hasattr(ticket.content, "unexpected_field")


# ---------------------------------------------------------------------------
# _get_ticket_priority
# ---------------------------------------------------------------------------


class TestGetTicketPriority:
    def test_returns_priority_high(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority="3")
        assert _get_ticket_priority(path) is TicketPriority.HIGH

    def test_returns_priority_highest(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority="4")
        assert _get_ticket_priority(path) is TicketPriority.HIGHEST

    def test_returns_priority_medium(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority="2")
        assert _get_ticket_priority(path) is TicketPriority.MEDIUM

    def test_returns_priority_low(self, tickets_dir: Path) -> None:
        path = _write_ticket(tickets_dir, id=1, priority="1")
        assert _get_ticket_priority(path) is TicketPriority.LOW

    def test_invalid_file_raises(self, tickets_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _get_ticket_priority(tickets_dir / "nope.json")


# ---------------------------------------------------------------------------
# get_open_ticket
# ---------------------------------------------------------------------------


class TestGetOpenTicket:
    def test_by_id_returns_matching_ticket(self, tickets_dir: Path) -> None:
        _write_ticket(tickets_dir, id=1, title="one")
        _write_ticket(tickets_dir, id=2, title="two")

        ticket = get_open_ticket(id=2)
        assert ticket.content.id == 2
        assert ticket.content.title == "two"

    def test_by_missing_id_raises_file_not_found(self, tickets_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            get_open_ticket(id=999)

    def test_no_id_picks_highest_priority(self, tickets_dir: Path) -> None:
        _write_ticket(tickets_dir, id=1, priority="1", title="low")
        _write_ticket(tickets_dir, id=2, priority="4", title="highest")
        _write_ticket(tickets_dir, id=3, priority="2", title="medium")

        ticket = get_open_ticket()
        assert ticket.content.id == 2
        assert ticket.content.priority is TicketPriority.HIGHEST

    def test_no_id_single_ticket_returned(self, tickets_dir: Path) -> None:
        _write_ticket(tickets_dir, id=5, priority="2", title="only")
        ticket = get_open_ticket()
        assert ticket.content.id == 5

    def test_no_id_empty_dir_raises(self, tickets_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No open tickets"):
            get_open_ticket()

    def test_glob_ignores_non_matching_files(self, tickets_dir: Path) -> None:
        # Files not matching ticket_*.json must be ignored
        (tickets_dir / "readme.txt").write_text("ignore me")
        (tickets_dir / "notes.json").write_text("{}")
        _write_ticket(tickets_dir, id=1, priority="2")

        ticket = get_open_ticket()
        assert ticket.content.id == 1

    def test_id_zero_resolves_to_ticket_0000(self, tickets_dir: Path) -> None:
        """Guards against regression to `if id:` (which treats 0 as falsy).

        A higher-priority sibling is included so that, if the truthy-check
        regression returns, the 'highest priority' branch picks the wrong
        ticket and this test fails loudly.
        """
        _write_ticket(tickets_dir, id=0, title="zero", priority="1")
        _write_ticket(tickets_dir, id=1, title="other", priority="4")

        ticket = get_open_ticket(id=0)
        assert ticket.content.id == 0
        assert ticket.content.title == "zero"

    def test_non_int_id_raises_type_error(self) -> None:
        # Truthy non-int ids bubble TypeError up from _resolve_id.
        with pytest.raises(TypeError):
            get_open_ticket(id="1")  # type: ignore[arg-type]

    def test_no_id_tie_returns_some_max_priority_ticket(self, tickets_dir: Path) -> None:
        """When multiple tickets share the max priority, glob/max order is
        filesystem-dependent. Lock in the weaker guarantee: the returned
        ticket has the max priority (but which one is unspecified)."""
        _write_ticket(tickets_dir, id=1, priority="4", title="a")
        _write_ticket(tickets_dir, id=2, priority="4", title="b")
        _write_ticket(tickets_dir, id=3, priority="2", title="loser")

        ticket = get_open_ticket()
        assert ticket.content.priority is TicketPriority.HIGHEST
        assert ticket.content.id in {1, 2}

    def test_no_id_ignores_subdirectories(self, tickets_dir: Path) -> None:
        # glob("ticket_*.json") is non-recursive; nested files are ignored.
        nested = tickets_dir / "archive"
        nested.mkdir()
        _write_ticket(nested, id=99, priority="4", title="nested-highest")
        _write_ticket(tickets_dir, id=1, priority="2", title="top-level")

        ticket = get_open_ticket()
        assert ticket.content.id == 1


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------


class TestGetTicket:
    def test_returns_ticket_by_id(self, tickets_dir: Path) -> None:
        _write_ticket(tickets_dir, id=42, title="forty-two")
        ticket = get_ticket(42)
        assert ticket.content.id == 42
        assert ticket.content.title == "forty-two"

    def test_missing_id_raises_file_not_found(self, tickets_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            get_ticket(999)

    def test_id_zero_resolves_to_ticket_0000(self, tickets_dir: Path) -> None:
        # Unlike get_open_ticket, get_ticket has no falsy check, so id=0 works.
        _write_ticket(tickets_dir, id=0, title="zero")
        ticket = get_ticket(0)
        assert ticket.content.id == 0
        assert ticket.content.title == "zero"

    def test_non_int_id_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            get_ticket("1")  # type: ignore[arg-type]

    def test_none_id_raises_type_error(self) -> None:
        # Unlike get_open_ticket, get_ticket has no `id is not None` short-circuit;
        # passing None goes straight to _resolve_id which rejects it.
        with pytest.raises(TypeError):
            get_ticket(None)  # type: ignore[arg-type]

    def test_returned_path_points_at_file(self, tickets_dir: Path) -> None:
        written = _write_ticket(tickets_dir, id=3)
        ticket = get_ticket(3)
        assert ticket.path == written
        assert ticket.path.exists()


# ---------------------------------------------------------------------------
# Enum sanity checks (lock in numeric values)
# ---------------------------------------------------------------------------


class TestEnums:
    def test_priority_ordering(self) -> None:
        assert TicketPriority.HIGHEST > TicketPriority.HIGH
        assert TicketPriority.HIGH > TicketPriority.MEDIUM
        assert TicketPriority.MEDIUM > TicketPriority.LOW

    def test_priority_values(self) -> None:
        assert TicketPriority.HIGHEST == 4
        assert TicketPriority.HIGH == 3
        assert TicketPriority.MEDIUM == 2
        assert TicketPriority.LOW == 1

    def test_ticket_type_values(self) -> None:
        assert TicketType.RESEARCH.value == "research"
        assert TicketType.CODING.value == "coding"

    def test_repo_values(self) -> None:
        assert Repo.CODER.value == "coder"
        assert Repo.RESEARCH.value == "research"


# ---------------------------------------------------------------------------
# TicketContent (pydantic model)
# ---------------------------------------------------------------------------


class TestTicketContent:
    def test_valid_payload_parses(self) -> None:
        content = TicketContent.model_validate(
            {
                "id": 1,
                "type": "coding",
                "priority": "4",
                "repo": "coder",
                "title": "t",
                "body": "b",
            }
        )
        assert content.id == 1
        assert content.type is TicketType.CODING
        assert content.priority is TicketPriority.HIGHEST
        assert content.repo is Repo.CODER

    def test_id_must_be_int(self) -> None:
        with pytest.raises(ValidationError):
            TicketContent.model_validate(
                {
                    "id": "not-an-int",
                    "type": "coding",
                    "priority": "4",
                    "repo": "coder",
                    "title": "t",
                    "body": "b",
                }
            )
