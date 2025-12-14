from __future__ import annotations

from datetime import UTC, datetime

import pytest

from beadsflow.application.phase import Phase
from beadsflow.application.select import (
    determine_phase_from_comments,
    latest_marker,
    marker_from_comment,
    select_next_child,
)
from beadsflow.domain.models import Comment, IssueStatus, IssueSummary, Marker


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_latest_marker_picks_last_marker_comment() -> None:
    comments = [
        Comment(id=1, author="a", text="Ready for review:", created_at=_dt("2025-01-01T00:00:00")),
        Comment(id=2, author="b", text="Changes requested: fix X", created_at=_dt("2025-01-02T00:00:00")),
        Comment(id=3, author="c", text="LGTM", created_at=_dt("2025-01-03T00:00:00")),
    ]
    assert latest_marker(comments) is Marker.LGTM


def test_determine_phase_defaults_to_implement() -> None:
    assert determine_phase_from_comments([]) is Phase.IMPLEMENT


def test_determine_phase_ready_for_review_means_review() -> None:
    comments = [Comment(id=1, author="a", text="Ready for review:", created_at=_dt("2025-01-01T00:00:00"))]
    assert determine_phase_from_comments(comments) is Phase.REVIEW


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ready for review:", Marker.READY_FOR_REVIEW),
        ("\n\nReady for review:", Marker.READY_FOR_REVIEW),
        ("- Ready for review:", Marker.READY_FOR_REVIEW),
        ("**Ready for review:**", Marker.READY_FOR_REVIEW),
        ("`Ready for review:`", Marker.READY_FOR_REVIEW),
        ("LGTM", Marker.LGTM),
        ("LGTM.", Marker.LGTM),
        ("- **LGTM**", Marker.LGTM),
        ("`LGTM`", Marker.LGTM),
        ("Changes requested: please update", Marker.CHANGES_REQUESTED),
        ("- Changes requested: please update", Marker.CHANGES_REQUESTED),
        ("**Changes requested:** please update", Marker.CHANGES_REQUESTED),
        ("`Changes requested:` please update", Marker.CHANGES_REQUESTED),
    ],
)
def test_marker_from_comment_tolerates_common_markdown(text: str, expected: Marker) -> None:
    comment = Comment(id=1, author="a", text=text, created_at=_dt("2025-01-01T00:00:00"))
    assert marker_from_comment(comment) is expected


def test_marker_from_comment_finds_marker_not_on_first_line() -> None:
    comment = Comment(
        id=1,
        author="a",
        text="Some intro text\n\nLGTM\n\nMore details",
        created_at=_dt("2025-01-01T00:00:00"),
    )
    assert marker_from_comment(comment) is Marker.LGTM


def test_select_next_child_priority_then_oldest_then_id() -> None:
    children = [
        IssueSummary(
            id="a",
            title="a",
            status=IssueStatus.OPEN,
            priority=2,
            created_at=_dt("2025-01-02T00:00:00"),
        ),
        IssueSummary(
            id="b",
            title="b",
            status=IssueStatus.OPEN,
            priority=1,
            created_at=_dt("2025-01-03T00:00:00"),
        ),
        IssueSummary(
            id="c",
            title="c",
            status=IssueStatus.OPEN,
            priority=1,
            created_at=_dt("2025-01-01T00:00:00"),
        ),
    ]

    ready = {"a", "b", "c"}

    def is_ready(issue_id: str) -> bool:
        return issue_id in ready

    selected = select_next_child(children=children, resume_in_progress=False, is_ready=is_ready)
    assert selected is not None
    assert selected.id == "c"


def test_select_next_child_skips_not_ready() -> None:
    children = [
        IssueSummary(
            id="a",
            title="a",
            status=IssueStatus.OPEN,
            priority=1,
            created_at=_dt("2025-01-01T00:00:00"),
        ),
        IssueSummary(
            id="b",
            title="b",
            status=IssueStatus.OPEN,
            priority=1,
            created_at=_dt("2025-01-02T00:00:00"),
        ),
    ]

    def is_ready(issue_id: str) -> bool:
        return issue_id == "b"

    selected = select_next_child(children=children, resume_in_progress=False, is_ready=is_ready)
    assert selected is not None
    assert selected.id == "b"
