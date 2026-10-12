# Contributing

This page describes the local development workflow for beadsflow (using `uv` + `just`), the Definition of Done (DoD), and how to build the docs.

## Prerequisites

- `uv` installed
- `just` installed

## Local dev workflow (uv + just)

Create/refresh the virtualenv and sync dependencies:

```bash
uv sync
```

Run a command inside the environment:

```bash
uv run beadsflow --help
```

Most common tasks are wrapped in the `Justfile`:

```bash
just test
just typecheck
just lint
```

## Definition of Done

Before requesting review, run:

```bash
just test
just typecheck
just lint
```

## Docs

Build the HTML docs locally:

```bash
just docs
```

Equivalent manual command:

```bash
uv sync --group docs
uv run sphinx-build -b html docs docs/_build/html
```

## Automated quality checks

The `Quality` GitHub Actions workflow runs on pushes and pull requests with read-only repository permissions. It installs Python 3.11 and uv 0.13.0, syncs the committed lock with `uv sync --frozen`, then invokes installed tools directly. Sync or check failures fail the job; CI does not use the Justfile fallback. Reproduce it locally:

```bash
uv sync --frozen --python 3.11
export PATH="$PWD/.venv/bin:$PATH"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/pytest
```

Unit tests mock Beads and agent interactions; subprocess fixtures run only local Python commands in temporary directories. No Beads initialization, real database, agent login, owner environment, or subscription command is needed. Treat failed quality checks as blocking review evidence. This workflow does not publish or deploy.
