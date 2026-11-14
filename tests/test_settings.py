from __future__ import annotations

from pathlib import Path

import pytest

from beadsflow.settings import apply_cli_overrides, apply_env_overrides, load_settings


def test_load_settings_defaults_without_config() -> None:
    settings = load_settings(config_path=None)
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


def test_config_byte_boundary_and_opened_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    path = tmp_path / "config.toml"
    path.write_bytes(b"interval_seconds = 17\n#".ljust(65536, b"x"))
    original_open = os.open
    opens: list[int] = []

    def open_config(
        name: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        fd = original_open(name, flags, mode, dir_fd=dir_fd)
        opens.append(fd)
        path.rename(tmp_path / "original.toml")
        path.write_text("interval_seconds = 99", encoding="utf-8")
        return fd

    monkeypatch.setattr(os, "open", open_config)
    assert load_settings(config_path=path).interval_seconds == 17
    assert len(opens) == 1
    with pytest.raises(OSError):
        os.fstat(opens[0])


def test_growing_config_has_bounded_reads_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from beadsflow.application.errors import ConfigError

    path = tmp_path / "config.toml"
    path.write_bytes(b"# small config")
    original_read = os.read
    reads: list[tuple[int, int]] = []

    def read(fd: int, size: int) -> bytes:
        if not reads:
            with path.open("ab") as stream:
                stream.write(b"x" * 131072)
        result = original_read(fd, size)
        reads.append((fd, len(result)))
        return result

    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ConfigError, match="Cannot read config"):
        load_settings(config_path=path)
    assert sum(size for _, size in reads) == 65537
    with pytest.raises(OSError):
        os.fstat(reads[0][0])


@pytest.mark.parametrize("failure", [ValueError, RecursionError, MemoryError])
def test_parser_resource_failures_have_value_free_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    import tomllib

    from beadsflow.application.errors import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("# valid synthetic config", encoding="utf-8")

    def parse(text: str) -> dict[str, object]:
        raise failure("private-sentinel")

    monkeypatch.setattr(tomllib, "loads", parse)
    with pytest.raises(ConfigError, match="Cannot read config") as caught:
        load_settings(config_path=path)
    assert "private-sentinel" not in str(caught.value)


def test_config_regular_symlink_remains_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.toml"
    target.write_text("interval_seconds = 7", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    assert load_settings(config_path=link).interval_seconds == 7


def test_config_detects_in_place_change_below_byte_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from beadsflow.application.errors import ConfigError

    path = tmp_path / "config.toml"
    path.write_bytes(b"# before")
    original_read = os.read
    changed = False

    def read(fd: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(fd, size)
        if not changed:
            path.write_bytes(b"# after!")
            os.utime(path, ns=(0, 123456789))
            changed = True
        return chunk

    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ConfigError, match="Cannot read config"):
        load_settings(config_path=path)


@pytest.mark.parametrize("levels,accepted", [(64, True), (65, False)])
@pytest.mark.parametrize("kind", ["table", "array"])
def test_decoded_config_depth_boundary(tmp_path: Path, levels: int, accepted: bool, kind: str) -> None:
    from beadsflow.application.errors import ConfigError

    path = tmp_path / "config.toml"
    content = (
        "[" + ".".join(["unused"] * levels) + "]"
        if kind == "table"
        else ("unused = " + "[" * (levels - 1) + "0" + "]" * (levels - 1))
    )
    path.write_text(content, encoding="utf-8")
    if accepted:
        assert load_settings(config_path=path).interval_seconds == 30
    else:
        with pytest.raises(ConfigError):
            load_settings(config_path=path)
