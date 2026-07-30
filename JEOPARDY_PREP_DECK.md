# Jeopardy Smart Prep — Project Documentation

**Season 42 (Sept 2025+) not yet included.** Acceptable for now — Season 41 data is sufficient for recency weighting.

---

## Refreshing the Deck

There are two modes depending on whether this is your first time setting up or a subsequent refresh.

### First-time setup

```bash
# 1. Build the scored .apkg (Anki can be open)
cd ~/Documents/jeopardy-preperation-anki
python smart_prep.py jeopardy_smart_prep.colpkg jeopardy_smart_prep.apkg

# 2. Import into Anki
#    File → Import → select jeopardy_smart_prep.apkg
#    Tick "Import even if existing note has same key"

# 3. Close Anki, then apply card ordering
python study_optimizer.py

# 4. One-time: make new cards show before reviews (see Study Queue Ordering)
python configure_deck_options.py
```

### Refreshing (already studying — manual edits preserved)

`--live-db` reads **and** writes the live collection, skipping the import step entirely. No `.colpkg` is involved: scoring is computed from the collection itself, so your manual card edits are what gets scored, and there is no way for a stale source file to drift out of sync.

Your card content is never touched — only the frequency badge (field 14) and the `freq:`/`subject:`/`subcat:`/`era:` tags.

```bash
# Close Anki first, then:
python smart_prep.py --live-db "~/.local/share/Anki2/User 1/collection.anki2"
python archive_dead_cards.py     # park dead cards in the Archive subdeck
python study_optimizer.py        # ease tuning + weakness-weighted new-card order
```

Each script writes `collection.anki2.bak` before making changes, and refuses to run while Anki is open.

> **After a refresh, your next AnkiWeb sync will likely require a full sync.** Choose **Upload to AnkiWeb**, then sync your other devices — otherwise a stale remote copy can overwrite the rescored collection.

### ⚠️ AnkiWeb's 300 MB ceiling

AnkiWeb rejects collections over **314,572,800 bytes uncompressed**. With ~452K notes, anything written to *every* note costs ~450 KB per byte, so this limit is easy to trip:

- **Badge markup lives in the template, not the note.** The badge is `<b class="fq h">85</b>` (22 bytes) and `BADGE_STYLE_BLOCK` supplies the CSS once. An earlier inline-styled version was 174 bytes/note = **79 MB** on its own and broke syncing. Never inline styles into the badge.
- **Default-valued tags are not written.** `perf:new` (99.8% of the deck) and `perfsubcat:strong` are omitted; their absence means the same thing. Restoring them costs ~20 MB.
- **VACUUM after bulk updates.** Rewriting 452K notes leaves large free-page churn that only VACUUM reclaims — it recovered **134 MB** (382 MB → 248 MB) in one pass.

```bash
python -c "from pathlib import Path; from jeopardy_db_helpers import connect_anki; \
c=connect_anki(Path.home()/'.local/share/Anki2/User 1/collection.anki2'); c.execute('VACUUM'); c.close()"
```

Anki's **Tools → Check Database** performs an equivalent compaction from the GUI.

### Deck layout

Everything lives in one deck. The legacy split (a separate unscored `Jeopardy` deck) was merged by `consolidate_decks.py`; that script is idempotent and does nothing once merged.

```
Jeopardy Smart Prep            active study queue
Jeopardy Smart Prep::Archive   dead cards — out of rotation, never deleted
```

`python archive_dead_cards.py --restore` moves every archived card back.

---

## Scripts & Files

| File                        | Purpose                                                                                   |
| --------------------------- | ----------------------------------------------------------------------------------------- |
| `update_collection.py`      | Merges jwolle1 TSV clues (post-2019) into .colpkg                                         |
| `classify_categories.py`    | LLM-classifies on-air categories → `category_taxonomy.json`                               |
| `consolidate_taxonomy.py`   | Post-processes taxonomy: merges synonyms, strips temporal noise, injects manual overrides |
| `consolidate_decks.py`      | One-time merge of the legacy `Jeopardy` deck into `Jeopardy Smart Prep` (idempotent)      |
| `add_subject_to_badge.py`   | One-time: adds the subject label back onto the frequency badge (idempotent)               |
| `restore_category_front.py` | One-time: restores `{{Category}}` to the card front, off the back (idempotent)            |
| `archive_dead_cards.py`     | Moves dead cards to the Archive subdeck; `--restore` reverses it                          |
| `smart_prep.py`             | Blended frequency scoring + field/template + tag writes                                   |
| `study_optimizer.py`        | Ease tuning + perf tags + day-category grouped/value-sorted new-card `due` order          |
| `configure_deck_options.py` | One-time: sets deck options so new cards show before reviews (see *Study Queue Ordering*) |
| `jeopardy_consts.py`        | All constants: field indices, tier thresholds, recency weights, subjects                  |
| `jeopardy_types.py`         | TypedDicts: `CategoryClassification`, `NoteRow`, `AnkiCardRow`, etc.                      |
| `jeopardy_db_helpers.py`    | extract/repack .colpkg, SQLite helpers                                                    |
| `category_taxonomy.json`    | LLM classification cache: `{CATEGORY: {subject, sub_category, secondary_subject}}`        |
| `updated.colpkg`            | Merged 1984–2025 collection (source for Steps 2–5)                                        |

---

## Algorithm

### Blended Frequency Score (0–100)

Scoring runs in three stages.

**1 — Topic blend.** How much does this material come up at all?

```
topic = 0.40 × answer_percentile
      + 0.35 × sub_category_percentile
      + 0.25 × max(subject_percentile, secondary_subject_percentile)
```

Each component is the **percentile rank** of that note's **stake-weighted recency frequency** across all notes. The stake multiplier reflects round difficulty (Final Jeopardy > Daily Double > regular) and dollar value (higher = harder).

**2 — Relevance decay.** Is it *still* being asked?

```
raw = topic × liveness_weight(answer_last_seen_year) × card_age_weight(card_air_year)
```

- **`liveness_weight`** (1.00 → 0.15) keys off the most recent year that answer appeared *anywhere in the corpus*. This is the primary "no longer relevant" signal: a topic that stopped appearing in 1994 is retired no matter how often it came up back then. Measured effect: mean score 64.4 for topics last seen 2020+, versus 3.0 for pre-2000.
- **`card_age_weight`** (1.00 → 0.70) keys off the card's own air year and is deliberately much gentler. An old clue about a live topic keeps most of its value — a 1993 Geography card still scores ~52.8 on average.

**3 — Re-percentile.** The decayed values are ranked again, so the published 0–100 is a true percentile: a score of 85 means "more study-worthy than 85% of the deck". Tier thresholds therefore partition the deck at a stable 30 / 30 / 25 / 15.

> **Answer keys are normalized** (`normalize_answer`) before recurrence and liveness are computed: parentheticals and leading articles are stripped, and plural forms fold into the singular *only when both spellings actually occur*. Matching raw text instead made live topics look retired — "talons" last appears in 1999, but "talon" ran through 2025.

| Tier   | Score | Tag           |
| ------ | ----- | ------------- |
| high   | ≥ 70  | `freq:high`   |
| medium | 40–69 | `freq:medium` |
| low    | 15–39 | `freq:low`    |
| rare   | < 15  | `freq:rare`   |

### Recency Weights

```python
{y: 1.0 for y in range(2020, 2027)}   # peak
2019: 0.8,  2018: 0.6,  2017: 0.5,  2016: 0.4
{y: 0.3 for y in range(2010, 2016)}
{y: 0.2 for y in range(1984, 2010)}
```

### Stake Multipliers (Round & Value)

Each note's frequency weight is multiplied by a stake multiplier reflecting the clue's difficulty tier and dollar value:

| Category                       | Multiplier         |
| ------------------------------ | ------------------ |
| Final Jeopardy                 | 4.0x               |
| Daily Double (Double Jeopardy) | 2.5x               |
| Daily Double (Jeopardy)        | 2.0x               |
| Double Jeopardy $400–$2000     | 1.1x–1.5x (linear) |
| Jeopardy $200–$1000            | 0.6x–1.0x (linear) |

The multiplier is computed per-note by `compute_stake_multiplier()` in `smart_prep.py`, reading fields 3 (Round), 7 (Value), and 8 (Daily Double). For non-Daily-Double clues in regular rounds, the multiplier scales linearly with dollar value within that round (no overlap between rounds).

### secondary_subject (Wordplay + Domain) — populated, but doesn't move tiers

The intent: categories using a **wordplay format** (Before & After, Rhyme Time, Anagrams…) to test a **knowledge domain** — `SCIENCE BEFORE & AFTER` → `subject="Wordplay & Language"`, `secondary_subject="Science"` — should get credit for the domain they actually test, via `max(subject_score, secondary_subject_score)` and a `subcat2:` tag.

**The classifier prompt has always requested this field correctly** (`classify_categories.py` asks for and parses `secondary_subject` from the LLM). The bug was in `consolidate_taxonomy.py`: every rebuild path (`MANUAL_OVERRIDES`, catch-all elimination, the normal path, and the override-injection in `main()`) reconstructed each entry as `{subject, sub_category}` only, silently dropping `secondary_subject` — and the script overwrote `category_taxonomy.json` in place, destroying the classifier's original output with no way to recover it. Fixed 2026-07-28: `consolidate()` now carries `secondary_subject` through every path (with a guard that blanks it if it would ever equal the primary subject or `"Other"`), and the script defaults to writing a **new** file (`<input>.consolidated.json`) atomically instead of clobbering its input — pass `--output` explicitly to replace the input on purpose.

Re-running classification on the ~13,245 Wordplay & Language categories produced meaningfully different primary-subject calls for ~1,930 of them (mostly regressing to `Other/Miscellaneous`) — LLM judgment noise across runs, not signal. To avoid that churn, the fix was applied conservatively: every category's existing `subject`/`sub_category` was left untouched, and only `secondary_subject` was grafted in, and only where the reclassification's own subject call agreed with the original (stayed `Wordplay & Language` both times). Result: **2,642 of 13,245** Wordplay & Language categories (**15,020 cards**, 3.3% of the deck) now carry a non-empty `secondary_subject`, sampled and spot-checked for sanity (e.g. `METEOROLOGICAL RHYME TIME` → Science, `THE SUPERB OWL` → Sports, `BABEL-ING ON` → Religion & Mythology). `subcat2:` tags now appear on exactly those 15,020 notes.

**However, re-scoring the live collection (2026-07-28) changed zero cards' `freq:` tier.** `subject_score["Wordplay & Language"]` (44,078, recency-weighted) is larger than *every* `secondary_subject_score` value (the largest, Geography, is 993) — Wordplay & Language is the single biggest subject bucket in the whole taxonomy, so no per-domain secondary slice can ever outweigh it in `max(subject_score, secondary_subject_score)`. The subject component of a wordplay card's blended score was already at its ceiling before this fix; populating `secondary_subject` makes the data honest and lights up `subcat2:` tags (useful for browsing/filtering — see *Useful Anki Browser Searches*), but changing the actual scoring/ranking would need a different formula — e.g. comparing subject and secondary percentiles instead of raw recency-weighted sums. Not implemented; a candidate follow-up if the ranking effect is wanted, not just the tag.

### Taxonomy Pipeline

The LLM classifier produces raw output with ~54K entries. `consolidate_taxonomy.py` then:

1. Injects **296 MANUAL_OVERRIDES** for the highest-frequency on-air categories (SCIENCE, LITERATURE, HISTORY, OPERA, etc.) that otherwise stay uncategorized due to genericity
2. Eliminates catch-all sub-categories (Miscellaneous, Other, Potpourri → `sub_category=null`)
3. Strips temporal prefixes from sub-category names (`1950s Travel` → `Travel`)
4. Merges synonyms (`Films` → `Movies`, `TV Shows` → `Television`)

With MANUAL_OVERRIDES applied, ~39.2% of cards are already covered (177K/452K) even at partial classification.

---

## Anki Field Map

The "Jeopardy" notetype has 14 fields (0-indexed, `\x1f`-delimited):

| #   | Field            | Notes                                             |
| --- | ---------------- | ------------------------------------------------- |
| 0   | Show number      |                                                   |
| 1   | AirDate          | `YYYY-MM-DD`                                      |
| 2   | Extra Info       | TSV `comments`                                    |
| 3   | Round            | `Jeopardy` / `Double Jeopardy` / `Final Jeopardy` |
| 4   | Coords           | row,col position                                  |
| 5   | Category         | on-air category (used for taxonomy lookup)        |
| 6   | Order            |                                                   |
| 7   | Value            | `$400`, `$2000`, etc.                             |
| 8   | Daily Double     | `True` / `False`                                  |
| 9   | Question         | **The clue shown** (TSV `answer`)                 |
| 10  | Links            |                                                   |
| 11  | Answer           | **The correct response** (TSV `question`)         |
| 12  | Correct Attempts |                                                   |
| 13  | Wrong Attempts   |                                                   |

> **Warning:** TSV field names are reversed from natural language. In jwolle1 TSV, `answer` = clue shown, `question` = correct response. The importer maps accordingly.

After `smart_prep.py` runs, field 14 (`Frequency Score`) is added with the HTML badge.

---

## Tags Written by smart_prep.py

| Tag              | Example              | Meaning                                                        |
| ---------------- | -------------------- | -------------------------------------------------------------- |
| `freq:{tier}`    | `freq:high`          | Blended frequency tier                                         |
| `subject:{name}` | `subject:Literature` | Primary taxonomy subject                                       |
| `subcat:{name}`  | `subcat:Shakespeare` | Normalized sub-category                                        |
| `subcat2:{name}` | `subcat2:Science`    | Secondary domain (wordplay only)                               |
| `era:{era}`      | `era:recent`         | Air date bucket (recent=2020+, modern=2010–2019, old=pre-2010) |
| `archived:{why}` | `archived:dead-topic` | Why the card was archived (`archive_dead_cards.py`)           |
| `perf:{tier}`    | `perf:weak`          | Your accuracy on this card (`study_optimizer.py`)              |
| `perfsubcat:{t}` | `perfsubcat:weak`    | Your accuracy across the whole sub-category                    |

Previous `freq:`, `subject:`, `subcat:`, `era:` tags are stripped and replaced on each run (idempotent).

---

## Archive Rules

`archive_dead_cards.py` moves genuinely dead cards to the Archive subdeck. Nothing is deleted, and `--restore` reverses everything. Cards you have **already reviewed** and anything aired **2020 or later** are always exempt.

| Reason       | Rule                                                        | Cards  |
| ------------ | ----------------------------------------------------------- | ------ |
| `dead-topic` | Answer topic not seen anywhere since 2005                   | 26,182 |
| `duplicate`  | Near-verbatim restatement of another clue (newest is kept)  | 7,791  |
| `one-off`    | Answer never repeats in 42 seasons, and clue predates 2010  | 6,725  |
| `stale`      | Time-anchored wording, aired pre-2015                       | 1,579  |
| `malformed`  | Answer is empty or punctuation-only, or the clue is blank   | 609    |

Two traps that cost real accuracy here, both now guarded:

- **"this year" / "this month" is not a time anchor.** In Jeopardy phrasing, `this X` is the self-referential pointer to the thing being asked for ("the carnation is the flower for this month"). Matching it flagged tens of thousands of timeless clues, so those patterns are deliberately excluded from the stale regex — only genuine anchors (`current`, `recently`, `-elect`, `as of 1998`, …) count. `current events` is excluded as an idiom.
- **Short answers are not malformed.** `9`, `H` and `4` are all real responses. Only empty or punctuation-only answers qualify.

---

## Study Queue Ordering

`study_optimizer.py` doesn't just rank new cards individually — it groups each day's on-air category into a block so related clues surface together instead of being scattered across the deck.

**Groups are up to 5 clues from one category board on one day** (`air_date`, `round`, on-air `category`), ordered **by dollar value, descending** ($1000 → $800 → … or $2000 → $1600 → … in Double Jeopardy) — highest-stakes clue first. Groups themselves are still ordered by the existing weakness-weighted frequency score (highest-priority category first; see *Blended Frequency Score* above), so this changes *presentation order within and around* a category, not which categories are prioritized.

**Final Jeopardy is held out and interspersed, not grouped.** It's one clue/day with a 4x stake multiplier — grouped in with everything else, its outsized score would cluster every Final Jeopardy clue at the very front of the queue instead of spreading them out. Instead, FJ clues are ranked among themselves by the same priority score, then spread evenly across the whole queue (`interleave_blocks()`) so they show up every once in a while — measured at a steady ~14-15 groups apart (roughly one every 60-70 cards) on the current collection.

### Fixed 2026-07-29: groups were spanning years, not days

Groups were previously keyed on `(Show number, category)`. Show number (field 0) is blank on **~81,652 cards (18% of the deck)** — every clue merged in from the jwolle1 post-2019 TSV, via `update_collection.py`, which has no show-number column in its source data (`clue_to_note_fields()` deliberately leaves it `""`). With Show number blank, the group key collapsed to `("", category)` for all of them, so every reused broad category name — "AMERICAN HISTORY", "POTPOURRI", "BEFORE & AFTER" — merged into one group spanning years of games, exactly the symptom reported: a single category flooding the queue with unrelated clues from many different air dates.

Fixed by keying groups on `(air_date, round, category)` instead — `air_date` is populated on **every** note, so it's a reliable per-game identifier regardless of source. `round` is included too, guarding the rare case (107 instances found) of the same category name reused in both the Jeopardy and Double Jeopardy rounds on the same day, which would otherwise merge into one 10-card group. Verified against the live collection: 0 of 90,465 resulting groups exceed 5 cards or span more than one category/air_date.

### One-time setup: `configure_deck_options.py`

Grouping cards via `due` order only controls which cards get pulled *from the new-card pool* — by default, Anki still freely interleaves due reviews in between them, so a review card could land in the middle of a 5-card group. Run once:

```bash
python configure_deck_options.py
```

This sets **New/review order** (and the equivalent interday-learning-mix setting) to **"before reviews"** for every deck options group, so new cards — and therefore whole category groups — are always exhausted before that day's review/relearning cards. It's idempotent (safe to re-run) and isn't part of the regular refresh loop.

**What this can't fix:** a card you mark Again/Hard earlier in the *same session* re-enters the queue on its own learning-step timer regardless of this setting — that's Anki's short-term relearning behavior working as intended (you got it wrong; it's supposed to come back soon), and no deck option suppresses it. Only cross-session reviews and multi-day relearning are deferred behind new cards.

---

## Useful Anki Browser Searches

```
tag:freq:high                          → highest-priority cards
tag:freq:high tag:subject:Literature   → Literature cards worth studying most
tag:era:recent                         → 2020+ questions only
tag:era:recent tag:freq:high           → recent + high-frequency (best study focus)
deck:"Jeopardy Smart Prep::Archive"    → review what was archived
tag:archived:dead-topic                → archived because the topic retired
-deck:*Archive* tag:perfsubcat:weak    → active cards in your weak sub-categories
```

Targeting the measured weak spots (see *Study Performance* below):

```
tag:perfsubcat:weak tag:freq:high      → weak AND frequently asked — best ROI
tag:subject:People tag:freq:high       → the weakest subject cluster
tag:subcat:U_S_Presidents              → 46.4% accuracy, worst measured
```

---

## Study Performance (measured 2026-07-28)

From 6,037 reviews across 2,108 cards. Accuracy is Bayesian-blended (prior 70% @ 5 reviews) so thin categories are not over-read.

**Weakest subjects, weighted by share of recent-game clue volume:**

| Subject             | Accuracy | Share of 2020+ clues |
| ------------------- | -------- | -------------------- |
| Wordplay & Language | 82.9%    | **22.0%**            |
| Literature          | **70.5%**| 8.6%                 |
| Film & TV           | **72.4%**| 7.8%                 |
| History             | 76.1%    | 8.2%                 |
| Music               | 75.4%    | 6.0%                 |
| Science             | 86.6%    | 5.5%                 |

**The dominant pattern is people-based recall.** The worst sub-categories cluster hard: U.S. Presidents 46.4%, Americans 47.7%, Politicians 40.9%, People 50.0%, European Royalty 55.0%, American Women 54.2%, Historical Figures 58.7%, Biblical Characters 53.1%. Naming *who did a thing* is the weak spot, not the thing itself.

Geography is the clear strength (Cities 94.2%, Countries 87.5%, Rivers & Lakes 80.4%) — coast there.

**Scale reality:** at ~78 cards/day you will see ~28,470 cards in a year, roughly 7% of the active deck. Ordering quality matters far more than deck size; the deck will never be "finished".

---

## TODOs — Further Optimizations

### Done

- [x] **Re-run `consolidate_taxonomy.py`** on full 54,519 classified categories
- [x] **Run `smart_prep.py`** with stake weighting
- [x] **Consolidate decks** — the legacy `Jeopardy` deck (370,616 cards, never scored) merged into `Jeopardy Smart Prep`; all 452,268 notes now carry a badge and tags
- [x] **Topic-liveness + card-age decay** — outdated material now scores low; see Algorithm above
- [x] **Archive dead cards** — 42,886 cards (9.5%) parked in the Archive subdeck
- [x] **Weakness-weighted card ordering** — `study_priority()` in `study_optimizer.py`
- [x] **Day-category grouping, value order, Final Jeopardy interspersion** — fixed 2026-07-29: groups were keyed on `(Show number, category)`, but Show number is blank on 18% of the deck (the jwolle1 post-2019 import has no show-number column), collapsing every reused broad category name into one group spanning years of games. Regrouped on `(air_date, round, category)` — verified 0 of 90,465 groups now exceed 5 cards or span more than one category/date. Also added descending-value sort within each group and even interspersion of Final Jeopardy cards (`interleave_blocks()`). See *Study Queue Ordering* above. New one-time script `configure_deck_options.py` sets deck options so new cards show before reviews.

### Score Improvements

- [x] **Round & value weighting** — Final Jeopardy clues are highest-stakes. Stake multipliers applied during frequency accumulation:
  - Final Jeopardy: 4.0x
  - Daily Double (Double Jeopardy): 2.5x
  - Daily Double (Jeopardy): 2.0x
  - DJ $400–$2000: linear 1.1x–1.5x by value
  - J $200–$1000: linear 0.6x–1.0x by value
  - Implementation: `compute_stake_multiplier()` reads fields 3 (Round), 7 (Value), 8 (Daily Double) in `smart_prep.py` and multiplies `recency_weight(year)` during frequency accumulation.

### Next up (highest value first)

- [x] **Populate `secondary_subject`** — fixed 2026-07-28: the bug was in `consolidate_taxonomy.py` dropping the field on every write, not the classifier prompt (see above). 2,642 categories / 15,020 cards now carry it and get `subcat2:` tags. Re-scoring changed 0 cards' `freq:` tier, though — the `max(subject_score, secondary_subject_score)` formula can't move a card whose primary subject (Wordplay & Language) is already the largest bucket in the taxonomy. Making the ranking actually respond to this would need a different formula (percentile-based comparison instead of raw recency-weighted sums) — not done here.

- [ ] **Fix the `Unclassified` bucket** — it is 11.1% of recent-game clue volume and the largest single group in your review history (392 reviews), yet those cards earn no topic credit in scoring and cannot be targeted by tag. Worth a focused classification pass over the highest-volume unclassified categories.

- [ ] **Consolidate "Other" subject** — currently 3,875 categories land in `Other` (mostly obscure one-off categories). Null out their `sub_category` so they fall back to answer-only scoring rather than dragging down the Other subject percentile.

- [ ] **Season 42 gap** — jwolle1 dataset ends July 2025. When Season 42 data becomes available, re-run `update_collection.py` and re-score.

### Study Experience

- [ ] **Filtered deck presets** — document recommended Anki filtered deck queries for targeted sessions (e.g., Final Jeopardy practice, recent high-frequency, subject deep-dives)

- [ ] **Anki add-on for dynamic scheduling** — optional: an add-on that shortens intervals for `freq:high` cards (0.7× multiplier) and lengthens for `freq:rare` (1.5×), so high-frequency answers get proportionally more review time without manual filtered decks

- [ ] **Category coverage report** — after full classification + consolidation, print which subjects have the most cards and which sub-categories drive the most `freq:high` hits; use this to prioritize weak-area study

- [ ] **Wagering simulator** — interactive CLI or Anki card that presents a game state (your score, 2nd place score, category) and asks for the optimal bet; validates against the FJ math formulas in JEOPARDY_STRATEGY.md
