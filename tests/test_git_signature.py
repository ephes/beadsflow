import logging
import os
import socket
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from beadsflow.application.errors import CommandError
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


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked"])
def test_dirty_submodule_refuses_all_byte_versions(
    loop: EpicRunLoop, add_submodule: Callable[[Path], Path], kind: str
) -> None:
    root = loop.repo_paths.worktree_root
    module = add_submodule(root)
    name = "untracked\nentry" if kind == "untracked" else "tracked"
    for text in ["edit one\n", "edit two\n"]:
        (module / name).write_text(text)
        if kind == "staged":
            git(module, "add", name)
        with pytest.raises(CommandError, match="Dirty submodule"):
            loop._git_signature(exclude_beads=True)


def test_clean_submodule_commit_updates_remain_reviewable(
    loop: EpicRunLoop, add_submodule: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from beadsflow.application.review import _collect_git_context

    root = loop.repo_paths.worktree_root
    module = add_submodule(root)
    monkeypatch.chdir(root)
    previous = loop._git_signature()
    for text in ["commit one\n", "commit two\n"]:
        (module / "tracked").write_text(text)
        git(module, "add", "tracked")
        git(module, "commit", "-m", "Synthetic submodule change")
        current = loop._git_signature()
        assert current != previous
        assert current == loop._git_signature()
        assert "Subproject commit" in _collect_git_context(8000)[2]
        previous = current
    git(root, "add", module.name)
    assert loop._git_signature() != previous
    assert "Staged:" in _collect_git_context(8000)[2]


def test_rename_source_name_cannot_imitate_submodule_status(loop: EpicRunLoop) -> None:
    root = loop.repo_paths.worktree_root
    path = root / "1 M. S.M. synthetic\nentry"
    path.write_text("synthetic source\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Synthetic rename baseline")
    before = loop._git_signature()
    git(root, "mv", path.name, "renamed")
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


def test_nested_repository_is_not_reviewable(loop: EpicRunLoop) -> None:
    from beadsflow.application.errors import CommandError

    root = loop.repo_paths.repo_root
    nested = root / "nested"
    nested.mkdir()
    git(nested, "init")
    with pytest.raises(CommandError, match="not a regular file or symlink"):
        loop._git_signature()


@pytest.mark.parametrize("kind", ["directory", "fifo", "socket"])
def test_unsupported_entry_is_refused_without_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    path = tmp_path / "unsupported"
    monkeypatch.chdir(tmp_path)  # Keep the AF_UNIX address below the platform length limit.
    with socket.socket(socket.AF_UNIX) as listener:
        if kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            listener.bind(path.name)
        directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:

            def refuse_open(*args: object, **kwargs: object) -> int:
                pytest.fail("Unsupported entry must never be opened")

            monkeypatch.setattr(signatures.os, "open", refuse_open)
            with pytest.raises(CommandError, match="not a regular file or symlink"):
                signatures._entry_signature(directory, path.name, signatures.FingerprintBudget.start())
        finally:
            os.close(directory)


@pytest.mark.parametrize("boundary", ["before_open", "after_open", "after_final_fstat"])
@pytest.mark.parametrize("same_content", [False, True])
def test_untracked_file_replacement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, same_content: bool
) -> None:
    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    path = tmp_path / "entry"
    path.write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"original" if same_content else b"new data")
    original_open = os.open
    original_fstat = os.fstat
    replaced = False
    fstats = 0

    def replace() -> None:
        nonlocal replaced
        os.replace(replacement, path)
        replaced = True

    def open_entry(name: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        if name == path.name and boundary == "before_open":
            replace()
        fd = original_open(name, flags, dir_fd=dir_fd)
        if name == path.name and boundary == "after_open":
            replace()
        return fd

    def fstat(fd: int) -> os.stat_result:
        nonlocal fstats
        result = original_fstat(fd)
        if stat.S_ISREG(result.st_mode):
            fstats += 1
        if fstats == 2 and boundary == "after_final_fstat":
            replace()
        return result

    monkeypatch.setattr(signatures.os, "open", open_entry)
    monkeypatch.setattr(signatures.os, "fstat", fstat)
    with pytest.raises(CommandError, match="changed.*fingerprint"):
        signatures.untracked_signature(tmp_path, path.name)
    assert replaced
    assert path.read_bytes() == (b"original" if same_content else b"new data")


@pytest.mark.parametrize("boundary", ["before_readlink", "after_readlink"])
@pytest.mark.parametrize("same_target", [False, True])
def test_untracked_symlink_replacement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, same_target: bool
) -> None:
    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    path = tmp_path / "entry"
    path.symlink_to("original")
    replacement = tmp_path / "replacement"
    target = "original" if same_target else "new data"
    replacement.symlink_to(target)
    original_readlink = os.readlink
    replaced = False

    def readlink(name: str, *, dir_fd: int | None = None) -> str:
        nonlocal replaced
        if boundary == "before_readlink":
            os.replace(replacement, path)
        result = original_readlink(name, dir_fd=dir_fd)
        if boundary == "after_readlink":
            os.replace(replacement, path)
        replaced = True
        return result

    monkeypatch.setattr(signatures.os, "readlink", readlink)
    with pytest.raises(CommandError, match="changed.*fingerprint"):
        signatures.untracked_signature(tmp_path, path.name)
    assert replaced
    assert original_readlink(path) == target


@pytest.mark.parametrize("consumer", ["signature", "source"])
@pytest.mark.parametrize("ancestor", ["root", "direct", "intermediate"])
@pytest.mark.parametrize("substitution", ["directory", "symlink"])
@pytest.mark.parametrize("boundary", ["after_open", "after_read"])
def test_untracked_ancestor_replacement_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    ancestor: str,
    substitution: str,
    boundary: str,
) -> None:
    from collections.abc import Iterator

    import beadsflow.infra.git_signature as signatures

    root = tmp_path / "worktree"
    path = root / "outer" / "inner" / "entry"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"reviewed")
    parent = {"root": root, "direct": path.parent, "intermediate": path.parent.parent}[ancestor]
    moved = tmp_path / "detached"
    replaced = False
    original_open = os.open
    original_chunks = signatures._regular_chunks

    def replace_parent() -> None:
        nonlocal replaced
        parent.rename(moved)
        if substitution == "symlink":
            parent.symlink_to(moved, target_is_directory=True)
        else:
            replacement = parent / path.relative_to(parent)
            replacement.parent.mkdir(parents=True)
            replacement.write_bytes(b"replacement")
        replaced = True

    def open_entry(name: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        fd = original_open(name, flags, dir_fd=dir_fd)
        if boundary == "after_open" and name == "entry":
            replace_parent()
        return fd

    def chunks(
        directory: int, name: str, metadata: os.stat_result, budget: signatures.FingerprintBudget
    ) -> Iterator[bytes]:
        yield from original_chunks(directory, name, metadata, budget)
        if boundary == "after_read":
            replace_parent()

    monkeypatch.setattr(signatures.os, "open", open_entry)
    monkeypatch.setattr(signatures, "_regular_chunks", chunks)
    with pytest.raises(CommandError, match="ancestor changed.*fingerprint"):
        if consumer == "signature":
            signatures.untracked_signature(root, "outer/inner/entry")
        else:
            signatures.untracked_source(root, "outer/inner/entry", budget=signatures.FingerprintBudget.start())
    assert replaced
    assert path.read_bytes() == (b"reviewed" if substitution == "symlink" else b"replacement")


def test_unchanged_nested_untracked_evidence(tmp_path: Path) -> None:
    from beadsflow.infra.git_signature import FingerprintBudget, untracked_signature, untracked_source

    path = tmp_path / "outer" / "inner" / "entry"
    path.parent.mkdir(parents=True)
    path.write_text("reviewed")
    assert untracked_signature(tmp_path, "outer/inner/entry") == untracked_signature(tmp_path, "outer/inner/entry")
    assert untracked_source(tmp_path, "outer/inner/entry", budget=FingerprintBudget.start()) == "reviewed"


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
    # Raw NUL content reaches admission (no helper conversion) and is refused.
    with pytest.raises(CommandError, match="NUL"):
        _collect_git_context(8000)
    assert not marker.exists()
    tracked.write_bytes(b"changed again\0\r\n")
    assert loop._git_signature() != before
    git(root, "add", "tracked")
    with pytest.raises(CommandError, match="NUL"):
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
    # Replacement text would hide the reviewed bytes: refuse instead.
    with pytest.raises(CommandError, match="not valid UTF-8"):
        _collect_git_context(8000)
    signature = loop._git_signature()
    tracked.write_bytes(b"caf\xef\n")
    assert loop._git_signature() != signature
    git(root, "add", "latin1.txt")
    with pytest.raises(CommandError, match="not valid UTF-8"):
        _collect_git_context(8000)


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


@pytest.mark.parametrize("inventory", ["sparse", "aggregate"])
def test_untracked_inventory_has_one_byte_budget(
    loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch, inventory: str
) -> None:
    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    monkeypatch.setattr(signatures, "MAX_FINGERPRINT_BYTES", 128, raising=False)
    root = loop.repo_paths.repo_root
    if inventory == "sparse":
        with (root / "oversized").open("wb") as handle:
            handle.truncate(129)
    else:
        for i in range(3):
            (root / str(i)).write_bytes(b"x" * 50)
    with pytest.raises(CommandError, match="fingerprint.*byte budget"):
        loop._git_signature()


@pytest.mark.parametrize("inventory", ["single", "many"])
def test_untracked_inventory_has_one_deadline(
    loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch, inventory: str
) -> None:
    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    ticks = 0

    def clock() -> float:
        nonlocal ticks
        ticks += 1
        return float(ticks)

    monkeypatch.setattr(signatures, "monotonic", clock, raising=False)
    monkeypatch.setattr(signatures, "FINGERPRINT_TIMEOUT_SECONDS", 8, raising=False)
    root = loop.repo_paths.repo_root
    for i in range(1 if inventory == "single" else 10):
        (root / str(i)).write_bytes(b"x" * (65536 * 10 if inventory == "single" else 1))
    with pytest.raises(CommandError, match="fingerprint.*deadline"):
        loop._git_signature()


@pytest.mark.parametrize("mutation", ["grow", "shrink"])
def test_untracked_read_is_bound_to_opened_size(
    loop: EpicRunLoop, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import IO

    import beadsflow.infra.git_signature as signatures
    from beadsflow.application.errors import CommandError

    path = loop.repo_paths.repo_root / "changing"
    path.write_bytes(b"x" * (65536 * 2))
    original_fdopen = os.fdopen
    changes = 0
    reads = 0

    @contextmanager
    def fdopen(fd: int, mode: str, *, buffering: int = -1) -> Iterator[IO[bytes]]:
        nonlocal reads, changes
        with original_fdopen(fd, mode, buffering=buffering) as handle:
            original_read = handle.read

            def read(size: int = -1) -> bytes:
                nonlocal reads, changes
                reads += 1
                chunk: bytes = original_read(size)
                # Finite even with the original read-to-EOF implementation.
                if chunk and changes < 3:
                    with path.open("ab" if mutation == "grow" else "wb") as writer:
                        if mutation == "grow":
                            writer.write(b"y" * 65536)
                    changes += 1
                return chunk

            monkeypatch.setattr(handle, "read", read)
            yield handle

    monkeypatch.setattr(signatures.os, "fdopen", fdopen)
    try:
        with pytest.raises(CommandError, match="changed.*fingerprint"):
            loop._git_signature()
    finally:
        assert changes > 0  # The injected file mutation really executed.
    assert reads <= 2  # Never chase appended data beyond the opened size.
