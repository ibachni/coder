from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel


class Status(Enum):
    CONT = "continue"
    FAILURE = "failure"


class TicketType(Enum):
    RESEARCH = "research"
    CODING = "coding"


class TicketPriority(IntEnum):
    HIGHEST = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1


class TicketComplexity(IntEnum):
    HIGHEST = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1


class Repo(Enum):
    CODER = "coder"
    RESEARCH = "research"


class TicketContent(BaseModel):
    id: int
    type: TicketType
    priority: TicketPriority
    repo: Repo
    title: str
    body: str


class Ticket(BaseModel):
    content: TicketContent
    path: Path


class AgentState(BaseModel):
    status: Status
    step: int
    artifact: Mapping[str, Any]
    ticket_id: Optional[str] = None
    ticket: Optional[Ticket] = None
    repo_path: Optional[Path] = None
    questions: Optional[list[dict[str, str]]] = None
    answers: Optional[str] = None
    complexity: Optional[TicketComplexity] = None
