from __future__ import annotations

import argparse
from dataclasses import dataclass

from beadsflow.application.run_epic import RunEpicRequest, run_epic


@dataclass(frozen=True, slots=True)
class CliResult:
    exit_code: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="beadsflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the automation loop for an epic")
    run_parser.add_argument("epic_id")
    run_parser.add_argument("--beads-dir", default=".beads")
    run_parser.add_argument("--config", default=None)
    run_parser.add_argument("--once", action="store_true", help="Do a single iteration then exit")
    run_parser.add_argument("--interval", type=int, default=30, help="Sleep interval between iterations")
    run_parser.add_argument("--dry-run", action="store_true", help="Print what would run without executing")

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
        beads_dir=str(args.beads_dir),
        config_path=str(args.config) if args.config is not None else None,
        once=bool(args.once),
        interval_seconds=int(args.interval),
        dry_run=bool(args.dry_run),
    )
    return CliResult(exit_code=run_epic(request))


def _handle_session(args: argparse.Namespace) -> CliResult:
    # Placeholder: v0.1 will integrate zellij here.
    _ = args
    return CliResult(exit_code=0)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _handle_run(args).exit_code

    if args.command == "session":
        return _handle_session(args).exit_code

    raise AssertionError(f"Unhandled command: {args.command}")
