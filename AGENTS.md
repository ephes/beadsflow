# AGENTS.md (beadsflow)

This repository is the home of **beadsflow**: a standalone tool/package for automating Beads-driven work (eg “epic → child beads”) in a **repo-local** Beads database (no `ops-meta` dependency).

## How to start work on a bead

Always start by reading the bead context **from this repo’s Beads DB**:

```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"
bd --no-daemon show <beadsflow-id>
```

Then follow the dependency chain:

```bash
bd --no-daemon dep tree <beadsflow-id>
bd --no-daemon show <parent-id>
```

If a spec/PRD is referenced, read it before changing code.

## Beads workflow conventions

- Statuses: `open`, `in_progress`, `blocked`, `closed`
- Reviews are requested via bead comments:
  - Start with `Ready for review:` and include validation steps.
  - Reviewer replies with either `LGTM` or `Changes requested:`.
- Prefer small, reviewable increments and keep changes scoped to the current bead.

## Worktrees and syncing (important)

Assume git worktrees may be used for parallel work.

- Keep the daemon disabled: `export BEADS_NO_DAEMON=1`
- In worktrees, only do flush-only sync:
  - `bd --no-daemon sync --flush-only`
- Run full `bd --no-daemon sync` only from the primary checkout (not a linked worktree).

`sync.branch` is configured to `beads/sync` (avoid syncing from `main`).

## Agent defaults

- Shell examples should include fish variants when helpful.
- Prefer `uv`/`uvx` for running Python entrypoints.
- Treat documentation as a first-class deliverable.

## Repo conventions (code + tooling)

- Layout:
  - Source: `src/`
  - Tests: `tests/` (PyTest)
  - Docs: `docs/` (Sphinx + MyST; published via ReadTheDocs)
- Architecture:
  - DDD-oriented separation: entrypoints (CLI) call application/use-case code; domain logic stays independent of infra/CLI.
- Tooling:
  - Project/package management + build backend: `uv` + `uv_build`
  - Commands: `just` (see `Justfile`)
  - Lint/format: `ruff` (via pre-commit and `just lint`)
  - Typechecking: `mypy` (`just typecheck`)

## Definition of done

A change is not “done” unless all of these pass:

- `just test`
- `just typecheck`
- `just lint`
