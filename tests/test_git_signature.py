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
