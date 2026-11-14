from __future__ import annotations

import hashlib
import json
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


def comment_order_key(comment: Comment) -> tuple[datetime, int]:
    return (comment.created_at, comment.id)


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
    dependency_type: str = "blocks"


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
    description: str = ""
    acceptance_criteria: str = ""

    def requirements_digest(self) -> str:
        """Canonical reviewed requirements, excluding scheduling and discussion state."""
        context = {
            "id": self.id,
            "title": self.title,
            "issue_type": self.issue_type.value,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "dependencies": sorted((dep.id, dep.dependency_type) for dep in self.dependencies),
        }
        canonical = json.dumps(context, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()
