from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import beadsflow.application.review as review
import beadsflow.entrypoints.cli as cli
import beadsflow.infra.git_command as git_command
from beadsflow.application.errors import CommandError
from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandSpec
from beadsflow.settings import Profile, Settings


@pytest.mark.parametrize("boundary", ["review", "signature"])
@pytest.mark.parametrize("failure", ["timeout", "exit", "launch"])
def test_git_failure_stops_before_agent(
    boundary: str, failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Substitute Python for Git at process launch: no shell scripts or host Git config.
    pids = tmp_path / "pids"
    script = """
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
def stop(*args):
    child.kill()
    child.wait()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
Path(sys.argv[1]).write_text(str(os.getpid()) + ' ' + str(child.pid))
time.sleep(3)
stop()
"""
    original = subprocess.Popen
    calls = 0

    def fake_git(argv: list[str], **kwargs: Any) -> Any:
        nonlocal calls
        assert argv[0] == "git"
        calls += 1
        if failure == "launch":
            raise OSError("synthetic launch failure")
        replacement = [sys.executable, "-c", script, str(pids)]
        if failure == "exit":
            replacement = [sys.executable, "-c", "import sys; sys.exit(2)"]
        return original(replacement, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_git)
    # Lower only the production Git budget, retaining the real cleanup path.
    monkeypatch.setattr(git_command, "GIT_TIMEOUT_SECONDS", 0.5)
    started = time.monotonic()
    if boundary == "review":
        monkeypatch.setattr(
            review.BeadsCli,
            "get_issue",
            lambda *args: SimpleNamespace(
                comments=[],
                id="test-child",
                title="Synthetic",
                status=SimpleNamespace(value="open"),
                description="",
                acceptance_criteria="",
            ),
        )

        def forbidden(**kwargs: Any) -> str:
            pytest.fail("reviewer launched with incomplete Git context")

        monkeypatch.setattr(review, "_run_reviewer_command", forbidden)
        assert cli.main(["review", "--issue-id", "test-child", "--epic-id", "test-epic"]) == 1
        output = capsys.readouterr()
        assert not output.out
        assert "git" in output.err
        if failure == "timeout":
            assert "timed out" in output.err
    else:
        settings = Settings.defaults()
        settings.implementers["test"] = Profile(CommandSpec(["unused"]), "none", "", "", True)
        loop = EpicRunLoop(
            beads=None,  # type: ignore[arg-type]
            epic_id="test-epic",
            settings=settings,
            repo_paths=RepoPaths(repo_root=tmp_path, beads_dir=tmp_path / ".beads"),
            implementer_name="test",
            reviewer_name=None,
            logger=logging.getLogger("test"),
        )

        def forbidden_profile(*args: Any, **kwargs: Any) -> Any:
            pytest.fail("implementer launched without a Git signature")

        monkeypatch.setattr(EpicRunLoop, "_run_profile_command", forbidden_profile)
        with pytest.raises(CommandError, match="git"):
            loop._run_implementer(issue_id="test-child")
    assert calls == 1
    assert time.monotonic() - started < 2.5
    if failure == "timeout":
        assert pids.exists(), "the hanging command must actually start"
        for pid in map(int, pids.read_text().split()):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
