# Jeopardy Preparation & Language Decks — Claude Code Guide

## Documentation & File Naming

- **README.md** exists only at the repo root. All other documentation must use descriptive filenames (e.g., `TRIVIA_DECKS.md`, `LANGUAGE_DECKS.md`, `JEOPARDY_PREP_DECK.md`). This holds for all future `.md` files — no generic `README.md` in subdirectories.

## Python

- **PDM only** for dependencies (`pdm install` / `pdm add` / `pdm remove`); `pdm.lock` is canonical; use the PDM-managed `.venv`.
- **Ruff is the only linter/formatter** (`pdm run ruff check .`, `pdm run ruff format .`) — no black, no isort.
- **Pyright must report 0 errors** (`npx --yes pyright`) before finishing any Python change. Config is `pyrightconfig.json`; never delete the `sys.path.insert` calls its `extraPaths` mirrors.

## Git Operations

- **NEVER commit or push without explicit user authorization** — this is non-negotiable for any agent or automated process.
