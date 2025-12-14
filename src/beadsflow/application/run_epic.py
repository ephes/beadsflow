from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunEpicRequest:
    epic_id: str
    beads_dir: str
    config_path: str | None
    once: bool
    interval_seconds: int
    dry_run: bool


def run_epic(request: RunEpicRequest) -> int:
    _ = request
    # Placeholder: v0.1 will select next ready child bead and run implement/review phases.
    return 0
