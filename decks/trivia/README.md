# Trivia decks

Trivia decks are built from source data and then refreshed in place in the live collection with **Anki closed**. This is the older of the two deck families; the flag-driven language tooling in [`../language/`](../language/README.md) works differently (Anki open, through AnkiConnect).

| Deck | Folder | Source data | How it reaches Anki |
| --- | --- | --- | --- |
| Jeopardy Smart Prep (≈409K notes) | [`jeopardy/`](jeopardy/JEOPARDY_PREP_DECK.md) | J-Archive clues (`jwolle1/jeopardy_clue_dataset`, through Season 41) + an LLM-built category taxonomy | First build: `.apkg` import; routine refresh: direct SQLite on the live collection (`smart_prep.py --live-db`) |
| US Presidents (49 notes live, 47 built) | [`us_presidents/`](us_presidents/) | Hard-coded in `build_presidents_deck.py` | `genanki` builds `US Presidents.apkg`; you import it (additive — removed or split entries must be deleted by hand) |

## Jeopardy

All the code, data, tests and docs for the Jeopardy deck live in [`jeopardy/`](jeopardy/). Start with [`JEOPARDY_PREP_DECK.md`](jeopardy/JEOPARDY_PREP_DECK.md) (refresh procedure, scoring algorithm, taxonomy pipeline, per-card subjects, field map) and [`JEOPARDY_STRATEGY.md`](jeopardy/JEOPARDY_STRATEGY.md). Run everything as a module from the repo root, e.g.:

```bash
python -m decks.trivia.jeopardy.smart_prep --live-db "$HOME/.local/share/Anki2/User 1/collection.anki2"
python -m decks.trivia.jeopardy.verify_refresh snapshot "$DB" /tmp/jeopardy-before.json
python -m pytest decks/trivia/jeopardy/tests -q
```

The rules that matter most (details in the deck doc):

- Anki must be **closed** for every live-database script; they refuse to run otherwise. Never kill Anki — quit it.
- Scores are percentiles over the current deck, so removing cards makes stored scores stale; a refresh then re-ranks some cards. Changes that would shift many cards' tiers are quantified and approved first.
- `study_optimizer.py` rewrites card ease/due (scheduling data): never run it unasked.
- The deck must stay under AnkiWeb's 314,572,800-byte collection ceiling.

## US Presidents

`us_presidents/build_presidents_deck.py` writes `US Presidents.apkg` (gitignored) using fixed `DECK_ID`/`MODEL_ID` and `guid_for("us-presidents", number)`, so re-imports update matching notes. Import is additive: the live deck still holds the merged "22 & 24" and "45 & 47" notes that were later split into separate terms — delete them by hand.

## Not in this group

The *NFL Fantasy Playoffs 2026* deck is not managed by this repo.
