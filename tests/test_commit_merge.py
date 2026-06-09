"""Tests for the real commit_push / merge git side effects (§3.9 / §3.10).

commit_push runs against a throwaway git repo (real commits + a local bare remote);
merge is exercised with a monkeypatched `subprocess.run` so neither `gh` nor a real
`main` checkout is required — we assert the right command is invoked (§9).
"""

import subprocess
from pathlib import Path

import pytest

import nodes.general.nodes as gn
from classes import AgentState, Repo, Status, Ticket, TicketContent, TicketPriority, TicketType

BRANCH = "ticket_42/add-a-retry-budget"


def _state(repo: Path) -> AgentState:
    ticket = Ticket(
        content=TicketContent(
            id=42,
            type=TicketType.CODING,
            priority=TicketPriority.HIGH,
            repo=Repo.CODER,
            title="add a retry budget",
            body="b",
        ),
        path=Path("/tmp/ticket"),
    )
    return AgentState(
        status=Status.CONT, step=0, artifact={}, ticket_id="42", ticket=ticket, repo_path=repo
    )


@pytest.fixture
def repo_on_branch(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "README.md").write_text("init\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    git("checkout", "-q", "-b", BRANCH)
    return repo


def _commit_count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    return int(out.stdout.strip())


def _last_message(repo: Path) -> str:
    out = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True
    )
    return out.stdout.strip()


class TestCommitPush:
    def test_commits_changes_with_ticket_message(self, repo_on_branch: Path) -> None:
        (repo_on_branch / "retry.py").write_text("MAX = 3\n")
        before = _commit_count(repo_on_branch)

        result = gn.commit_push(_state(repo_on_branch))

        assert _commit_count(repo_on_branch) == before + 1
        assert _last_message(repo_on_branch) == "ticket 42: add a retry budget"
        assert result.step == 1

    def test_clean_tree_makes_no_commit(self, repo_on_branch: Path) -> None:
        before = _commit_count(repo_on_branch)
        gn.commit_push(_state(repo_on_branch))  # nothing staged
        assert _commit_count(repo_on_branch) == before  # no empty commit, no crash

    def test_no_remote_skips_push_cleanly(self, repo_on_branch: Path) -> None:
        (repo_on_branch / "x.py").write_text("x\n")
        result = gn.commit_push(_state(repo_on_branch))  # no remote configured
        assert result.status is Status.CONT
        assert _last_message(repo_on_branch) == "ticket 42: add a retry budget"

    def test_pushes_to_origin_when_remote_exists(
        self, repo_on_branch: Path, tmp_path: Path
    ) -> None:
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", str(bare)],
            cwd=repo_on_branch,
            check=True,
            capture_output=True,
        )
        (repo_on_branch / "x.py").write_text("x\n")

        gn.commit_push(_state(repo_on_branch))

        refs = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=repo_on_branch,
            capture_output=True,
            text=True,
        ).stdout
        assert BRANCH in refs


class TestMerge:
    def test_opens_pr_by_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            out = "origin\n" if cmd[:2] == ["git", "remote"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        monkeypatch.setattr(gn.subprocess, "run", fake_run)
        monkeypatch.delenv("CODER_MERGE_MODE", raising=False)

        result = gn.merge(_state(tmp_path))

        assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls)
        assert not any(
            cmd[:2] == ["git", "checkout"] for cmd in calls
        )  # PR default doesn't touch main
        assert result.step == 1

    def test_no_remote_skips_pr_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd, 0, stdout="", stderr=""
            )  # git remote -> no origin

        monkeypatch.setattr(gn.subprocess, "run", fake_run)
        monkeypatch.delenv("CODER_MERGE_MODE", raising=False)

        result = gn.merge(_state(tmp_path))

        assert not any(
            cmd[:3] == ["gh", "pr", "create"] for cmd in calls
        )  # can't PR without a remote
        assert result.status is Status.CONT
        assert result.step == 1

    def test_squash_mode_merges_into_main(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            out = "ticket_42/x\n" if cmd[:2] == ["git", "rev-parse"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        monkeypatch.setattr(gn.subprocess, "run", fake_run)
        monkeypatch.setenv("CODER_MERGE_MODE", "squash")

        gn.merge(_state(tmp_path))

        joined = [" ".join(c) for c in calls]
        assert any(c.startswith("git checkout main") for c in joined)
        assert any("git merge --squash ticket_42/x" == c for c in joined)
        assert any(c.startswith("git commit -m ticket 42:") for c in joined)
        assert not any(c.startswith("gh ") for c in joined)
