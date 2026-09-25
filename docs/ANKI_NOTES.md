# Anki collection notes

Facts about the live collection, the installed add-ons and the hazards found in the existing code. Everything here was measured read-only on **2026-09-25** (Anki closed; the collection opened with `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)`). Nothing was written.

Where a fact is inferred rather than observed it is marked *(inferred)*; where it has not been tried on this machine it is marked *(unverified)*.

## Installation

| Thing | Value |
| --- | --- |
| Anki GUI | **26.9.2** (`prefs21.db` `last_run_version = 260902`; `/usr/local/share/anki/app_packages/anki-26.9.2.dist-info`). `/usr/local/bin/anki` is the launcher. |
| Stale launcher venv | `~/.local/share/AnkiProgramFiles/.venv` holds pylib **25.9.4** — ignore it; do not use it as evidence for 26.9.2 behaviour |
| Profile | `~/.local/share/Anki2/User 1/` (`collection.anki2`, `collection.media/`, `backups/`) |
| Collection schema | ver 18 (modern: decks/notetypes/fields in dedicated tables; the `col` JSON columns are empty) |
| Sync | AnkiWeb, `autoSync` on, `syncKey` set (sync22.ankiweb.net). Sync state was clean (0 rows with `usn = -1`) |
| Collection size | 299,900,928 bytes vs AnkiWeb's 314,572,800-byte ceiling (≈14.7 MB headroom) |
| Python | 3.12 (`requires-python = "==3.12.*"`); `.pdm-python` points at the **shared** `/home/max/Documents/Clara/.venv` |
| Add-ons | AwesomeTTS (1436550454), AnkiConnect (2055492159), Review Heatmap, Large and Colorful Buttons, Button Colours Good Again, Mini Format Pack, True Retention |

## Decks, note types, presets

| Deck | id | Cards | Note type |
| --- | --- | --- | --- |
| Español | 1703865136849 | 157 | Basic |
| Français | **1** (Anki's renamed *Default* deck) | 1,799 | Basic |
| 中文 | 1575506557597 | 3,453 | Basic |
| US Presidents | 1785464335001 | 49 | US Presidents (1958372645456) |
| Jeopardy Smart Prep | 1780955714503 | 409,382 | Jeopardy (1560061137470) |
| NFL Fantasy Playoffs 2026 | 1781801996093 | 32 | NFL Fantasy 2025/2026 Model — **out of scope** |

- `Basic` = id 1556219583788, fields `Front`, `Back`, one template `Card 1` (`{{Front}}` / `{{FrontSide}}<hr id=answer>{{Back}}`). It is **shared by all three language decks** (5,409 notes), so editing its template would affect all of them.
- The `Archive` subdeck of Jeopardy no longer exists in the live collection.
- Deck presets (decoded from `decks.kind`): Français → *French* (1785426811230), 中文 → *Chinese* (1785426779250), Jeopardy → *Jeopardy* (1785426844320); Español, US Presidents and NFL share *Default* (id 1).
- The US Presidents deck id differs from the id in `build_presidents_deck.py` (Anki minted a new deck on import); the note type id matches. 47 of 49 notes match the builder's guids; the other two are stale merged notes ("22 & 24", "45 & 47").

## Flags

- Anki flag ints: 1 red, 2 orange, 3 green, 4 blue, 5 pink, 6 turquoise, 7 purple.
- Stored in the **low 3 bits** of `cards.flags`. Write `(raw & ~7) | n`; clear with `raw & ~7`. Preserve the upper bits (none of the live cards uses them today: values seen are 0, 2, 3).
- A card has **at most one flag**.
- Live flags: 9 orange + 1 green, all in 中文; no blue.

## AnkiConnect (as installed, read from source)

Location: `~/.local/share/Anki2/addons21/2055492159/`. Config: `webBindAddress 127.0.0.1`, `webBindPort 8765`, `apiKey null`, CORS list `["http://localhost"]`. Line numbers refer to `__init__.py`.

**Not yet run on Anki 26.9.2** *(unverified)* — it imports `anki.exporting`/`anki.importing` at load and uses deprecated aliases (`tags.bulkAdd`, `media.writeData`, `models.byName`, `Collection.addNote`) that still resolve but warn. Run the smoke test on a scratch profile before relying on it.

| Topic | Fact |
| --- | --- |
| Availability | Binds at add-on load, before any profile opens. Until a profile loads, calls fail with `collection is not available` (`:162-165`). Requires Anki ≥ 23.10. |
| Threading | Requests are served on the Qt main thread by a 25 ms `QTimer` poll (`:81-83`, `util.py:72`) — calls serialise with the GUI and **freeze it while they run**. Keep batches small. |
| Auth | `apiKey` null → no key. Requests with no `Origin` header are allowed (`web.py:243`). |
| Reply shape | JSON `{"result", "error"}` for version 6. Nested `multi` actions default to version 4 and return bare results (`:110`) — set `"version": 6` on each nested action. |
| `multi` | `list(map(self.handler, actions))` (`:515-516`): **does not abort on an error**; each item carries its own result/error. Use for read batches only. |
| Find | `findCards(query)` (`:1525`) → normal Anki search, e.g. `deck:"Français" flag:4 -tag:needs-review`. |
| Card info | `cardsInfo` (`:1533-1580`) returns `flags` (raw int), `note` (nid), `deckName`, and `mod` — the **card's** mod, which changes when the flag changes and misses field edits. It also **renders each card's HTML/CSS**, so use it only on small sets. |
| Note info | `notesInfo` (`:1699-1745`) returns `fields`, `tags`, `mod` (the note's), `cards`; accepts `query=` for scans. `notesModTime` (`:1748`). |
| Flags | **No dedicated action.** `setSpecificValueOfCard(card=<int>, keys=["flags"], newValues=[n])` (`:992-1023`). `flags` is not on the denied-key list. One card per call; returns `[True]` or `[[False, msg]]` (or `False` for a list). |
| Fields | `updateNoteFields` (`:822-834`) sets only the named fields; **silently ignores unknown field names** (validate against `modelFieldNames`, `:1204`). Returns nothing — read back to verify. Unchanged notes are skipped by the Rust `update_note` (no `mod` churn). |
| Tags | `addTags`/`removeTags` (`:918-926`, bulk). `updateNote` with `tags`, and `updateNoteTags`, strip **all** tags and re-add non-atomically (`:901-911`) — do not use. `addTags`/`removeTags` do create undo entries; the other writes do not. |
| Media | `storeMediaFile(filename, data\|path\|url, skipHash, deleteExisting=True)` (`:690-713`). Default `deleteExisting=True` trashes a same-named file first — pass **False**. Returns the final stored name (`write_data` renames on collision): use the returned name. `getMediaFilesNames(pattern)` (`:731`) checks existence. |
| Undo | Every write passes `skip_undo_entry=True` (`:833, 898, 952, 969, 988, 1018`) — **Edit → Undo cannot revert them.** |
| GUI awareness | `guiSelectedNotes` (`:1834`), `guiCurrentCard` (`:1909`, raises outside a review — wrap in try), `guiBrowse` (`:1784`, re-runs a search to refresh the Browser). |
| Other | `exportPackage` (`:2122-2134`, synchronous on the GUI thread, path must end `.apkg`), `sync` (`:502-513`, raises unless the result is NO_CHANGES/NORMAL_SYNC, so it errors after a Jeopardy full-upload), `getProfiles`/`loadProfile` (`:466`/`:474`), `apiReflect` (`:2153`). |

**Editor hazard:** a note open in the Browser or the *Edit Current* editor keeps a stale copy and can overwrite the script's edit on the next keystroke; direct backend calls don't fire the operation hook that would refresh it. Browser rows also show stale flags until the search is re-run.

**Bookkeeping.** `update_note` (Rust) sets `mtime`/`usn` and recomputes `csum`/`sfld` (rslib `notes/mod.rs`); AnkiConnect does not bump `col.scm` *(inferred from the source; unverified on 26.9.2)*, so sync should stay incremental.

## AwesomeTTS (add-on 1436550454)

Full analysis is in [`decks/language/README.md`](../decks/language/README.md) §6. Key facts:

- Presets: Google Translate `zh-CN`, `fr`, `es`, speed 1.0; filename mode `hash`; cache dir `addons21/1436550454/user_files/cache` (391 files, purged after 365 days); real store is `collection.media`.
- Endpoint `https://translate.google.com/translate_tts`, ≤100-char chunks, no key, no retry. Google's `robots.txt` disallows `/translate`.
- Filename `google-<sha1 of "text/google/speed=1.0;voice=CODE" split 8/8/8/8/8>.mp3` — reproducible; derived from text, **not bytes**.
- Cannot be driven from a script (`aqt` at import, no CLI). AnkiConnect has no AwesomeTTS action.
- Its per-note editor dialog appended ` [sound:x]` to a field; the mass generator has never completed a run here.

## Media

- `collection.media`: 8,955 files — 4,674 `google-*`, 2,843 `paste-*`, 370 `images-*`, 98 `googletts-*`, plus `collection.media.db2`.
- Dropping files into the folder by hand: Anki notices *added* files on the next media sync/scan, and the manual advises **Tools → Check Media** afterwards. Files stored through `storeMediaFile` are registered natively — one more reason to use AnkiConnect.

## Hazards found in the existing (Jeopardy-era) code

Not all of these affect the language tooling; they are recorded so they are not rediscovered.

1. **`smart_prep.py --clean-export PATH --live-db DB` is destructive** (`:1070-1085`): renames the live deck, runs `reset_review_progress` (`DELETE FROM revlog` + `UPDATE cards` for **every card in every deck**) and packs the live DB, with no backup. It would wipe language-deck progress. *(Proposed: refuse it — needs the user's OK.)*
2. **A missing taxonomy is silent.** `smart_prep.load_taxonomy` (`:266-268`) logs a warning and returns `{}`, so every card becomes "Other" and `--live-db` would rewrite ≈409K notes' badges/tags. Every default path is relative to the current directory; nothing uses `Path(__file__)`. *(Proposed: anchor paths and make it fatal in live mode — needs the user's OK.)*
3. **`configure_deck_options.py` without `--preset` rewrites every preset** with Jeopardy step timings. *(Proposed: make `--preset` required.)*
4. **Hierarchy separator.** Anki stores hierarchical `decks.name` with `\x1f`; `::` is display only. `archive_dead_cards.py:289` inserts a literal `"Jeopardy Smart Prep::Archive"`. Any deck created/renamed via SQL must use `\x1f`. (One reason the language decks are grouped by repo folder, not by Anki parent deck.)
5. **Name-keyed deck lookups** (`archive_dead_cards.py`, `study_optimizer.py:203`, `consolidate_decks.py:79-80`, `smart_prep.py:1013,1208` with `LIKE '%Jeopardy%'`) would break if a deck were reparented. `get_deck_id` (`jeopardy_db_helpers.py:182-222`) defaults to `"Jeopardy"`, falls back to a substring match, then to the first non-default deck — a typo would silently target the wrong deck.
6. **`field_checksum` (`jeopardy_db_helpers.py:621`) does not match Anki's `csum` for HTML-rich fields**: it matched 2,130 of 5,409 language notes; an emulation that keeps `<img src>` filenames and unescapes entities matched 5,031. `update_collection.py:178` also stores a raw `sfld`. Irrelevant when Anki itself does the write (AnkiConnect), fatal for raw SQL edits of Front.
7. `update_collection.py:185,204` writes `mod` in milliseconds; live `notes.mod`/`cards.mod` are seconds (`col.mod`/`col.scm` are ms). `smart_prep.apply_scores_and_tags` (`:885`) writes `flds`/`tags` with no `mod`/`usn` and relies on the `col.scm` bump to force a full sync.
8. **`verify_refresh.py` is bound to Jeopardy** (`JEOPARDY_NOTETYPE_ID`, line 25) and fingerprints every non-Jeopardy note and every `cards` column including `flags` — any language edit between `snapshot` and `compare` reports CHANGED.
9. **Single overwritten backup.** Seven scripts copy `shutil.copy2(db, db + ".bak")` (overwritten on every run); `copy2` copies neither `-wal` nor `-shm`. `require_anki_closed` scans `/proc/*/fd` (Linux-only) and must be called *before* `connect_anki`. `study_optimizer.py` has only the weaker WAL check.
10. No script copies media into `collection.media`; `pack_apkg` writes an empty media manifest.
11. `pytest` is not a declared dependency (installed in the shared venv only); `[tool.mypy]` has only `mypy_path = "stubs"`, strictness is a CLI flag. Baselines: `mypy --strict .` = 27 errors in 2 files (`filter_export.py` 26, `tests/test_clean_export.py` 1); `ruff check` = 3; pytest collects 125.
12. Cross-decks: a stale `collection.anki2-shm` and several `*-shm`/`*-wal` leftovers exist in the profile dir; the dir holds ≈2 GB of restore-point copies.

## Reading the collection safely

- **Anki closed:** `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)` plus a stub `unicase` collation: `con.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))`. Fields are joined by `chr(0x1f)` in `notes.flds`.
- **Anki open:** do **not** use `immutable=1` (it is only valid when nothing else writes). Copy `collection.anki2` **and** `-wal` together to a scratch dir and read the copy, or use AnkiConnect.
