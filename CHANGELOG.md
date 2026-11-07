# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [Unreleased]

### Security

- Bound command stdout/stderr capture to a combined 8 MiB (64 MiB for Git context/signatures), stop owned process groups on overflow, and reject truncated profile approvals (exit 125).
- Create unique exclusive mode-0600 failure logs in an owned mode-0700 directory; refuse redirected directories, preserve earlier logs, and omit profile arguments from progress messages. Preserve profile failure reporting when a log cannot be written. Existing log files require manual retention/permission review.

- Refresh affected development/docs lock chains (filelock, Pygments, pytest, virtualenv, idna, requests, soupsieve, urllib3). Related tooling constraints update distlib/packaging and add python-discovery for virtualenv. The frozen all-groups registry audit is clean; runtime dependencies remain empty. Re-sync development environments.

- Update the locked certifi CA bundle to 2026.10.19 for the eMudhra/emSign CA compromise ([GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)). Lock-only change; re-sync environments created from the lock.

### Added

- Exercise the real review orchestration with synthetic Beads/Git/reviewer boundaries, including prompt sections, UTF-8 limits, timeout forwarding and invalid verdicts.

- Add read-only push/PR quality CI with frozen uv dependencies, Ruff lint/format, mypy, and isolated unit tests.
- Add optional profile settings for comment handling (`comment_mode`, `comment_prefix`, `comment_suffix`) and `require_git_changes` gating for implementers.
- Support `beads_no_db`/`BEADSFLOW_BEADS_NO_DB` to run internal `bd` calls with `--no-db`.
- Truncate stdout-based comments by default to avoid oversized Beads entries; configure via `BEADSFLOW_MAX_COMMENT_BYTES` and `BEADSFLOW_MAX_COMMENT_LINES`.

### Fixed

- Run profiles at the discovered repository root, with root-relative paths and absolute Beads environment values.
- Exclude concurrent epic runs in one worktree with a stable execution lock acquired before the per-epic lock; separate worktrees retain independent execution ownership.

- Order equal-timestamp comments by numeric ID consistently for phase selection and review context.
- Validate consumed Beads issue schemas and identities at the adapter boundary; reject malformed fields, duplicate IDs and timezone-less timestamps with bounded domain errors before using that context.
- Bound noninteractive Zellij status/stop calls to 10 seconds and report timeout/startup failures as operation errors; preserve interactive attach/start lifetimes.

- Bound each review-context and runner-signature Git command to 30 seconds with owned process-group cleanup, disable external diff/textconv helpers, preserve raw signature output while replacing invalid UTF-8 in review text, and stop on incomplete Git context.

- Bound internal Beads CLI operations and out-of-sync recovery to a shared 30-second deadline; reap timed-out commands, release locks, and never retry ambiguous mutations.

- Report explicit missing/unreadable/invalid config files and malformed profile command quoting as configuration errors before locking.

- Reject coerced TOML booleans/numbers and out-of-range timing/iteration settings; validate CLI/environment overrides before work starts.

- Honor dependencies outside the selected epic when deciding whether a child can run or resume.

- Refuse symlinked epic lock paths and nonregular files; close handles and release locks on acquisition or PID-write failures.
- Refuse epic IDs that are not a plain filename component (letters, digits, `.`, `_`, `-`; no path separators or leading dot) before building the lock path.
- Terminate runner profile process groups on timeout and interruption while retaining timeout output.
- Count HEAD changes and untracked file bytes/modes in the Git-change requirement; exclude ignored files and never follow untracked symlinks.
- Bound standalone reviewer commands with a configurable positive `--timeout-seconds` (default 3600); timeouts fail without a review comment.
- Fail closed on quoted, malformed, later-body, or conflicting review approvals in helper output and comment ingestion.
- Treat missing Beads `priority` values as P0 (0) instead of raising `KeyError`.

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
