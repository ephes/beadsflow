from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    src = (Path(__file__).resolve().parents[1] / "src").as_posix()
    if src not in sys.path:
        sys.path.insert(0, src)
