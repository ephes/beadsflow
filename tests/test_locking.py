import os
import select
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest

from beadsflow.application.errors import LockError
from beadsflow.application.run_epic import RunEpicRequest, run_epic
from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra import locking
from beadsflow.infra.locking import EpicLock, epic_lock_path


@contextmanager
def hold_lock_in_process(path: Path) -> Iterator[None]:
    holder = """
import select, sys
from pathlib import Path
from beadsflow.infra.locking import EpicLock
with EpicLock(Path(sys.argv[1])):
    print("held", flush=True)
    if not select.select([sys.stdin], [], [], 10)[0]:
        raise RuntimeError("release deadline")
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", holder, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0], "holder did not signal acquisition"
        assert process.stdout.readline() == "held\n"
        yield
    finally:
        try:
            _, stderr = process.communicate("release\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        assert process.returncode == 0, stderr


@pytest.mark.parametrize("swapped", ["file", "locks", "beads"])
def test_named_inode_swap_refused_while_replacement_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swapped: str
) -> None:
    path = epic_lock_path(tmp_path / ".beads", "synthetic-1")
    path.parent.mkdir(parents=True)
    path.write_text("original synthetic sentinel")
    assert locking.fcntl is not None
    original_flock = locking.fcntl.flock
    original_metadata = path.stat()
    original_open = locking.os.open
    opened: list[int] = []
    replacement_bytes: bytes | None = None
    monkeypatch.chdir(tmp_path)
    for variable in os.environ:
        if variable.startswith("BEADSFLOW_"):
            monkeypatch.delenv(variable)

    def refuse_automation(*args: Any, **kwargs: Any) -> int:
        pytest.fail("lost lock capability admitted Beads/agent work")

    monkeypatch.setattr(EpicRunLoop, "run", refuse_automation)

    def track_open(*args: Any, **kwargs: Any) -> int:
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    # The original descriptor is open, but not locked yet. Keep the replacement
    # locked in another process until the first contender has refused entry.
    with ExitStack() as replacement:

        def swap_before_flock(fd: int, operation: int) -> None:
            nonlocal replacement_bytes
            if operation == locking.fcntl.LOCK_EX | locking.fcntl.LOCK_NB and os.path.samestat(
                os.fstat(fd), original_metadata
            ):
                target = {"file": path, "locks": path.parent, "beads": path.parent.parent}[swapped]
                target.rename(tmp_path / "retired")
                replacement.enter_context(hold_lock_in_process(path))
                replacement_bytes = path.read_bytes()
            original_flock(fd, operation)

        monkeypatch.setattr(locking.os, "open", track_open)
        monkeypatch.setattr(locking.fcntl, "flock", swap_before_flock)
        request = RunEpicRequest(
            epic_id="synthetic-1",
            beads_dir=str(path.parent.parent),
            config_path=None,
            once=True,
            interval_seconds=None,
            dry_run=False,
        )
        assert run_epic(request) == 1
        assert replacement_bytes is not None
        assert replacement_bytes.startswith(b"pid=")
        retired = tmp_path / "retired"
        if swapped == "locks":
            retired /= path.name
        elif swapped == "beads":
            retired = retired / "locks" / path.name
        assert retired.read_text() == "original synthetic sentinel"
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert path.read_bytes() == replacement_bytes

    monkeypatch.undo()
    with EpicLock(path):
        pass


@pytest.mark.parametrize("change", ["hardlink", "unlink", "symlink"])
def test_inode_readmitted_after_flock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    path = tmp_path / "lock"
    path.write_text("synthetic sentinel")
    assert locking.fcntl is not None
    original_flock = locking.fcntl.flock

    def change_before_flock(fd: int, operation: int) -> None:
        if operation == locking.fcntl.LOCK_EX | locking.fcntl.LOCK_NB:
            if change == "hardlink":
                (tmp_path / "alias").hardlink_to(path)
            elif change == "unlink":
                path.unlink()
            else:
                path.rename(tmp_path / "original")
                path.symlink_to(tmp_path / "original")
        original_flock(fd, operation)

    monkeypatch.setattr(locking.fcntl, "flock", change_before_flock)
    failed = EpicLock(path)
    with pytest.raises(LockError):
        with failed:
            pytest.fail("changed lock admitted protected work")
    assert failed._file.closed
    if path.exists():
        assert path.read_text() == "synthetic sentinel"


def test_hardlinked_lock_refused_without_touching_victim(tmp_path: Path) -> None:
    victim = tmp_path / "invented-victim"
    victim.write_bytes(b"synthetic private bytes\x00\xff\n")
    path = epic_lock_path(tmp_path / ".beads", "synthetic-1")
    path.parent.mkdir(parents=True)
    path.hardlink_to(victim)

    with pytest.raises(LockError):
        with EpicLock(path):
            pass

    assert victim.read_bytes() == b"synthetic private bytes\x00\xff\n"
    assert path.stat().st_nlink == 2


@pytest.mark.parametrize("unsafe", ["hardlink", "owner"])
def test_unsafe_inode_closes_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str) -> None:
    path = tmp_path / "lock"
    path.write_bytes(b"synthetic sentinel")
    if unsafe == "hardlink":
        (tmp_path / "alias").hardlink_to(path)
    else:
        current_uid = os.getuid()
        monkeypatch.setattr(locking.os, "getuid", lambda: current_uid + 1)

    original_open = locking.os.open
    opened: list[int] = []

    def track_open(*args: Any, **kwargs: Any) -> int:
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(locking.os, "open", track_open)
    with pytest.raises(LockError):
        with EpicLock(path):
            pass
    assert path.read_bytes() == b"synthetic sentinel"
    assert opened
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_private_lock_excludes_another_process(tmp_path: Path) -> None:
    path = epic_lock_path(tmp_path / ".beads", "synthetic-1")
    contender = """
import sys
from pathlib import Path
from beadsflow.application.errors import LockError
from beadsflow.infra.locking import EpicLock
try:
    with EpicLock(Path(sys.argv[1])):
        print("acquired")
except LockError:
    print("refused")
"""
    with EpicLock(path):
        result = subprocess.run(
            [sys.executable, "-c", contender, str(path)], capture_output=True, text=True, timeout=10, check=True
        )
        assert result.stdout == "refused\n"
    result = subprocess.run(
        [sys.executable, "-c", contender, str(path)], capture_output=True, text=True, timeout=10, check=True
    )
    assert result.stdout == "acquired\n"


def test_symlink_file_refused(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    path = tmp_path / "lock"
    path.symlink_to(sentinel)
    with pytest.raises(LockError):
        with EpicLock(path):
            pass
    assert sentinel.read_bytes() == b"untouched"


def test_symlink_directory_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(LockError):
        with EpicLock(link / "locks" / "lock"):
            pass
    assert list(target.iterdir()) == []


def test_nonregular_file_refused(tmp_path: Path) -> None:
    import os

    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(LockError):
        with EpicLock(path):
            pass


def test_contention_closes_failed_handle(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    failed = EpicLock(path)
    with EpicLock(path):
        with pytest.raises(LockError, match="already held"):
            failed.__enter__()
        assert failed._file.closed
    with EpicLock(path):
        assert path.read_text().startswith("pid=")


@pytest.mark.parametrize("operation", ["flock", "truncate", "write", "flush"])
def test_entry_failure_closes_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    original_fdopen = locking.os.fdopen

    class FailingFile:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped

        def __getattr__(self, name: str) -> Any:
            if name == operation:

                def fail(*args: Any) -> None:
                    raise OSError("injected")

                return fail
            return getattr(self.wrapped, name)

    with monkeypatch.context() as patch:
        if operation == "flock":
            assert locking.fcntl is not None

            def fail_flock(*args: Any) -> None:
                raise OSError("injected")

            patch.setattr(locking.fcntl, "flock", fail_flock)
        else:
            patch.setattr(locking.os, "fdopen", lambda *a, **kw: FailingFile(original_fdopen(*a, **kw)))
        failed = EpicLock(tmp_path / "lock")
        with pytest.raises(LockError):
            failed.__enter__()
        assert failed._file.closed
    with EpicLock(tmp_path / "lock"):
        pass


def test_trusted_ancestor_alias_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    with EpicLock(alias / ".beads" / "locks" / "lock"):
        assert (repo / ".beads" / "locks" / "lock").read_text().startswith("pid=")


@pytest.mark.parametrize("epic_id", ["beadsflow-1", "bd-a3f8.1", "Epic_42", "7"])
def test_epic_lock_path_accepts_plain_ids(tmp_path: Path, epic_id: str) -> None:
    path = epic_lock_path(tmp_path, epic_id)
    assert path == tmp_path / "locks" / f"beadsflow-{epic_id}.lock"
    assert path.parent.parent == tmp_path


@pytest.mark.parametrize(
    "epic_id",
    [
        "",
        ".",
        "..",
        ".hidden",
        "-flag",
        "a/b",
        "../escape",
        "x/../../escape",
        "/abs",
        "a\\b",
        "a b",
        "a\x00b",
        "a\n",
        "épic",
    ],
)
def test_epic_lock_path_refuses_unsafe_ids(tmp_path: Path, epic_id: str) -> None:
    with pytest.raises(LockError, match="Refusing epic ID"):
        epic_lock_path(tmp_path, epic_id)
    assert list(tmp_path.iterdir()) == []
