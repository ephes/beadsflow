from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from beadsflow.application.run_epic import RunEpicRequest, run_epic
from beadsflow.application.session import (
    SessionAttachRequest,
    SessionRequest,
    SessionStartRequest,
    SessionStatusRequest,
    SessionStopRequest,
    handle_session,
)


@dataclass(frozen=True, slots=True)
class CliResult:
    exit_code: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="beadsflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the automation loop for an epic")
    run_parser.add_argument("epic_id")
    run_parser.add_argument("--beads-dir", default=None, help="Beads directory (default: discover .beads)")
    run_parser.add_argument("--config", default=None)
    run_parser.add_argument("--once", action="store_true", help="Do a single iteration then exit")
    run_parser.add_argument("--interval", type=int, default=None, help="Sleep interval between iterations")
    run_parser.add_argument("--dry-run", action="store_true", help="Print what would run without executing")
    run_parser.add_argument("--implementer", default=None, help="Implementer profile name")
    run_parser.add_argument("--reviewer", default=None, help="Reviewer profile name")
    run_parser.add_argument("--max-iterations", type=int, default=None, help="Safety cap for iterations")
    run_parser.add_argument("--verbose", action="store_true")
    run_parser.add_argument("--quiet", action="store_true")

    session_parser = subparsers.add_parser("session", help="Manage a zellij session for an epic run")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)

    session_start = session_subparsers.add_parser("start", help="Start a session")
    session_start.add_argument("name")
    session_start.add_argument("--epic", required=True)

    session_attach = session_subparsers.add_parser("attach", help="Attach to a session")
    session_attach.add_argument("name")

    session_stop = session_subparsers.add_parser("stop", help="Stop a session")
    session_stop.add_argument("name")

    session_status = session_subparsers.add_parser("status", help="Show session status")
    session_status.add_argument("name")

    return parser


def _handle_run(args: argparse.Namespace) -> CliResult:
    request = RunEpicRequest(
        epic_id=str(args.epic_id),
        beads_dir=str(args.beads_dir) if args.beads_dir is not None else None,
        config_path=str(args.config) if args.config is not None else None,
        once=bool(args.once),
        interval_seconds=int(args.interval) if args.interval is not None else None,
        dry_run=bool(args.dry_run),
        implementer=str(args.implementer) if args.implementer is not None else None,
        reviewer=str(args.reviewer) if args.reviewer is not None else None,
        max_iterations=int(args.max_iterations) if args.max_iterations is not None else None,
        verbose=bool(args.verbose),
        quiet=bool(args.quiet),
    )
    return CliResult(exit_code=run_epic(request))


def _handle_session(args: argparse.Namespace) -> CliResult:
    request: SessionRequest
    match args.session_command:
        case "start":
            run_args = list(getattr(args, "run_args", []))
            if run_args and run_args[0] == "--":
                run_args = run_args[1:]
            request = SessionStartRequest(name=str(args.name), epic_id=str(args.epic), run_args=run_args)
        case "attach":
            request = SessionAttachRequest(name=str(args.name))
        case "stop":
            request = SessionStopRequest(name=str(args.name))
        case "status":
            request = SessionStatusRequest(name=str(args.name))
        case _:
            raise AssertionError(f"Unhandled session command: {args.session_command}")

    try:
        exit_code = handle_session(request)
        if isinstance(request, SessionStatusRequest):
            print("running" if exit_code == 0 else "not running")
        return CliResult(exit_code=exit_code)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return CliResult(exit_code=1)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv

    # `session start` forwards all remaining args to `beadsflow run`, which means we must
    # accept flags that this CLI does not know about (e.g. `--interval`).
    if len(argv) >= 2 and argv[0] == "session" and argv[1] == "start":
        args, run_args = parser.parse_known_args(argv)
        args.run_args = run_args
    else:
        args = parser.parse_args(argv)

    if args.command == "run":
        return _handle_run(args).exit_code

    if args.command == "session":
        return _handle_session(args).exit_code

    raise AssertionError(f"Unhandled command: {args.command}")
