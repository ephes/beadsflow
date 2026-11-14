import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from beadsflow.application.runner import EpicRunLoop
from beadsflow.domain.models import Dependency, Issue, IssueStatus, IssueSummary, IssueType
from beadsflow.infra.paths import RepoPaths
from beadsflow.settings import Settings


@pytest.mark.parametrize("external_status", list(IssueStatus))
@pytest.mark.parametrize("internal_status", [IssueStatus.OPEN, IssueStatus.CLOSED])
@pytest.mark.parametrize("child_status", [IssueStatus.OPEN, IssueStatus.IN_PROGRESS])
def test_all_dependencies_gate_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_status: IssueStatus,
    internal_status: IssueStatus,
    child_status: IssueStatus,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    child = Issue(
        id="child-1",
        title="Child",
        status=child_status,
        priority=1,
        issue_type=IssueType.TASK,
        created_at=now,
        updated_at=now,
        comments=[],
        dependents=[],
        dependencies=[
            Dependency("other-1", external_status),
            Dependency("child-2", internal_status),
            Dependency("epic-1", IssueStatus.OPEN, "parent-child"),
            Dependency("related-1", IssueStatus.OPEN, "related"),
            Dependency("source-1", IssueStatus.OPEN, "discovered-from"),
        ],
    )
    epic = replace(
        child,
        id="epic-1",
        issue_type=IssueType.EPIC,
        dependencies=[],
        dependents=[
            IssueSummary("child-1", "Child", child_status, 1, now),
            IssueSummary("child-2", "Sibling", IssueStatus.CLOSED, 2, now),
        ],
    )

    class FakeBeads:
        def get_issue(self, issue_id: str) -> Issue:
            return {epic.id: epic, child.id: child}[issue_id]

    commands: list[str] = []
    monkeypatch.setattr(EpicRunLoop, "_run_implementer", lambda self, *, issue_id: commands.append(issue_id))
    loop = EpicRunLoop(
        beads=FakeBeads(),  # type: ignore[arg-type]
        epic_id=epic.id,
        settings=Settings.defaults(),
        repo_paths=RepoPaths(tmp_path, tmp_path / ".beads"),
        implementer_name="synthetic",
        reviewer_name=None,
        logger=logging.getLogger("test"),
    )
    ready = external_status is IssueStatus.CLOSED and internal_status is IssueStatus.CLOSED
    assert loop.run(once=True, dry_run=False, max_iterations=1) == (0 if ready else 2)
    assert commands == ([child.id] if ready else [])


@pytest.mark.parametrize("current_status", list(IssueStatus))
@pytest.mark.parametrize("resume", [True, False])
def test_refreshed_child_status_gates_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current_status: IssueStatus, resume: bool
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = Issue("child-1", "First", current_status, 1, IssueType.TASK, now, now, [], [], [])
    second = replace(first, id="child-2", status=IssueStatus.OPEN)
    epic = replace(
        first,
        id="epic",
        issue_type=IssueType.EPIC,
        dependents=[IssueSummary(c.id, c.title, IssueStatus.OPEN, i, now) for i, c in enumerate((first, second))],
    )

    class FakeBeads:
        def get_issue(self, issue_id: str) -> Issue:
            return {epic.id: epic, first.id: first, second.id: second}[issue_id]

    dispatched: list[str] = []
    monkeypatch.setattr(EpicRunLoop, "_run_implementer", lambda self, *, issue_id: dispatched.append(issue_id))
    defaults = Settings.defaults()
    loop = EpicRunLoop(
        beads=FakeBeads(),  # type: ignore[arg-type]
        epic_id=epic.id,
        settings=replace(defaults, run=replace(defaults.run, resume_in_progress=resume)),
        repo_paths=RepoPaths(tmp_path, tmp_path / ".beads"),
        implementer_name="fake",
        reviewer_name=None,
        logger=logging.getLogger("test.status"),
    )
    assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
    eligible = current_status is IssueStatus.OPEN or (resume and current_status is IssueStatus.IN_PROGRESS)
    assert dispatched == [first.id if eligible else second.id]


@pytest.mark.parametrize("status", list(IssueStatus))
@pytest.mark.parametrize("dry_run", [False, True])
def test_completion_refreshes_closed_summaries_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status: IssueStatus,
    dry_run: bool,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    child = Issue("child", "Current child", status, 1, IssueType.TASK, now, now, [], [], [])
    epic = replace(
        child,
        id="epic",
        issue_type=IssueType.EPIC,
        dependents=[IssueSummary(child.id, "Stale child", IssueStatus.CLOSED, 1, now)],
    )
    reads: list[str] = []

    class FakeBeads:
        def get_issue(self, issue_id: str) -> Issue:
            reads.append(issue_id)
            return epic if issue_id == epic.id else child

    dispatched: list[str] = []
    monkeypatch.setattr(EpicRunLoop, "_run_implementer", lambda self, *, issue_id: dispatched.append(issue_id))
    loop = EpicRunLoop(
        beads=FakeBeads(),  # type: ignore[arg-type]
        epic_id=epic.id,
        settings=Settings.defaults(),
        repo_paths=RepoPaths(tmp_path, tmp_path / ".beads"),
        implementer_name="synthetic",
        reviewer_name=None,
        logger=logging.getLogger("test.completion"),
    )
    with caplog.at_level(logging.INFO):
        assert loop.run(once=True, dry_run=dry_run, max_iterations=1) == (2 if status is IssueStatus.BLOCKED else 0)
    assert reads == ["epic", "child"]
    assert ("Epic complete." in caplog.text) == (status is IssueStatus.CLOSED)
    eligible = status in {IssueStatus.OPEN, IssueStatus.IN_PROGRESS}
    assert dispatched == (["child"] if eligible and not dry_run else [])


@pytest.mark.parametrize("empty", [True, False])
def test_empty_epic_completes_but_child_read_failure_stops(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    empty: bool,
) -> None:
    from beadsflow.application.errors import BeadsError

    now = datetime(2026, 1, 1, tzinfo=UTC)
    epic = Issue(
        "epic",
        "Epic",
        IssueStatus.OPEN,
        1,
        IssueType.EPIC,
        now,
        now,
        [],
        [] if empty else [IssueSummary("child", "Child", IssueStatus.CLOSED, 1, now)],
        [],
    )

    class FakeBeads:
        def get_issue(self, issue_id: str) -> Issue:
            if issue_id != "epic":
                raise BeadsError("Synthetic child read failure")
            return epic

    loop = EpicRunLoop(
        beads=FakeBeads(),  # type: ignore[arg-type]
        epic_id=epic.id,
        settings=Settings.defaults(),
        repo_paths=RepoPaths(tmp_path, tmp_path / ".beads"),
        implementer_name=None,
        reviewer_name=None,
        logger=logging.getLogger("test.completion"),
    )
    with caplog.at_level(logging.INFO):
        if empty:
            assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
            assert "Epic complete." in caplog.text
        else:
            with pytest.raises(BeadsError, match="child read failure"):
                loop.run(once=True, dry_run=False, max_iterations=1)
            assert "Epic complete." not in caplog.text
