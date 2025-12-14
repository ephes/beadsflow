from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import assert_never

from beadsflow.application.phase import Phase, SelectedWork
from beadsflow.domain.models import Comment, IssueStatus, IssueSummary, Marker


def _priority_sort_key(issue: IssueSummary) -> tuple[int, datetime, str]:
    return (issue.priority, issue.created_at, issue.id)


def _normalize_marker_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith(">"):
        stripped = stripped.lstrip(">").lstrip()
    stripped = re.sub(r"^([-*+]|\d+[.)])\s+", "", stripped)

    # Be tolerant of common markdown wrappers around the marker.
    stripped = re.sub(r"^(\*\*|__|`)([^\s].*?)(\1)(\s|$)", r"\2\4", stripped)
    for _ in range(3):
        before = stripped
        stripped = stripped.strip()
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            stripped = stripped[2:-2]
        if stripped.startswith("__") and stripped.endswith("__") and len(stripped) > 4:
            stripped = stripped[2:-2]
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            stripped = stripped[1:-1]
        if stripped == before:
            break
    return stripped.strip()


def _marker_from_first_line(first: str) -> Marker | None:
    lower = first.lower()
    if lower == "ready for review" or lower.startswith("ready for review:"):
        return Marker.READY_FOR_REVIEW
    if first.upper().startswith("LGTM") and (len(first) == 4 or not first[4].isalnum()):
        return Marker.LGTM
    if lower == "changes requested" or lower.startswith("changes requested:"):
        return Marker.CHANGES_REQUESTED
    return None


def select_next_child(
    *,
    children: Iterable[IssueSummary],
    resume_in_progress: bool,
    is_ready: Callable[[str], bool],
) -> IssueSummary | None:
    eligible_statuses = {IssueStatus.OPEN}
    if resume_in_progress:
        eligible_statuses.add(IssueStatus.IN_PROGRESS)

    eligible = [child for child in children if child.status in eligible_statuses]
    for child in sorted(eligible, key=_priority_sort_key):
        if is_ready(child.id):
            return child
    return None


def marker_from_comment(comment: Comment) -> Marker | None:
    for line in comment.text.splitlines():
        first = _normalize_marker_line(line)
        if not first:
            continue
        marker = _marker_from_first_line(first)
        if marker is not None:
            return marker
    return None


def latest_marker(comments: Iterable[Comment]) -> Marker | None:
    marker: Marker | None = None
    for comment in sorted(comments, key=lambda c: c.created_at):
        maybe = marker_from_comment(comment)
        if maybe is not None:
            marker = maybe
    return marker


def determine_phase_from_comments(comments: Iterable[Comment]) -> Phase:
    marker = latest_marker(comments)
    match marker:
        case None:
            return Phase.IMPLEMENT
        case Marker.READY_FOR_REVIEW:
            return Phase.REVIEW
        case Marker.CHANGES_REQUESTED:
            return Phase.IMPLEMENT
        case Marker.LGTM:
            return Phase.CLOSE
        case _:
            assert_never(marker)


def determine_next_work(*, issue_id: str, comments: Iterable[Comment]) -> SelectedWork:
    return SelectedWork(issue_id=issue_id, phase=determine_phase_from_comments(comments))
