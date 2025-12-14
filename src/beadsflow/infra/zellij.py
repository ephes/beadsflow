from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

ZellijErrorKind = Literal["missing", "failed"]


@dataclass(frozen=True, slots=True)
class ZellijError(Exception):
    kind: ZellijErrorKind
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class Zellij:
    executable: str = "zellij"

    def _ensure_available(self) -> None:
        if shutil.which(self.executable) is None:
            raise ZellijError(kind="missing", message=f"{self.executable} not found")

    def start(self, *, session_name: str, argv: list[str]) -> None:
        self._ensure_available()
        completed = subprocess.run(
            [self.executable, "-s", session_name, "-c", "--", *argv],
            check=False,
        )
        if completed.returncode != 0:
            raise ZellijError(kind="failed", message=f"Failed to start zellij session: {session_name}")

    def attach(self, *, session_name: str) -> None:
        self._ensure_available()
        completed = subprocess.run([self.executable, "attach", session_name], check=False)
        if completed.returncode != 0:
            raise ZellijError(kind="failed", message=f"Failed to attach to zellij session: {session_name}")

    def stop(self, *, session_name: str) -> None:
        self._ensure_available()
        completed = subprocess.run([self.executable, "kill-session", session_name], check=False)
        if completed.returncode != 0:
            raise ZellijError(kind="failed", message=f"Failed to stop zellij session: {session_name}")

    def has_session(self, *, session_name: str) -> bool:
        self._ensure_available()
        completed = subprocess.run(
            [self.executable, "list-sessions"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise ZellijError(kind="failed", message="Failed to list zellij sessions")

        for line in completed.stdout.splitlines():
            token = self._parse_session_name(line)
            if token == session_name:
                return True
        return False

    @staticmethod
    def _parse_session_name(line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        match = re.match(r"^([\w.-]+)", stripped)
        if match is None:
            return None
        return match.group(1)
