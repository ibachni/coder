"""Tests for the evidence-ledger I/O in src/ledger.py."""

import json
from pathlib import Path

import pytest

from classes import ChangeStatus, WorkUnit
from ledger import (
    LEDGER_ROOT,
    PLAN_FILENAME,
    append_jsonl,
    change_dir,
    changes_path,
    load_changes,
    read_json,
    read_ledger_slice,
    ticket_dir,
    write_changes,
    write_json,
    write_plan,
    write_text,
)


class TestPaths:
    def test_ticket_dir_created_under_ledger_root(self, tmp_path: Path) -> None:
        d = ticket_dir(tmp_path, "42")
        assert d == tmp_path / LEDGER_ROOT / "42"
        assert d.is_dir()

    def test_change_dir_nested_under_ticket(self, tmp_path: Path) -> None:
        d = change_dir(tmp_path, "42", "c01")
        assert d == tmp_path / LEDGER_ROOT / "42" / "c01"
        assert d.is_dir()

    def test_rejects_path_separator_in_ticket_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            ticket_dir(tmp_path, "a/b")

    def test_rejects_parent_traversal_in_ticket_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            ticket_dir(tmp_path, "..")

    def test_rejects_traversal_in_change_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            change_dir(tmp_path, "42", "../escape")


class TestWriteRead:
    def test_json_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "x.json"
        write_json(p, {"a": 1, "b": ["c"]})
        assert read_json(p) == {"a": 1, "b": ["c"]}

    def test_write_text_creates_parents(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "b" / "c.md"
        write_text(p, "hello")
        assert p.read_text() == "hello"

    def test_write_json_serializes_non_native_via_default_str(self, tmp_path: Path) -> None:
        p = tmp_path / "x.json"
        write_json(p, {"path": tmp_path})  # Path is not JSON-native
        assert read_json(p)["path"] == str(tmp_path)

    def test_append_jsonl_one_record_per_line(self, tmp_path: Path) -> None:
        p = tmp_path / "attempts.jsonl"
        append_jsonl(p, {"n": 1})
        append_jsonl(p, {"n": 2})
        records = [json.loads(line) for line in p.read_text().splitlines()]
        assert records == [{"n": 1}, {"n": 2}]


class TestPlanAndChanges:
    def test_write_plan_lands_under_ticket_dir(self, tmp_path: Path) -> None:
        p = write_plan(tmp_path, "42", "## Plan\nbody")
        assert p == tmp_path / LEDGER_ROOT / "42" / PLAN_FILENAME
        assert p.read_text() == "## Plan\nbody"

    def test_changes_roundtrip_preserves_fields(self, tmp_path: Path) -> None:
        units = [
            WorkUnit(id="c01", title="first", soft_loc=120, needs_research=True),
            WorkUnit(
                id="c02",
                title="second",
                status=ChangeStatus.DONE,
                ledger_path=tmp_path / ".coder" / "runs" / "42" / "c02",
            ),
        ]
        write_changes(tmp_path, "42", units)
        loaded = load_changes(tmp_path, "42")
        assert loaded == units

    def test_status_enum_survives_roundtrip(self, tmp_path: Path) -> None:
        write_changes(tmp_path, "42", [WorkUnit(id="c01", title="x", status=ChangeStatus.DONE)])
        loaded = load_changes(tmp_path, "42")
        assert loaded[0].status is ChangeStatus.DONE  # not the string "done"

    def test_envelope_shape(self, tmp_path: Path) -> None:
        write_changes(tmp_path, "42", [WorkUnit(id="c01", title="x")])
        data = read_json(changes_path(tmp_path, "42"))
        assert set(data) == {"version", "changes"}
        assert isinstance(data["changes"], list)

    def test_version_starts_at_one_and_bumps_on_rewrite(self, tmp_path: Path) -> None:
        units = [WorkUnit(id="c01", title="x")]
        write_changes(tmp_path, "42", units)
        assert read_json(changes_path(tmp_path, "42"))["version"] == 1
        # Rewrite (e.g. implement_change marking a unit DONE) bumps the counter.
        units[0].status = ChangeStatus.DONE
        write_changes(tmp_path, "42", units)
        assert read_json(changes_path(tmp_path, "42"))["version"] == 2


class TestReadLedgerSlice:
    def test_default_returns_tail_capped_to_max_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        write_text(p, "\n".join(str(i) for i in range(500)))
        out = read_ledger_slice(p, max_lines=50)
        lines = out.splitlines()
        assert len(lines) == 50
        assert lines[-1] == "499"  # tail
        assert lines[0] == "450"

    def test_explicit_range_is_one_indexed_inclusive_start(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        write_text(p, "\n".join(str(i) for i in range(100)))
        out = read_ledger_slice(p, start=10, end=12)
        assert out.splitlines() == ["9", "10", "11"]

    def test_char_cap_truncates(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        write_text(p, "x" * 10000)
        out = read_ledger_slice(p, max_chars=100)
        assert len(out) <= 100

    def test_short_file_returned_whole(self, tmp_path: Path) -> None:
        p = tmp_path / "small.log"
        write_text(p, "only line")
        assert read_ledger_slice(p) == "only line"
