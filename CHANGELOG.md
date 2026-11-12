# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [Unreleased]

### Security

- Pass identities through quoted environment expansions in the quickstart Bash profiles instead of interpolating placeholders into shell source.
- Reject malformed canonical issue, dependency and child IDs before selection, profile execution or subsequent Beads operations.
- Bound each untracked fingerprint inventory to 64 MiB and a shared 30-second deadline; read only the opened snapshot size and refuse detected file changes. Incomplete evidence stops review/publication and approval revalidation without closing the issue or replacing approval state. The same budget applies to implementer `require_git_changes` checks; a failure after the implementer ran posts a fixed failure comment instead of stopping silently.
- Apply the shared 8 MiB combined stdout/stderr cap and owned process-group cleanup to standalone reviewer commands; overflow fails without printing a review comment or accepting a partial `LGTM`. A reviewer's own exit 124/125 is reported as an ordinary failure, not as a timeout or overflow.

- Recheck Beads/log directory identities below the trusted parent before reporting a private diagnostic path; detected swaps retain private artifacts without publishing the affected path. Attempt each owned directory descriptor's close once even when another close reports an I/O error. Add deterministic swap and I/O-failure resource regressions.

- Refuse runner review results when the Git signature changes during review, before publishing stdout or accepting a verdict; retain changed files and log the refusal privately. Reviews now require an available Git worktree; non-Git Beads projects fail before reviewer launch. Ignore the configured Beads metadata subdirectory during review signatures so tracked JSONL comment exports remain usable; implementer change signatures still include it.
- Keep automatic profile-failure and invalid-verdict comments/errors free of raw stdout/stderr; preserve details in private local logs and report trusted phase/exit or verdict-validation messages and log references. Comment-posting failures preserve the primary outcome without echoing exception details.

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

- Compare complete session names using Zellij's short, unformatted list output; avoid ANSI/display-prefix status errors. Normalize interactive start/attach launch failures to safe operation errors while preserving terminal inheritance and interactive lifetimes.

- Require `bd show` responses to match the requested full canonical issue ID; backend mismatches stop automation without exposing the returned ID. Short/partial request IDs now require the full ID.
- Recheck refreshed child statuses against `open`/configured resumable `in_progress` before dispatch or close, continuing past stale epic summaries.
- Keep dry-run reads free of automatic import recovery; out-of-sync databases require an explicit manual `bd sync --import-only`. Normal recovery and documented dry-run locks remain unchanged.
- Persist reviewed Git signature evidence in a runner-recorded approval comment and revalidate it before automatic close, including resumed runs. Exclude the runner execution lock from review signatures so changing process IDs do not invalidate unchanged approvals. The digest checks content freshness, not authorship. Changed content, legacy/manual approvals or missing/invalid evidence request review again; dry runs only preview it.

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
