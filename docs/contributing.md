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
