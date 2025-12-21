from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.application.runner import TRUNCATED_NOTICE, EpicRunLoop, _cap_comment_body
from beadsflow.domain.models import Comment, Issue, IssueStatus, IssueType
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandSpec
from beadsflow.settings import Profile, RunSettings, Settings


@dataclass
class _FakeBeads:
    comments: list[tuple[str, str]]

    def comment(self, issue_id: str, text: str) -> None:
        self.comments.append((issue_id, text))


def test_runner_writes_log_and_includes_path_in_comment(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    repo_paths = RepoPaths(repo_root=tmp_path, beads_dir=beads_dir)

    settings = Settings(
        beads_dir=str(beads_dir),
        beads_no_db=False,
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string(
                    "python -c \"import sys; print('hello'); print('boom', file=sys.stderr); sys.exit(2)\""
                ),
                comment_mode="command",
                comment_prefix="",
                comment_suffix="",
                require_git_changes=False,
            )
        },
        reviewers={},
        run=RunSettings(
            max_iterations=1,
            resume_in_progress=True,
            selection_strategy="priority_then_oldest",
            on_command_failure="stop",
            command_timeout_seconds=60,
        ),
    )

    fake_beads = _FakeBeads(comments=[])
    loop = EpicRunLoop(
        beads=fake_beads,  # type: ignore[arg-type]
        epic_id="epic-1",
        settings=settings,
        repo_paths=repo_paths,
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )

    with pytest.raises(CommandError) as excinfo:
        loop._run_implementer(issue_id="issue-1")  # noqa: SLF001

    assert "Implementer failed with code 2" in str(excinfo.value)
    assert fake_beads.comments
    _issue_id, comment = fake_beads.comments[-1]
    assert "implementer command failed (exit 2)." in comment
    assert "Log:" in comment
    assert "Output:" in comment

    logs_dir = beads_dir / "logs" / "beadsflow"
    log_files = list(logs_dir.glob("issue-1.implementer.*.log"))
    assert log_files, "expected a log file to be written"
    content = log_files[0].read_text(encoding="utf-8")
    assert "returncode=2" in content
    assert "boom" in content


@dataclass
class _FakeBeadsWithIssues:
    comments: dict[str, list[Comment]]

    def comment(self, issue_id: str, text: str) -> None:
        now = datetime.now(UTC)
        entries = self.comments.setdefault(issue_id, [])
        entries.append(Comment(id=len(entries) + 1, author="beadsflow", text=text, created_at=now))

    def get_issue(self, issue_id: str) -> Issue:
        now = datetime.now(UTC)
        return Issue(
            id=issue_id,
            title="Test",
            status=IssueStatus.OPEN,
            priority=2,
            issue_type=IssueType.TASK,
            created_at=now,
            updated_at=now,
            dependencies=[],
            dependents=[],
            comments=self.comments.get(issue_id, []),
        )


def test_runner_posts_stdout_comment(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    repo_paths = RepoPaths(repo_root=tmp_path, beads_dir=beads_dir)

    settings = Settings(
        beads_dir=str(beads_dir),
        beads_no_db=False,
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string("python -c \"print('Ready for review:\\n\\nSummary')\""),
                comment_mode="stdout",
                comment_prefix="",
                comment_suffix="",
                require_git_changes=False,
            )
        },
        reviewers={},
        run=RunSettings(
            max_iterations=1,
            resume_in_progress=True,
            selection_strategy="priority_then_oldest",
            on_command_failure="stop",
            command_timeout_seconds=60,
        ),
    )

    fake_beads = _FakeBeadsWithIssues(comments={})
    loop = EpicRunLoop(
        beads=fake_beads,  # type: ignore[arg-type]
        epic_id="epic-1",
        settings=settings,
        repo_paths=repo_paths,
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )

    loop._run_implementer(issue_id="issue-1")  # noqa: SLF001
    assert fake_beads.comments["issue-1"][0].text.startswith("Ready for review:")


def test_runner_rejects_stdout_without_marker(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    repo_paths = RepoPaths(repo_root=tmp_path, beads_dir=beads_dir)

    settings = Settings(
        beads_dir=str(beads_dir),
        beads_no_db=False,
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string("python -c \"print('No marker here')\""),
                comment_mode="stdout",
                comment_prefix="",
                comment_suffix="",
                require_git_changes=False,
            )
        },
        reviewers={},
        run=RunSettings(
            max_iterations=1,
            resume_in_progress=True,
            selection_strategy="priority_then_oldest",
            on_command_failure="stop",
            command_timeout_seconds=60,
        ),
    )

    fake_beads = _FakeBeadsWithIssues(comments={})
    loop = EpicRunLoop(
        beads=fake_beads,  # type: ignore[arg-type]
        epic_id="epic-1",
        settings=settings,
        repo_paths=repo_paths,
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )

    with pytest.raises(CommandError) as excinfo:
        loop._run_implementer(issue_id="issue-1")  # noqa: SLF001

    assert "output missing expected marker" in str(excinfo.value)
    assert fake_beads.comments["issue-1"]
    assert "output missing expected marker" in fake_beads.comments["issue-1"][0].text


def test_runner_rejects_empty_stdout_comment(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    repo_paths = RepoPaths(repo_root=tmp_path, beads_dir=beads_dir)

    settings = Settings(
        beads_dir=str(beads_dir),
        beads_no_db=False,
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string('python -c ""'),
                comment_mode="stdout",
                comment_prefix="",
                comment_suffix="",
                require_git_changes=False,
            )
        },
        reviewers={},
        run=RunSettings(
            max_iterations=1,
            resume_in_progress=True,
            selection_strategy="priority_then_oldest",
            on_command_failure="stop",
            command_timeout_seconds=60,
        ),
    )

    fake_beads = _FakeBeadsWithIssues(comments={})
    loop = EpicRunLoop(
        beads=fake_beads,  # type: ignore[arg-type]
        epic_id="epic-1",
        settings=settings,
        repo_paths=repo_paths,
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )

    with pytest.raises(CommandError) as excinfo:
        loop._run_implementer(issue_id="issue-1")  # noqa: SLF001

    assert "produced no stdout" in str(excinfo.value)
    assert fake_beads.comments["issue-1"]
    assert "produced no stdout" in fake_beads.comments["issue-1"][0].text


def test_cap_comment_body_truncates_by_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_LINES", "2")
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_BYTES", "0")

    body = "line-1\nline-2\nline-3\n"
    capped = _cap_comment_body(body=body, prefix="", suffix="")

    assert capped.startswith("line-1\nline-2\n")
    assert "line-3" not in capped
    assert TRUNCATED_NOTICE.strip() in capped


def test_cap_comment_body_truncates_by_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_LINES", "0")
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_BYTES", "60")

    prefix = "Ready for review:\n"
    suffix = "\nValidation:\n- uv run pytest"
    body = "A" * 200
    capped = _cap_comment_body(body=body, prefix=prefix, suffix=suffix)

    comment_text = f"{prefix}{capped}{suffix}"
    assert len(comment_text.encode("utf-8")) <= 60
    assert TRUNCATED_NOTICE.strip() in capped


def test_cap_comment_body_no_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_LINES", "0")
    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_BYTES", "0")

    body = "line-1\nline-2\n"
    capped = _cap_comment_body(body=body, prefix="pre-", suffix="-suf")

    assert capped == body
