import json
import os
import re
import sqlite3
import subprocess

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.types import interrupt

from classes import AgentState, Status
from helper.authTokenLoader import load_oauth_token
from helper.cleanSubscriptionEnv import clean_subscription_env
from helper.repoPaths import resolve_repo
from prompt_loader import render
from tools.get_ticket import TicketType, get_open_ticket, get_ticket

# === Startup ===

oauth_token = load_oauth_token()

# === Variables ===

MAX_RETRIES = 3

# === Helper ===


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


# === Nodes ===

"""
Two modes supported for now:
1. Coding
2. Researching

Future:
- Set up (python, typescript, or swift repo)
"""


def pick_up_ticket(state: AgentState) -> AgentState:
    """
    Picks up the next ticket with high prio. Calls a simple tools which does that.
    """
    if state.ticket_id:
        ticket = get_ticket(int(state.ticket_id))
    else:
        ticket = get_open_ticket()
    state.ticket = ticket
    state.repo_path = resolve_repo(state.ticket.content.repo)
    state.step += 1
    return state


def open_branch(state: AgentState) -> AgentState:
    """
    Mimic a developer's flow: refuse a dirty tree, pull main, then switch to
    (or create) the ticket branch off the latest main.
    """
    assert state.ticket is not None, "open_branch requires a ticket to be set"
    assert state.repo_path is not None, "open_branch requires repo_path to be set"

    branch = f"ticket_{state.ticket_id}/{_slugify(state.ticket.content.title)}"
    cwd = state.repo_path

    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        if dirty.stdout.strip():
            raise RuntimeError(f"Working tree is dirty in {cwd}; aborting open_branch.")

        subprocess.run(
            ["git", "checkout", "main"], cwd=cwd, capture_output=True, text=True, check=True
        )
        subprocess.run(
            ["git", "pull", "--ff-only"], cwd=cwd, capture_output=True, text=True, check=True
        )

        exists = (
            subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                cwd=cwd,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        checkout_cmd = ["git", "checkout", branch] if exists else ["git", "checkout", "-b", branch]
        subprocess.run(checkout_cmd, cwd=cwd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"git command failed: {' '.join(e.cmd)}")
        print(f"stderr: {e.stderr}")
        raise

    state.step += 1
    return state


def surface_questions(state: AgentState) -> AgentState:
    """
    Usually the text body given is not specific enough.
    In this step, I want to surface any questions.
    The questions are attached to the state as a list of strings.
    -> Human in the loop
    """
    assert state.ticket is not None
    assert state.repo_path is not None
    prompt = render(
        "surface_questions",
        ticket_title=state.ticket.content.title,
        ticket_repo=state.ticket.content.repo,
        ticket_body=state.ticket.content.body,
        repo_path=state.repo_path,
    )
    result = subprocess.run(
        ["claude", "-p", prompt],
        env=clean_subscription_env(oauth_token),
        cwd=state.repo_path,
        timeout=600,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        state.status = Status.FAILURE
        return state
    raw = result.stdout.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    try:
        state.questions = json.loads(raw)
    except json.JSONDecodeError:
        state.status = Status.FAILURE
        return state
    state.step += 1
    return state


def get_user_answer(state: AgentState) -> AgentState:
    if not state.questions:
        state.step += 1
        return state
    answers = interrupt({"questions": state.questions})
    state.answers = answers
    state.step += 1
    return state


# === Coding only nodes ===


def spec(state: AgentState) -> AgentState:
    state.step += 1
    return state


def write_tests(state: AgentState) -> AgentState:
    prompt = render("write_tests", ticket_id=state.ticket_id)
    result = subprocess.run(
        ["claude", "-p", prompt],
        env=clean_subscription_env(oauth_token),
        cwd=state.repo_path,
        timeout=600,
        capture_output=True,
    )
    print(result)
    return state


def write_code(state: AgentState) -> AgentState:
    state.step += 1
    return state


# === Research / Non-coding edges ===


def research(state: AgentState) -> AgentState:
    """ """
    state.step += 1
    return state


# === Continuing ====


def review(state: AgentState) -> AgentState:
    state.step += 1
    return state


def commit_push(state: AgentState) -> AgentState:
    state.step += 1
    return state


def merge(state: AgentState) -> AgentState:
    state.step += 1
    return state


# === Routing Functions ====


def assert_coding(state: AgentState) -> bool:
    assert state.ticket is not None
    if state.ticket.content.type == TicketType.CODING:
        return True
    else:
        return False


# === Databse ===

conn = sqlite3.connect(
    os.path.expanduser("~/.local/share/coder/state.db"),
    check_same_thread=False,
)

checkpointer = SqliteSaver(
    conn,
    serde=JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("classes", "Status"),
            ("tools.get_ticket", "TicketType"),
            ("tools.get_ticket", "TicketPriority"),
            ("tools.get_ticket", "Repo"),
            ("tools.get_ticket", "Ticket"),
        ]
    ),
)


# === Building Graph ===

graph = StateGraph(AgentState)

# === Adding Nodes ===

graph.add_node("pick_up_ticket", pick_up_ticket)
graph.add_node("open_branch", open_branch)
graph.add_node("surface_questions", surface_questions)
graph.add_node("get_user_answer", get_user_answer)
# === Coding ===
graph.add_node("spec", spec)
graph.add_node("write_tests", write_tests)
graph.add_node("write_code", write_code)
# === Research ===
graph.add_node("research", research)


# === combined path ===
graph.add_node("review", review)
graph.add_node("commit_push", commit_push)
graph.add_node("merge", merge)


# === Adding edges ===

graph.add_edge(START, "pick_up_ticket")
graph.add_edge("pick_up_ticket", "open_branch")
graph.add_edge("open_branch", "surface_questions")
# === Coding path
graph.add_conditional_edges("surface_questions", assert_coding, {True: "spec", False: "research"})
graph.add_edge("spec", "write_tests")
graph.add_edge("write_tests", "write_code")
graph.add_edge("write_code", "review")
graph.add_edge("research", "review")
graph.add_edge("review", "commit_push")
graph.add_edge("commit_push", "merge")

graph = graph.compile(checkpointer=checkpointer)
