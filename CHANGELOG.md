# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [Unreleased]

### Added

- Add optional profile settings for comment handling (`comment_mode`, `comment_prefix`, `comment_suffix`) and `require_git_changes` gating for implementers.
- Support `beads_no_db`/`BEADSFLOW_BEADS_NO_DB` to run internal `bd` calls with `--no-db`.

## [0.1.1] - 2025-12-15

### Added

- Write full stdout/stderr logs for failed implementer/reviewer commands under `<beads_dir>/logs/beadsflow/` and include the log path in the failure comment.

## [0.1.0] - 2025-12-14

### Added

- Initial `beadsflow` Python package and CLI skeleton (`beadsflow run`, `beadsflow session` placeholder).
- `beadsflow run` MVP:
  - Repo-local `.beads` support via `bd --no-daemon --json`.
  - Deterministic child selection (`priority_then_oldest`) and phase detection via comment markers.
  - Per-epic locking under `.beads/locks/`.
  - Implementer/reviewer command execution (argv, no shell) with timeouts and failure comments.
  - `--dry-run` and `--once` support.
- Project tooling:
  - `uv` + `uv_build` packaging, `Justfile` workflows.
  - Ruff (lint/format), mypy (typecheck), pytest (+ `just cov` for coverage), pre-commit hooks.
  - Sphinx + MyST docs with ReadTheDocs config.
