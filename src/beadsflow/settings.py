from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beadsflow.application.errors import ConfigError
from beadsflow.infra.run_command import CommandSpec


@dataclass(frozen=True, slots=True)
class Profile:
    command: CommandSpec


@dataclass(frozen=True, slots=True)
class RunSettings:
    max_iterations: int
    resume_in_progress: bool
    selection_strategy: str
    on_command_failure: str
    command_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class Settings:
    beads_dir: str
    interval_seconds: int
    log_level: str
    implementer: str | None
    reviewer: str | None
    implementers: dict[str, Profile]
    reviewers: dict[str, Profile]
    run: RunSettings

    @staticmethod
    def defaults() -> Settings:
        return Settings(
            beads_dir=".beads",
            interval_seconds=30,
            log_level="info",
            implementer=None,
            reviewer=None,
            implementers={},
            reviewers={},
            run=RunSettings(
                max_iterations=500,
                resume_in_progress=True,
                selection_strategy="priority_then_oldest",
                on_command_failure="stop",
                command_timeout_seconds=3600,
            ),
        )


def _parse_profile(value: Any) -> Profile:
    if not isinstance(value, dict):
        raise ConfigError("Profile must be a table")
    command = value.get("command")
    if not isinstance(command, str):
        raise ConfigError("Profile.command must be a string")
    return Profile(command=CommandSpec.from_string(command))


def _parse_run_settings(value: Any, base: RunSettings) -> RunSettings:
    if value is None:
        return base
    if not isinstance(value, dict):
        raise ConfigError("[run] must be a table")
    return RunSettings(
        max_iterations=int(value.get("max_iterations", base.max_iterations)),
        resume_in_progress=bool(value.get("resume_in_progress", base.resume_in_progress)),
        selection_strategy=str(value.get("selection_strategy", base.selection_strategy)),
        on_command_failure=str(value.get("on_command_failure", base.on_command_failure)),
        command_timeout_seconds=int(value.get("command_timeout_seconds", base.command_timeout_seconds)),
    )


def load_settings(*, config_path: Path | None) -> Settings:
    settings = Settings.defaults()
    if config_path is None or not config_path.exists():
        return settings

    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a TOML table at the root")

    implementers_raw = raw.get("implementers", {})
    reviewers_raw = raw.get("reviewers", {})
    if not isinstance(implementers_raw, dict) or not isinstance(reviewers_raw, dict):
        raise ConfigError("implementers/reviewers must be TOML tables")

    implementers = {str(name): _parse_profile(profile) for name, profile in implementers_raw.items()}
    reviewers = {str(name): _parse_profile(profile) for name, profile in reviewers_raw.items()}

    settings = Settings(
        beads_dir=str(raw.get("beads_dir", settings.beads_dir)),
        interval_seconds=int(raw.get("interval_seconds", settings.interval_seconds)),
        log_level=str(raw.get("log_level", settings.log_level)),
        implementer=str(raw.get("implementer")) if raw.get("implementer") is not None else None,
        reviewer=str(raw.get("reviewer")) if raw.get("reviewer") is not None else None,
        implementers=implementers,
        reviewers=reviewers,
        run=_parse_run_settings(raw.get("run"), settings.run),
    )

    return settings


def apply_env_overrides(settings: Settings) -> Settings:
    beads_dir = os.environ.get("BEADSFLOW_BEADS_DIR", settings.beads_dir)
    interval = int(os.environ.get("BEADSFLOW_INTERVAL", str(settings.interval_seconds)))
    implementer = os.environ.get("BEADSFLOW_IMPLEMENTER")
    reviewer = os.environ.get("BEADSFLOW_REVIEWER")
    config_log_level = os.environ.get("BEADSFLOW_LOG_LEVEL")
    return Settings(
        beads_dir=beads_dir,
        interval_seconds=interval,
        log_level=config_log_level or settings.log_level,
        implementer=implementer or settings.implementer,
        reviewer=reviewer or settings.reviewer,
        implementers=settings.implementers,
        reviewers=settings.reviewers,
        run=settings.run,
    )


def apply_cli_overrides(
    *,
    settings: Settings,
    beads_dir: str | None,
    interval_seconds: int | None,
    implementer: str | None,
    reviewer: str | None,
    max_iterations: int | None,
    verbose: bool,
    quiet: bool,
) -> Settings:
    _ = verbose
    _ = quiet
    return Settings(
        beads_dir=beads_dir or settings.beads_dir,
        interval_seconds=interval_seconds if interval_seconds is not None else settings.interval_seconds,
        log_level=settings.log_level,
        implementer=implementer or settings.implementer,
        reviewer=reviewer or settings.reviewer,
        implementers=settings.implementers,
        reviewers=settings.reviewers,
        run=RunSettings(
            max_iterations=max_iterations if max_iterations is not None else settings.run.max_iterations,
            resume_in_progress=settings.run.resume_in_progress,
            selection_strategy=settings.run.selection_strategy,
            on_command_failure=settings.run.on_command_failure,
            command_timeout_seconds=settings.run.command_timeout_seconds,
        ),
    )
