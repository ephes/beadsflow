from __future__ import annotations

from dataclasses import dataclass

from beadsflow.application.errors import ConfigError
from beadsflow.infra.zellij import Zellij, ZellijError


@dataclass(frozen=True, slots=True)
class SessionStartRequest:
    name: str
    epic_id: str
    run_args: list[str]


@dataclass(frozen=True, slots=True)
class SessionAttachRequest:
    name: str


@dataclass(frozen=True, slots=True)
class SessionStopRequest:
    name: str


@dataclass(frozen=True, slots=True)
class SessionStatusRequest:
    name: str


SessionRequest = SessionStartRequest | SessionAttachRequest | SessionStopRequest | SessionStatusRequest


def handle_session(request: SessionRequest) -> int:
    zellij = Zellij()
    try:
        match request:
            case SessionStartRequest():
                zellij.start(
                    session_name=request.name,
                    argv=_build_run_argv(epic_id=request.epic_id, run_args=request.run_args),
                )
                return 0
            case SessionAttachRequest():
                zellij.attach(session_name=request.name)
                return 0
            case SessionStopRequest():
                zellij.stop(session_name=request.name)
                return 0
            case SessionStatusRequest():
                return 0 if zellij.has_session(session_name=request.name) else 1
    except ZellijError as exc:
        raise ConfigError(_format_zellij_error(exc, request=request)) from exc


def _build_run_argv(*, epic_id: str, run_args: list[str]) -> list[str]:
    return ["uv", "run", "beadsflow", "run", epic_id, *run_args]


def _format_zellij_error(exc: ZellijError, *, request: SessionRequest) -> str:
    if isinstance(request, SessionStartRequest) and exc.kind == "missing":
        manual = " ".join(_build_run_argv(epic_id=request.epic_id, run_args=request.run_args))
        return (
            f"zellij is required for `beadsflow session` but was not found on PATH.\n\nRun without zellij:\n{manual}"
        )
    return str(exc)
