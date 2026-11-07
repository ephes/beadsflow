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
        start = cwd.resolve()
        ancestors = (start, *start.parents)
        for current in ancestors:
            candidate = current / beads_dir_name
            if not Path(beads_dir_name).is_absolute() and candidate.is_dir():
                return RepoPaths(repo_root=current, beads_dir=candidate)
            # Absolute/shared databases cannot identify the current project.
            # Keep ancestor Beads projects working, even inside a larger Git repo.
            if (current / ".beads").is_dir() or (current / ".git").exists():
                return RepoPaths(repo_root=current, beads_dir=candidate)
        return RepoPaths(repo_root=start, beads_dir=(start / beads_dir_name))

    @property
    def worktree_root(self) -> Path:
        # Beads projects may live below the Git root but still share tracked files.
        for current in (self.repo_root, *self.repo_root.parents):
            if (current / ".git").exists():
                return current
        return self.repo_root
