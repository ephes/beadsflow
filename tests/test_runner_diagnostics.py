from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandSpec
from beadsflow.settings import Profile, RunSettings, Settings


@dataclass
class _FakeBeads:
    comments: list[tuple[str, str]]

    def comment(self, issue_id: str, text: str) -> None:
        self.comments.append((issue_id, text))


def test_runner_writes_log_and_includes_path_in_comment(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    repo_paths = RepoPaths(repo_root=tmp_path, beads_dir=beads_dir)

    settings = Settings(
        beads_dir=str(beads_dir),
        interval_seconds=30,
        log_level="info",
        implementer="test",
        reviewer=None,
        implementers={
            "test": Profile(
                command=CommandSpec.from_string(
                    "python -c \"import sys; print('hello'); print('boom', file=sys.stderr); sys.exit(2)\""
                )
            )
        },
        reviewers={},
        run=RunSettings(
            max_iterations=1,
            resume_in_progress=True,
            selection_strategy="priority_then_oldest",
            on_command_failure="stop",
            command_timeout_seconds=60,
        ),
    )

    fake_beads = _FakeBeads(comments=[])
    loop = EpicRunLoop(
        beads=fake_beads,  # type: ignore[arg-type]
        epic_id="epic-1",
        settings=settings,
        repo_paths=repo_paths,
        implementer_name="test",
        reviewer_name=None,
        logger=__import__("logging").getLogger("test"),
    )

    with pytest.raises(CommandError) as excinfo:
        loop._run_implementer(issue_id="issue-1")  # noqa: SLF001

    assert "Implementer failed with code 2" in str(excinfo.value)
    assert fake_beads.comments
    _issue_id, comment = fake_beads.comments[-1]
    assert "implementer command failed (exit 2)." in comment
    assert "Log:" in comment
    assert "Output:" in comment

    logs_dir = beads_dir / "logs" / "beadsflow"
    log_files = list(logs_dir.glob("issue-1.implementer.*.log"))
    assert log_files, "expected a log file to be written"
    content = log_files[0].read_text(encoding="utf-8")
    assert "returncode=2" in content
    assert "boom" in content
