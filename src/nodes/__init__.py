import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph

from classes import AgentState
from serde_config import make_serializer
from nodes.general.nodes import (
    pick_up_ticket,
    assert_coding,
    open_branch,
    surface_questions,
    get_user_answer,
    review,
    commit_push,
    merge,
)
from nodes.coding.nodes import spec, write_tests, write_code
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
