import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from beadsflow.application.errors import LockError
from beadsflow.infra import locking
from beadsflow.infra.locking import EpicLock, epic_lock_path


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
