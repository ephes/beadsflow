import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from beadsflow.infra.run_command import run_command

# The parent owns/reaps its child on TERM. The child ignores TERM, exercising escalation.
CHILD = """
import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(2)
Path(sys.argv[1]).write_text('leaked')
time.sleep(20)
"""
PARENT = """
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[1]])
def stop(*args):
    child.kill()
    child.wait()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
Path(sys.argv[2]).write_text(str(os.getpid()) + ' ' + str(child.pid))
print('partial stdout', flush=True)
print('partial stderr', file=sys.stderr, flush=True)
time.sleep(30)
"""


def assert_gone(pids: Path) -> None:
    for pid in map(int, pids.read_text().split()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_timeout_cleans_descendant(tmp_path: Path) -> None:
    marker, pids = tmp_path / "marker", tmp_path / "pids"
    result = run_command(argv=[sys.executable, "-c", PARENT, str(marker), str(pids), CHILD], timeout_seconds=1, env={})
    try:
        assert result.returncode == 124
        assert "partial stdout" in result.stdout
        assert "partial stderr" in result.stderr
        assert_gone(pids)
        time.sleep(2)
        assert not marker.exists()
    finally:
        for pid in map(int, pids.read_text().split()):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_interrupt_cleans_descendant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker, pids = tmp_path / "marker", tmp_path / "pids"
    original = subprocess.Popen.communicate
    interrupted = False

    def interrupt(self: subprocess.Popen[str], *args: object, **kwargs: object) -> tuple[str, str]:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            deadline = time.monotonic() + 3
            while not pids.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_command(argv=[sys.executable, "-c", PARENT, str(marker), str(pids), CHILD], timeout_seconds=10, env={})
    assert interrupted
    assert_gone(pids)
    assert not marker.exists()


def test_timeout_diagnostic_and_success() -> None:
    timed = run_command(argv=[sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=1, env={})
    assert timed.returncode == 124
    assert "Timed out after 1 seconds" in timed.stderr
    success = run_command(argv=[sys.executable, "-c", "print('done')"], timeout_seconds=5, env={})
    assert success.returncode == 0
    assert success.stdout == "done\n"


def test_timeout_escalates_for_stubborn_group(tmp_path: Path) -> None:
    marker, pids = tmp_path / "marker", tmp_path / "pids"
    stubborn = PARENT.replace("signal.signal(signal.SIGTERM, stop)", "signal.signal(signal.SIGTERM, signal.SIG_IGN)")
    result = run_command(
        argv=[sys.executable, "-c", stubborn, str(marker), str(pids), CHILD], timeout_seconds=1, env={}
    )
    assert result.returncode == 124
    parent, _child = map(int, pids.read_text().split())
    with pytest.raises(ProcessLookupError):
        os.kill(parent, 0)
    # Python cannot reap grandchildren; the missing delayed marker verifies
    # descendant termination without depending on the host's orphan reaper.
    time.sleep(2)
    assert not marker.exists()


def test_escaped_descendant_cannot_hold_timeout_open(tmp_path: Path) -> None:
    pids = tmp_path / "escaped-pid"
    parent = """
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], start_new_session=True)
Path(sys.argv[1]).write_text(str(child.pid))
print('before timeout', flush=True)
time.sleep(20)
"""
    started = time.monotonic()
    try:
        result = run_command(argv=[sys.executable, "-c", parent, str(pids)], timeout_seconds=1, env={})
        assert result.returncode == 124
        assert "before timeout" in result.stdout
        assert time.monotonic() - started < 4
    finally:
        if pids.exists():
            try:
                os.kill(int(pids.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_exited_leader_permission_error_is_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    from beadsflow.infra.run_command import _signal_group

    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True, text=True)
    process.wait()

    def denied(*args: object) -> None:
        raise PermissionError("zombie group")

    monkeypatch.setattr(os, "killpg", denied)
    _signal_group(process, signal.SIGKILL)
