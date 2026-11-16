from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import beadsflow.application.review as review
from beadsflow.application.errors import CommandError
from beadsflow.domain.models import Issue, IssueStatus, IssueType


@pytest.fixture
def review_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    issue = Issue("child", "Synthetic", IssueStatus.OPEN, 0, IssueType.TASK, now, now, [], [], [])
    monkeypatch.setattr(review.BeadsCli, "get_issue", lambda self, issue_id: issue)
    return tmp_path


def request(limit: int = 8000) -> review.ReviewRequest:
    return review.ReviewRequest("child", "epic", ".beads", False, "synthetic", "-p", limit, 4000)


@pytest.mark.parametrize("nested", [False, True])
def test_untracked_source_bytes_are_shown_to_reviewer(
    review_tree: Path, monkeypatch: pytest.MonkeyPatch, nested: bool
) -> None:
    (review_tree / "new source.py").write_text("def synthetic_unique_code():\n    return '雪'\n", encoding="utf-8")
    project = review_tree / "project" if nested else review_tree
    project.mkdir(exist_ok=True)
    (project / ".beads").mkdir()
    (project / ".beads" / "issues.jsonl").write_text("excluded-metadata")
    lock = review_tree / ".beadsflow" / "locks" / "execution.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("excluded-execution-lock")
    monkeypatch.chdir(project)
    prompts: list[str] = []

    def reviewer(*, prompt: str, **kwargs: object) -> str:
        prompts.append(prompt)
        return "LGTM"

    monkeypatch.setattr(review, "_run_reviewer_command", reviewer)
    assert review.run_review(request()) == "LGTM"
    assert len(prompts) == 1
    assert "new source.py" in prompts[0]
    assert "def synthetic_unique_code():" in prompts[0]
    assert "return '雪'" in prompts[0]
    assert "excluded-metadata" not in prompts[0]
    assert "excluded-execution-lock" not in prompts[0]


@pytest.mark.parametrize("kind", ["binary", "invalid-utf8", "oversize", "symlink", "fifo", "directory"])
def test_unsupported_untracked_source_refuses_before_reviewer(
    review_tree: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = review_tree / "new"
    if kind == "symlink":
        path.symlink_to("outside")
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
        subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    else:
        path.write_bytes({"binary": b"a\0b", "invalid-utf8": b"\xff", "oversize": b"x" * 9000}[kind])
    if kind == "fifo":
        # Git omits FIFOs on some platforms. Exercise admission with a reported path.
        original = review.capture_git

        def capture(args: list[str], **kwargs: object) -> str:
            if args[0] == "ls-files":
                return "new\0"
            return original(args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(review, "capture_git", capture)

    def forbidden(**kwargs: object) -> str:
        pytest.fail("reviewer launched with unsupported source")

    monkeypatch.setattr(review, "_run_reviewer_command", forbidden)
    with pytest.raises(CommandError):
        review.run_review(request())


@pytest.mark.parametrize("limit", [0, -1, 50, 8000])
def test_incomplete_patch_cannot_return_approval(
    review_tree: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    source = review_tree / "tracked.py"
    source.write_text("synthetic baseline\n")
    subprocess.run(["git", "add", "tracked.py"], check=True, capture_output=True)
    source.write_text("safe-looking-prefix\n" + "x\n" * 5000 + "synthetic_defect_at_tail()\n")
    launched: list[str] = []

    def reviewer(**kwargs: object) -> str:
        launched.append("reviewer")
        return "LGTM"

    monkeypatch.setattr(review, "_run_reviewer_command", reviewer)
    with pytest.raises(CommandError, match="complete.*patch|positive"):
        review.run_review(request(limit))
    assert launched == []


def test_complete_patch_at_limit_can_approve(review_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (review_tree / "new.py").write_text("synthetic_complete_source()\n")
    patch = review._collect_git_context(8000)[2]
    size = len(patch.encode("utf-8"))
    monkeypatch.setattr(review, "_run_reviewer_command", lambda **kwargs: "LGTM")
    assert review.run_review(request(size)) == "LGTM"
    with pytest.raises(CommandError, match="complete.*patch"):
        review.run_review(request(size - 1))
