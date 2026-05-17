"""
Get that ticket that is open with the highest priority.
"""

import json
from enum import Enum, IntEnum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # src/tools/get_ticket.py → repo root
TICKETS_DIR = PROJECT_ROOT / "tickets" / "open"


class TicketType(Enum):
    RESEARCH = "research"
    CODING = "coding"


class TicketPriority(IntEnum):
    HIGHEST = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1


class TicketContent(BaseModel):
    id: int
    type: TicketType
    priority: TicketPriority
    title: str
    body: str


class Ticket(BaseModel):
    content: TicketContent
    path: Path


def _parse_ticket(path_to_ticket: Path) -> Ticket:
    with path_to_ticket.open() as f:
        raw = json.load(f)
    content = TicketContent.model_validate(raw)
    return Ticket(content=content, path=path_to_ticket)


def _resolve_id(id: int) -> Path:
    if type(id) is not int:
        raise TypeError(f"Wrong id type; expected int, received {type(id)}")
    normalized = f"{id:04d}"
    path = Path(TICKETS_DIR) / f"ticket_{normalized}.json"
    return path


def _get_ticket_priority(path_to_ticket: Path) -> TicketPriority:
    with path_to_ticket.open() as f:
        raw = json.load(f)
    content = TicketContent.model_validate(raw)
    return content.priority


def get_open_ticket(id: Optional[int] = None) -> Ticket:
    """
    Go through open tickets, selects either by ID or highest prio.
    -> relative to current place
    """
    if id:
        path: Path = _resolve_id(id)
        if not path.exists():
            raise FileNotFoundError(path)
        return _parse_ticket(path)
    else:
        tickets = [_parse_ticket(p) for p in Path(TICKETS_DIR).glob("ticket_*.json")]
        if not tickets:
            raise FileNotFoundError("No open tickets")
        return max(tickets, key=lambda t: t.content.priority)


def get_ticket(id: int) -> Ticket:
    path: Path = _resolve_id(id)
    if not path.exists():
        raise FileNotFoundError(path)
    return _parse_ticket(path)
