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
    assert "Output:" not in comment
    assert "boom" not in comment
    assert "hello" not in comment

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


def test_runner_rejects_conflict_lost_by_truncation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import logging

    from beadsflow.domain.models import Marker
    from beadsflow.infra.run_command import CommandResult

    monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_BYTES", "100")
    fake = _FakeBeads(comments=[])
    loop = EpicRunLoop(
        beads=fake,  # type: ignore[arg-type]
        epic_id="epic",
        settings=Settings.defaults(),
        repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=tmp_path / ".beads"),
        implementer_name=None,
        reviewer_name=None,
        logger=logging.getLogger("test"),
    )
    result = CommandResult(argv=[], returncode=0, stdout="LGTM\n" + "x" * 1000 + "\nChanges requested: fix", stderr="")
    with pytest.raises(CommandError, match="changed verdict"):
        loop._comment_from_stdout(
            issue_id="issue",
            result=result,
            prefix="",
            suffix="",
            expected_markers={Marker.LGTM, Marker.CHANGES_REQUESTED},
            phase="reviewer",
        )
    assert fake.comments == []


def diagnostic_loop(tmp_path: Path) -> EpicRunLoop:
    import logging

    return EpicRunLoop(
        beads=_FakeBeads(comments=[]),  # type: ignore[arg-type]
        epic_id="epic",
        settings=Settings.defaults(),
        repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=tmp_path / ".beads"),
        implementer_name=None,
        reviewer_name=None,
        logger=logging.getLogger("test.private-diagnostics"),
    )


def test_output_overflow_cannot_post_approval(tmp_path: Path) -> None:
    import subprocess
    import sys
    from dataclasses import replace

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    beads = _FakeBeadsWithIssues(comments={})
    loop = replace(diagnostic_loop(tmp_path), beads=beads)  # type: ignore[arg-type]
    profile = Profile(
        command=CommandSpec(
            argv=[sys.executable, "-c", "print('LGTM'); print('x' * (9 * 1024 * 1024)); print('LGTM')"]
        ),
        comment_mode="stdout",
        comment_prefix="",
        comment_suffix="",
        require_git_changes=False,
    )
    loop = replace(loop, reviewer_name="test", settings=replace(loop.settings, reviewers={"test": profile}))
    with pytest.raises(CommandError, match="failed with code 125"):
        loop._run_reviewer(issue_id="issue")
    assert len(beads.comments["issue"]) == 1
    assert "command failed (exit 125)" in beads.comments["issue"][0].text


def test_failure_logs_are_private_and_unique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import stat
    import time

    from beadsflow.infra.run_command import CommandResult

    loop = diagnostic_loop(tmp_path)
    directory = loop.repo_paths.beads_dir / "logs" / "beadsflow"
    directory.mkdir(parents=True)
    monkeypatch.setattr(time, "strftime", lambda *_args: "20000101-000000")
    result = CommandResult(argv=["synthetic", "private-argument"], returncode=1, stdout="out", stderr="err")
    mask = os.umask(0o022)
    try:
        first = loop._write_command_log(issue_id="issue", phase="reviewer", result=result)
        second = loop._write_command_log(issue_id="issue", phase="reviewer", result=result)
    finally:
        os.umask(mask)
    assert first != second
    assert "20000101-000000" in first.name
    assert "20000101-000000" in second.name
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in (first, second):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "private-argument" in path.read_text()
        assert "stdout:\n  out" in path.read_text()


@pytest.mark.parametrize("component", [".beads", "logs", "beadsflow"])
def test_failure_log_rejects_redirected_directory(tmp_path: Path, component: str) -> None:
    from beadsflow.infra.run_command import CommandResult

    loop = diagnostic_loop(tmp_path)
    directory = loop.repo_paths.beads_dir / "logs" / "beadsflow"
    target = tmp_path / "outside"
    target.mkdir()
    parts = {".beads": loop.repo_paths.beads_dir, "logs": directory.parent, "beadsflow": directory}
    redirected = parts[component]
    redirected.parent.mkdir(parents=True, exist_ok=True)
    redirected.symlink_to(target, target_is_directory=True)
    with pytest.raises(CommandError, match="diagnostic log"):
        loop._write_command_log(
            issue_id="issue",
            phase="reviewer",
            result=CommandResult(argv=[], returncode=1, stdout="private", stderr=""),
        )
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("issue_id,phase", [("../escape", "reviewer"), ("issue", "../../escape")])
def test_failure_log_refuses_path_components(tmp_path: Path, issue_id: str, phase: str) -> None:
    from beadsflow.infra.run_command import CommandResult

    with pytest.raises(CommandError, match="diagnostic log"):
        diagnostic_loop(tmp_path)._write_command_log(
            issue_id=issue_id, phase=phase, result=CommandResult(argv=[], returncode=1, stdout="private", stderr="")
        )


def test_profile_arguments_are_not_displayed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging
    import sys
    from dataclasses import replace

    from beadsflow.application.phase import Phase

    loop = diagnostic_loop(tmp_path)
    profile = Profile(
        command=CommandSpec(argv=[sys.executable, "-c", "pass", "synthetic-secret"]),
        comment_mode="command",
        comment_prefix="",
        comment_suffix="",
        require_git_changes=False,
    )
    loop = replace(loop, reviewer_name="test", settings=replace(loop.settings, reviewers={"test": profile}))
    with caplog.at_level(logging.INFO):
        loop._run_profile_command(profile=profile, issue_id="issue", phase="reviewer")
        loop._log_dry_run(phase=Phase.REVIEW, issue_id="issue")
    assert "reviewer" in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_log_creation_collision_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from beadsflow.infra import command_log
    from beadsflow.infra.run_command import CommandResult

    loop = diagnostic_loop(tmp_path)
    directory = loop.repo_paths.beads_dir / "logs" / "beadsflow"
    directory.mkdir(parents=True)
    outside = tmp_path / "sentinel"
    outside.write_text("untouched")
    monkeypatch.setattr(command_log.time, "strftime", lambda *_args: "20000101-000000")
    collision = directory / "issue.reviewer.20000101-000000.fixed.log"
    collision.symlink_to(outside)
    monkeypatch.setattr(command_log.secrets, "token_hex", lambda _size: "fixed")
    with pytest.raises(CommandError, match="diagnostic log"):
        loop._write_command_log(
            issue_id="issue",
            phase="reviewer",
            result=CommandResult(argv=[], returncode=1, stdout="private", stderr=""),
        )
    assert outside.read_text() == "untouched"
    assert collision.is_symlink()


@pytest.mark.parametrize("overflow", [False, True])
def test_log_failure_preserves_profile_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, overflow: bool
) -> None:
    import sys

    loop = diagnostic_loop(tmp_path)
    directory = loop.repo_paths.beads_dir
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "logs").symlink_to(outside, target_is_directory=True)
    script = "print('x' * (9 * 1024 * 1024))" if overflow else "import sys; print('failed'); sys.exit(2)"
    code = 125 if overflow else 2
    profile = Profile(CommandSpec([sys.executable, "-c", script]), "stdout", "", "", False)
    with pytest.raises(CommandError, match=f"Reviewer failed with code {code}"):
        loop._run_profile_command(profile=profile, issue_id="issue", phase="reviewer")
    assert len(loop.beads.comments) == 1  # type: ignore[attr-defined]
    comment = loop.beads.comments[0][1]  # type: ignore[attr-defined]
    assert f"command failed (exit {code})" in comment
    assert "Output:" not in comment
    assert "output exceeded" not in comment
    assert "Log:" not in comment
    assert "Cannot write diagnostic log" in caplog.text
    assert list(outside.iterdir()) == []


def test_implementer_change_check_failure_posts_trusted_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    settings = Settings(
        beads_dir=str(beads_dir),
        beads_no_db=False,
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string("python -c \"print('ok')\""),
                comment_mode="command",
                comment_prefix="",
                comment_suffix="",
                require_git_changes=True,
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
        repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=beads_dir),
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )
    signatures = iter(["before"])

    def signature(self: EpicRunLoop, *, exclude_beads: bool = False) -> str:
        try:
            return next(signatures)
        except StopIteration:
            raise CommandError("Cannot fingerprint untracked path: secret-name.bin") from None

    monkeypatch.setattr(EpicRunLoop, "_git_signature", signature)
    with pytest.raises(CommandError, match="could not collect Git evidence"):
        loop._run_implementer(issue_id="issue-1")  # noqa: SLF001
    assert fake_beads.comments == [
        ("issue-1", "Implementer change check could not collect Git evidence; stopping. See local logs.")
    ]
    assert "secret-name.bin" in caplog.text
