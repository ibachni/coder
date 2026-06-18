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
    ResearchMode,
    Status,
    Ticket,
    TicketContent,
    TicketPriority,
    TicketType,
    WatchEntry,
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
        # research `new` mode (R1)
        "classify_research_type",
        "frame_brief",
        "approve_brief",
        "research_agent",
        "save_report",
        "commit_push",
        "merge",
    }
    assert expected <= nodes
    # The coding stubs were unwired (§2), and the research/review stubs were replaced by
    # the R1 nodes above; none should remain graph nodes.
    assert {
        "spec",
        "write_tests",
        "write_code",
        "surface_questions",
        "get_user_answer",
        "research",
        "review",
    }.isdisjoint(nodes)


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


def test_research_ticket_runs_end_to_end(monkeypatch: pytest.MonkeyPatch, cloned_repo: Path) -> None:
    """A research ticket flows open_branch → (skip bootstrap) → classify → brief → gate →
    agent → save → merge, landing report.md on main. Agents + the gate are mocked."""
    import nodes.research.nodes as rn

    ticket = Ticket(
        content=TicketContent(
            id=8,
            type=TicketType.RESEARCH,
            priority=TicketPriority.HIGH,
            repo=Repo.RESEARCH,
            title="state of AI agents",
            body="where are agents heading in 2026?",
        ),
        path=Path("/tmp/ticket"),
    )
    brief = {"brief_md": "## Brief\n\n- Trends — done-when: 3 sources", "questions": []}
    report = {"report_md": "# Agents\n\nImproving fast [a](https://a.example).", "sources": [{"url": "https://a.example"}]}

    monkeypatch.setattr(gn, "get_ticket", lambda _id: ticket)
    monkeypatch.setattr(gn, "resolve_repo", lambda _repo: cloned_repo)
    monkeypatch.setattr(
        rn, "run_agent", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=json.dumps(brief), stderr="")
    )
    monkeypatch.setattr(
        rn,
        "run_research_agent",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"is_error": False, "result": json.dumps(report)}), stderr=""
        ),
    )
    monkeypatch.setattr(rn, "interrupt", lambda payload: {"approved": True})
    monkeypatch.setenv("CODER_MERGE_MODE", "squash")

    from nodes import graph

    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    graph.invoke(AgentState(status=Status.CONT, step=0, artifact={}, ticket_id="8"), config)

    report_md = (cloned_repo / "research" / "8-state-of-ai-agents" / "report.md").read_text()
    assert "# Agents" in report_md
    sources = (cloned_repo / "research" / "8-state-of-ai-agents" / "sources.jsonl").read_text()
    assert "https://a.example" in sources
    log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=cloned_repo, capture_output=True, text=True
    ).stdout
    assert "ticket 8: state of AI agents" in log


def test_continuous_ticket_runs_end_to_end(monkeypatch: pytest.MonkeyPatch, cloned_repo: Path) -> None:
    """A `continuous` ticket flows open_branch → classify → load_prior → gather → append →
    merge, prepending a dated insights section to the prior report. Agent is mocked."""
    import research_io
    import nodes.research.nodes as rn

    ticket = Ticket(
        content=TicketContent(
            id=5,
            type=TicketType.RESEARCH,
            priority=TicketPriority.HIGH,
            repo=Repo.RESEARCH,
            title="AI agent news",
            body="track new AI agent releases",
            research_mode=ResearchMode.CONTINUOUS,
        ),
        path=Path("/tmp/ticket"),
    )
    slug = "5-ai-agent-news"
    # Pre-seed a prior report + watchlist, committed so open_branch sees a clean tree.
    research_io.write_report(cloned_repo, slug, "# AI agent news\n\nprior body")
    research_io.write_watchlist(cloned_repo, slug, [WatchEntry(url="https://blog.example", kind="blog")])
    research_io.write_last_run(cloned_repo, slug, {"ran_at": "2026-06-01", "seen_source_urls": []})
    for cmd in (["git", "add", "-A"], ["git", "commit", "-qm", "seed"], ["git", "push", "-q"]):
        subprocess.run(cmd, cwd=cloned_repo, check=True, capture_output=True)

    updates = {
        "insights_md": "Release X shipped [a](https://blog.example/x).",
        "sources": [{"url": "https://blog.example/x"}],
        "stale_urls": [],
    }
    monkeypatch.setattr(gn, "get_ticket", lambda _id: ticket)
    monkeypatch.setattr(gn, "resolve_repo", lambda _repo: cloned_repo)
    monkeypatch.setattr(
        rn,
        "run_research_agent",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"is_error": False, "result": json.dumps(updates)}), stderr=""
        ),
    )
    monkeypatch.setattr(rn, "_now", lambda: "2026-06-18")
    monkeypatch.setenv("CODER_MERGE_MODE", "squash")

    from nodes import graph

    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    graph.invoke(AgentState(status=Status.CONT, step=0, artifact={}, ticket_id="5"), config)

    report = (cloned_repo / "research" / slug / "report.md").read_text()
    assert report.startswith("## Insights — 2026-06-18")
    assert "prior body" in report  # prior report retained
    log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=cloned_repo, capture_output=True, text=True
    ).stdout
    assert "ticket 5: AI agent news" in log
