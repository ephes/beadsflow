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
    acquired: list[int] = field(default_factory=list)
    released: list[int] = field(default_factory=list)
    fault: str = ""
    hits: list[str] = field(default_factory=list)
    interleave: Callable[[str], None] = lambda _: None

    def boundary(self, name: str) -> None:
        self.hits.append(name)
        self.interleave(name)
        if name == self.fault:
            raise OSError(_PRIVATE)

    def release(self, fd: int) -> None:
        self.released.append(self.active.pop(fd))


@dataclass
class Handle:
    handle: IO[str]
    fd: int
    resources: Resources

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
            self.resources.release(self.fd)
        self.resources.boundary("close:file")


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> Iterator[Resources]:
    resources = Resources()
    real_open, real_close, real_fdopen = os.open, os.close, os.fdopen
    real_mkdir, real_fstat, real_fchmod = os.mkdir, os.fstat, os.fchmod

    def open_fd(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        label = "file" if flags & os.O_CREAT else str(path) if dir_fd is not None else "root"
        resources.boundary(f"open:{label}")
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        assert fd not in resources.active
        generation = len(resources.acquired)
        resources.acquired.append(generation)
        resources.active[fd] = generation
        resources.boundary(f"opened:{label}")
        return fd

    def close_fd(fd: int) -> None:
        assert fd in resources.active, "descriptor closed twice"
        real_close(fd)
        resources.release(fd)
        # A close failure can be reported after the OS releases the descriptor.
        resources.boundary("close:directory")

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
        return Handle(real_fdopen(fd, *args, **kwargs), fd, resources)

    monkeypatch.setattr(command_log.os, "open", open_fd)
    monkeypatch.setattr(command_log.os, "close", close_fd)
    monkeypatch.setattr(command_log.os, "mkdir", mkdir)
    monkeypatch.setattr(command_log.os, "fstat", fstat)
    monkeypatch.setattr(command_log.os, "fchmod", fchmod)
    monkeypatch.setattr(command_log.os, "fdopen", fdopen)
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


@pytest.mark.parametrize("boundary", ["fchmod", "fdopen", "write", "close:file", "close:directory"])
def test_runner_preserves_primary_failure_when_log_io_fails(
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
    assert_released(resources)
