"""Research output-folder I/O — the published knowledge artifacts.

Distinct from the evidence ledger (src/ledger.py / ``.coder/runs/``), which holds raw
process traces. This module writes the *clean, committed* research outputs under
``<repo>/research/<slug>/``:

    report.md        the synthesized, cited answer (continuous: dated insight sections)
    brief.md         framed question + sub-questions + done-when criteria
    sources.jsonl    provenance of cited claims — one JSON row per source
    watchlist.jsonl  sites the continuous mode re-scrapes — one WatchEntry row each
    last_run.json    continuous bookkeeping (ran_at, seen content hashes, ...)

The research agent returns text; the *node* calls these helpers, so the agent needs no
file tools (docs/research/implementation-plan.md §0.7, invariant 7). `watchlist.jsonl`
(input sites) is deliberately kept separate from `sources.jsonl` (claim provenance) —
see the runbook's "Watchlist ≠ sources" invariant.
"""

import json
from pathlib import Path
from typing import Optional

from classes import WatchEntry
from ledger import _safe_segment, write_json, write_text

RESEARCH_ROOT = "research"
REPORT_FILENAME = "report.md"
BRIEF_FILENAME = "brief.md"
SOURCES_FILENAME = "sources.jsonl"
WATCHLIST_FILENAME = "watchlist.jsonl"
LAST_RUN_FILENAME = "last_run.json"


# --- paths (pure; writers create the parent dir on demand) -----------------------


def research_dir(repo: Path, slug: str) -> Path:
    """The output folder for one research ticket: ``<repo>/research/<slug>/``.

    `slug` is validated as a single safe path segment (it originates from a ticket
    title via `slugify`) so it can't escape the research root.
    """
    return repo / RESEARCH_ROOT / _safe_segment(slug)


def report_path(repo: Path, slug: str) -> Path:
    return research_dir(repo, slug) / REPORT_FILENAME


def brief_path(repo: Path, slug: str) -> Path:
    return research_dir(repo, slug) / BRIEF_FILENAME


def sources_path(repo: Path, slug: str) -> Path:
    return research_dir(repo, slug) / SOURCES_FILENAME


def watchlist_path(repo: Path, slug: str) -> Path:
    return research_dir(repo, slug) / WATCHLIST_FILENAME


def last_run_path(repo: Path, slug: str) -> Path:
    return research_dir(repo, slug) / LAST_RUN_FILENAME


# --- jsonl helpers ---------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    return path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- report + brief (markdown) ---------------------------------------------------


def write_report(repo: Path, slug: str, text: str) -> Path:
    return write_text(report_path(repo, slug), text)


def read_report(repo: Path, slug: str) -> Optional[str]:
    p = report_path(repo, slug)
    return p.read_text() if p.exists() else None


def write_brief(repo: Path, slug: str, text: str) -> Path:
    return write_text(brief_path(repo, slug), text)


def read_brief(repo: Path, slug: str) -> Optional[str]:
    p = brief_path(repo, slug)
    return p.read_text() if p.exists() else None


# --- sources (claim provenance) --------------------------------------------------


def write_sources(repo: Path, slug: str, sources: list[dict]) -> Path:
    """Overwrite sources.jsonl (used by the `new`-mode one-shot)."""
    return _write_jsonl(sources_path(repo, slug), sources)


def append_sources(repo: Path, slug: str, sources: list[dict]) -> Path:
    """Append rows to sources.jsonl (used by `continuous` runs)."""
    path = sources_path(repo, slug)
    return _write_jsonl(path, _read_jsonl(path) + list(sources))


def read_sources(repo: Path, slug: str) -> list[dict]:
    return _read_jsonl(sources_path(repo, slug))


# --- watchlist (sites the continuous mode re-scrapes) ----------------------------


def write_watchlist(repo: Path, slug: str, entries: list[WatchEntry]) -> Path:
    return _write_jsonl(watchlist_path(repo, slug), [e.model_dump(mode="json") for e in entries])


def read_watchlist(repo: Path, slug: str) -> list[WatchEntry]:
    return [WatchEntry.model_validate(row) for row in _read_jsonl(watchlist_path(repo, slug))]


# --- last_run (continuous bookkeeping) -------------------------------------------


def write_last_run(repo: Path, slug: str, data: dict) -> Path:
    return write_json(last_run_path(repo, slug), data)


def read_last_run(repo: Path, slug: str) -> dict:
    p = last_run_path(repo, slug)
    return json.loads(p.read_text()) if p.exists() else {}
