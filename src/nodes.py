import os
import sqlite3
from langgraph.graph import START, StateGraph
from classes import AgentState
from langgraph.checkpoint.sqlite import SqliteSaver

# === Nodes ===


def pick_up_ticket(state: AgentState) -> AgentState:
    """
    Picks up the next ticket with high prio. Calls a simple tools which does that.
    """
    state.step += 1
    return state


def open_branch(state: AgentState) -> AgentState:
    state.step += 1
    return state


def spec(state: AgentState) -> AgentState:
    state.step += 1
    return state


def write_tests(state: AgentState) -> AgentState:
    state.step += 1
    return state


def write_code(state: AgentState) -> AgentState:
    state.step += 1
    return state


def review(state: AgentState) -> AgentState:
    state.step += 1
    return state


def commit_push(state: AgentState) -> AgentState:
    state.step += 1
    return state


def merge(state: AgentState) -> AgentState:
    state.step += 1
    return state


# === Databse ===

conn = sqlite3.connect(
    os.path.expanduser("~/.local/share/coder/state.db"),
    check_same_thread=False,
)

checkpointer = SqliteSaver(conn)

# === Building Graph ===

graph = StateGraph(AgentState)

# === Adding Nodes ===

graph.add_node("pick_up_ticket", pick_up_ticket)
graph.add_node("open_branch", open_branch)
graph.add_node("spec", spec)
graph.add_node("write_tests", write_tests)
graph.add_node("write_code", write_code)
graph.add_node("review", review)
graph.add_node("commit_push", commit_push)
graph.add_node("merge", merge)


# === Adding edges ===

graph.add_edge(START, "pick_up_ticket")
graph.add_edge("pick_up_ticket", "open_branch")
graph.add_edge("open_branch", "spec")
graph.add_edge("spec", "write_tests")
graph.add_edge("write_tests", "write_code")
graph.add_edge("write_code", "review")
graph.add_edge("review", "commit_push")
graph.add_edge("commit_push", "merge")

graph = graph.compile(checkpointer=checkpointer)
