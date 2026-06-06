"""Detect a repo's build/test commands — the brain behind `repo_bootstrap_check`.

Kept separate from the node so it is unit-testable without a graph or checkpointer.
Commands are runner-style argv (e.g. ["uv", "run", "pytest"]) so the inner loop can
append specific test files for targeted runs.
"""

from pathlib import Path

from pydantic import BaseModel


class BootstrapError(RuntimeError):
    """Raised when a repo's build/test commands cannot be determined."""


class BootstrapConfig(BaseModel):
    test_cmd: list[str]
    lint_cmd: list[str]
    typecheck_cmd: list[str]
    # Informational hint only — `.venv` existence is too weak a signal to fail-close
    # on, so the node does not gate on it. Real dependency verification happens when
    # the gates first actually run (a missing dep surfaces as an import/command error).
    install_ok: bool = True


def detect_commands(repo: Path) -> BootstrapConfig:
    """Determine how to test/lint/typecheck `repo`.

    Supports the Python/uv stack this project targets. Raises BootstrapError on an
    unrecognized layout so the node can hard-fail to the human rather than guess.
    """
    if (repo / "pyproject.toml").exists():
        uses_uv = (repo / "uv.lock").exists()
        prefix = ["uv", "run"] if uses_uv else []
        target = "src" if (repo / "src").is_dir() else "."
        return BootstrapConfig(
            test_cmd=prefix + ["pytest"],
            lint_cmd=prefix + ["ruff", "check", target],
            typecheck_cmd=prefix + ["pyright", target],
            install_ok=(not uses_uv) or (repo / ".venv").exists(),
        )
    raise BootstrapError(f"Could not detect build commands for repo: {repo}")
