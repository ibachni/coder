from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel

from tools.get_ticket import Ticket


class Status(Enum):
    CONT = "continue"
    FAILURE = "failure"


class AgentState(BaseModel):
    status: Status
    step: int
    artifact: Mapping[str, Any]
    ticket_id: Optional[str] = None
    ticket: Optional[Ticket] = None
    repo_path: Optional[Path] = None
    questions: Optional[list[dict[str, str]]] = None
    answers: Optional[str] = None
