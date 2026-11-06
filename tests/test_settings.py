from __future__ import annotations

from pathlib import Path

import pytest

from beadsflow.settings import apply_cli_overrides, apply_env_overrides, load_settings


def test_load_settings_defaults_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    settings = load_settings(config_path=config_path)
    assert settings.beads_dir == ".beads"
    assert settings.interval_seconds == 30


def test_load_settings_parses_profiles(tmp_path: Path) -> None:
    config_path = tmp_path / "beadsflow.toml"
    config_path.write_text(
        """
beads_dir = ".beads"
beads_no_db = true
interval_seconds = 10
implementer = "codex"
reviewer = "claude"

[implementers.codex]
command = "echo impl {issue_id}"
comment_mode = "stdout"
comment_prefix = "Ready for review:\\n\\n"
comment_suffix = "\\n\\nValidation:\\n- uv run pytest"
require_git_changes = true

[reviewers.claude]
command = "echo rev {issue_id}"

[run]
max_iterations = 7
resume_in_progress = false
selection_strategy = "priority_then_oldest"
on_command_failure = "stop"
command_timeout_seconds = 12
""".lstrip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path)
    assert settings.interval_seconds == 10
    assert settings.beads_no_db is True
    assert settings.implementer == "codex"
    assert settings.reviewer == "claude"
    assert "codex" in settings.implementers
    profile = settings.implementers["codex"]
    assert profile.comment_mode == "stdout"
    assert profile.comment_prefix.startswith("Ready for review")
    assert profile.comment_suffix.strip().startswith("Validation")
    assert profile.require_git_changes is True
    assert settings.run.max_iterations == 7
    assert settings.run.command_timeout_seconds == 12


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(config_path=None)
    monkeypatch.setenv("BEADSFLOW_BEADS_DIR", ".beads-alt")
    monkeypatch.setenv("BEADSFLOW_BEADS_NO_DB", "1")
    monkeypatch.setenv("BEADSFLOW_INTERVAL", "55")
    monkeypatch.setenv("BEADSFLOW_IMPLEMENTER", "codex")
    monkeypatch.setenv("BEADSFLOW_REVIEWER", "claude")
    overridden = apply_env_overrides(settings)
    assert overridden.beads_dir == ".beads-alt"
    assert overridden.beads_no_db is True
    assert overridden.interval_seconds == 55
    assert overridden.implementer == "codex"
    assert overridden.reviewer == "claude"


def test_cli_overrides_take_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = apply_env_overrides(load_settings(config_path=None))
    monkeypatch.setenv("BEADSFLOW_INTERVAL", "55")
    env_settings = apply_env_overrides(settings)
    final = apply_cli_overrides(
        settings=env_settings,
        beads_dir=".beads-cli",
        interval_seconds=99,
        implementer="impl",
        reviewer="rev",
        max_iterations=3,
        verbose=False,
        quiet=False,
    )
    assert final.beads_dir == ".beads-cli"
    assert final.interval_seconds == 99
    assert final.implementer == "impl"
    assert final.reviewer == "rev"
    assert final.run.max_iterations == 3


@pytest.mark.parametrize(
    "config",
    [
        'beads_no_db = "false"',
        '[implementers.fake]\ncommand = "echo"\nrequire_git_changes = "false"',
        '[run]\nresume_in_progress = "false"',
        "interval_seconds = true",
        'interval_seconds = "2"',
        "interval_seconds = 1.5",
        "interval_seconds = -1",
        "interval_seconds = 86401",
        "[run]\nmax_iterations = false",
        "[run]\nmax_iterations = 1.5",
        '[run]\nmax_iterations = "2"',
        "[run]\nmax_iterations = 0",
        "[run]\nmax_iterations = 1000001",
        "[run]\ncommand_timeout_seconds = true",
        '[run]\ncommand_timeout_seconds = "2"',
        "[run]\ncommand_timeout_seconds = 1.5",
        "[run]\ncommand_timeout_seconds = -1",
        "[run]\ncommand_timeout_seconds = 0",
        "[run]\ncommand_timeout_seconds = 86401",
    ],
)
def test_config_rejects_coerced_types_and_out_of_range_values(tmp_path: Path, config: str) -> None:
    from beadsflow.application.errors import ConfigError

    path = tmp_path / "beadsflow.toml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(config_path=path)


@pytest.mark.parametrize("interval,iterations,timeout", [(0, 1, 1), (86400, 1000000, 86400)])
def test_config_numeric_boundaries(tmp_path: Path, interval: int, iterations: int, timeout: int) -> None:
    path = tmp_path / "beadsflow.toml"
    path.write_text(
        f"interval_seconds = {interval}\n[run]\nmax_iterations = {iterations}\ncommand_timeout_seconds = {timeout}",
        encoding="utf-8",
    )
    settings = load_settings(config_path=path)
    assert (settings.interval_seconds, settings.run.max_iterations, settings.run.command_timeout_seconds) == (
        interval,
        iterations,
        timeout,
    )
