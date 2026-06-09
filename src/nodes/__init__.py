import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from classes import AgentState
from serde_config import make_serializer
from nodes.general.nodes import (
    pick_up_ticket,
    open_branch,
    repo_bootstrap_check,
    route_after_bootstrap,
    review,
    final_review,
    commit_push,
    merge,
)
from nodes.coding.nodes import (
    big_plan,
    route_after_big_plan,
    approve_plan,
    route_after_approval,
    select_next_change,
    route_change,
    implement_change,
)
from nodes.research.nodes import research


# === Variables ===

MAX_RETRIES = 3


# === Database ===

# Overridable so tests (and CI) can point at a throwaway DB instead of the real
# state store; the parent directory is created on demand so import never fails.
DB_PATH = os.environ.get("CODER_STATE_DB", os.path.expanduser("~/.local/share/coder/state.db"))
if db_dir := os.path.dirname(DB_PATH):
    os.makedirs(db_dir, exist_ok=True)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)

checkpointer = SqliteSaver(conn, serde=make_serializer())


# === Building Graph ===

graph = StateGraph(AgentState)

# === Adding Nodes ===

graph.add_node("pick_up_ticket", pick_up_ticket)
graph.add_node("open_branch", open_branch)
graph.add_node("repo_bootstrap_check", repo_bootstrap_check)
# === Coding outer loop ===
graph.add_node("big_plan", big_plan)
graph.add_node("approve_plan", approve_plan)
graph.add_node("select_next_change", select_next_change)
graph.add_node("implement_change", implement_change)
graph.add_node("final_review", final_review)
# === Research (still on its existing stub; Phase 5 rebuilds) ===
graph.add_node("research", research)
graph.add_node("review", review)
# === Shared tail ===
graph.add_node("commit_push", commit_push)
graph.add_node("merge", merge)


# === Adding edges ===

graph.add_edge(START, "pick_up_ticket")
graph.add_edge("pick_up_ticket", "open_branch")
graph.add_edge("open_branch", "repo_bootstrap_check")

# Bootstrap gate: FAILURE → END, else split by ticket type (§3.1/§3.2).
graph.add_conditional_edges(
    "repo_bootstrap_check",
    route_after_bootstrap,
    {"end": END, "big_plan": "big_plan", "research": "research"},
)

# Plan → gate → iterate → review → land.
graph.add_conditional_edges(
    "big_plan", route_after_big_plan, {"end": END, "approve_plan": "approve_plan"}
)
graph.add_conditional_edges(
    "approve_plan",
    route_after_approval,
    {"select_next_change": "select_next_change", "big_plan": "big_plan", "end": END},
)
graph.add_conditional_edges(
    "select_next_change",
    route_change,
    {"implement_change": "implement_change", "final_review": "final_review"},
)
graph.add_edge("implement_change", "select_next_change")
graph.add_edge("final_review", "commit_push")

# Research path stays on the old stub.
graph.add_edge("research", "review")
graph.add_edge("review", "commit_push")

graph.add_edge("commit_push", "merge")
graph.add_edge("merge", END)

graph = graph.compile(checkpointer=checkpointer)
