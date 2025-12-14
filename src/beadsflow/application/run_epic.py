from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from beadsflow.application.errors import BeadsflowError, ConfigError
from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.locking import EpicLock
from beadsflow.infra.paths import RepoPaths
from beadsflow.settings import apply_cli_overrides, apply_env_overrides, load_settings


@dataclass(frozen=True, slots=True)
class RunEpicRequest:
    epic_id: str
    beads_dir: str | None
    config_path: str | None
    once: bool
    interval_seconds: int | None
    dry_run: bool
    implementer: str | None = None
    reviewer: str | None = None
    max_iterations: int | None = None
    verbose: bool = False
    quiet: bool = False


def run_epic(request: RunEpicRequest) -> int:
    logger = logging.getLogger("beadsflow")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if request.quiet:
        logger.setLevel(logging.ERROR)
    elif request.verbose:
        logger.setLevel(logging.DEBUG)

    provisional_beads_dir = request.beads_dir or os.environ.get("BEADSFLOW_BEADS_DIR") or ".beads"
    provisional_paths = RepoPaths.discover(cwd=Path.cwd(), beads_dir=provisional_beads_dir)

    config_path = Path(request.config_path) if request.config_path is not None else None
    if config_path is None:
        env_config = os.environ.get("BEADSFLOW_CONFIG")
        if env_config:
            config_path = Path(env_config)
    if config_path is None:
        default_config = provisional_paths.repo_root / "beadsflow.toml"
        config_path = default_config if default_config.exists() else None

    try:
        settings = apply_env_overrides(load_settings(config_path=config_path))
        settings = apply_cli_overrides(
            settings=settings,
            beads_dir=request.beads_dir,
            interval_seconds=request.interval_seconds,
            implementer=request.implementer,
            reviewer=request.reviewer,
            max_iterations=request.max_iterations,
            verbose=request.verbose,
            quiet=request.quiet,
        )
        implementer_name = settings.implementer
        reviewer_name = settings.reviewer
        max_iterations = settings.run.max_iterations

        if settings.run.selection_strategy != "priority_then_oldest":
            raise ConfigError(f"Unsupported selection_strategy: {settings.run.selection_strategy}")
        if settings.run.on_command_failure != "stop":
            raise ConfigError(f"Unsupported on_command_failure: {settings.run.on_command_failure}")

        repo_paths = RepoPaths.discover(cwd=Path.cwd(), beads_dir=settings.beads_dir)
        if not repo_paths.beads_dir.is_dir():
            raise ConfigError(f"Beads directory not found: {repo_paths.beads_dir}")

        beads_cli = BeadsCli(beads_dir=str(repo_paths.beads_dir))
        lock_path = repo_paths.beads_dir / "locks" / f"beadsflow-{request.epic_id}.lock"

        with EpicLock(lock_path=lock_path):
            loop = EpicRunLoop(
                beads=beads_cli,
                epic_id=request.epic_id,
                settings=settings,
                repo_paths=repo_paths,
                implementer_name=implementer_name,
                reviewer_name=reviewer_name,
                logger=logger,
            )
            return loop.run(once=request.once, dry_run=request.dry_run, max_iterations=max_iterations)
    except BeadsflowError as exc:
        logger.error(str(exc))
        return 1
