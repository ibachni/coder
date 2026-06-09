"""Two-tier evidence ledger I/O. See docs/evidence-ledger.md.

Tier 1 (disk): full-fidelity artifacts under ``<repo>/.coder/runs/<ticket>/[<change>/]``.
Tier 2 (context): callers assemble small distilled payloads; this module provides the
bounded read helper (`read_ledger_slice`) that lets distillation stay aggressive
without ever losing access to ground truth.
"""

import json
from pathlib import Path
from typing import Any

from classes import WorkUnit

LEDGER_ROOT = ".coder/runs"
DEFAULT_MAX_LINES = 100
DEFAULT_MAX_CHARS = 8000
PLAN_FILENAME = "plan.md"
CHANGES_FILENAME = "changes.json"


# --- path helpers (create the dir on access) -------------------------------------


def _safe_segment(value: str) -> str:
    """Reject ids that would escape the ledger root.

    ticket/change ids become path segments; once they originate from plan output
    (not just internal ints) a `..` or `a/b` could write outside `.coder/runs/`.
    """
    s = str(value)
    if s in ("", ".", "..") or s != Path(s).name:
        raise ValueError(f"unsafe ledger path segment: {value!r}")
    return s


def ticket_dir(repo: Path, ticket_id: str) -> Path:
    d = repo / LEDGER_ROOT / _safe_segment(ticket_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def change_dir(repo: Path, ticket_id: str, change_id: str) -> Path:
    d = ticket_dir(repo, ticket_id) / _safe_segment(change_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- write / read ----------------------------------------------------------------


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_text(path, json.dumps(data, indent=2, default=str))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def append_jsonl(path: Path, record: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return path


# --- plan + changes (the ticket-tier outer-loop artifacts) -----------------------
#
# `big_plan` writes `plan.md` (human-facing, gated) and `changes.json` (the ordered
# work units the outer loop iterates). The `implement_change` loop rewrites
# `changes.json` as units move PENDING → DONE so a resume sees real progress. Both
# producers go through here so the path + schema live in one place.


def changes_path(repo: Path, ticket_id: str) -> Path:
    return ticket_dir(repo, ticket_id) / CHANGES_FILENAME


def plan_path(repo: Path, ticket_id: str) -> Path:
    return ticket_dir(repo, ticket_id) / PLAN_FILENAME


def write_plan(repo: Path, ticket_id: str, plan_md: str) -> Path:
    """Persist the high-level `plan.md` under the ticket tier."""
    return write_text(plan_path(repo, ticket_id), plan_md)


def write_changes(repo: Path, ticket_id: str, units: list[WorkUnit]) -> Path:
    """Serialize the ordered work units to `changes.json`.

    Shape: ``{"version": N, "changes": [<WorkUnit>, ...]}``. `version` is a monotonic
    write counter (1 on first write, bumped on every rewrite) so an observer can tell
    a re-plan / progress update from the original plan. Units are dumped in JSON mode
    so enums (`status`) and `Path`s round-trip cleanly back through `load_changes`.
    """
    path = changes_path(repo, ticket_id)
    prior = read_json(path).get("version", 0) if path.exists() else 0
    data = {"version": prior + 1, "changes": [u.model_dump(mode="json") for u in units]}
    return write_json(path, data)


def load_changes(repo: Path, ticket_id: str) -> list[WorkUnit]:
    """Read `changes.json` back into `WorkUnit`s (inverse of `write_changes`)."""
    data = read_json(changes_path(repo, ticket_id))
    return [WorkUnit.model_validate(c) for c in data["changes"]]


# --- Tier-2 pull valve -----------------------------------------------------------


def read_ledger_slice(
    path: Path,
    *,
    start: int | None = None,
    end: int | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return a hard-capped window of a ledger file.

    The whole anti-bloat design *pushes* a distilled payload at each agent; this is
    the *pull* valve for when one genuinely needs a raw excerpt the payload omitted.

    Without `start`/`end`, returns the last `max_lines` lines (the failing tail is
    usually what matters). The result is always capped to `max_lines` lines and
    `max_chars` characters. `start` is 1-indexed and inclusive; `end` is exclusive.

    Note: the whole file is read into memory before slicing. Ledger logs are small
    (gated by the producers), so this is fine; revisit if multi-MB logs appear.
    """
    lines = path.read_text().splitlines()
    if start is not None or end is not None:
        s = max((start - 1) if start else 0, 0)
        e = end if end is not None else len(lines)
        window = lines[s:e]
    else:
        window = lines[-max_lines:]
    window = window[:max_lines]
    text = "\n".join(window)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text
