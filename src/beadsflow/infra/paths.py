from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RepoPaths:
    repo_root: Path
    beads_dir: Path

    @staticmethod
    def discover(*, cwd: Path, beads_dir: str | None) -> RepoPaths:
        beads_dir_name = beads_dir or ".beads"
        current = cwd.resolve()
        while True:
            candidate = current / beads_dir_name
            if candidate.is_dir():
                return RepoPaths(repo_root=current, beads_dir=candidate)
            if current.parent == current:
                return RepoPaths(repo_root=cwd.resolve(), beads_dir=(cwd / beads_dir_name).resolve())
            current = current.parent
