"""Deterministic hard gates. Exit code 0 == pass.

These run BEFORE any judging agent (never spend tokens on code that doesn't
compile or has no failing-first tests). The commands themselves come from
bootstrap.json (see bootstrap.detect_commands); these functions just run a
command in the repo and reduce it to a boolean / count.

Infrastructure failures surface, they are not masked as a failed check: a command
that cannot be found (FileNotFoundError) or that exceeds the timeout
(subprocess.TimeoutExpired) raises to the caller — and thence to the human — rather
than quietly reporting "not green".
"""

import subprocess
from pathlib import Path

DEFAULT_TIMEOUT = 600

# The harness's own ledger lives in the target repo; it must never count as
# "changed code" nor be confused with the implementer's output.
_CODER_DIR = ".coder"


def _run(
    cmd: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def run_tests(repo: Path, test_cmd: list[str], files: list[str] | None = None) -> bool:
    """True iff the test command exits 0. Optionally scope to specific files."""
    return _run(list(test_cmd) + list(files or []), repo).returncode == 0


def tests_are_red(
    repo: Path,
    test_cmd: list[str],
    files: list[str] | None = None,
    *,
    no_tests_exit: int = 5,
) -> bool:
    """True iff the tests RAN and FAILED — the RED-gate primitive.

    A test that was never red proves nothing. Crucially, "no tests collected"
    (pytest's exit code 5) is NOT red: an *absent* test must not satisfy the gate,
    or the implementer could pass it by writing no tests at all. Both that case and
    a green run return False. `no_tests_exit` is pytest's convention; override it for
    a different runner.
    """
    rc = _run(list(test_cmd) + list(files or []), repo).returncode
    if rc == 0 or rc == no_tests_exit:
        return False
    return True


def lint_clean(repo: Path, lint_cmd: list[str]) -> bool:
    return _run(list(lint_cmd), repo).returncode == 0


def typecheck_clean(repo: Path, typecheck_cmd: list[str]) -> bool:
    return _run(list(typecheck_cmd), repo).returncode == 0


def _is_ledger_path(path: str) -> bool:
    return path == _CODER_DIR or path.startswith(_CODER_DIR + "/")


def loc_changed(repo: Path, base_ref: str = "HEAD") -> int:
    """Lines changed (added + deleted) vs `base_ref`, for the soft LoC budget (§3.7).

    Counts tracked changes AND new untracked files (the implementer's main output is
    usually new files, which `git diff` alone misses), but excludes the harness's own
    `.coder/` ledger. Binary files (numstat `-`) count as 0.
    """
    total = 0
    tracked = _run(["git", "diff", "--numstat", base_ref], repo).stdout
    for line in tracked.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and not _is_ledger_path(parts[2]):
            for n in parts[:2]:
                if n.isdigit():
                    total += int(n)
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], repo).stdout
    for rel in untracked.splitlines():
        if _is_ledger_path(rel):
            continue
        try:
            total += len((repo / rel).read_text(errors="replace").splitlines())
        except OSError:
            continue
    return total
