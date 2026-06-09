"""Graph wiring tests (phase-1.md §2 / §9).

`test_coding_ticket_runs_end_to_end` drives the *compiled* graph for a coding ticket
with the agent + interrupt mocked and a real (cloned) git repo, asserting the flow
reaches END with every change DONE and the work landed on `main`.
"""

import json
import subprocess
import uuid
from pathlib import Path

import pytest

import nodes.coding.nodes as cn
import nodes.general.nodes as gn
from classes import (
    AgentState,
    ChangeStatus,
    Repo,
    Status,
    Ticket,
    TicketContent,
    TicketPriority,
    TicketType,
)
from ledger import load_changes

PLAN = {
    "plan_md": "## Plan\n\nAdd a retry budget, then consume it.",
    "changes": [
        {"id": "c01", "title": "add config field", "intent": "bound retries", "soft_loc": 20},
        {"id": "c02", "title": "consume the budget", "intent": "stop retry", "soft_loc": 40},
    ],
    "questions": [],
}


def test_graph_has_the_rewired_nodes() -> None:
    from nodes import graph

    nodes = set(graph.get_graph().nodes)
    expected = {
        "pick_up_ticket",
        "open_branch",
        "repo_bootstrap_check",
        "big_plan",
        "approve_plan",
        "select_next_change",
        "implement_change",
        "final_review",
        "research",
        "review",
        "commit_push",
        "merge",
    }
    assert expected <= nodes
    # The coding stubs were unwired (§2); they must no longer be graph nodes.
    assert {"spec", "write_tests", "write_code", "surface_questions", "get_user_answer"}.isdisjoint(
        nodes
    )


@pytest.fixture
def cloned_repo(tmp_path: Path) -> Path:
    """A work repo cloned from a bare origin, so open_branch's `git pull --ff-only` works."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True, capture_output=True
    )
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0'\n")
    (repo / "README.md").write_text("init\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    git("push", "-q", "-u", "origin", "main")
    return repo


def test_coding_ticket_runs_end_to_end(monkeypatch: pytest.MonkeyPatch, cloned_repo: Path) -> None:
    ticket = Ticket(
        content=TicketContent(
            id=7,
            type=TicketType.CODING,
            priority=TicketPriority.HIGH,
            repo=Repo.CODER,
            title="add retry budget",
            body="bound how many times the inner loop retries",
        ),
        path=Path("/tmp/ticket"),
    )
    monkeypatch.setattr(gn, "get_ticket", lambda _id: ticket)
    monkeypatch.setattr(gn, "resolve_repo", lambda _repo: cloned_repo)
    monkeypatch.setattr(
        cn,
        "run_agent",
        lambda prompt, repo, *, timeout=600: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(PLAN), stderr=""
        ),
    )
    monkeypatch.setattr(cn, "interrupt", lambda payload: {"approved": True})
    monkeypatch.setenv("CODER_MERGE_MODE", "squash")  # local squash to main; no `gh` needed

    from nodes import graph

    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    graph.invoke(AgentState(status=Status.CONT, step=0, artifact={}, ticket_id="7"), config)

    # Every change drained to DONE in the persisted ledger.
    units = load_changes(cloned_repo, "7")
    assert [u.id for u in units] == ["c01", "c02"]
    assert all(u.status is ChangeStatus.DONE for u in units)

    # merge (squash mode) landed the work as one commit on main.
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cloned_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "main"
    log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=cloned_repo, capture_output=True, text=True
    ).stdout
    assert "ticket 7: add retry budget" in log
