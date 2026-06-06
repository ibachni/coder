import json
import re
import subprocess
from langgraph.types import interrupt

from nodes.helpers import oauth_token, slugify
from classes import AgentState, Status
from helper.cleanSubscriptionEnv import clean_subscription_env
from helper.repoPaths import resolve_repo
from prompt_loader import render
from tools.get_ticket import get_open_ticket, get_ticket
from classes import TicketType
from bootstrap import BootstrapError, detect_commands
from ledger import ticket_dir, write_json

### Initial steps


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


def assert_coding(state: AgentState) -> bool:
    assert state.ticket is not None
    if state.ticket.content.type == TicketType.CODING:
        return True
    else:
        return False


def open_branch(state: AgentState) -> AgentState:
    """
    Mimic a developer's flow: refuse a dirty tree, pull main, then switch to
    (or create) the ticket branch off the latest main.
    """
    assert state.ticket is not None, "open_branch requires a ticket to be set"
    assert state.repo_path is not None, "open_branch requires repo_path to be set"

    branch = f"ticket_{state.ticket_id}/{slugify(state.ticket.content.title)}"
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


def repo_bootstrap_check(state: AgentState) -> AgentState:
    """Discover the repo's test/lint/typecheck commands and persist them.

    Hard gate: if we cannot determine how to build/test the repo, fail fast and
    surface to the human rather than letting the inner loop flail on unknown
    commands. Writes the detected commands to the ticket-tier `bootstrap.json`.
    """
    assert state.repo_path is not None, "repo_bootstrap_check requires repo_path"
    assert state.ticket_id is not None, "repo_bootstrap_check requires ticket_id"

    try:
        config = detect_commands(state.repo_path)
    except BootstrapError:
        state.status = Status.FAILURE
        return state

    bootstrap_path = ticket_dir(state.repo_path, state.ticket_id) / "bootstrap.json"
    write_json(bootstrap_path, config.model_dump())
    state.step += 1
    return state


def surface_questions(state: AgentState) -> AgentState:
    """
    Usually the text body given is not specific enough.
    In this step, the goal is to surface any questions.
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


### Last steps


def review(state: AgentState) -> AgentState:
    state.step += 1
    return state


def commit_push(state: AgentState) -> AgentState:
    state.step += 1
    return state


def merge(state: AgentState) -> AgentState:
    state.step += 1
    return state
