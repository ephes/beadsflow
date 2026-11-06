import logging
import os
import subprocess
from pathlib import Path

import pytest

from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra.paths import RepoPaths
from beadsflow.settings import Settings


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def loop(tmp_path: Path) -> EpicRunLoop:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    return EpicRunLoop(
        beads=None,  # type: ignore[arg-type]
        epic_id="epic",
        settings=Settings.defaults(),
        repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=tmp_path / ".beads"),
        implementer_name=None,
        reviewer_name=None,
        logger=logging.getLogger("test"),
    )


def test_untracked_content_and_mode(loop: EpicRunLoop) -> None:
    path = loop.repo_paths.repo_root / "untracked\n name"
    path.write_bytes(b"first")
    path.chmod(0o644)  # Explicit start mode: the result must not depend on the umask.
    before = loop._git_signature()
    assert loop._git_signature() == before
    path.write_bytes(b"second")
    after = loop._git_signature()
    assert after != before
    path.chmod(0o755)
    assert loop._git_signature() != after


def test_clean_commit_changes_signature(loop: EpicRunLoop) -> None:
    root = loop.repo_paths.repo_root
    git(root, "commit", "--allow-empty", "-m", "Initial")
    before = loop._git_signature()
    (root / "tracked").write_text("new")
    git(root, "add", "tracked")
    git(root, "commit", "-m", "Change")
    assert loop._git_signature() != before
    assert loop._git_signature() == loop._git_signature()


def test_ignored_and_symlink_entries(loop: EpicRunLoop, tmp_path: Path) -> None:
    root = loop.repo_paths.repo_root
    (root / ".gitignore").write_text("ignored\n")
    external = tmp_path.parent / (tmp_path.name + "-outside")
    external.write_text("external one")
    link = root / "link"
    link.symlink_to(external)
    before = loop._git_signature()
    (root / "ignored").write_text("ignored one")
    external.write_text("external two")
    assert loop._git_signature() == before
    link.unlink()
    link.symlink_to("missing")
    assert loop._git_signature() != before


def test_tracked_and_staged_changes(loop: EpicRunLoop) -> None:
    root = loop.repo_paths.repo_root
    path = root / "tracked"
    path.write_text("one")
    git(root, "add", "tracked")
    git(root, "commit", "-m", "Initial")
    before = loop._git_signature()
    path.write_text("two")
    unstaged = loop._git_signature()
    assert unstaged != before
    git(root, "add", "tracked")
    assert loop._git_signature() != unstaged


def test_unicode_untracked_name(loop: EpicRunLoop) -> None:
    path = loop.repo_paths.repo_root / "name-雪"
    path.write_bytes(b"one")
    before = loop._git_signature()
    path.write_bytes(b"two")
    assert loop._git_signature() != before


def test_nested_repository_and_fifo(loop: EpicRunLoop) -> None:
    root = loop.repo_paths.repo_root
    nested = root / "nested"
    nested.mkdir()
    git(nested, "init")
    fifo = root / "fifo"
    os.mkfifo(fifo)
    # Explicit start modes: the mode changes below must not depend on the umask.
    nested.chmod(0o755)
    fifo.chmod(0o644)
    before = loop._git_signature()
    assert loop._git_signature() == before
    nested.chmod(0o700)
    assert loop._git_signature() != before
    from beadsflow.infra.git_signature import untracked_signature

    special = untracked_signature(root, "fifo")
    fifo.chmod(0o600)
    assert untracked_signature(root, "fifo") != special


def test_carriage_return_name_is_not_confused_with_newline_name(loop: EpicRunLoop) -> None:
    root = loop.repo_paths.repo_root
    carriage = root / "name\rsuffix"
    carriage.write_bytes(b"first")
    before = loop._git_signature()
    assert loop._git_signature() == before
    carriage.write_bytes(b"second")
    changed = loop._git_signature()
    assert changed != before
    newline = root / "name\nsuffix"
    newline.write_bytes(b"other")
    both = loop._git_signature()
    assert both != changed
    carriage.write_bytes(b"third!")
    assert loop._git_signature() != both


@pytest.mark.parametrize("helper_kind", ["external", "textconv"])
def test_diff_helpers_disabled(
    loop: EpicRunLoop, helper_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shlex
    import sys

    from beadsflow.application.review import _collect_git_context

    root = loop.repo_paths.repo_root
    marker = tmp_path / "helper-ran"
    script = tmp_path / "helper.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nprint('converted')\n")
    command = shlex.join([sys.executable, str(script)])
    git(root, "config", "diff.external" if helper_kind == "external" else "diff.synthetic.textconv", command)
    (root / ".gitattributes").write_text("tracked diff=synthetic\n")
    tracked = root / "tracked"
    tracked.write_bytes(b"original\0\r\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Synthetic baseline")
    tracked.write_bytes(b"changed\0\r\n")
    before = loop._git_signature()
    monkeypatch.chdir(root)
    _collect_git_context(8000)
    assert not marker.exists()
    tracked.write_bytes(b"changed again\0\r\n")
    assert loop._git_signature() != before
    git(root, "add", "tracked")
    _collect_git_context(8000)
    assert not marker.exists()


def test_review_context_handles_non_utf8_patch(loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch) -> None:
    from beadsflow.application.review import _collect_git_context

    root = loop.repo_paths.repo_root
    tracked = root / "latin1.txt"
    tracked.write_bytes(b"before\n")
    git(root, "add", "latin1.txt")
    git(root, "commit", "-m", "Synthetic text")
    tracked.write_bytes(b"caf\xe9\n")
    monkeypatch.chdir(root)
    _status, _stat, patch = _collect_git_context(8000)
    assert "caf\N{REPLACEMENT CHARACTER}" in patch
    patch.encode("utf-8")
    signature = loop._git_signature()
    tracked.write_bytes(b"caf\xef\n")
    assert loop._git_signature() != signature
    git(root, "add", "latin1.txt")
    _status, _stat, patch = _collect_git_context(8000)
    assert "caf\N{REPLACEMENT CHARACTER}" in patch


def test_large_tracked_diff_keeps_signature_working(loop: EpicRunLoop) -> None:
    from beadsflow.infra.git_command import capture_git

    root = loop.repo_paths.repo_root
    path = root / "tracked"
    path.write_text("small\n")
    git(root, "add", "tracked")
    git(root, "commit", "-m", "Initial")
    before = loop._git_signature()
    path.write_bytes(b"synthetic line\n" * 700_000)
    assert len(capture_git(["diff", "--binary"], cwd=root)) > 8 * 1024 * 1024
    after = loop._git_signature()
    assert after != before
    assert loop._git_signature() == after
