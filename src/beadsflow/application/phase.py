from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(Enum):
    IMPLEMENT = "implement"
    REVIEW = "review"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class SelectedWork:
    issue_id: str
    phase: Phase
