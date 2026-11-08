from __future__ import annotations

import json
import logging
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.application.runner import EpicRunLoop
from beadsflow.domain.models import Comment, Issue, IssueStatus, IssueSummary, IssueType
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandSpec
from beadsflow.settings import Profile, Settings

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class Beads:
    def __init__(self) -> None:
        self.comments = [Comment(1, "synthetic", "Ready for review:", NOW)]
        self.closed: list[str] = []
        self.reject_comments = False
        self.export_file: Path | None = None

    def get_issue(self, issue_id: str) -> Issue:
        if self.export_file is not None:
            data = json.loads(self.export_file.read_text())
            self.comments = [Comment(i, "synthetic", text, NOW) for i, text in enumerate(data["comments"], 1)]
        closed = bool(self.closed)
        return Issue(
            id=issue_id,
            title="Synthetic",
            priority=1,
            status=IssueStatus.CLOSED if closed else IssueStatus.OPEN,
            issue_type=IssueType.EPIC if issue_id == "epic" else IssueType.TASK,
            created_at=NOW,
            updated_at=NOW,
            dependencies=[],
            dependents=[IssueSummary("child", "Child", IssueStatus.CLOSED if closed else IssueStatus.OPEN, 1, NOW)]
            if issue_id == "epic"
            else [],
            comments=self.comments if issue_id == "child" else [],
        )

    def comment(self, issue_id: str, text: str) -> None:
        if self.reject_comments:
            raise RuntimeError("private-sentinel-in-comment-error")
        assert issue_id == "child"
        self.comments.append(Comment(len(self.comments) + 1, "synthetic", text, NOW))
        if self.export_file is not None:
            self.export_file.write_text(json.dumps({"id": issue_id, "comments": [c.text for c in self.comments]}))

    def close(self, issue_id: str) -> None:
        self.closed.append(issue_id)


@pytest.fixture
def review_loop(tmp_path: Path) -> EpicRunLoop:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic")
    git(tmp_path, "config", "user.email", "synthetic@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".gitignore").write_text(".beads/\n.beadsflow/\nignored\n")
    (tmp_path / "tracked").write_text("before")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "Synthetic baseline")
    (tmp_path / "untracked").write_text("before")
    settings = replace(Settings.defaults(), interval_seconds=0)
    return EpicRunLoop(
        beads=Beads(),  # type: ignore[arg-type]
        epic_id="epic",
        settings=settings,
        repo_paths=RepoPaths(tmp_path, tmp_path / ".beads"),
        implementer_name="fake",
        reviewer_name="fake",
        logger=logging.getLogger("test.publication"),
    )


def with_script(loop: EpicRunLoop, script: str, *, mode: str = "stdout") -> EpicRunLoop:
    profile = Profile(CommandSpec([sys.executable, "-c", script]), mode, "", "", False)
    return replace(loop, settings=replace(loop.settings, reviewers={"fake": profile}, implementers={"fake": profile}))


@pytest.mark.parametrize("change", ["tracked", "staged", "untracked", "new-untracked", "head"])
def test_changed_review_cannot_publish_approval_or_close(review_loop: EpicRunLoop, change: str) -> None:
    path = "tracked" if change in {"tracked", "staged", "head"} else change
    script = f"from pathlib import Path; Path({path!r}).write_text('changed'); "
    if change in {"staged", "head"}:
        script += "import subprocess; subprocess.run(['git', 'add', 'tracked'], check=True); "
    if change == "head":
        script += "subprocess.run(['git', 'commit', '-m', 'Synthetic change'], check=True); "
    loop = with_script(review_loop, script + "print('LGTM')")
    with pytest.raises(CommandError, match="changed during review"):
        loop.run(once=False, dry_run=False, max_iterations=3)
    assert not any(c.text.startswith("LGTM") for c in loop.beads.comments)  # type: ignore[attr-defined]
    assert loop.beads.closed == []  # type: ignore[attr-defined]
    assert (loop.repo_paths.repo_root / path).read_text() == "changed"


@pytest.mark.parametrize(
    "script", ["print('LGTM')", "from pathlib import Path; Path('ignored').write_text('ok'); print('LGTM')"]
)
def test_unchanged_review_approves_and_closes(review_loop: EpicRunLoop, script: str) -> None:
    loop = with_script(review_loop, script)
    assert loop.run(once=False, dry_run=False, max_iterations=3) == 0
    assert [c.text for c in loop.beads.comments[:2]] == ["Ready for review:", "LGTM\n"]  # type: ignore[attr-defined]
    assert loop.beads.comments[-1].text.startswith("LGTM\n\nBeadsflow reviewed Git signature: ")  # type: ignore[attr-defined]
    assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("phase", ["implementer", "reviewer"])
@pytest.mark.parametrize("failure", ["exit", "invalid", "empty", "truncation"])
@pytest.mark.parametrize("reject_comments", [False, True])
def test_failure_output_stays_in_private_log(
    review_loop: EpicRunLoop,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: str,
    reject_comments: bool,
) -> None:
    sentinel = "private-sentinel"
    script = "import sys; print('private-sentinel-stderr', file=sys.stderr); "
    if failure == "exit":
        script += "print('private-sentinel-stdout'); sys.exit(7)"
    elif failure == "empty":
        script += "pass"
    elif failure == "truncation":
        monkeypatch.setenv("BEADSFLOW_MAX_COMMENT_BYTES", "1")
        marker = "LGTM" if phase == "reviewer" else "Ready for review:"
        script += f"print({marker!r}); print('private-sentinel-stdout')"
    else:
        script += "print('private-sentinel-invalid-verdict')"
    loop = with_script(review_loop, script)
    loop.beads.reject_comments = reject_comments  # type: ignore[attr-defined]
    if phase == "implementer":
        loop.beads.comments.clear()  # type: ignore[attr-defined]
    with caplog.at_level(logging.INFO), pytest.raises(CommandError) as exc:
        loop.run(once=True, dry_run=False, max_iterations=1)
    public = str(exc.value) + caplog.text + repr(loop.beads.comments)  # type: ignore[attr-defined]
    assert sentinel not in public
    assert "Log:" in public or "log:" in public
    if failure == "exit":
        assert "failed with code 7" in str(exc.value)
    else:
        assert "stdout" in str(exc.value) or "marker" in str(exc.value) or "verdict" in str(exc.value)
    logs = list((loop.repo_paths.beads_dir / "logs" / "beadsflow").glob(f"child.{phase}.*.log"))
    assert len(logs) == 1
    assert sentinel in logs[0].read_text()
    assert stat.S_IMODE(logs[0].stat().st_mode) == 0o600
    assert loop.beads.closed == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("failure_at", [1, 2])
def test_signature_failure_stops_review_publication(
    review_loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch, failure_at: int
) -> None:
    loop = with_script(review_loop, "from pathlib import Path; Path('ignored').write_text('ran'); print('LGTM')")
    original = EpicRunLoop._git_signature
    calls = 0

    def signature(self: EpicRunLoop, *, exclude_beads: bool = False) -> str:
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise CommandError("synthetic Git signature failure")
        return original(self, exclude_beads=exclude_beads)

    monkeypatch.setattr(EpicRunLoop, "_git_signature", signature)
    with pytest.raises(CommandError, match="Git signature failure"):
        loop.run(once=False, dry_run=False, max_iterations=3)
    assert (loop.repo_paths.repo_root / "ignored").exists() == (failure_at == 2)
    assert [c.text for c in loop.beads.comments] == ["Ready for review:"]  # type: ignore[attr-defined]
    assert loop.beads.closed == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("mode", ["command", "stdout"])
@pytest.mark.parametrize("nested_project", [False, True])
@pytest.mark.parametrize("change_code", [False, True])
def test_tracked_beads_export_does_not_mask_code_changes(
    review_loop: EpicRunLoop, mode: str, change_code: bool, nested_project: bool
) -> None:
    root = review_loop.repo_paths.repo_root
    project = root / "project" if nested_project else root
    project.mkdir(exist_ok=True)
    export = project / ".beads" / "issues.jsonl"
    export.parent.mkdir()
    review_loop = replace(review_loop, repo_paths=RepoPaths(project, export.parent))
    export.write_text(json.dumps({"id": "child", "comments": ["Ready for review:"]}))
    git(root, "add", "-f", export.relative_to(root).as_posix())
    git(root, "commit", "-m", "Track synthetic Beads export")
    review_loop.beads.export_file = export  # type: ignore[attr-defined]
    comments = ["Ready for review:", "LGTM"] if mode == "command" else ["Ready for review:"]
    payload = json.dumps({"id": "child", "comments": comments, "updated": True})
    script = f"from pathlib import Path; Path('.beads/issues.jsonl').write_text({payload!r}); "
    if change_code:
        tracked_path = "../tracked" if nested_project else "tracked"
        script += f"Path({tracked_path!r}).write_text('changed'); "
    script += "print('LGTM')" if mode == "stdout" else "pass"
    loop = with_script(review_loop, script, mode=mode)
    before = loop._git_signature()
    if change_code:
        with pytest.raises(CommandError, match="changed during review"):
            loop.run(once=False, dry_run=False, max_iterations=3)
        assert loop.beads.closed == []  # type: ignore[attr-defined]
        # A safe refusal supersedes even a directly posted command-mode approval.
        assert loop.beads.comments[-1].text.startswith("Changes requested:")  # type: ignore[attr-defined]
        assert (root / "tracked").read_text() == "changed"
    else:
        assert loop.run(once=False, dry_run=False, max_iterations=3) == 0
        assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]
    assert loop._git_signature() != before  # Export really changed; implementer signatures still include it.


def test_non_git_review_fails_before_launch(review_loop: EpicRunLoop) -> None:
    import shutil

    root = review_loop.repo_paths.repo_root
    shutil.rmtree(root / ".git")
    loop = with_script(review_loop, "from pathlib import Path; Path('ignored').write_text('ran'); print('LGTM')")
    with pytest.raises(CommandError, match="git status"):
        loop.run(once=True, dry_run=False, max_iterations=1)
    assert not (root / "ignored").exists()
    assert loop.beads.closed == []  # type: ignore[attr-defined]


def test_refused_review_keeps_private_output_when_comment_fails(
    review_loop: EpicRunLoop, caplog: pytest.LogCaptureFixture
) -> None:
    loop = with_script(
        review_loop,
        "from pathlib import Path; Path('tracked').write_text('changed'); print('LGTM\\nprivate-sentinel')",
    )
    loop.beads.reject_comments = True  # type: ignore[attr-defined]
    with caplog.at_level(logging.INFO), pytest.raises(CommandError, match="changed during review") as exc:
        loop.run(once=True, dry_run=False, max_iterations=1)
    assert "private-sentinel" not in str(exc.value) + caplog.text
    logs = list((loop.repo_paths.beads_dir / "logs" / "beadsflow").glob("child.reviewer.*.log"))
    assert len(logs) == 1
    assert "private-sentinel" in logs[0].read_text()
    assert loop.beads.closed == []  # type: ignore[attr-defined]


def test_beads_export_through_symlinked_ancestor_can_approve(review_loop: EpicRunLoop) -> None:
    root = review_loop.repo_paths.repo_root
    export = root / ".beads" / "issues.jsonl"
    export.parent.mkdir()
    export.write_text(json.dumps({"id": "child", "comments": ["Ready for review:"]}))
    git(root, "add", "-f", ".beads/issues.jsonl")
    git(root, "commit", "-m", "Track synthetic Beads export")
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    review_loop.beads.export_file = export  # type: ignore[attr-defined]
    loop = replace(review_loop, repo_paths=RepoPaths(root, alias / ".beads"))
    payload = json.dumps({"id": "child", "comments": ["Ready for review:", "LGTM"]})
    loop = with_script(
        loop,
        "import os; from pathlib import Path; "
        f"(Path(os.environ['BEADS_DIR']) / 'issues.jsonl').write_text({payload!r})",
        mode="command",
    )
    assert loop.run(once=False, dry_run=False, max_iterations=3) == 0
    assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("change", ["tracked", "staged", "untracked", "head", "unchanged"])
def test_resumed_close_requires_reviewed_content(review_loop: EpicRunLoop, change: str) -> None:
    loop = with_script(review_loop, "print('LGTM')")
    assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
    root = loop.repo_paths.repo_root
    if change != "unchanged":
        (root / ("untracked" if change == "untracked" else "tracked")).write_text("after review")
    if change in {"staged", "head"}:
        git(root, "add", "tracked")
    if change == "head":
        git(root, "commit", "-m", "Synthetic later change")
    # A fresh loop instance resumes from persisted comments, with no reviewer selected.
    resumed = replace(loop, reviewer_name=None)
    if change == "unchanged":
        assert resumed.run(once=True, dry_run=False, max_iterations=1) == 0
        assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]
        assert resumed.run(once=True, dry_run=False, max_iterations=1) == 0
        assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]
    else:
        from beadsflow.application.errors import ConfigError

        with pytest.raises(ConfigError, match="No reviewer selected"):
            resumed.run(once=True, dry_run=False, max_iterations=1)
        assert loop.beads.closed == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("dry_run", [False, True])
def test_legacy_approval_requests_review(review_loop: EpicRunLoop, dry_run: bool) -> None:
    loop = with_script(review_loop, "print('LGTM')")
    loop.beads.comment("child", "LGTM")
    if dry_run:
        before = list(loop.beads.comments)  # type: ignore[attr-defined]
        assert loop.run(once=True, dry_run=True, max_iterations=1) == 0
        assert loop.beads.comments == before  # type: ignore[attr-defined]
    else:
        assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
        assert any(c.text.startswith("Ready for review:") for c in loop.beads.comments[2:])  # type: ignore[attr-defined]
        assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
        assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]
    assert loop.beads.closed == ([] if dry_run else ["child"])  # type: ignore[attr-defined]


@pytest.mark.parametrize("replacement", ["LGTM", "LGTM\n\nBeadsflow reviewed Git signature: broken"])
def test_replaced_approval_needs_new_review(review_loop: EpicRunLoop, replacement: str) -> None:
    from beadsflow.application.errors import ConfigError

    loop = with_script(review_loop, "print('LGTM')")
    assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
    loop.beads.comment("child", replacement)
    with pytest.raises(ConfigError, match="No reviewer selected"):
        replace(loop, reviewer_name=None).run(once=True, dry_run=False, max_iterations=1)
    assert loop.beads.closed == []  # type: ignore[attr-defined]


def test_change_during_approval_publication_cannot_get_evidence(
    review_loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Beads.comment

    def comment(self: Beads, issue_id: str, text: str) -> None:
        original(self, issue_id, text)
        if text.strip() == "LGTM":
            (review_loop.repo_paths.repo_root / "tracked").write_text("changed during publication")

    monkeypatch.setattr(Beads, "comment", comment)
    loop = with_script(review_loop, "print('LGTM')")
    with pytest.raises(CommandError, match="changed while publishing review"):
        loop.run(once=True, dry_run=False, max_iterations=1)
    assert not any("Beadsflow reviewed Git signature:" in c.text for c in loop.beads.comments)  # type: ignore[attr-defined]
    assert loop.beads.closed == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("change", ["lock", "other"])
def test_resumed_approval_excludes_only_execution_lock(review_loop: EpicRunLoop, change: str) -> None:
    from beadsflow.application.errors import ConfigError

    root = review_loop.repo_paths.repo_root
    (root / ".gitignore").write_text(".beads/\nignored\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "Keep execution metadata unignored")
    lock = root / ".beadsflow" / "locks" / "execution.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("pid=111\n")
    other = root / ".beadsflow" / "notes"
    other.write_text("before")
    loop = with_script(review_loop, "print('LGTM')")
    assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
    lock.write_text("pid=222\n")  # A later process owns the same execution lock.
    if change == "other":
        other.write_text("after review")
    resumed = replace(loop, reviewer_name=None)
    if change == "lock":
        assert resumed.run(once=True, dry_run=False, max_iterations=1) == 0
        assert loop.beads.closed == ["child"]  # type: ignore[attr-defined]
    else:
        with pytest.raises(ConfigError, match="No reviewer selected"):
            resumed.run(once=True, dry_run=False, max_iterations=1)
        assert loop.beads.closed == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("stage", ["before", "after", "resume"])
def test_fingerprint_budget_failure_preserves_approval_state(
    review_loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import beadsflow.infra.git_signature as signatures

    root = review_loop.repo_paths.repo_root
    loop = with_script(review_loop, "print('LGTM')")
    if stage == "resume":
        assert loop.run(once=True, dry_run=False, max_iterations=1) == 0
    if stage == "after":
        loop = with_script(loop, "from pathlib import Path; Path('large').write_bytes(b'x' * 256); print('LGTM')")
    else:
        (root / "large").write_bytes(b"x" * 256)
    comments = list(loop.beads.comments)  # type: ignore[attr-defined]
    monkeypatch.setattr(signatures, "MAX_FINGERPRINT_BYTES", 128, raising=False)
    with pytest.raises(CommandError, match="fingerprint.*byte budget"):
        loop.run(once=True, dry_run=False, max_iterations=1)
    assert loop.beads.comments == comments  # type: ignore[attr-defined]
    assert loop.beads.closed == []  # type: ignore[attr-defined]
