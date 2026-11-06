from __future__ import annotations

from pathlib import Path

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.application.review import (
    DEFAULT_COMMENT_MAX_BYTES,
    DEFAULT_DIFF_MAX_BYTES,
    TRUNCATED_NOTICE,
    _ensure_marker,
    _truncate_utf8,
    _truncate_with_notice,
    resolve_review_request,
)


def test_truncate_utf8_returns_empty_for_non_positive() -> None:
    assert _truncate_utf8("abc", 0) == ""
    assert _truncate_utf8("abc", -5) == ""


def test_truncate_utf8_drops_partial_multibyte() -> None:
    snowman = "\N{SNOWMAN}"
    text = f"hi{snowman}"
    assert _truncate_utf8(text, 4) == "hi"
    assert _truncate_utf8(text, 5) == text


def test_truncate_with_notice_no_truncation() -> None:
    text = "short"
    assert _truncate_with_notice(text, 64) == text


def test_truncate_with_notice_adds_notice() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    max_bytes = len(TRUNCATED_NOTICE.encode("utf-8")) + 2
    truncated = _truncate_with_notice(text, max_bytes)
    assert truncated.startswith("ab")
    assert truncated.endswith(TRUNCATED_NOTICE)
    assert len(truncated.encode("utf-8")) <= max_bytes


def test_ensure_marker_empty_output_defaults_to_changes_requested() -> None:
    assert _ensure_marker("") == "Changes requested:\n\n(no review output)"


def test_ensure_marker_keeps_first_marker_line() -> None:
    output = "LGTM\n\nLooks good"
    assert _ensure_marker(output) == output


def test_ensure_marker_rejects_first_approval_with_markdown() -> None:
    output = "**LGTM**\n\nLooks good"
    assert _ensure_marker(output) == "Changes requested:\n\n" + output


def test_ensure_marker_trims_leading_blank_lines() -> None:
    output = "\n\nLGTM\nAll good"
    assert _ensure_marker(output) == "LGTM\nAll good"


def test_ensure_marker_rejects_approval_from_later_line() -> None:
    output = "Looks good\n\nLGTM\nMore details"
    expected = "Changes requested:\n\nLooks good\n\nLGTM\nMore details"
    assert _ensure_marker(output) == expected


def test_ensure_marker_rejects_approval_from_later_line_with_markdown() -> None:
    output = "Looks good\n\n**LGTM**\nMore details"
    expected = "Changes requested:\n\nLooks good\n\n**LGTM**\nMore details"
    assert _ensure_marker(output) == expected


def test_ensure_marker_prepends_changes_requested_when_missing() -> None:
    output = "Needs work"
    assert _ensure_marker(output) == "Changes requested:\n\nNeeds work"


def test_resolve_review_request_requires_issue_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEADSFLOW_ISSUE_ID", raising=False)
    with pytest.raises(CommandError):
        resolve_review_request(
            issue_id=None,
            epic_id="E-1",
            beads_dir=None,
            beads_no_db=False,
            cli_command="claude",
            prompt_arg="-p",
            diff_max_bytes=1,
            comment_max_bytes=1,
        )


def test_resolve_review_request_requires_epic_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEADSFLOW_EPIC_ID", raising=False)
    with pytest.raises(CommandError):
        resolve_review_request(
            issue_id="I-1",
            epic_id=None,
            beads_dir=None,
            beads_no_db=False,
            cli_command="claude",
            prompt_arg="-p",
            diff_max_bytes=1,
            comment_max_bytes=1,
        )


def test_resolve_review_request_uses_env_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEADSFLOW_ISSUE_ID", "I-2")
    monkeypatch.setenv("BEADSFLOW_EPIC_ID", "E-2")
    monkeypatch.setenv("BEADS_DIR", ".beads-alt")
    monkeypatch.setenv("BEADSFLOW_BEADS_NO_DB", "yes")
    request = resolve_review_request(
        issue_id=None,
        epic_id=None,
        beads_dir=None,
        beads_no_db=False,
        cli_command="claude",
        prompt_arg="-p",
        diff_max_bytes=None,
        comment_max_bytes=None,
    )
    assert request.issue_id == "I-2"
    assert request.epic_id == "E-2"
    assert request.beads_dir == ".beads-alt"
    assert request.beads_no_db is True
    assert request.diff_max_bytes == DEFAULT_DIFF_MAX_BYTES
    assert request.comment_max_bytes == DEFAULT_COMMENT_MAX_BYTES


def test_reviewer_timeout_cli_fails_without_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import os
    import shlex
    import sys
    import time

    import beadsflow.entrypoints.cli as cli
    from beadsflow.application.review import _run_reviewer_command

    script = tmp_path / "reviewer.py"
    pidfile = tmp_path / "pid"
    script.write_text(
        "import os, pathlib, time\npathlib.Path("
        + repr(str(pidfile))
        + ").write_text(str(os.getpid()))\nprint('LGTM', flush=True)\ntime.sleep(30)\n"
    )

    def fake_review(request: object) -> str:
        from beadsflow.application.review import ReviewRequest

        assert isinstance(request, ReviewRequest)
        return _run_reviewer_command(
            cli_command=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
            prompt_arg="-p",
            prompt="test",
            timeout_seconds=request.timeout_seconds,
        )

    monkeypatch.setattr(cli, "run_review", fake_review)
    started = time.monotonic()
    assert cli.main(["review", "--issue-id", "I", "--epic-id", "E", "--timeout-seconds", "3"]) == 1
    assert time.monotonic() - started < 10
    output = capsys.readouterr()
    assert output.out == ""
    assert "timed out after 3 seconds" in output.err
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize("timeout", [0, -1])
def test_invalid_review_timeout_fails_before_review(
    monkeypatch: pytest.MonkeyPatch, timeout: int, capsys: pytest.CaptureFixture[str]
) -> None:
    import beadsflow.entrypoints.cli as cli

    def forbidden_review(request: object) -> str:
        raise AssertionError("must validate before review")

    monkeypatch.setattr(cli, "run_review", forbidden_review)
    assert cli.main(["review", "--issue-id", "I", "--epic-id", "E", "--timeout-seconds", str(timeout)]) == 1
    assert capsys.readouterr().out == ""


def test_reviewer_success_and_failure() -> None:
    import shlex
    import sys

    from beadsflow.application.review import _run_reviewer_command

    command = f"{shlex.quote(sys.executable)} -c "
    assert (
        _run_reviewer_command(cli_command=command + shlex.quote("print('LGTM')"), prompt_arg="-p", prompt="test")
        == "LGTM\n"
    )
    with pytest.raises(CommandError, match="exit 3.*broken"):
        _run_reviewer_command(
            cli_command=command + shlex.quote("import sys; print('broken', file=sys.stderr); sys.exit(3)"),
            prompt_arg="-p",
            prompt="test",
        )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("\nLGTM\nChecked", "LGTM\nChecked"),
        ("Unexpected reviewer prose", "Changes requested:\n\nUnexpected reviewer prose"),
        ("LGTM\nChanges requested: fix", "Changes requested:\n\nLGTM\nChanges requested: fix"),
    ],
)
def test_run_review_assembles_context_and_forwards_request(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: str
) -> None:
    from datetime import UTC, datetime

    import beadsflow.application.review as review
    from beadsflow.domain.models import Comment, Issue, IssueStatus, IssueType

    now = datetime(2026, 1, 1, tzinfo=UTC)
    issue = Issue(
        id="child-1",
        title="Synthetic title",
        status=IssueStatus.IN_PROGRESS,
        priority=0,
        issue_type=IssueType.TASK,
        created_at=now,
        updated_at=now,
        dependencies=[],
        dependents=[],
        description="Synthetic description",
        acceptance_criteria="Synthetic criteria",
        comments=[
            Comment(1, "older-ready", "Ready for review: obsolete", now),
            Comment(2, "older-review", "LGTM", now),
            Comment(10, "current-ready", "Ready for review: " + "雪" * 30 + "READY_TAIL", now),
            Comment(11, "current-review", "Changes requested: " + "雪" * 30 + "REVIEW_TAIL", now),
            Comment(12, "bystander", "No marker here", now),
        ],
    )
    events: list[object] = []

    class FakeBeads:
        def __init__(self, *, beads_dir: str, no_db: bool) -> None:
            events.append((beads_dir, no_db))

        def get_issue(self, issue_id: str) -> Issue:
            events.append(issue_id)
            return issue

    git_outputs = {
        ("status", "-sb"): "## synthetic-branch",
        ("diff", "--stat"): "unstaged-stat",
        ("diff", "--staged", "--stat"): "staged-stat",
        ("diff", "--patch"): "unstaged-patch-" + "雪" * 30 + "PATCH_TAIL",
        ("diff", "--staged", "--patch"): "staged-patch",
    }

    def fake_git(args: list[str]) -> str:
        events.append(tuple(args))
        return git_outputs[tuple(args)]

    recorded: list[dict[str, object]] = []

    def reviewer(**kwargs: object) -> str:
        recorded.append(kwargs)
        return output

    monkeypatch.setattr(review, "BeadsCli", FakeBeads)
    monkeypatch.setattr(review, "_run_git", fake_git)
    monkeypatch.setattr(review, "_run_reviewer_command", reviewer)
    request = review.ReviewRequest(
        "child-1", "epic-1", "synthetic-beads", True, "fake-review --flag", "--prompt", 55, 45, 17
    )
    assert review.run_review(request) == expected
    assert events[:2] == [("synthetic-beads", True), "child-1"]
    assert set(events[2:]) == set(git_outputs)
    assert len(recorded) == 1
    call = recorded[0]
    assert call["cli_command"] == "fake-review --flag"
    assert call["prompt_arg"] == "--prompt"
    assert call["timeout_seconds"] == 17
    prompt = call["prompt"]
    assert isinstance(prompt, str)
    for required in (
        "Review bead child-1 under epic epic-1.",
        "Title: Synthetic title",
        "Status: in_progress",
        "Synthetic description",
        "Synthetic criteria",
        "current-ready",
        "current-review",
        "## synthetic-branch",
        "Unstaged:\nunstaged-stat",
        "Staged:\nstaged-stat",
    ):
        assert required in prompt
    assert "obsolete" not in prompt and "older-review" not in prompt and "bystander" not in prompt
    for heading, next_heading, prefix in (
        ("READY_COMMENT", "LAST_REVIEW_COMMENT", "Ready for review:"),
        ("LAST_REVIEW_COMMENT", "Git status:", "Changes requested:"),
    ):
        section = prompt.split(heading, 1)[1].split(next_heading, 1)[0].strip()
        body = section.split("\n", 1)[1]
        assert body.startswith(prefix) and body.endswith(TRUNCATED_NOTICE)
        assert len(body.encode("utf-8")) <= 45
        assert "雪" in body
    patch = prompt.split("Git diff (patch, truncated):\n", 1)[1].split("Respond with a single", 1)[0].strip()
    assert "Unstaged:\nunstaged-patch-" in patch
    assert len(patch.encode("utf-8")) <= 55
    assert patch.endswith(TRUNCATED_NOTICE)
    assert prompt.count(TRUNCATED_NOTICE) == 3
    assert "PATCH_TAIL" not in prompt and "READY_TAIL" not in prompt and "REVIEW_TAIL" not in prompt
    assert "\ufffd" not in prompt


def test_run_review_stops_on_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    import beadsflow.application.review as review
    from beadsflow.domain.models import Issue, IssueStatus, IssueType

    now = datetime(2026, 1, 1, tzinfo=UTC)
    issue = Issue("child-1", "Synthetic", IssueStatus.OPEN, 0, IssueType.TASK, now, now, [], [], [])
    monkeypatch.setattr(review.BeadsCli, "get_issue", lambda self, issue_id: issue)

    def missing(*args: object, **kwargs: object) -> str:
        raise CommandError("Cannot execute git (FileNotFoundError)")

    def forbidden(**kwargs: object) -> str:
        pytest.fail("reviewer must not run without Git context")

    monkeypatch.setattr(review, "capture_git", missing)
    monkeypatch.setattr(review, "_run_reviewer_command", forbidden)
    request = review.ReviewRequest("child-1", "epic-1", "synthetic", False, "fake", "-p", 55, 45)
    with pytest.raises(CommandError, match="Cannot execute git"):
        review.run_review(request)
