from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.infra import command_log

_PRIVATE = "synthetic-private-payload"


@dataclass
class Resources:
    """Observe public descriptor ownership without /proc or platform fd counts."""

    active: dict[int, int] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    acquired: list[int] = field(default_factory=list)
    released: list[int] = field(default_factory=list)
    fault: str = ""
    hits: list[str] = field(default_factory=list)
    interleave: Callable[[str], None] = lambda _: None
    real_fsync: Callable[[int], None] = os.fsync

    def boundary(self, name: str) -> None:
        self.hits.append(name)
        self.interleave(name)
        if name == self.fault:
            raise OSError(_PRIVATE)

    def release(self, fd: int) -> None:
        self.released.append(self.active.pop(fd))

    def fsync(self, fd: int) -> None:
        self.boundary(f"sync:{self.labels[self.active[fd]]}")
        self.real_fsync(fd)


@dataclass
class Handle:
    handle: IO[str]
    fd: int
    resources: Resources
    closefd: bool

    def __enter__(self) -> Handle:
        return self

    def write(self, content: str) -> int:
        if self.resources.fault == "write":
            self.handle.write(content[:5])
        self.resources.boundary("write")
        return self.handle.write(content)

    def __exit__(self, *args: object) -> None:
        try:
            self.handle.close()
        finally:
            if self.closefd:
                self.resources.release(self.fd)
        self.resources.boundary("close:file")


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> Iterator[Resources]:
    resources = Resources()
    real_open, real_close, real_fdopen = os.open, os.close, os.fdopen
    real_mkdir, real_fstat, real_fchmod = os.mkdir, os.fstat, os.fchmod
    real_link = os.link

    def open_fd(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        label = "file" if flags & os.O_CREAT else str(path) if dir_fd is not None else "root"
        resources.boundary(f"open:{label}")
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        assert fd not in resources.active
        generation = len(resources.acquired)
        resources.acquired.append(generation)
        resources.labels.append(label)
        resources.active[fd] = generation
        resources.boundary(f"opened:{label}")
        return fd

    def close_fd(fd: int) -> None:
        assert fd in resources.active, "descriptor closed twice"
        kind = "directory" if stat.S_ISDIR(real_fstat(fd).st_mode) else "descriptor"
        real_close(fd)
        resources.release(fd)
        # A close failure can be reported after the OS releases the descriptor.
        resources.boundary(f"close:{kind}")

    def mkdir(path: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        resources.boundary(f"mkdir:{path}")
        real_mkdir(path, mode, dir_fd=dir_fd)

    def fstat(fd: int) -> os.stat_result:
        if fd in resources.active:
            resources.boundary("fstat")
        return real_fstat(fd)

    def fchmod(fd: int, mode: int) -> None:
        resources.boundary("fchmod")
        real_fchmod(fd, mode)

    def fdopen(fd: int, *args: Any, **kwargs: Any) -> Handle:
        resources.boundary("fdopen")
        return Handle(real_fdopen(fd, *args, **kwargs), fd, resources, kwargs.get("closefd", True))

    def link(src: str, dst: str, **kwargs: Any) -> None:
        real_link(src, dst, **kwargs)
        resources.boundary("linked:file")

    monkeypatch.setattr(command_log.os, "open", open_fd)
    monkeypatch.setattr(command_log.os, "close", close_fd)
    monkeypatch.setattr(command_log.os, "mkdir", mkdir)
    monkeypatch.setattr(command_log.os, "fstat", fstat)
    monkeypatch.setattr(command_log.os, "fchmod", fchmod)
    monkeypatch.setattr(command_log.os, "fdopen", fdopen)
    monkeypatch.setattr(command_log.os, "link", link)
    monkeypatch.setattr(command_log.os, "fsync", resources.fsync)
    yield resources
    # Also clean up if the production bug leaks a descriptor during a red run.
    for fd in list(resources.active):
        real_close(fd)


def assert_released(resources: Resources) -> None:
    assert resources.active == {}
    assert sorted(resources.acquired) == sorted(resources.released)


def write_log(tmp_path: Path) -> Path:
    return command_log.write_command_log(
        beads_dir=tmp_path / ".beads", issue_id="issue", phase="reviewer", content=_PRIVATE
    )


def outside_sentinel(tmp_path: Path) -> tuple[Path, Path, int]:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched")
    return outside, sentinel, stat.S_IMODE(outside.stat().st_mode)


@pytest.mark.parametrize("component", [".beads", "logs", "beadsflow"])
def test_component_swapped_before_open_is_refused(tmp_path: Path, resources: Resources, component: str) -> None:
    outside, sentinel, mode = outside_sentinel(tmp_path)
    target = tmp_path / ".beads"
    if component != ".beads":
        target /= "logs"
    if component == "beadsflow":
        target /= "beadsflow"
    swapped = False

    def swap(boundary: str) -> None:
        nonlocal swapped
        if boundary == f"open:{component}":
            target.rename(target.with_name(target.name + "-owned"))
            target.symlink_to(outside, target_is_directory=True)
            swapped = True

    resources.interleave = swap
    with pytest.raises(CommandError, match="unsafe directory or I/O failure"):
        write_log(tmp_path)
    assert swapped
    assert sentinel.read_text() == "untouched"
    assert list(outside.iterdir()) == [sentinel]
    assert stat.S_IMODE(outside.stat().st_mode) == mode
    assert_released(resources)


@pytest.mark.parametrize("component", [".beads", "logs", "beadsflow"])
def test_renamed_open_directory_never_reports_redirected_path(
    tmp_path: Path, resources: Resources, component: str
) -> None:
    outside, sentinel, mode = outside_sentinel(tmp_path)
    target = tmp_path / ".beads"
    if component != ".beads":
        target /= "logs"
    if component == "beadsflow":
        target /= "beadsflow"
    owned = target.with_name(target.name + "-owned")
    swapped = False

    def swap(boundary: str) -> None:
        nonlocal swapped
        if boundary == f"opened:{component}":
            target.rename(owned)
            target.symlink_to(outside, target_is_directory=True)
            swapped = True

    resources.interleave = swap
    with pytest.raises(CommandError, match="unsafe directory or I/O failure"):
        write_log(tmp_path)
    assert swapped
    assert sentinel.read_text() == "untouched"
    assert list(outside.iterdir()) == [sentinel]
    assert stat.S_IMODE(outside.stat().st_mode) == mode
    # Any retained diagnostic was written through the original directory chain.
    for path in owned.rglob("*.log"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == _PRIVATE
    assert_released(resources)


@pytest.mark.parametrize(
    "boundary",
    [
        "open:root",
        "mkdir:.beads",
        "open:.beads",
        "mkdir:logs",
        "open:logs",
        "mkdir:beadsflow",
        "open:beadsflow",
        "fstat",
        "fchmod",
        "open:file",
        "fdopen",
        "write",
        "close:file",
        "close:descriptor",
        "close:directory",
    ],
)
def test_io_failures_release_resources_and_keep_artifacts_private(
    tmp_path: Path, resources: Resources, boundary: str
) -> None:
    outside, sentinel, mode = outside_sentinel(tmp_path)
    resources.fault = boundary
    with pytest.raises(CommandError, match="unsafe directory or I/O failure") as exc:
        write_log(tmp_path)
    assert boundary in resources.hits
    assert _PRIVATE not in str(exc.value)
    assert sentinel.read_text() == "untouched"
    assert stat.S_IMODE(outside.stat().st_mode) == mode
    assert_released(resources)
    for path in (tmp_path / ".beads").rglob("*.log"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_success_releases_resources(tmp_path: Path, resources: Resources) -> None:
    path = write_log(tmp_path)
    assert path.read_text() == _PRIVATE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert_released(resources)


def test_receipt_follows_file_link_and_directory_syncs(tmp_path: Path, resources: Resources) -> None:
    directory = tmp_path / ".beads" / "logs" / "beadsflow"

    def inspect_names(event: str) -> None:
        if event == "sync:file":
            (staged,) = directory.glob(".*.tmp")
            assert staged.read_text() == _PRIVATE
            assert not list(directory.glob("*.log"))
        if event == "sync:beadsflow":
            assert not list(directory.glob(".*.tmp"))
            (published,) = directory.glob("*.log")
            assert published.read_text() == _PRIVATE

    resources.interleave = inspect_names
    assert write_log(tmp_path).is_file()
    assert [event for event in resources.hits if event.startswith("sync:") or event == "linked:file"] == [
        "sync:file",
        "linked:file",
        "sync:beadsflow",
        "sync:logs",
        "sync:.beads",
        "sync:root",
    ]
    assert_released(resources)


@pytest.mark.parametrize(
    ("fault", "occupy_final"),
    [("write", False), ("close:file", False), ("write", True), ("", True)],
)
def test_failed_publication_preserves_existing_logs(
    tmp_path: Path, resources: Resources, monkeypatch: pytest.MonkeyPatch, fault: str, occupy_final: bool
) -> None:
    earlier = write_log(tmp_path)
    monkeypatch.setattr(command_log.time, "strftime", lambda _format: "20260101-000000")
    monkeypatch.setattr(command_log.secrets, "token_hex", lambda _size: "synthetic")
    final = earlier.with_name("issue.reviewer.20260101-000000.synthetic.log")
    visible_during_write: list[bool] = []

    def observe_write(boundary: str) -> None:
        if boundary == "write":
            visible_during_write.append(final.exists())
            if occupy_final:
                final.unlink(missing_ok=True)
                final.write_text("foreign diagnostic")

    resources.fault = fault
    resources.interleave = observe_write
    with pytest.raises(CommandError, match="unsafe directory or I/O failure"):
        write_log(tmp_path)
    assert visible_during_write == [False]
    assert earlier.read_text() == _PRIVATE
    assert set(earlier.parent.iterdir()) == ({earlier, final} if occupy_final else {earlier})
    if occupy_final:
        assert final.read_text() == "foreign diagnostic"
    assert_released(resources)


@pytest.mark.parametrize(
    "boundary",
    [
        "fchmod",
        "fdopen",
        "write",
        "close:file",
        "close:directory",
        "replace:stage",
        "replace:file",
        "sync:file",
        "sync:beadsflow",
        "sync:root",
    ],
)
def test_runner_preserves_primary_failure_when_log_cannot_be_reported(
    tmp_path: Path,
    resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    boundary: str,
) -> None:
    import logging

    from beadsflow.application import runner
    from beadsflow.infra.paths import RepoPaths
    from beadsflow.infra.run_command import CommandResult, CommandSpec
    from beadsflow.settings import Profile, Settings

    @dataclass
    class Beads:
        comments: list[str] = field(default_factory=list)

        def comment(self, issue_id: str, text: str) -> None:
            self.comments.append(text)

    beads = Beads()
    loop = runner.EpicRunLoop(
        beads=beads,  # type: ignore[arg-type]
        epic_id="epic",
        settings=Settings.defaults(),
        repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=tmp_path / ".beads"),
        implementer_name=None,
        reviewer_name=None,
        logger=logging.getLogger("test.log-io"),
    )
    monkeypatch.setattr(
        runner,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=[], returncode=7, stdout=_PRIVATE, stderr=_PRIVATE),
    )
    if boundary in {"replace:stage", "replace:file"}:
        stage = boundary == "replace:stage"
        swap_at = "close:file" if stage else "linked:file"
        pattern = ".*.tmp" if stage else "*.log"

        def replace_leaf(event: str) -> None:
            if event == swap_at:
                (leaf,) = (tmp_path / ".beads" / "logs" / "beadsflow").glob(pattern)
                retired = leaf.with_suffix(".retired")
                leaf.rename(retired)
                leaf.symlink_to(retired.name)
                resources.hits.append(boundary)

        resources.interleave = replace_leaf
    else:
        resources.fault = boundary
    profile = Profile(CommandSpec(["synthetic"]), "stdout", "", "", False)
    with pytest.raises(CommandError, match="Reviewer failed with code 7") as exc:
        loop._run_profile_command(profile=profile, issue_id="issue", phase="reviewer")
    assert boundary in resources.hits
    assert _PRIVATE not in str(exc.value)
    assert _PRIVATE not in caplog.text
    assert len(beads.comments) == 1
    assert "command failed (exit 7)" in beads.comments[0]
    assert "Log:" not in beads.comments[0]
    assert _PRIVATE not in beads.comments[0]
    if boundary.startswith("sync:"):
        directory = tmp_path / ".beads" / "logs" / "beadsflow"
        assert not list(directory.glob(".*.tmp"))
        if boundary == "sync:file":
            assert not list(directory.glob("*.log"))
            assert "Cannot write diagnostic log: unsafe directory or I/O failure" in caplog.messages
        else:
            (published,) = directory.glob("*.log")
            assert _PRIVATE in published.read_text()
            assert stat.S_IMODE(published.stat().st_mode) == 0o600
            assert "Cannot confirm durability of published diagnostic log" in caplog.messages
    assert_released(resources)
