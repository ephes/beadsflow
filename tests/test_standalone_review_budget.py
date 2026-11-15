from __future__ import annotations

import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import beadsflow.application.review as review
import beadsflow.entrypoints.cli as cli
from beadsflow.application.errors import CommandError
from beadsflow.domain.models import Issue, IssueStatus, IssueType


@pytest.mark.parametrize("marker_first", [True, False])
def test_standalone_overflow_prints_no_approval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], marker_first: bool
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    issue = Issue("child", "Synthetic", IssueStatus.OPEN, 0, IssueType.TASK, now, now, [], [], [])
    monkeypatch.setattr(review.BeadsCli, "get_issue", lambda self, issue_id: issue)
    monkeypatch.setattr(review, "_collect_git_context", lambda limit: ("status", "stat", "patch"))
    script = (
        "import os\n"
        + ("os.write(1, b'LGTM\\n')\n" if marker_first else "")
        + "for _ in range(65):\n    os.write(1, b'x' * 65536)\n    os.write(2, b'x' * 65536)\n"
        + ("" if marker_first else "os.write(1, b'\\nLGTM\\n')\n")
    )
    command = shlex.join([sys.executable, "-c", script])
    result = cli.main(["review", "--issue-id", "child", "--epic-id", "epic", "--cli", command])
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert "exit 125" in captured.err
    assert "output exceeded" in captured.err


def test_standalone_overflow_cleans_owned_descendant(tmp_path: Path) -> None:
    pids = tmp_path / "pids"
    script = """
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
def stop(*args):
    child.kill()
    child.wait()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
Path(sys.argv[1]).write_text(str(os.getpid()) + ' ' + str(child.pid))
print('LGTM', flush=True)
for _ in range(65):
    os.write(1, b'x' * 65536)
    os.write(2, b'x' * 65536)
# Keep the old implementation finite too, and reap the fixture child on every path.
time.sleep(1)
stop()
"""
    try:
        with pytest.raises(CommandError, match="exit 125"):
            review._run_reviewer_command(
                cli_command=shlex.join([sys.executable, "-c", script, str(pids)]), prompt_arg="-p", prompt="synthetic"
            )
        for pid in map(int, pids.read_text().split()):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if pids.exists():
            for pid in map(int, pids.read_text().split()):
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("code", [124, 125])
def test_reviewer_own_exit_code_is_not_reported_as_harness_limit(code: int) -> None:
    script = f"import sys; sys.stderr.write('synthetic reviewer failure'); sys.exit({code})"
    with pytest.raises(CommandError) as caught:
        review._run_reviewer_command(
            cli_command=shlex.join([sys.executable, "-c", script]), prompt_arg="-p", prompt="synthetic"
        )
    message = str(caught.value)
    assert f"exit {code}" in message
    assert "synthetic reviewer failure" in message
    assert "timed out" not in message
    assert "output exceeded" not in message


@pytest.mark.parametrize("section", ["title", "description", "acceptance_criteria", "status", "stat", "combined"])
def test_complete_prompt_overflow_refuses_launch_and_approval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], section: str
) -> None:
    from dataclasses import replace

    now = datetime(2026, 1, 1, tzinfo=UTC)
    issue = Issue("child", "Synthetic", IssueStatus.OPEN, 0, IssueType.TASK, now, now, [], [], [])
    large = "雪" * 24000
    issue = replace(
        issue,
        title=large if section == "title" else issue.title,
        description=large if section == "description" else "x" * 24000 if section == "combined" else "",
        acceptance_criteria=large
        if section == "acceptance_criteria"
        else "y" * 24000
        if section == "combined"
        else "",
    )
    monkeypatch.setattr(review.BeadsCli, "get_issue", lambda self, issue_id: issue)
    monkeypatch.setattr(
        review,
        "_collect_git_context",
        lambda limit: (
            large if section == "status" else "status",
            large if section == "stat" else "z" * 24000 if section == "combined" else "stat",
            "patch",
        ),
    )

    def forbidden(**kwargs: object) -> None:
        pytest.fail("oversized complete context must not launch a reviewer")

    monkeypatch.setattr(review, "run_command", forbidden)
    assert cli.main(["review", "--issue-id", "child", "--epic-id", "epic", "--cli", "synthetic-review"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "prompt exceeded" in captured.err
    assert "雪" not in captured.err


@pytest.mark.parametrize("size", [65535, 65536, 65537])
def test_prompt_budget_counts_utf8_bytes_at_boundary(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    from beadsflow.infra.run_command import CommandResult

    prompt = "雪" * (size // 3) + "x" * (size % 3)
    calls: list[str] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv[-1])
        return CommandResult(argv, 0, "LGTM", "")

    monkeypatch.setattr(review, "run_command", run)
    if size <= 65536:
        assert review._run_reviewer_command(cli_command="synthetic", prompt_arg="-p", prompt=prompt) == "LGTM"
        assert calls == [prompt]
    else:
        with pytest.raises(CommandError, match="prompt exceeded"):
            review._run_reviewer_command(cli_command="synthetic", prompt_arg="-p", prompt=prompt)
        assert not calls


@pytest.mark.parametrize("prompt", ["synthetic\0context", "synthetic\ud800context"])
def test_unencodable_argv_context_is_a_controlled_refusal(prompt: str) -> None:
    with pytest.raises(CommandError, match="review context refused"):
        review._run_reviewer_command(cli_command="never-run", prompt_arg="-p", prompt=prompt)
