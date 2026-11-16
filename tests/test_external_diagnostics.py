from __future__ import annotations

import json
import traceback
from typing import Any

import pytest

import beadsflow.application.review as review
import beadsflow.infra.beads_cli as bd
from beadsflow.application.errors import BeadsError, CommandError
from beadsflow.infra.run_command import CommandResult

SECRET = "synthetic-private-prompt-comment-\x1b]52;secret\x07"


def assert_private_error(error: BaseException) -> None:
    assert "synthetic-private-prompt-comment" not in "".join(traceback.format_exception(error))
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert "synthetic-private-prompt-comment" not in str(current)
        assert "\x1b" not in str(current)
        assert len(str(current)) < 600
        pending.extend(exc for exc in (current.__cause__, current.__context__) if exc is not None)


@pytest.mark.parametrize("failure", ["exit", "launch"])
def test_reviewer_failure_does_not_expose_command_output_or_exception_chain(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def run(*, argv: list[str], **kwargs: Any) -> CommandResult:
        if failure == "launch":
            raise OSError(SECRET)
        return CommandResult(argv, 7, SECRET, SECRET)

    monkeypatch.setattr(review, "run_command", run)
    with pytest.raises(CommandError) as caught:
        review._run_reviewer_command(cli_command="synthetic-reviewer", prompt_arg="-p", prompt=SECRET)
    assert_private_error(caught.value)
    assert ("exit 7" if failure == "exit" else "OSError") in str(caught.value)


@pytest.mark.parametrize("failure", ["initial", "sync", "retry", "launch", "json", "utf8", "schema"])
def test_bd_failure_does_not_expose_command_output_or_exception_chain(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls = 0

    def run(*, argv: list[str], **kwargs: Any) -> CommandResult:
        nonlocal calls
        calls += 1
        if failure == "launch":
            raise OSError(SECRET)
        if failure == "utf8":
            raise UnicodeDecodeError("utf-8", SECRET.encode() + b"\xff", 0, 1, SECRET)
        if failure == "json":
            return CommandResult(argv, 0, '{"' + SECRET + '": 1, "' + SECRET + '": 2}', "")
        if failure == "schema":
            return CommandResult(argv, 0, json.dumps([{"id": "child", "title": "Synthetic", "status": SECRET}]), "")
        if calls == 1 and failure in {"sync", "retry"}:
            return CommandResult(argv, 1, SECRET, "Database out of sync with JSONL " + SECRET)
        if calls == 2 and failure == "retry":
            return CommandResult(argv, 0, "", "")
        return CommandResult(argv, 7, SECRET, SECRET)

    monkeypatch.setattr(bd, "run_command", run)
    with pytest.raises(BeadsError) as caught:
        bd.BeadsCli(".beads").get_issue("child")
    assert_private_error(caught.value)


def test_comment_failure_cli_diagnostic_never_contains_comment_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*, argv: list[str], **kwargs: Any) -> CommandResult:
        assert argv[-1] == SECRET
        return CommandResult(argv, 7, SECRET, SECRET)

    monkeypatch.setattr(bd, "run_command", run)
    with pytest.raises(BeadsError) as caught:
        bd.BeadsCli(".beads").comment("child", SECRET)
    assert_private_error(caught.value)


@pytest.mark.parametrize("boundary", ["bd", "reviewer"])
def test_review_cli_prints_only_safe_failure_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], boundary: str
) -> None:
    from datetime import UTC, datetime

    from beadsflow.domain.models import Issue, IssueStatus, IssueType
    from beadsflow.entrypoints.cli import main

    def run(*, argv: list[str], **kwargs: Any) -> CommandResult:
        return CommandResult(argv, 7, SECRET, SECRET)

    if boundary == "bd":
        monkeypatch.setattr(bd, "run_command", run)
    else:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        issue = Issue("child", "Synthetic", IssueStatus.OPEN, 0, IssueType.TASK, now, now, [], [], [])
        monkeypatch.setattr(bd.BeadsCli, "get_issue", lambda self, issue_id: issue)
        monkeypatch.setattr(review, "_collect_git_context", lambda *args, **kwargs: ("status", "stat", "patch"))
        monkeypatch.setattr(review, "run_command", run)
    assert main(["review", "--issue-id", "child", "--epic-id", "epic", "--cli", "synthetic"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "exit 7" in captured.err
    assert "synthetic-private-prompt-comment" not in captured.err
    assert "\x1b" not in captured.err
    assert len(captured.err) < 600
