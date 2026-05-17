from langchain_core.runnables import RunnableConfig

from classes import AgentState, Status
from nodes import graph


def run(ticket_id: str, resume=False):
    config: RunnableConfig = {"configurable": {"thread_id": ticket_id}}
    if resume:
        graph.invoke(None, config=config)
    else:
        initial_state = AgentState(status=Status.CONT, step=0, artifact={}, ticket_id=ticket_id)
        graph.invoke(initial_state, config=config)
