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


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf"), -1, 0])
def test_invalid_timeout_is_rejected_before_spawn(timeout_seconds: float, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_spawn(*args: object, **kwargs: object) -> None:
        pytest.fail("command spawned with an invalid timeout")

    monkeypatch.setattr(subprocess, "Popen", unexpected_spawn)
    with pytest.raises(ValueError, match="timeout_seconds must be finite and positive"):
        run_command(argv=[sys.executable, "-c", "pass"], timeout_seconds=timeout_seconds, env={})


def test_background_command_gets_eof_without_consuming_launcher_input() -> None:
    launcher = """
import sys
from beadsflow.infra.run_command import run_command

result = run_command(argv=[sys.executable, "-c", "import os; print(os.read(0, 1))"], timeout_seconds=2, env={})
assert result.returncode == 0, result
print(result.stdout, end="")
"""
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb") as writer:
        writer.write(b"x")
        writer.flush()
        completed = subprocess.run(
            [sys.executable, "-c", launcher],
            stdin=reader,
            capture_output=True,
            check=True,
            timeout=10,
        )
        assert completed.stdout == b"b''\n"
        os.set_blocking(reader.fileno(), False)
        assert reader.read(1) == b"x"


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
    import beadsflow.infra.run_command as module

    original = module._drain
    interrupted = False

    def interrupt(*args: object, **kwargs: object) -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            deadline = time.monotonic() + 3
            while not pids.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_drain", interrupt)
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


def test_timeout_diagnostic_preserves_child_stderr() -> None:
    write_stderr = "import os; os.write(2, b'child diagnostic'); "
    timed = run_command(
        argv=[sys.executable, "-c", write_stderr + "import time; time.sleep(30)"],
        timeout_seconds=1,
        env={},
    )
    assert timed.returncode == 124
    assert timed.limit == "timeout"
    assert timed.stderr.startswith("child diagnostic\nTimed out after 1 seconds.")

    completed = run_command(
        argv=[sys.executable, "-c", write_stderr + "raise SystemExit(124)"],
        timeout_seconds=5,
        env={},
    )
    assert completed.returncode == 124
    assert completed.limit is None
    assert completed.stderr == "child diagnostic"


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
    from beadsflow.infra.run_command import _signal_group, _wait_for_exit

    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    _wait_for_exit(process, 2)

    def denied(*args: object) -> None:
        raise PermissionError("zombie group")

    monkeypatch.setattr(os, "killpg", denied)
    try:
        _signal_group(process, signal.SIGKILL)
        assert process.returncode is None
    finally:
        assert process.wait() == 0


def test_reaped_leader_group_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    from beadsflow.infra.run_command import _signal_group

    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    process.wait()

    def forbidden(*args: object) -> None:
        pytest.fail("a reaped leader no longer pins the process-group identity")

    monkeypatch.setattr(os, "killpg", forbidden)
    _signal_group(process, signal.SIGKILL)


def test_exit_observation_preserves_unreaped_leader_outcome() -> None:
    from beadsflow.infra.run_command import _wait_for_exit

    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(17)"], start_new_session=True)
    try:
        _wait_for_exit(process, 2)
        assert process.returncode is None
    finally:
        assert process.wait() == 17


@pytest.mark.parametrize("preserve_output", [False, True])
def test_output_decoding_and_cwd(tmp_path: Path, preserve_output: bool) -> None:
    payload = b"patch\r\nname\r\xff\0"
    result = run_command(
        argv=[sys.executable, "-c", f"import os, pathlib; pathlib.Path('ran').touch(); os.write(1, {payload!r})"],
        timeout_seconds=5,
        env={},
        cwd=tmp_path,
        preserve_output=preserve_output,
    )
    assert result.returncode == 0
    assert (tmp_path / "ran").exists()
    if preserve_output:
        assert result.stdout.encode("utf-8", errors="surrogateescape") == payload
    else:
        assert result.stdout == "patch\nname\n\N{REPLACEMENT CHARACTER}\0"


@pytest.mark.parametrize("stream", [1, 2])
def test_output_limit_bounds_capture_and_cleans_descendant(tmp_path: Path, stream: int) -> None:
    import tracemalloc

    marker, pids = tmp_path / "marker", tmp_path / "pids"
    flood = PARENT.replace(
        "time.sleep(30)",
        f"for _ in range(1024):\n    os.write({stream}, b'x' * 65536)\nprint('LGTM', flush=True)",
    )
    tracemalloc.start()
    try:
        result = run_command(
            argv=[sys.executable, "-c", flood, str(marker), str(pids), CHILD],
            timeout_seconds=5,
            env={},
            max_output_bytes=8192,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.returncode == 125
    assert "output exceeded" in result.stderr.lower()
    assert len(result.stdout.encode()) + len(result.stderr.encode()) < 8500
    assert peak < 1024 * 1024
    assert_gone(pids)
    assert not marker.exists()


def test_combined_output_budget_and_exact_boundary() -> None:
    for stderr_bytes, expected in [(4096, 0), (4097, 125)]:
        result = run_command(
            argv=[sys.executable, "-c", f"import os; os.write(1, b'a'*4096); os.write(2, b'b'*{stderr_bytes})"],
            timeout_seconds=5,
            env={},
            max_output_bytes=8192,
        )
        assert result.returncode == expected
        if expected == 0:
            assert result.stdout == "a" * 4096
            assert result.stderr == "b" * 4096


@pytest.mark.parametrize("exit_code", [0, 7])
def test_completed_leader_retires_closed_pipe_descendant(tmp_path: Path, exit_code: int) -> None:
    marker, pid_file = tmp_path / "late-write", tmp_path / "pid"
    parent = """
import subprocess, sys
from pathlib import Path
child = subprocess.Popen(
    [sys.executable, '-c', sys.argv[3], sys.argv[1]],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
Path(sys.argv[2]).write_text(str(child.pid))
print('leader output', flush=True)
sys.exit(int(sys.argv[4]))
"""
    try:
        from beadsflow.infra.locking import EpicLock

        lock_path = tmp_path / "execution.lock"
        with EpicLock(lock_path):
            result = run_command(
                argv=[sys.executable, "-c", parent, str(marker), str(pid_file), CHILD, str(exit_code)],
                timeout_seconds=5,
                env={},
            )
        assert result.returncode == exit_code
        assert result.limit is None
        assert result.stdout == "leader output\n"
        with EpicLock(lock_path):
            assert run_command(argv=[sys.executable, "-c", "pass"], timeout_seconds=2, env={}).returncode == 0
        time.sleep(2.2)
        assert not marker.exists()
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_leader_can_close_pipes_before_finishing(tmp_path: Path) -> None:
    marker = tmp_path / "completed"
    result = run_command(
        argv=[
            sys.executable,
            "-c",
            "import os, time, pathlib, sys; os.close(1); os.close(2); "
            "time.sleep(.1); pathlib.Path(sys.argv[1]).touch()",
            str(marker),
        ],
        timeout_seconds=2,
        env={},
    )
    assert result.returncode == 0
    assert marker.exists()


@pytest.mark.parametrize("outcome", ["completed", "timeout", "output"])
def test_unreapable_child_has_finite_cleanup(outcome: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO

    import beadsflow.infra.run_command as module
    from beadsflow.application.errors import CommandError

    clock = [0.0]
    waits: list[float] = []

    class UnreapableProcess:
        args = ["synthetic-command"]
        returncode = None
        stdout = BytesIO()
        stderr = BytesIO()

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None, "cleanup must never wait without a deadline"
            assert timeout >= 0
            waits.append(timeout)
            clock[0] += timeout
            raise subprocess.TimeoutExpired(self.args, timeout)

    process = UnreapableProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "_signal_group", lambda *args: None)
    first_drain = True

    def drain(*args: object, **kwargs: object) -> None:
        nonlocal first_drain
        if first_drain:
            first_drain = False
            if outcome == "completed":
                return
            if outcome == "output":
                raise module._OutputLimitExceeded
            raise subprocess.TimeoutExpired(process.args, 10)
        # Consume the allotted drain interval, simulating a child holding pipes open.
        allowance = args[2]
        assert isinstance(allowance, (int, float))
        clock[0] += allowance
        raise subprocess.TimeoutExpired(process.args, allowance)

    monkeypatch.setattr(module, "_drain", drain)
    if outcome == "completed":
        with pytest.raises(CommandError, match="cleanup incomplete"):
            run_command(argv=process.args, timeout_seconds=10, env={})
    else:
        result = run_command(argv=process.args, timeout_seconds=10, env={})
        assert result.returncode == (124 if outcome == "timeout" else 125)
        assert result.limit == outcome
        assert "cleanup incomplete" in result.stderr.lower()
    assert waits
    assert clock[0] <= 1.5
    assert process.stdout.closed
    assert process.stderr.closed


def _inject_cleanup_failure(
    outcome: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_exc: type[BaseException] = OSError,
) -> tuple[BaseException, list[str], list[str]]:
    import beadsflow.infra.run_command as module

    events: list[str] = []
    private = "synthetic-private-detail\x1b[31m"

    def record(stage: str) -> None:
        events.append(stage)
        if failure in {stage, "all"}:
            raise cleanup_exc(private)

    class Pipe:
        def close(self) -> None:
            record(self.name)

        def __init__(self, name: str) -> None:
            self.name = name

    class Process:
        args = ["synthetic-command"]
        returncode: int | None = None
        stdout = Pipe("stdout")
        stderr = Pipe("stderr")

        def wait(self, timeout: float | None = None) -> int:
            assert timeout is not None
            record("wait")
            self.returncode = 0
            return 0

    process = Process()
    primary: BaseException = {
        "interrupt": KeyboardInterrupt("initiating interruption"),
        "timeout": subprocess.TimeoutExpired(process.args, 10),
        "output": module._OutputLimitExceeded(),
    }.get(outcome, OSError("initiating read error"))
    first_drain = True

    def drain(*args: object, **kwargs: object) -> None:
        nonlocal first_drain
        if first_drain:
            first_drain = False
            if outcome != "completed":
                raise primary
        else:
            record("drain")

    def signal_group(process: object, sig: signal.Signals) -> None:
        record("term" if sig == signal.SIGTERM else "kill")

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module, "_drain", drain)
    monkeypatch.setattr(module, "_signal_group", signal_group)
    return primary, events, process.args


@pytest.mark.parametrize(
    ("outcome", "failure"),
    [
        (outcome, failure)
        for outcome in ["completed", "interrupt", "error", "timeout", "output"]
        for failure in ["term", "kill", "drain", "wait", "stdout", "stderr", "all"]
        if (outcome, failure) != ("completed", "drain")
    ],
)
def test_cleanup_preserves_primary_and_attempts_remaining_stages(
    outcome: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from beadsflow.application.errors import CommandError

    primary, events, argv = _inject_cleanup_failure(outcome, failure, monkeypatch)
    if outcome in {"interrupt", "error"}:
        with pytest.raises(type(primary)) as caught:
            run_command(argv=argv, timeout_seconds=10, env={})
        assert caught.value is primary
        diagnostic = " ".join(caught.value.__notes__)
    elif outcome == "completed":
        with pytest.raises(CommandError) as caught_cleanup:
            run_command(argv=argv, timeout_seconds=10, env={})
        diagnostic = str(caught_cleanup.value)
    else:
        result = run_command(argv=argv, timeout_seconds=10, env={})
        assert result.returncode == (124 if outcome == "timeout" else 125)
        assert result.limit == outcome
        diagnostic = result.stderr
    assert "cleanup incomplete" in diagnostic.lower()
    assert len(diagnostic) < 600
    assert "synthetic-private-detail" not in diagnostic
    assert "\x1b" not in diagnostic
    assert events[0] == "term"
    assert "kill" in events
    assert events.index("kill") < events.index("wait")
    assert events[-2:] == ["stdout", "stderr"]
    if outcome != "completed":
        assert "drain" in events


@pytest.mark.parametrize(
    ("outcome", "failure"),
    [
        (outcome, failure)
        for outcome in ["completed", "interrupt", "error", "timeout", "output"]
        for failure in ["term", "kill", "drain", "wait", "stdout", "stderr"]
        if (outcome, failure) != ("completed", "drain")
    ],
)
def test_interrupt_during_cleanup_is_reraised_after_remaining_stages(
    outcome: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, events, argv = _inject_cleanup_failure(outcome, failure, monkeypatch, cleanup_exc=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        run_command(argv=argv, timeout_seconds=10, env={})
    if outcome == "interrupt":
        # The initiating interruption wins over a later one.
        assert caught.value is primary
    else:
        assert caught.value is not primary
        assert "synthetic-private-detail" in str(caught.value)
    diagnostic = " ".join(caught.value.__notes__)
    assert "cleanup incomplete" in diagnostic.lower()
    assert "synthetic-private-detail" not in diagnostic
    assert events[0] == "term"
    assert "kill" in events
    assert events[-2:] == ["stdout", "stderr"]
