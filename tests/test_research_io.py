"""Research output-folder I/O (src/research_io.py).

Round-trips for the published artifacts under research/<slug>/, and the invariants
that R0 must hold: missing files read as empty (not errors), the watchlist round-trips
as WatchEntry rows, and the output folder is separate from the .coder/runs ledger.
"""

from pathlib import Path

import research_io as rio
from classes import WatchEntry


class TestMarkdown:
    def test_report_round_trip(self, tmp_path: Path) -> None:
        rio.write_report(tmp_path, "ai-agents", "# Report\n\nFindings.\n")
        assert rio.read_report(tmp_path, "ai-agents") == "# Report\n\nFindings.\n"

    def test_brief_round_trip(self, tmp_path: Path) -> None:
        rio.write_brief(tmp_path, "ai-agents", "## Brief\n")
        assert rio.read_brief(tmp_path, "ai-agents") == "## Brief\n"

    def test_missing_reads_as_none(self, tmp_path: Path) -> None:
        assert rio.read_report(tmp_path, "nope") is None
        assert rio.read_brief(tmp_path, "nope") is None


class TestSources:
    def test_write_and_read(self, tmp_path: Path) -> None:
        rows = [{"url": "https://a.example", "claim_ids": ["c1"]}, {"url": "https://b.example"}]
        rio.write_sources(tmp_path, "topic", rows)
        assert rio.read_sources(tmp_path, "topic") == rows

    def test_append(self, tmp_path: Path) -> None:
        rio.write_sources(tmp_path, "topic", [{"url": "https://a.example"}])
        rio.append_sources(tmp_path, "topic", [{"url": "https://b.example"}])
        assert [r["url"] for r in rio.read_sources(tmp_path, "topic")] == [
            "https://a.example",
            "https://b.example",
        ]

    def test_missing_reads_as_empty(self, tmp_path: Path) -> None:
        assert rio.read_sources(tmp_path, "nope") == []


class TestWatchlist:
    def test_round_trip(self, tmp_path: Path) -> None:
        entries = [
            WatchEntry(url="https://blog.example", kind="blog", why="primary source"),
            WatchEntry(url="https://news.example/feed", scope="rss", status="stale"),
        ]
        rio.write_watchlist(tmp_path, "topic", entries)
        assert rio.read_watchlist(tmp_path, "topic") == entries

    def test_missing_reads_as_empty(self, tmp_path: Path) -> None:
        assert rio.read_watchlist(tmp_path, "nope") == []


class TestLastRun:
    def test_round_trip(self, tmp_path: Path) -> None:
        data = {"ran_at": "2026-06-17", "seen_source_hashes": ["abc", "def"]}
        rio.write_last_run(tmp_path, "topic", data)
        assert rio.read_last_run(tmp_path, "topic") == data

    def test_missing_reads_as_empty_dict(self, tmp_path: Path) -> None:
        assert rio.read_last_run(tmp_path, "nope") == {}


class TestLayout:
    def test_under_research_root_not_ledger(self, tmp_path: Path) -> None:
        rio.write_report(tmp_path, "topic", "x")
        assert (tmp_path / "research" / "topic" / "report.md").exists()
        # The published folder is distinct from the evidence ledger.
        assert not (tmp_path / ".coder").exists()

    def test_unsafe_slug_rejected(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError):
            rio.write_report(tmp_path, "../escape", "x")
