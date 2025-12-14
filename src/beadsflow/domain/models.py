from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class IssueStatus(Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    CLOSED = "closed"


class IssueType(Enum):
    BUG = "bug"
    FEATURE = "feature"
    TASK = "task"
    EPIC = "epic"
    CHORE = "chore"


class Marker(Enum):
    READY_FOR_REVIEW = "ready_for_review"
    LGTM = "lgtm"
    CHANGES_REQUESTED = "changes_requested"


@dataclass(frozen=True, slots=True)
class Comment:
    id: int
    author: str
    text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssueSummary:
    id: str
    title: str
    status: IssueStatus
    priority: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Dependency:
    id: str
    status: IssueStatus


@dataclass(frozen=True, slots=True)
class Issue:
    id: str
    title: str
    status: IssueStatus
    priority: int
    issue_type: IssueType
    created_at: datetime
    updated_at: datetime
    dependencies: list[Dependency]
    dependents: list[IssueSummary]
    comments: list[Comment]
