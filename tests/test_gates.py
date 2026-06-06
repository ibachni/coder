"""Tests for the hard gates in src/gates.py.

Gates are tested with trivial shell commands (`true`/`false`/`test`) so the
exit-code → bool logic is exercised without depending on a real toolchain.
`loc_changed` uses a throwaway git repo.
"""

import os
import subprocess
from pathlib import Path

import pytest

import gates  # `tests_are_red` is referenced via the module so pytest doesn't collect it as a test
from gates import (
    lint_clean,
    loc_changed,
    run_tests,
    typecheck_clean,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    git("init", "-q")
    (repo / "a.txt").write_text("line1\nline2\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    return repo


class TestRunTests:
    def test_exit_zero_is_green(self, tmp_path: Path) -> None:
        assert run_tests(tmp_path, ["true"]) is True

    def test_nonzero_is_not_green(self, tmp_path: Path) -> None:
        assert run_tests(tmp_path, ["false"]) is False

    def test_files_are_appended_to_argv(self, tmp_path: Path) -> None:
        present = tmp_path / "here.txt"
        present.write_text("x")
        # `test -e <path>` exits 0 iff the path exists → proves files reach argv.
        assert run_tests(tmp_path, ["test", "-e"], files=[str(present)]) is True
        assert run_tests(tmp_path, ["test", "-e"], files=[str(tmp_path / "nope")]) is False


class TestTestsAreRed:
    def test_red_when_command_fails(self, tmp_path: Path) -> None:
        assert gates.tests_are_red(tmp_path, ["false"]) is True  # exit 1 == ran and failed

    def test_not_red_when_command_passes(self, tmp_path: Path) -> None:
        assert gates.tests_are_red(tmp_path, ["true"]) is False  # exit 0 == green

    def test_no_tests_collected_is_not_red(self, tmp_path: Path) -> None:
        # pytest exits 5 when nothing is collected; an absent test must NOT pass the
        # RED gate, or the implementer could satisfy it by writing no tests at all.
        cmd = ["python3", "-c", "import sys; sys.exit(5)"]
        assert gates.tests_are_red(tmp_path, cmd) is False

    def test_no_tests_exit_is_configurable(self, tmp_path: Path) -> None:
        # Treat exit 1 as the "no tests" code → a failing run is no longer counted red.
        assert gates.tests_are_red(tmp_path, ["false"], no_tests_exit=1) is False


class TestLintAndTypecheck:
    def test_lint_clean_true_false(self, tmp_path: Path) -> None:
        assert lint_clean(tmp_path, ["true"]) is True
        assert lint_clean(tmp_path, ["false"]) is False

    def test_typecheck_clean_true_false(self, tmp_path: Path) -> None:
        assert typecheck_clean(tmp_path, ["true"]) is True
        assert typecheck_clean(tmp_path, ["false"]) is False


class TestLocChanged:
    def test_zero_on_clean_tree(self, git_repo: Path) -> None:
        assert loc_changed(git_repo) == 0

    def test_counts_added_and_deleted(self, git_repo: Path) -> None:
        # Replace 2 lines with 3 → 3 added + 2 deleted = 5.
        (git_repo / "a.txt").write_text("x\ny\nz\n")
        assert loc_changed(git_repo) == 5

    def test_counts_staged_new_file_additions(self, git_repo: Path) -> None:
        (git_repo / "b.txt").write_text("one\ntwo\n")
        subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True, capture_output=True)
        assert loc_changed(git_repo) == 2

    def test_counts_unstaged_new_file(self, git_repo: Path) -> None:
        # The implementer's output is usually brand-new files; `git diff` alone misses
        # them, so loc_changed must include untracked files.
        (git_repo / "new.py").write_text("a\nb\nc\n")  # untracked, never `git add`ed
        assert loc_changed(git_repo) == 3

    def test_excludes_coder_ledger(self, git_repo: Path) -> None:
        ledger = git_repo / ".coder" / "runs" / "42"
        ledger.mkdir(parents=True)
        (ledger / "x.json").write_text("1\n2\n3\n4\n5\n")  # must not count
        (git_repo / "real.py").write_text("one\ntwo\n")
        assert loc_changed(git_repo) == 2
