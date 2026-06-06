"""Two-tier evidence ledger I/O. See docs/evidence-ledger.md.

Tier 1 (disk): full-fidelity artifacts under ``<repo>/.coder/runs/<ticket>/[<change>/]``.
Tier 2 (context): callers assemble small distilled payloads; this module provides the
bounded read helper (`read_ledger_slice`) that lets distillation stay aggressive
without ever losing access to ground truth.
"""

import json
from pathlib import Path
from typing import Any

LEDGER_ROOT = ".coder/runs"
DEFAULT_MAX_LINES = 100
DEFAULT_MAX_CHARS = 8000


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
