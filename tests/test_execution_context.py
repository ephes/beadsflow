from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from beadsflow.application.run_epic import RunEpicRequest, run_epic
from beadsflow.application.runner import EpicRunLoop


def request(epic: str = "epic-1") -> RunEpicRequest:
    return RunEpicRequest(
        epic_id=epic,
        beads_dir=None,
        config_path=None,
        once=True,
        interval_seconds=None,
        dry_run=False,
    )


@pytest.mark.parametrize("absolute_beads", [False, True])
@pytest.mark.parametrize("nested_project", [False, True])
def test_nested_invocation_runs_profile_at_discovered_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absolute_beads: bool, nested_project: bool
) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    root = tmp_path / "project" if nested_project else tmp_path
    root.mkdir(exist_ok=True)
    (root / ".beads").mkdir()
    (root / "fixture.txt").write_text("root fixture")
    nested = root / "nested" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    def run(self: EpicRunLoop, **kwargs: object) -> int:
        result = self._exec(
            argv=[
                sys.executable,
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "print(Path.cwd()); print(Path('fixture.txt').read_text()); "
                    "print(os.environ['BEADS_DIR']); print(os.environ['BEADSFLOW_EPIC_ID']); "
                    "print(os.environ['BEADSFLOW_ISSUE_ID']); print(os.environ['BEADS_NO_DAEMON'])"
                ),
            ],
            issue_id="child-1",
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            str(root),
            "root fixture",
            str(root / ".beads"),
            "epic-1",
            "child-1",
            "1",
        ]
        return 0

    monkeypatch.setattr(EpicRunLoop, "run", run)
    from dataclasses import replace

    config = replace(request(), beads_dir=str(root / ".beads")) if absolute_beads else request()
    assert run_epic(config) == 0


# Each process enters the actual run_epic locking path, then runs a local profile.
# No bd process, database or coding agent is involved.
WORKER = '''
import sys
from pathlib import Path
from beadsflow.application.run_epic import RunEpicRequest, run_epic
from beadsflow.application.runner import EpicRunLoop
root, epic, beads, entered, release, outcome = sys.argv[1:]
def run(self, **kwargs):
    result = self._exec(argv=[sys.executable, '-c', """
import sys, time
from pathlib import Path
entered, release, outcome = sys.argv[1:]
Path(entered).write_text('entered')
deadline = time.monotonic() + 15
while not Path(release).exists():
    if time.monotonic() > deadline:
        raise RuntimeError('release deadline')
    time.sleep(0.01)
sys.exit(int(outcome))
""", entered, release, outcome], issue_id='child')
    return result.returncode
EpicRunLoop.run = run
sys.exit(run_epic(RunEpicRequest(epic_id=epic, beads_dir=beads, config_path=None,
    once=True, interval_seconds=None, dry_run=False)))
'''


def start(root: Path, epic: str, beads: Path, entered: Path, release: Path, outcome: int = 0) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, str(root), epic, str(beads), str(entered), str(release), str(outcome)],
        cwd=root,
        env={k: v for k, v in os.environ.items() if not k.startswith("BEADSFLOW_")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_entered(process: subprocess.Popen[str], entered: Path) -> None:
    deadline = time.monotonic() + 10
    while not entered.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "profile did not enter"
        time.sleep(0.01)


@pytest.mark.parametrize("nested_projects", [False, True])
def test_distinct_epics_exclude_writers_and_release_after_failure(tmp_path: Path, nested_projects: bool) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    first_root = root / "project-one" if nested_projects else root
    second_root = root / "project-two" if nested_projects else root
    first_root.mkdir(exist_ok=True)
    second_root.mkdir(exist_ok=True)
    nested = second_root / "nested"
    nested.mkdir()
    # Different configured DBs must still contend for one worktree.
    first_db, second_db = (
        first_root / ".beads" if nested_projects else root / "db-one",
        second_root / ".beads" if nested_projects else root / "db-two",
    )
    first_db.mkdir()
    second_db.mkdir()
    entered, release = tmp_path / "entered", tmp_path / "release"
    first = start(first_root, "epic-one", first_db, entered, release, outcome=7)
    try:
        wait_entered(first, entered)
        second_entry = tmp_path / "second-entry"
        second = start(nested, "epic-two", second_db, second_entry, release)
        try:
            deadline = time.monotonic() + 10
            while second.poll() is None and not second_entry.exists():
                assert time.monotonic() < deadline, "second run neither entered nor refused"
                time.sleep(0.01)
            assert not second_entry.exists(), "distinct epics ran overlapping profiles"
            _out, err = second.communicate(timeout=10)
            assert second.returncode == 1
            assert "Lock is already held" in err
        finally:
            release.touch()
            second.communicate(timeout=20)
        release.touch()
        first.communicate(timeout=10)
        assert first.returncode == 7
        retry = start(nested, "epic-two", second_db, second_entry, release)
        retry.communicate(timeout=10)
        assert retry.returncode == 0
        assert second_entry.exists()
        assert not (root / ".beads").exists()
        assert not (nested / ".beadsflow").exists()
    finally:
        release.touch()
        first.communicate(timeout=20)


def test_separate_worktrees_can_run_distinct_epics_with_shared_database(tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two"]
    roots[0].mkdir()
    for args in (
        ["init", str(roots[0])],
        [
            "-C",
            str(roots[0]),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "Synthetic baseline",
        ],
        ["-C", str(roots[0]), "worktree", "add", "--detach", str(roots[1]), "HEAD"],
    ):
        subprocess.run(["git", *args], check=True, capture_output=True)
    shared = tmp_path / "shared-db"
    shared.mkdir()
    release = tmp_path / "release"
    processes: list[subprocess.Popen[str]] = []
    try:
        for i, root in enumerate(roots):
            entered = tmp_path / f"entered-{i}"
            process = start(root, f"epic-{i}", shared, entered, release)
            processes.append(process)
            wait_entered(process, entered)
    finally:
        release.touch()
        for process in processes:
            process.communicate(timeout=20)
    assert [process.returncode for process in processes] == [0, 0]
