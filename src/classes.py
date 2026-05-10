from pydantic import BaseModel
from enum import Enum
from typing import Mapping, Any


class Status(Enum):
    cont = "continue"
    failure = "failure"


class AgentState(BaseModel):
    status: Status
    step: int
    artifact: Mapping[str, Any]
    ticket_id: str
