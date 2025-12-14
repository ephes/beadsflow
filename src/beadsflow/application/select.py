from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import assert_never

from beadsflow.application.phase import Phase, SelectedWork
from beadsflow.domain.models import Comment, IssueStatus, IssueSummary, Marker


def _priority_sort_key(issue: IssueSummary) -> tuple[int, datetime, str]:
    return (issue.priority, issue.created_at, issue.id)


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
        first = line.strip()
        if not first:
            continue
        if first.startswith("Ready for review:"):
            return Marker.READY_FOR_REVIEW
        if first == "LGTM" or first.startswith("LGTM "):
            return Marker.LGTM
        if first.startswith("Changes requested:"):
            return Marker.CHANGES_REQUESTED
        return None
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
