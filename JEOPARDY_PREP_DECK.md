# Jeopardy Smart Prep — Project Documentation

## Pipeline Status

```
Step 1: Merge post-2019 clues
  update_collection.py  ✅ DONE
  → updated.colpkg (1984-09-10 → 2025-07-25, 452,268 notes)
  → 81,652 clues added from jwolle1/jeopardy_clue_dataset (Season 1–41)
  → 7,200 reviewed cards + full revlog preserved

Step 2: Classify categories via LLM
  classify_categories.py  ⏳ IN PROGRESS (~39K / 54,519 done)
  → category_taxonomy.json (incremental checkpoint, safe to interrupt)
  → Sorted by card frequency: highest-impact categories first
  → Resume: CLAUDE_CODE_EXECPATH=<claude-bin> nohup python3 classify_categories.py updated.colpkg > classify.log 2>&1 &
  → claude binary: /home/max/.vscode/extensions/anthropic.claude-code-2.1.168-linux-x64/resources/native-binary/claude

Step 3: Consolidate taxonomy
  consolidate_taxonomy.py  ✅ WRITTEN, applied once (will re-run after Step 2)
  → Merges 54K LLM categories → ~600–800 clean sub-categories
  → Injects 296 MANUAL_OVERRIDES for top high-frequency on-air categories
  → Run: python3 consolidate_taxonomy.py [--dry-run]

Step 4: Score + tag cards
  smart_prep.py  ✅ WRITTEN, not yet run
  → Adds "Frequency Score" field + badge to notetype
  → Tags all notes: freq:* subject:* subcat:* subcat2:* era:*
  → Run: python3 smart_prep.py updated.colpkg jeopardy_scored.colpkg

Step 5: Import into Anki
  ⏳ User step — import jeopardy_scored.colpkg into Anki desktop
```

**Season 42 (Sept 2025+) not yet included.** Acceptable for now — Season 41 data is sufficient for recency weighting.

---

## Scripts & Files

| File | Purpose |
|---|---|
| `update_collection.py` | Merges jwolle1 TSV clues (post-2019) into .colpkg |
| `classify_categories.py` | LLM-classifies on-air categories → `category_taxonomy.json` |
| `consolidate_taxonomy.py` | Post-processes taxonomy: merges synonyms, strips temporal noise, injects manual overrides |
| `smart_prep.py` | Blended frequency scoring + field/template + tag writes |
| `jeopardy_consts.py` | All constants: field indices, tier thresholds, recency weights, subjects |
| `jeopardy_types.py` | TypedDicts: `CategoryClassification`, `NoteRow`, `AnkiCardRow`, etc. |
| `jeopardy_db_helpers.py` | extract/repack .colpkg, SQLite helpers |
| `category_taxonomy.json` | LLM classification cache: `{CATEGORY: {subject, sub_category, secondary_subject}}` |
| `updated.colpkg` | Merged 1984–2025 collection (source for Steps 2–5) |

---

## Algorithm

### Blended Frequency Score (0–100)

```
score = 0.40 × answer_percentile
      + 0.35 × sub_category_percentile
      + 0.25 × max(subject_percentile, secondary_subject_percentile)
```

Each component is the **percentile rank** of that note's recency-weighted frequency across all notes. Result scaled to 0–100, mapped to tier:

| Tier | Score | Tag |
|---|---|---|
| high | ≥ 70 | `freq:high` |
| medium | 40–69 | `freq:medium` |
| low | 15–39 | `freq:low` |
| rare | < 15 | `freq:rare` |

### Recency Weights

```python
{y: 1.0 for y in range(2020, 2027)}   # peak
2019: 0.8,  2018: 0.6,  2017: 0.5,  2016: 0.4
{y: 0.3 for y in range(2010, 2016)}
{y: 0.2 for y in range(1984, 2010)}
```

### secondary_subject (Wordplay + Domain)

Some categories use a **wordplay format** (Before & After, Rhyme Time, Anagrams…) to test a **knowledge domain**. Example: `SCIENCE BEFORE & AFTER` → `subject="Wordplay & Language"`, `secondary_subject="Science"`.

These cards are tagged with both `subject:Wordplay_Language` and `subcat2:Science`, and the subject component uses `max(subject_score, secondary_subject_score)` so the card gets credit for the knowledge domain it actually tests.

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

| # | Field | Notes |
|---|---|---|
| 0 | Show number | |
| 1 | AirDate | `YYYY-MM-DD` |
| 2 | Extra Info | TSV `comments` |
| 3 | Round | `Jeopardy` / `Double Jeopardy` / `Final Jeopardy` |
| 4 | Coords | row,col position |
| 5 | Category | on-air category (used for taxonomy lookup) |
| 6 | Order | |
| 7 | Value | `$400`, `$2000`, etc. |
| 8 | Daily Double | `True` / `False` |
| 9 | Question | **The clue shown** (TSV `answer`) |
| 10 | Links | |
| 11 | Answer | **The correct response** (TSV `question`) |
| 12 | Correct Attempts | |
| 13 | Wrong Attempts | |

> **Warning:** TSV field names are reversed from natural language. In jwolle1 TSV, `answer` = clue shown, `question` = correct response. The importer maps accordingly.

After `smart_prep.py` runs, field 14 (`Frequency Score`) is added with the HTML badge.

---

## Tags Written by smart_prep.py

| Tag | Example | Meaning |
|---|---|---|
| `freq:{tier}` | `freq:high` | Blended frequency tier |
| `subject:{name}` | `subject:Literature` | Primary taxonomy subject |
| `subcat:{name}` | `subcat:Shakespeare` | Normalized sub-category |
| `subcat2:{name}` | `subcat2:Science` | Secondary domain (wordplay only) |
| `era:{era}` | `era:recent` | Air date bucket (recent=2020+, modern=2010–2019, old=pre-2010) |

Previous `freq:`, `subject:`, `subcat:`, `era:` tags are stripped and replaced on each run (idempotent).

---

## Useful Anki Browser Searches

```
tag:freq:high                          → highest-priority cards
tag:freq:high tag:subject:Literature   → Literature cards worth studying most
tag:era:recent                         → 2020+ questions only
tag:subcat2:Science                    → wordplay clues that test Science knowledge
tag:era:recent tag:freq:high           → recent + high-frequency (best study focus)
```

---

## TODOs — Further Optimizations

### Immediate (after classifier finishes)

- [ ] **Re-run `consolidate_taxonomy.py`** on full 54K taxonomy to apply all rules cleanly
- [ ] **Run `smart_prep.py`** → `jeopardy_scored.colpkg`
- [ ] **Import into Anki** and verify: frequency badge on cards, tags searchable, review history intact

### Score Improvements

- [ ] **Round & value weighting** — Final Jeopardy clues are highest-stakes; currently weighted same as $200 Jeopardy. Proposed multipliers applied during frequency accumulation:
  - Final Jeopardy: 2.0x
  - Daily Double: 1.5x
  - DJ $1600/$2000: 1.5x, DJ $1200: 1.3x
  - J $800/$1000: 1.5x, J $600: 1.3x
  - Implementation: read fields 3 (Round), 7 (Value), 8 (Daily Double) in `smart_prep.py`

- [ ] **Consolidate "Other" subject** — currently 3,875 categories land in `Other` (mostly obscure one-off categories). Null out their `sub_category` so they fall back to answer-only scoring rather than dragging down the Other subject percentile.

- [ ] **Season 42 gap** — jwolle1 dataset ends July 2025. When Season 42 data becomes available, re-run `update_collection.py` and re-score.

### Study Experience

- [ ] **Filtered deck presets** — document recommended Anki filtered deck queries for targeted sessions (e.g., Final Jeopardy practice, recent high-frequency, subject deep-dives)

- [ ] **Anki add-on for dynamic scheduling** — optional: an add-on that shortens intervals for `freq:high` cards (0.7× multiplier) and lengthens for `freq:rare` (1.5×), so high-frequency answers get proportionally more review time without manual filtered decks

- [ ] **Category coverage report** — after full classification + consolidation, print which subjects have the most cards and which sub-categories drive the most `freq:high` hits; use this to prioritize weak-area study

- [ ] **Wagering simulator** — interactive CLI or Anki card that presents a game state (your score, 2nd place score, category) and asks for the optimal bet; validates against the FJ math formulas in JEOPARDY_STRATEGY.md
