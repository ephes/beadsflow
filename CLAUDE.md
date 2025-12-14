# CLAUDE.md (beadsflow)

You are working in the **beadsflow** repository.

## Project goal

Build a standalone tool/package that helps run Beads-based workflows in **repo-local Beads databases**:

- Drive “epic → child beads” loops using comment markers (`Ready for review:`, `LGTM`, `Changes requested:`).
- Support unattended runs (eg via zellij) and multiple concurrent epics via separate workspaces/sessions.
- Be generic: no `ops-meta`, `ops-control`, or `ops-library` assumptions.

## Beads DB location (critical)

This repo uses a repo-local Beads DB under `.beads/`.

To avoid accidentally operating on a different Beads database (eg from a globally-set `BEADS_DIR`), run commands like:

**bash/zsh**
```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"
bd --no-daemon list
```

**fish**
```fish
set -gx BEADS_NO_DAEMON 1
set -gx BEADS_DIR "$PWD/.beads"
bd --no-daemon list
```

## How to work on an issue

1. Read the bead:
   - `bd --no-daemon show <beadsflow-id>`
2. Follow dependencies / parents:
   - `bd --no-daemon dep tree <beadsflow-id>`
3. Implement narrowly against acceptance criteria.
4. Request review by commenting:
   - `bd --no-daemon comment <id> "Ready for review: <summary + validation steps>"`

## Coding preferences

- Prefer simple, explicit Python with good CLI UX.
- Use `uv`/`uvx` for running Python.
- Add docs when behavior changes.
- Avoid over-engineering; keep MVPs shippable.

## Repo structure and tooling

- Source code lives in `src/` (DDD-oriented separation between entrypoints, application/use-cases, and domain logic).
- Tests live in `tests/` and are run with PyTest.
- Formatting/linting uses Ruff; lint hooks are enforced via pre-commit.
- Typechecking uses mypy.
- Docs live in `docs/` and build with Sphinx + MyST; docs are intended to publish on ReadTheDocs.
- Prefer running all workflows via `just` + `uv`:
  - `just test`
  - `just typecheck`
  - `just lint`
  - `just docs`

## Definition of done (required)

Do not consider work complete unless all pass locally:

- Tests: `just test`
- Typechecks: `just typecheck`
- Lint/format checks: `just lint`
