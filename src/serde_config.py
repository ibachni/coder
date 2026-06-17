"""Serializer config for the LangGraph SqliteSaver checkpointer.

Factored out of nodes/__init__.py so the allow-list is a single source of truth
and can be exercised in tests without opening the on-disk state database.

`allowed_msgpack_modules` is the allow-list of types the serializer may
reconstruct from a checkpoint — every custom enum/model that lands in AgentState
must be listed here or a resume will fail to rehydrate it.
"""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ALLOWED_MSGPACK_MODULES = [
    ("classes", "Status"),
    ("classes", "TicketType"),
    ("classes", "TicketPriority"),
    ("classes", "Repo"),
    ("classes", "Ticket"),
    ("classes", "TicketContent"),
    ("classes", "ChangeStatus"),
    ("classes", "WorkUnit"),
    ("classes", "ResearchMode"),
    ("classes", "WatchEntry"),
    ("classes", "AgentState"),
]


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES)
