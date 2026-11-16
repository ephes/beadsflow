import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def add_submodule(tmp_path_factory: pytest.TempPathFactory) -> Callable[[Path], Path]:
    """Create only local synthetic repositories; never fetch an external fixture."""
    source = tmp_path_factory.mktemp("submodule-source")

    def git(root: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    def configure(root: Path) -> None:
        git(root, "config", "user.name", "Synthetic")
        git(root, "config", "user.email", "synthetic@example.com")
        git(root, "config", "commit.gpgsign", "false")

    git(source, "init")
    configure(source)
    (source / "tracked").write_text("baseline\n")
    git(source, "add", "tracked")
    git(source, "commit", "-m", "Synthetic submodule baseline")

    def add(root: Path) -> Path:
        configure(root)
        name = "module space 雪"
        git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(source), name)
        git(root, "add", ".gitmodules", name)
        git(root, "commit", "-m", "Add synthetic submodule")
        # User ignore policies must not conceal nested worktree changes.
        git(root, "config", f"submodule.{name}.ignore", "all")
        git(root, "config", "diff.ignoreSubmodules", "all")
        module = root / name
        configure(module)
        return module

    return add
