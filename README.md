# Anki deck automation

Scripts that build, score and edit decks in a personal Anki collection. Decks fall into two groups:

| Group | Decks | What the scripts do |
| --- | --- | --- |
| **Trivia** — [`decks/trivia/`](decks/trivia/README.md) | Jeopardy Smart Prep (≈409K cards), US Presidents (49) | Build and score the deck from source data; refresh it in place while Anki is **closed** |
| **Foreign language** — [`decks/language/`](decks/language/README.md) | Français, Español, 中文 | You flag cards in Anki (blue = pronunciation, green = image, orange = context); a script proposes the edit, you review, it applies and clears the flag. Runs against Anki while it is **open**, through AnkiConnect |

More decks may be added later, but only within these two groups.

The *NFL Fantasy Playoffs 2026* deck in the collection is **not** managed by this repo.

## Layout

```text
README.md
docs/ANKI_NOTES.md            collection facts, AnkiConnect facts, hazards found in the code
anki_common/                  shared code (stdlib only) — added as the language tooling is built
decks/
  trivia/
    jeopardy/                 scoring, taxonomy, live refresh, data/, tests/, its own docs
    us_presidents/            genanki builder for the US Presidents deck
  language/                   flag-driven automation (spec + decision log in its README)
stubs/                        vendored .pyi stubs (no `# type: ignore` in this repo)
coding-requirements.md        the coding rules every change follows
```

Everything is a regular Python package (`__init__.py` in each directory) and is run as a module from the repo root:

```bash
python -m decks.trivia.jeopardy.smart_prep --live-db "$HOME/.local/share/Anki2/User 1/collection.anki2"
python -m decks.trivia.us_presidents.build_presidents_deck
python -m pytest                       # all tests
```

Deck procedures are documented next to the code: start with [`decks/trivia/jeopardy/JEOPARDY_PREP_DECK.md`](decks/trivia/jeopardy/JEOPARDY_PREP_DECK.md) or [`decks/language/README.md`](decks/language/README.md).

## Two ways of touching Anki — never mix them

| | Anki state | Mechanism | Used by |
| --- | --- | --- | --- |
| Live-database scripts | **Closed** (scripts refuse to run otherwise) | Direct SQLite edits on `collection.anki2`, backed up first | Jeopardy refresh, archive, optimizer |
| AnkiConnect scripts | **Open** | The AnkiConnect add-on's HTTP API (`127.0.0.1:8765`), so Anki itself does the write | Language decks |

Never write to the collection underneath a running Anki: the change is lost or the collection is corrupted. Never kill Anki to make a script run — quit it. Details and hazards: [`docs/ANKI_NOTES.md`](docs/ANKI_NOTES.md).

## Conventions

- **Dependencies: PDM** (`pdm add`, `pdm add -G <group>`), never bare `pip`. `.pdm-python` currently points at a venv shared with another project — create a project venv before adding dependencies.
- **Typing:** `mypy --strict`, no `Any`, no `# type: ignore` (vendor a `.pyi` into `stubs/` instead), constants over magic values, small functions, no function-scoped imports. Module split: `<x>_consts.py` / `<x>_types.py` / `<x>_helpers.py` / entry scripts. Full rules: [`coding-requirements.md`](coding-requirements.md).
- **Tests:** `unittest.TestCase`, added only for pure logic and only when asked. Run with `python -m pytest`.
- **Formatting:** `black` is *not* clean on every existing file (hand-aligned tables, a few long lines). Format only files you create; never run `black .` across the repo.
- **Git:** nothing is ever committed or pushed without an explicit request. The editor may stage files concurrently — an unexpected staged index is not to be "fixed".
- **Scoring/tier changes** (Jeopardy) are quantified and approved before they ship.
