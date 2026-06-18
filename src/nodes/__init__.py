import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from classes import AgentState
from serde_config import make_serializer
from nodes.general.nodes import (
    pick_up_ticket,
    open_branch,
    route_after_open_branch,
    repo_bootstrap_check,
    route_after_bootstrap,
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
from nodes.research.nodes import (
    classify_research_type,
    frame_brief,
    route_after_frame_brief,
    approve_brief,
    route_after_brief,
    research_agent,
    route_after_research_agent,
    save_report,
)


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
# === Research `new` mode (R1) ===
graph.add_node("classify_research_type", classify_research_type)
graph.add_node("frame_brief", frame_brief)
graph.add_node("approve_brief", approve_brief)
graph.add_node("research_agent", research_agent)
graph.add_node("save_report", save_report)
# === Shared tail ===
graph.add_node("commit_push", commit_push)
graph.add_node("merge", merge)


# === Adding edges ===

graph.add_edge(START, "pick_up_ticket")
graph.add_edge("pick_up_ticket", "open_branch")

# Split by ticket type after branching: coding bootstraps; research skips it (§4.2).
graph.add_conditional_edges(
    "open_branch",
    route_after_open_branch,
    {"coding": "repo_bootstrap_check", "research": "classify_research_type"},
)

# Bootstrap gate (coding only): FAILURE → END, else plan (§3.1/§3.2).
graph.add_conditional_edges(
    "repo_bootstrap_check",
    route_after_bootstrap,
    {"end": END, "big_plan": "big_plan"},
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

# Research `new` mode (R1): classify → brief → gate → agent → save → land.
graph.add_edge("classify_research_type", "frame_brief")
graph.add_conditional_edges(
    "frame_brief", route_after_frame_brief, {"end": END, "approve_brief": "approve_brief"}
)
graph.add_conditional_edges(
    "approve_brief",
    route_after_brief,
    {"research_agent": "research_agent", "frame_brief": "frame_brief", "end": END},
)
graph.add_conditional_edges(
    "research_agent", route_after_research_agent, {"end": END, "save_report": "save_report"}
)
graph.add_edge("save_report", "commit_push")

graph.add_edge("commit_push", "merge")
graph.add_edge("merge", END)

graph = graph.compile(checkpointer=checkpointer)
