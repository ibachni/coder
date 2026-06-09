from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel


class Status(Enum):
    CONT = "continue"
    FAILURE = "failure"


class ChangeStatus(Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


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
    goals: Optional[str] = None  # ticket-level goal hierarchy + non-goals (§2.10)


class Ticket(BaseModel):
    content: TicketContent
    path: Path


class WorkUnit(BaseModel):
    """One bounded unit of work — a coding change (or, later, a research sub-question).

    Set by `big_plan`; see docs/implementation-plan-v2.md §3.2/§3.3/§3.7.
    """

    id: str
    title: str
    intent: str = ""  # one-line rationale from big_plan; surfaced at the approval gate (§3.4)
    dod: dict = {}  # coding: test classes | research: coverage criteria
    status: ChangeStatus = ChangeStatus.PENDING
    ledger_path: Optional[Path] = None
    # coding-specific
    soft_loc: Optional[int] = None  # soft size budget for this change
    needs_research: bool = False  # → run inner_plan_and_research first
    needs_planning: bool = False  # → write inner_plan.md first
    inner_plan_path: Optional[Path] = None  # pointer to the low-level plan, if any


class AgentState(BaseModel):
    status: Status
    step: int  # telemetry only
    artifact: Mapping[str, Any]
    ticket_id: Optional[str] = None
    ticket: Optional[Ticket] = None
    repo_path: Optional[Path] = None
    plan_path: Optional[Path] = None  # plan.md (coding) / brief.md (research)
    units: list[WorkUnit] = []  # changes or sub-questions
    current_unit_id: Optional[str] = None
    has_open_questions: bool = False  # set by big_plan; gates approve_plan (§3.2)
    attempts: int = 0
    replans: int = 0  # shared cap across inner + outer replan
    autonomy: int = 1  # the autonomy knob (0=ask more … 3=decide)
    questions: Optional[list[dict]] = None  # structured UI payload (q + options)
    answers: Optional[str] = None
    # HITL resume payload {approved, answers, feedback}; phase-1 §3.4
    approval: Optional[dict] = None
    complexity: Optional[TicketComplexity] = None
