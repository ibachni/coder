from langchain_core.runnables import RunnableConfig
from nodes import graph
from classes import AgentState, Status


def run(ticket_id: str, resume=False):
    config: RunnableConfig = {"configurable": {"thread_id": ticket_id}}
    if resume:
        graph.invoke(None, config=config)
    else:
        initial_state = AgentState(status=Status.cont, step=0, artifact={}, ticket_id=ticket_id)
        graph.invoke(initial_state, config=config)
