import subprocess

from nodes.helpers import oauth_token
from classes import AgentState
from helper.cleanSubscriptionEnv import clean_subscription_env
from prompt_loader import render


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
