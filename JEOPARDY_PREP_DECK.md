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
python configure_deck_options.py --preset Jeopardy
```

### Refreshing (already studying — manual edits preserved)

`--live-db` reads **and** writes the live collection, skipping the import step entirely. No `.colpkg` is involved: scoring is computed from the collection itself, so your manual card edits are what gets scored, and there is no way for a stale source file to drift out of sync.

Your card content is never touched — only the frequency badge (field 14), the backside frequency details (field 15), and the `freq:`/`subject:`/`subcat:`/`subcat2:`/`era:` tags.

```bash
# Close Anki first, then:
python smart_prep.py --live-db "~/.local/share/Anki2/User 1/collection.anki2"
python archive_dead_cards.py     # park dead cards in the Archive subdeck
python study_optimizer.py        # ease tuning + weakness-weighted new-card order
```

Each script writes `collection.anki2.bak` before making changes, and refuses to run while Anki is open.

> **After a refresh, your next AnkiWeb sync will likely require a full sync.** Choose **Upload to AnkiWeb**, then sync your other devices — otherwise a stale remote copy can overwrite the rescored collection.

#### Safe live-refresh procedure

Anki must be **closed** (quit, not minimised): the scripts refuse to run while it holds the collection open, and anything written underneath a running Anki is lost or corrupts the collection.

```bash
cd ~/Documents/jeopardy-preperation-anki
DB="$HOME/.local/share/Anki2/User 1/collection.anki2"

# 1. Preview — writes nothing to the collection. Logs the evidence reclassification
#    (see "Taxonomy Pipeline") plus the tier summary, and lists every move in the TSV.
python smart_prep.py --live-db "$DB" --analysis-only --evidence-report /tmp/jeopardy-moves.tsv

# 2. Fingerprint your study data (review log, every card row, note content).
python verify_refresh.py snapshot "$DB" /tmp/jeopardy-before.json

# 3. Refresh. Writes "$DB.bak" first — overwritten on EVERY run, so for a restore
#    point older than the latest run, copy it aside first: cp "$DB" "$DB.pre-refresh"
python smart_prep.py --live-db "$DB"

# 4. Prove nothing but the badge, details and tags changed (exits 1 otherwise).
python verify_refresh.py compare "$DB" /tmp/jeopardy-before.json
```

Step 4 prints `Review-log rows: N before, N after` and, when all is well, `OK: review log, cards, and note content are byte-for-byte unchanged`. If it reports `CHANGED`, restore with Anki still closed: `cp "$DB.bak" "$DB"`.

Why this is safe: `smart_prep.py --live-db` only `UPDATE`s `notes.flds` (fields 14 and 15) and `notes.tags` for Jeopardy notes, plus the note type's mtime and `col.scm` (which is what forces the full sync above). It never writes `cards` or `revlog`, so card IDs, due dates, intervals, ease and review history are untouched. `verify_refresh.py` checks that claim rather than trusting it: it hashes every `revlog` and `cards` row and fields 0–13 of every note, and covers other note types (US Presidents, languages…) in full.

### ⚠️ AnkiWeb's 300 MB ceiling

AnkiWeb rejects collections over **314,572,800 bytes uncompressed**. With ~452K notes, anything written to _every_ note costs ~450 KB per byte, so this limit is easy to trip:

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

| File                           | Purpose                                                                                                      |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `update_collection.py`         | Merges jwolle1 TSV clues (post-2019) into .colpkg                                                            |
| `classify_categories.py`       | LLM-classifies on-air categories → `category_taxonomy.json`                                                  |
| `consolidate_taxonomy.py`      | Post-processes taxonomy: merges synonyms, strips temporal noise, injects manual overrides; `NO_CARD_SUBJECTS`  |
| `consolidate_decks.py`         | One-time merge of the legacy `Jeopardy` deck into `Jeopardy Smart Prep` (idempotent)                         |
| `add_subject_to_badge.py`      | One-time: adds the subject label back onto the frequency badge (idempotent)                                  |
| `restore_category_front.py`    | One-time: restores `{{Category}}` to the card front, off the back (idempotent)                               |
| `archive_dead_cards.py`        | Moves dead cards to the Archive subdeck; `--restore` reverses it                                             |
| `smart_prep.py`                | Blended frequency scoring + field/template + tag writes                                                      |
| `jeopardy_taxonomy_helpers.py` | Evidence-based reclassification of `Other` and `Wordplay & Language` categories (see _Taxonomy Pipeline_)    |
| `jeopardy_card_helpers.py`     | Per-card subjects for cards whose category is `Other` (see _Per-card subjects_)                              |
| `verify_refresh.py`            | `snapshot` / `compare` fingerprint of the review log, cards and note content around a refresh                |
| `tests/`                       | `python -m pytest tests -q` — reclassification rule, per-card subjects, end-to-end refresh, the verify guard |
| `study_optimizer.py`           | Ease tuning + perf tags + day-category grouped/value-sorted new-card `due` order                             |
| `configure_deck_options.py`    | One-time: new cards before reviews; `--preset` scopes it (see _Study Queue Ordering_)                        |
| `jeopardy_consts.py`           | All constants: field indices, tier thresholds, recency weights, subjects                                     |
| `jeopardy_types.py`            | TypedDicts: `CategoryClassification`, `NoteRow`, `AnkiCardRow`, etc.                                         |
| `jeopardy_db_helpers.py`       | extract/repack .colpkg, SQLite helpers                                                                       |
| `category_taxonomy.json`       | LLM classification cache: `{CATEGORY: {subject, sub_category, secondary_subject}}`                           |
| `updated.colpkg`               | Merged 1984–2025 collection (source for Steps 2–5)                                                           |

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

The `Other` subject and the `Miscellaneous` and `Unclassified` sub-categories mean "no topic", so they earn no subject or sub-category credit: a grab-bag card is scored on its answer frequency alone. (`Unclassified` was left out of this rule until 2026-09-25; see _Next up_.)

**2 — Relevance decay.** Is it _still_ being asked?

```
raw = topic × liveness_weight(answer_last_seen_year) × card_age_weight(card_air_year)
```

- **`liveness_weight`** (1.00 → 0.15) keys off the most recent year that answer appeared _anywhere in the corpus_. This is the primary "no longer relevant" signal: a topic that stopped appearing in 1994 is retired no matter how often it came up back then. Measured effect: mean score 64.4 for topics last seen 2020+, versus 3.0 for pre-2000.
- **`card_age_weight`** (1.00 → 0.70) keys off the card's own air year and is deliberately much gentler. An old clue about a live topic keeps most of its value — a 1993 Geography card still scores ~52.8 on average.

**3 — Re-percentile.** The decayed values are ranked again, so the published 0–100 is a true percentile: a score of 85 means "more study-worthy than 85% of the deck". Tier thresholds therefore partition the deck at a stable 30 / 30 / 25 / 15.

> **Answer keys are normalized** (`normalize_answer`) before recurrence and liveness are computed: parentheticals and leading articles are stripped, and plural forms fold into the singular _only when both spellings actually occur_. Matching raw text instead made live topics look retired — "talons" last appears in 1999, but "talon" ran through 2025.

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

**However, re-scoring the live collection (2026-07-28) changed zero cards' `freq:` tier.** `subject_score["Wordplay & Language"]` (44,078, recency-weighted) is larger than _every_ `secondary_subject_score` value (the largest, Geography, is 993) — Wordplay & Language is the single biggest subject bucket in the whole taxonomy, so no per-domain secondary slice can ever outweigh it in `max(subject_score, secondary_subject_score)`. The subject component of a wordplay card's blended score was already at its ceiling before this fix; populating `secondary_subject` makes the data honest and lights up `subcat2:` tags (useful for browsing/filtering — see _Useful Anki Browser Searches_), but changing the actual scoring/ranking would need a different formula — e.g. comparing subject and secondary percentiles instead of raw recency-weighted sums. Not implemented; a candidate follow-up if the ranking effect is wanted, not just the tag.

### Taxonomy Pipeline

The LLM classifier produces raw output with ~54K entries. `consolidate_taxonomy.py` then:

1. Injects **324 MANUAL_OVERRIDES** for the highest-frequency on-air categories (SCIENCE, LITERATURE, HISTORY, OPERA, etc.) that otherwise stay uncategorized due to genericity
2. Eliminates catch-all sub-categories (Miscellaneous, Other, Potpourri → `sub_category=null`)
3. Strips temporal prefixes from sub-category names (`1950s Travel` → `Travel`)
4. Merges synonyms (`Films` → `Movies`, `TV Shows` → `Television`)

With MANUAL_OVERRIDES applied, ~39.2% of cards are already covered (177K/452K) even at partial classification.

Then, on every `smart_prep.py` run, an **evidence-based reclassification** re-examines every category, including those the name-only pass already placed in a real subject (next section).

### Evidence-based reclassification (every `smart_prep.py` run)

**The problem.** `classify_categories.py` sees only a category's _name_. A pun like `A NOVEL PASSAGE` (five quotations from famous novels) reads as noise out of context, and a letter game like `CAPITAL "A"` reads as wordplay, so their cards showed _"Frequently asked theme: Other"_ or _Wordplay & Language_ even though the clues test Literature and Geography. `consolidate_taxonomy.py` can't help: it only relabels and applies manual overrides. About 6,500 categories (35,000 cards) sat in `Other` and 13,000 (80,000 cards) in `Wordplay & Language`.

**Content decides the subject, not the format.** A letter-format or pun-format category whose clues test Geography is Geography, not Wordplay & Language with a `secondary_subject`. `Wordplay & Language` is for categories whose content is language itself: etymology, vocabulary, spelling, grammar, and word games with no single content subject.

**Why `CAPITAL "C"` showed Other.** The jwolle1 TSV escapes a leading or trailing `"` as `\"`, and `update_collection.py` imported the category verbatim, so it is stored as `CAPITAL "C\"`. `classify_batch()` maps the LLM's answers back by exact name (`classify_categories.py:288-298`). The model echoes `CAPITAL "C"`, the lookup misses, and the category is silently filled as `Other / Miscellaneous` (renamed `Unclassified` by `consolidate_taxonomy.py`) with no log line. 827 categories (4,349 cards, all aired 2019–2025) carry the stray backslash, and 88.5% of them are `Other`, against 10.1% of other 2019+ categories. Any category the model drops from a 300-item batch takes the same silent path, which is how names like `THE ANDES MOUNTAINS` ended up in `Other`. The rule below never looks at names, so it re-homes these regardless: 77 of its moves (413 cards) are backslash names, `CAPITAL "C\"` → Geography among them (share 0.71 against History 0.08).

**The rule.** At the start of scoring, `smart_prep.py` re-examines every category whose subject is `Other` or `Wordplay & Language` (or that is missing from the taxonomy) using its _cards_ (`jeopardy_taxonomy_helpers.py`). Since 2026-09-25 categories in a real subject are re-checked too (see _Re-checking labeled categories_ below):

1. **The rest of the deck is the knowledge base.** For each answer, count the distinct _classified_ categories it appears in, per subject. `Wordplay & Language` and `Other` categories don't vote, because rhyme-time and before-&-after categories recycle answers from every domain. Letting Wordplay vote, even only in the denominator, cut leave-one-out recall from 17.1% to 10.5%.
2. **An answer's share for a subject** = that subject's votes ÷ all votes. An answer seen in fewer than 2 classified categories carries no evidence.
3. **A category's share for a subject** = the mean over its cards (answers without evidence count as 0).
4. **Winner takes all.** Rank the 15 content subjects (`EVIDENCE_TARGET_SUBJECTS`). The top one wins if the category has **≥ 3 cards**, its share reaches **0.45** (category from `Other`) or **0.50** (from `Wordplay & Language`), and it leads the runner-up by **≥ 0.20**.
5. **The clues must agree.** A naive-Bayes model of clue words, trained on every classified category in the deck (the 15 content subjects plus `Wordplay & Language`, uniform prior, add-one smoothing, no hand-written keyword lists), must pick the same subject from the category's own clues. This is the medium check: `RUSSIAN OPERA` (answers are novels, clues are about operas → Music), `THE 1995 TONY AWARDS`, `THE BEGINNING OF THE PLAY` and `SET IN THE CITY` all stay put.
6. A moved category gets `sub_category` = the subject name. Its `secondary_subject` is kept unless it now duplicates the subject.

Guard-rails:

- Categories in `MANUAL_OVERRIDES` are **never** moved — a pin always wins.
- A category's own answer votes are left out when it is judged, so a label cannot confirm itself; a category whose evidence points to its own subject is left untouched.
- `category_taxonomy.json` is **never written**. The moves are recomputed from the deck on every run (deterministic: same taxonomy and cards in, same result out), so they survive a re-classification or re-consolidation of the JSON.

**Calibration (measured 2026-09-25 on a copy of the live collection).** Every move was read by hand with its answers and two clues. _Strict_ counts a borderline move as wrong; _lenient_ counts it as right.

| Moved from          |     Moves |     Cards |  Wrong | Borderline | Correct (strict) | Correct (lenient) |
| ------------------- | --------: | --------: | -----: | ---------: | ---------------: | ----------------: |
| Other               |       768 |     3,887 |      7 |         11 |            97.7% |             99.1% |
| Wordplay & Language |       840 |     4,628 |      5 |         16 |            97.5% |             99.4% |
| **All**             | **1,608** | **8,515** | **12** |     **27** |        **97.6%** |         **99.3%** |

Per subject, next to leave-one-category-out on the labeled content categories: hide a category's own label and its own votes, then ask whether the rule puts it back. _LOO recall_ is the share it recovers. _LOO agreement_ is how often a prediction matches the LLM's label, which understates precision because some of those labels are wrong.

| Subject               |     Moves |     Cards |  Wrong | Borderline |   Correct |    Labeled | LOO recall |  LOO agreement |
| --------------------- | --------: | --------: | -----: | ---------: | --------: | ---------: | ---------: | -------------: |
| Literature            |       260 |     1,496 |      7 |          2 |     96.5% |      3,236 |      35.6% |          85.3% |
| History               |        70 |       332 |      1 |          4 |     92.9% |      3,237 |       6.5% |          72.5% |
| Geography             |       408 |     2,243 |      1 |          7 |     98.0% |      3,609 |      30.7% |          82.3% |
| Science               |       236 |     1,187 |      1 |          5 |     97.5% |      1,789 |      22.0% |          76.7% |
| Religion & Mythology  |        21 |       102 |      0 |          0 |    100.0% |        993 |      15.2% |          95.6% |
| Music                 |       136 |       696 |      0 |          1 |     99.3% |      3,274 |      23.0% |          89.7% |
| Art                   |        22 |       112 |      0 |          0 |    100.0% |        849 |      12.7% |          90.0% |
| Film & TV             |       250 |     1,268 |      2 |          7 |     96.4% |      4,517 |      30.3% |          80.7% |
| Sports                |        52 |       259 |      0 |          0 |    100.0% |      1,376 |      22.7% |          89.9% |
| Pop Culture           |         2 |         6 |      0 |          0 |    100.0% |      2,321 |       0.3% |          66.7% |
| Food & Drink          |       108 |       578 |      0 |          1 |     99.1% |      1,352 |      27.0% |          88.4% |
| People                |         1 |         4 |      0 |          0 |    100.0% |      2,092 |       0.0% |  100% (1 of 1) |
| Politics & Government |        11 |        56 |      0 |          0 |    100.0% |      1,315 |       5.4% |          91.0% |
| Business & Economics  |        11 |        51 |      0 |          0 |    100.0% |        965 |       2.9% |          84.8% |
| Nature & Animals      |        20 |       125 |      0 |          0 |    100.0% |        879 |       5.7% |          73.5% |
| **All**               | **1,608** | **8,515** | **12** |     **27** | **97.6%** | **31,804** |  **19.1%** |      **83.7%** |

The thresholds come from hand precision by score band, read over every candidate down to 0.40 before the clue check (strict / lenient):

| Top share | From Other    | From Wordplay & Language |
| --------- | ------------- | ------------------------ |
| ≥ 0.60    | 99.7% / 99.7% | 95.8% / 98.3%            |
| 0.55–0.60 | 97.2% / 100%  | 92.4% / 98.7%            |
| 0.50–0.55 | 92.3% / 95.3% | 88.6% / 91.7%            |
| 0.45–0.50 | 88.5% / 93.5% | 82.7% / 90.1%            |
| 0.40–0.45 | 83.3% / 92.2% | 73.8% / 81.7%            |

and, with the clue check on, from the trade-off at each bar (counted in the analysis run, which kept 5 more close calls from Wordplay than the shipped code's 840):

| Pool                | Bar               | Moves | Correct (strict / lenient) |
| ------------------- | ----------------- | ----: | -------------------------- |
| Other               | 0.50              |   568 | 98.8% / 99.6%              |
| Other               | **0.45** (chosen) |   768 | 97.7% / 99.1%              |
| Other               | 0.40              |   974 | 96.3% / 98.8%              |
| Wordplay & Language | 0.55              |   596 | 98.0% / 99.7%              |
| Wordplay & Language | **0.50** (chosen) |   845 | 97.5% / 99.4%              |
| Wordplay & Language | 0.45              | 1,121 | 95.9% / 98.7%              |

**The clue check, measured.** Without it the same thresholds would move 1,929 categories, 64 of them wrong and 59 borderline. The check removes 52 of the wrong moves and 32 of the borderline ones, and also 232 correct ones. Category-_name_ vocabulary was tried as the second signal too and rejected: at the 0.5 bar it vetoed 17 correct moves and only 2 wrong ones.

**How much is still misfiled (blind samples).** 150 random eligible categories per pool, labeled by hand before looking at the rule's output:

- `Other`: 51% are really about one content subject (95% CI 43–59%, about 3,200 of 6,244), and 9% are language content that belongs in Wordplay & Language, which the rule never targets. The rule moves 28% (19–39%) of the content ones, with 0 wrong moves in the sample.
- `Wordplay & Language`: **39% are really content categories** (31–47%, about 4,800 of 12,511), mostly Geography, Literature, Science and Film & TV. 11% are mixed trivia. The rule moves 22% (14–35%) of the content ones, again with 0 wrong moves.

The rule is tuned for precision. Most misfiled content categories stay where they were, but what moves is right about 98% of the time.

**Known misfires (12 categories, 49 cards).** Each is a category whose answers look like one subject while the category tests another. All 12 are pinned in `MANUAL_OVERRIDES` at their previous labels (2026-09-25), so the refresh leaves them alone:

- Titles or names used as word material: `"PRO"` (→ Science), `"WE" THE PEOPLE`, `SAY THE "MAGIC" WORDS`, `WAS HIS NAME O'`, `"JACKS" OF ALL TRADES` and `"NIGHT"S` (→ Literature), and `FOREIGN GEOGRAPHIC TERMS` (→ Geography, really vocabulary).
- The wrong medium: `ADAPTERS` (→ Literature, really screen and stage adaptations), `CLASSIC CRIME NOVELS` and `ACTUAL POLICE BLOTTER REPORTS` (→ Film & TV).
- Others: `TOUGH FACTS` (→ History, a grab-bag) and `THE 3-NAMED EDGARS` (→ Literature, really People).

27 moves (132 cards) are borderline. They are mostly etymology categories, which are language content about a domain (`MEDICAL ETYMOLOGY`, `ELEMENT ETYMOLOGY`, `COUNTRY NAME ETYMOLOGY`), and puns such as `ON THE "TOWN"`, `POP-POURRI` and `STATE OF THE ART`.

**Lost moves.** The clue check also vetoes correct moves whose clues read as wordplay. Seven categories (32 cards) that the earlier Literature-only fallback had moved return to `Other`: `ANAGRAMMED NOVELS`, `A NOVEL LOOK`, `A MOTHER GOOSE PICTORIAL`, `I HAVEN'T READ SHAKESPEARE, BUT...`, `JOHNNY GILBERT DOES SHAKESPEARE!`, `SCRAMBLED ROMANTIC POETS` and `THE LIFE OF A NOVELIST`. Accepted as the cost of the check (2026-09-25).

**Result on the current deck (live refresh 2026-09-25).** The rule proposes 1,608 moves; with the 12 misfires pinned, **1,596 categories (8,466 cards)** moved: 761 (3,859 cards) from `Other` and 835 (4,607 cards) from `Wordplay & Language`. 88 of them are the Literature moves the earlier fallback already made. `Other` shrank from 36,696 to 33,356 cards and Wordplay & Language from 86,959 to 82,352. Geography gained 2,238 cards, Film & TV 1,262, Science 1,182, Literature 949, Music 696 and Food & Drink 578. Against a plain refresh the moves alone change 7,300 cards' tier (1.8%: 3,645 up, 3,655 down), and the tier split stays 30/30/25/15%. Reading the cards and deciding takes about 10 s.

**Fixing a misfire.** Add the category to `MANUAL_OVERRIDES` in `consolidate_taxonomy.py` with the label the JSON already holds, such as `("Other", "Unclassified")` or its current Wordplay sub-category. That takes effect on the next refresh, because the rule checks the override table directly. A pin to any other subject also needs `consolidate_taxonomy.py` re-run so the JSON matches.

**Tunables** are the `EVIDENCE_*` constants in `jeopardy_consts.py`: `MIN_MEAN_SHARE_BY_SOURCE` (Other 0.45, Wordplay 0.5, every real subject `MIN_MEAN_SHARE_LABELED` = 0.5; a subject missing from it is never re-examined), `MIN_MARGIN` (0.2), `MIN_ANSWER_VOTES` (2), `MIN_CATEGORY_NOTES` (3), `NON_VOTING_SUBJECTS`, `TARGET_SUBJECTS`, and the clue model's `CLUE_SUBJECTS`, `CLUE_WORD_PATTERN` and `CLUE_SMOOTHING`.

**Re-checking labeled categories (live 2026-09-25).** Puns fool the name-only pass inside real subjects too: `CAPITALISM` (Business & Economics) is five world capitals, `PLEASE, NOT CHAPTER 11` (Business) is novels, `TWIN PEAKS` (Film & TV) is mountain ranges. Every one of the 31,804 labeled content categories is judged by the same rule, with three differences:

- Its own votes are removed from its answers first (it voted for its current subject).
- The bar is `EVIDENCE_MIN_MEAN_SHARE_LABELED` = **0.50**, with the same 0.20 margin and clue check. The clue model keeps the category's own clues in its current subject, which is conservative: excluding them added 54 moves of which only 43% were fixes.
- The sub-category is reset to the subject name, as for other moves (`Bankruptcy` means nothing under Literature).

Hand check of every candidate down to 0.45 (1,133) plus a random 73 below. _Fix_ = the new subject describes the clues better; _sideways_ = both fit; _wrong_ = the old label was better.

| Top share | Re-labels | Wrong | Sideways | Fixes | Not worse |
| --------- | --------: | ----: | -------: | ----: | --------: |
| ≥ 0.60 | 361 | 1 | 24 | 93.1% | 99.7% |
| 0.55–0.60 | 200 | 1 | 18 | 90.5% | 99.5% |
| 0.50–0.55 | 262 | 0 | 26 | 90.1% | 100% |
| 0.45–0.50 (not used) | 310 | 2 | 52 | 82.6% | 99.4% |
| 0.40–0.45 (sample) | 73 | 1 | 15 | 78.1% | 98.6% |
| **≥ 0.50 (used)** | **823** | **2** | **68** | **91.5%** | **99.8%** |

Sideways moves cluster in History → Geography (historical geography), Music → Film & TV (TV themes, soundtracks) and People / Pop Culture → Film & TV (celebrity gossip). People and Pop Culture only ever lose categories: their answers are asked everywhere, so they never win a vote. Below 0.50 the adaptation failure mode appears (Literature → Film & TV: `THE PLAYS OF NEIL SIMON`, `ELMORE LEONARD`, `THE NEBULA AWARDS`) and opera titles shared with books (`"O"PERA`).

The two wrong re-labels, `"CEL"EBRITY WORDS` (→ Science; really word play) and `MOBILE HOMES` (→ Geography), are pinned in `MANUAL_OVERRIDES`. Live result: **821 categories (4,557 cards)** re-labeled; Pop Culture −834 cards, People −725, History −328; Film & TV +738, Geography +734, Literature +610, Science +254. 7,412 cards (1.8%) changed tier (3,273 up, 4,139 down); the tier split is unchanged. Rehearsed and live: review log, cards and note content byte-for-byte unchanged; file 301.1 MB (299.8 MB after VACUUM). Restore point: `collection.anki2.pre-label-recheck`.

**Preview before writing:** `python smart_prep.py --live-db "$DB" --analysis-only --evidence-report /tmp/jeopardy-moves.tsv` logs `Evidence reclassification: 2417 categories (13,023 cards)`, the 15 most common source → subject pairs and the 15 largest moves, and writes every move (category, cards, from, to, share, runner-up, runner-up share) to the TSV.

### Per-card subjects (cards of `Other` categories)

> **Status (2026-09-25): implemented and rehearsed on a copy, not yet run on the live collection.** It waits for the approval page (see the last paragraph). Nothing below has changed a score or a tier.

**The problem.** The category rules judge a whole category, so a category whose clues test different things stays `Other`, however plain each card is. The card that started this is `DID I MISS ANYTHING?` ($800, "George Loveless was sent … to this distant place in 1834" → Australia). It is not a grab-bag: five clues, all from one 2021 board, about people who came home after years away, and the clue model calls every one History (about 31 log-units clear over the whole category). The category rule declined it for two reasons the doc's rule cannot avoid: the answers are places (Philippines, New Guinea, Australia), so the mean answer share is Geography 0.34 against History 0.24 (the bar is 0.45 with a 0.20 margin), and the rule needs the clue model to agree with the answers. Read blind, **41% of the categories still in `Other` are really about one subject, 9% are language content and 51% mix subjects; 77% of `Other` cards have a clear content subject when read one at a time.** Grab-bags cannot be fixed per category, so this layer judges a card.

**The rule** (`jeopardy_card_helpers.py`, run by `smart_prep.py` right after the category rules, on the refined taxonomy, so the categories they moved vote and teach the clue model; about 13 s). It applies to a card whose category is `Other`: that includes a category missing from the taxonomy and the grab-bags pinned as `Other` in `MANUAL_OVERRIDES` (POTPOURRI, HODGEPODGE… are the point of the layer). It never touches `Wordplay & Language` or a real subject.

1. **Score the 15 content subjects.** `score(subject) = clue log-likelihood + 4 × ln(answer share + 0.2)`. The clue term is the same naive-Bayes clue model as the category rule, applied to this one clue. The answer share is the share of the classified categories containing the answer that belong to the subject (`build_answer_votes`, at least 2 votes, else no evidence). The 4 lets one answer count for about as much as ~20 clue words, whose individual votes naive Bayes overstates; the 0.2 stops an answer with no votes for a subject from vetoing it.
2. **Show the winner** if it leads the runner-up by **at least 5** (`CARD_SUBJECT_MIN_GAP_BY_SOURCE`), is not **People** or **Pop Culture** (`CARD_SUBJECT_NEVER_SHOWN`) and the clue's words do not read as Wordplay & Language more than as the winner.

The Loveless card: the answer votes Geography 39%, History 8%; the clue alone puts History 5.1 ahead of Literature; together History leads Literature by 5.5 and Geography by 5.9, so it shows History. The answer alone would have said Geography. Result on the current deck: **9,473 cards (28.4% of `Other`) in 4,129 categories.** In the categories that really are about one subject, 33% of the cards get a subject and 94% of those match the category.

**What the card shows.** The per-card subject replaces the category's on the badge, in "Frequently asked theme", in the `subject:` tag and in the "Appeared N times" count (each card counts under the subject it shows). The `subcat:` tag stays the category's (`Unclassified`), which is also how to find these cards: `tag:subject:History tag:subcat:Unclassified`. A card without a confident subject still shows `Other`. The category's label in the taxonomy never changes.

**Scoring is deliberately unchanged.** `score_notes()` still sees the category's subject, so a per-card subject moves no score and no tier: **0 of 409,382 cards** (rehearsed: the 2026-09-25 tiers reproduce exactly before and after). Other treatments were simulated with the real scoring code and are **not** made; each would need approval:

| Cards labeled (gap ≥) | Subject credit only: cards that change tier | Full credit, as a category move: cards that change tier |
| --------------------- | ------------------------------------------: | ------------------------------------------------------: |
| 5 (9,473)             |                              13,081 (3.20%) |                                          27,641 (6.75%) |
| 6 (7,874)             |                              11,615 (2.84%) |                                          23,617 (5.77%) |
| 7 (6,417)             |                              10,222 (2.50%) |                                          20,096 (4.91%) |
| 8 (5,175)             |                               8,973 (2.19%) |                                          16,941 (4.14%) |

Most of the moved tiers belong to _other_ cards (8,036 of the 13,081 at gap 5), because a tier is a percentile and labeled cards rising push others down.

**Calibration (2026-09-25).** Every proposal was labeled by a reader who could not see it: 12,271 cards (every per-card candidate at gap ≥ 4 and every card of the category-move candidates below) in 25 batches read by independent sub-agents, with 189 control cards from my own blind hand labels mixed in. On the controls the readers pick my best subject 86% of the time and one of my two subjects 97% of the time (over the 650 cards both of us labeled: 90% and 97%). _Exactly right_ = the reader's best subject; _right or close_ also counts their second choice.

| Gap        |  Cards | Exactly right | Right or close | Cards at or above | Exactly right | Right or close |
| ---------- | -----: | ------------: | -------------: | ----------------: | ------------: | -------------: |
| 5–6        |  1,610 |         74.5% |          88.8% |             9,473 |         87.0% |          95.2% |
| 6–7        |  1,458 |         80.9% |          91.8% |             7,863 |         89.5% |          96.6% |
| 7–8        |  1,238 |         84.9% |          95.2% |             6,405 |         91.5% |          97.6% |
| 8 and up   |  5,167 |         93.0% |          98.2% |             5,167 |         93.0% |          98.2% |

The gap is the only signal that separated right from wrong inside a band: whether the two experts agree, whether the answer had votes and how long the clue is made a point or two of difference at most. By subject (gap ≥ 5): Geography 1,857 proposals (80.8% exactly right, 93.4% right or close), Science 1,648 (83.1%, 92.2%), History 1,472 (88.2%, 95.6%), Literature 913 (91.1%, 97.5%), Film & TV 822 (87.6%, 94.3%), Music 725 (94.1%, 98.2%), Food & Drink 656 (82.5%, 96.0%), Sports 375 (96.8%, 98.9%), Nature & Animals 283 (96.1%, 99.6%), Religion & Mythology 230 (96.5%, 99.6%), Art 195 (82.6%, 89.2%), Politics & Government 171 (88.3%, 99.4%), Business & Economics 126 (97.6%, 100%). **Pop Culture (110 proposals: 47.3% / 66.4%) and People (32: 18.8% / 56.2%) are never shown**: their answers are asked in every kind of category, so the votes cannot vouch for them. Of the 503 proposals at gap ≥ 5 that were plainly wrong, the reader found a different content subject for 341, no single subject for 105 and a language item for 57. Cutting the cutoff to 5 is the trade-off: the 5–6 band alone is 74.5% / 88.8% (the Loveless card, at 5.5, is in it), where the category rule's own bands were never accepted below about 88% / 93%. Raising it to 7 gives up 3,068 cards to gain 4.5 points of exactness.

**Wordplay categories are excluded, deliberately.** Blind sample of 100 Wordplay categories (462 cards): about 29% are single-subject content, 34% language content, 37% mixed. The same rule at gap ≥ 5 labels 16.5% of the cards but is exactly right only 71.8% of the time (89.2% right or close), and about one in six of those is a language item that a content subject misfiles. At gap ≥ 8 it is right or close on every card, but labels only 6.5% (37 cards in the sample). Half the cards in Wordplay categories are language items, and `Wordplay & Language` is the right theme for them. To try it anyway, add `"Wordplay & Language": 8.0` to `CARD_SUBJECT_MIN_GAP_BY_SOURCE`.

**Category-level recall, measured (leave-one-category-out over 31,247 labeled categories, plus blind samples and reading every candidate).** Today's rule: recall 19.7%, agreement with the LLM label 94.4% (per-subject recall matches the table above). Relaxing the margin from 0.20 to 0.10 recovers 0.1 point. Bar 0.45 → 0.40: recall 24.0%, agreement 90.8%, and the extra moves agree only 77% of the time; read blind, the 182 categories in the 0.40–0.45 band are 154 fit, 21 close, 7 wrong. Accepting the clue model's second choice: recall 20.4%, the extra moves agree 63%. Dropping the clue check: recall 22.7%, extra moves agree 73%. A second pass (moved categories also vote): recall 22.8%, extra moves agree 83%; read blind, the 116 new categories are 106 fit, 4 close, 6 wrong (`Other` only: passes 3 and 4 add 37 and 9 more). Consensus of card labels, a clue-led route and a mean product-of-experts score reached 15–40% recall at 80–92% card precision. **None is worth making.** The cards of those candidate categories are already labeled card by card: 890 of the 1,587 get a subject from the layer above at 99.8% right or close, so a category move would add labels mainly for the ~700 weakest cards, about 80% right, and it would move tiers. A name-only re-classification of the `Other` names is no better: guessing from the name alone (recorded before reading any clue, on 109 names) names the right subject for 57% of the single-subject categories, but it also names a subject for mixed categories, so only 66% of the subjects it names are right.

**Known limits.** About a third of the cards in a single-subject category and about 72% of `Other` cards overall get no subject: there is not enough evidence in the clue or the answer, so they keep `Other`. Ambiguous subjects (a president as History or Politics, a celebrity as Film & TV or Pop Culture) are the usual "close" cases. An image-only clue has no words, so only a unanimous answer can place it. A category with a sub-category the LLM did place under `Other` (`Annual Events`, `Calendar`) is labeled card by card like the rest. `Art` (82.6% / 89.2%) and `Science` (83.1% / 92.2%) are the weakest subjects that are shown.

**Tunables** are the `CARD_SUBJECT_*` constants in `jeopardy_consts.py`: `ANSWER_WEIGHT` (4.0), `ANSWER_SMOOTHING` (0.2), `MIN_GAP_BY_SOURCE` (`Other`: 5.0; a label missing from it is never labeled card by card) and `NEVER_SHOWN` (People, Pop Culture). **Opting a category out:** add it to `NO_CARD_SUBJECTS` in `consolidate_taxonomy.py`; a pin in `MANUAL_OVERRIDES` keeps the _category_ label but still lets its cards show their own subject. **Preview before writing:** `python smart_prep.py --live-db "$DB" --analysis-only --card-report /tmp/jeopardy-cards.tsv` writes every card (note id, category, from, to, gap, runner-up) and logs the counts by subject.

**Approval.** The full list, with every reader verdict and a saved Keep list, is on the approval page published 2026-09-25 (an Artifact). A dropped category goes into `NO_CARD_SUBJECTS`; the chosen cutoff goes into `CARD_SUBJECT_MIN_GAP_BY_SOURCE`.

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

After `smart_prep.py` runs, field 14 (`Frequency Score`) is added with the HTML badge and field 15 (`Frequency Details`) is added for the card back. The detail count covers the latest five years present in the collection; for the current 1984–2025 dataset, that is 2021–2025.

---

## Tags Written by smart_prep.py

| Tag              | Example               | Meaning                                                        |
| ---------------- | --------------------- | -------------------------------------------------------------- |
| `freq:{tier}`    | `freq:high`           | Blended frequency tier                                         |
| `subject:{name}` | `subject:Literature`  | Primary taxonomy subject; a card's own subject if it has one   |
| `subcat:{name}`  | `subcat:Shakespeare`  | Normalized sub-category                                        |
| `subcat2:{name}` | `subcat2:Science`     | Secondary domain (wordplay only)                               |
| `era:{era}`      | `era:recent`          | Air date bucket (recent=2020+, modern=2010–2019, old=pre-2010) |
| `archived:{why}` | `archived:dead-topic` | Why the card was archived (`archive_dead_cards.py`)            |
| `perf:{tier}`    | `perf:weak`           | Your accuracy on this card (`study_optimizer.py`)              |
| `perfsubcat:{t}` | `perfsubcat:weak`     | Your accuracy across the whole sub-category                    |

Previous `freq:`, `subject:`, `subcat:`, `subcat2:`, `era:` tags are stripped and replaced on each run (idempotent).

> **`subcat2:` was not stripped until 2026-09-25.** The rebuild matched only `freq:`, `subject:`, `subcat:` and `era:`, and `subcat2:…` doesn't start with `subcat:`, so each refresh appended another copy: all 13,480 notes with a secondary subject had 3 identical copies, and a category whose secondary subject changed kept the stale one. `subcat2:` is now stripped too; the 2026-09-25 refresh left 11,901 notes with exactly one.

---

## Archive Rules

`archive_dead_cards.py` moves genuinely dead cards to the Archive subdeck. Nothing is deleted, and `--restore` reverses everything. Cards you have **already reviewed** and anything aired **2020 or later** are always exempt.

| Reason       | Rule                                                       | Cards  |
| ------------ | ---------------------------------------------------------- | ------ |
| `dead-topic` | Answer topic not seen anywhere since 2005                  | 26,182 |
| `duplicate`  | Near-verbatim restatement of another clue (newest is kept) | 7,791  |
| `one-off`    | Answer never repeats in 42 seasons, and clue predates 2010 | 6,725  |
| `stale`      | Time-anchored wording, aired pre-2015                      | 1,579  |
| `malformed`  | Answer is empty or punctuation-only, or the clue is blank  | 609    |

Two traps that cost real accuracy here, both now guarded:

- **"this year" / "this month" is not a time anchor.** In Jeopardy phrasing, `this X` is the self-referential pointer to the thing being asked for ("the carnation is the flower for this month"). Matching it flagged tens of thousands of timeless clues, so those patterns are deliberately excluded from the stale regex — only genuine anchors (`current`, `recently`, `-elect`, `as of 1998`, …) count. `current events` is excluded as an idiom.
- **Short answers are not malformed.** `9`, `H` and `4` are all real responses. Only empty or punctuation-only answers qualify.

---

## Study Queue Ordering

`study_optimizer.py` doesn't just rank new cards individually — it groups each day's on-air category into a block so related clues surface together instead of being scattered across the deck.

**Groups are up to 5 clues from one category board on one day** (`air_date`, `round`, on-air `category`), ordered **by dollar value, descending** ($1000 → $800 → … or $2000 → $1600 → … in Double Jeopardy) — highest-stakes clue first. Groups themselves are still ordered by the existing weakness-weighted frequency score (highest-priority category first; see _Blended Frequency Score_ above), so this changes _presentation order within and around_ a category, not which categories are prioritized.

**Final Jeopardy is held out and interspersed, not grouped.** It's one clue/day with a 4x stake multiplier — grouped in with everything else, its outsized score would cluster every Final Jeopardy clue at the very front of the queue instead of spreading them out. Instead, FJ clues are ranked among themselves by the same priority score, then spread evenly across the whole queue (`interleave_blocks()`) so they show up every once in a while — measured at a steady ~14-15 groups apart (roughly one every 60-70 cards) on the current collection.

### Fixed 2026-07-29: groups were spanning years, not days

Groups were previously keyed on `(Show number, category)`. Show number (field 0) is blank on **~81,652 cards (18% of the deck)** — every clue merged in from the jwolle1 post-2019 TSV, via `update_collection.py`, which has no show-number column in its source data (`clue_to_note_fields()` deliberately leaves it `""`). With Show number blank, the group key collapsed to `("", category)` for all of them, so every reused broad category name — "AMERICAN HISTORY", "POTPOURRI", "BEFORE & AFTER" — merged into one group spanning years of games, exactly the symptom reported: a single category flooding the queue with unrelated clues from many different air dates.

Fixed by keying groups on `(air_date, round, category)` instead — `air_date` is populated on **every** note, so it's a reliable per-game identifier regardless of source. `round` is included too, guarding the rare case (107 instances found) of the same category name reused in both the Jeopardy and Double Jeopardy rounds on the same day, which would otherwise merge into one 10-card group. Verified against the live collection: 0 of 90,465 resulting groups exceed 5 cards or span more than one category/air_date.

### One-time setup: `configure_deck_options.py`

Grouping cards via `due` order only controls which cards get pulled _from the new-card pool_ — by default, Anki still freely interleaves due reviews in between them, so a review card could land in the middle of a 5-card group. Run once:

```bash
python configure_deck_options.py --preset Jeopardy
```

This sets **New/review order** (and the equivalent interday-learning-mix setting) to **"before reviews"**, so new cards — and therefore whole category groups — are always exhausted before that day's review/relearning cards. It's idempotent (safe to re-run) and isn't part of the regular refresh loop.

Without `--preset` it applies to **every** deck options group in the collection, which will also retune unrelated decks (Chinese, French, …) that want their own step timings. Pass the preset name.

> **This setting lives on the preset, not the deck — creating a new preset silently reverts it.** Assigning `Jeopardy Smart Prep` to a freshly-created options preset in the Anki GUI starts it from Anki's stock defaults ("mix with reviews", learn 1m/10m), regardless of what the old preset had. This is not theoretical: it happened between 2026-07-29 and 2026-08-01 and made a study session look like the grouping fix had failed, when the `due` order was in fact correct the whole time. If groups start getting interrupted again, check the preset first with `python configure_deck_options.py --dry-run` — it reports the current values for every preset and warns if the gather/sort order would break `due` ordering.

**What this can't fix:** a card you mark Again/Hard earlier in the _same session_ re-enters the queue on its own learning-step timer regardless of this setting — that's Anki's short-term relearning behavior working as intended (you got it wrong; it's supposed to come back soon), and no deck option suppresses it. Only cross-session reviews and multi-day relearning are deferred behind new cards.

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

Targeting the measured weak spots (see _Study Performance_ below):

```
tag:perfsubcat:weak tag:freq:high      → weak AND frequently asked — best ROI
tag:subject:People tag:freq:high       → the weakest subject cluster
tag:subcat:U_S_Presidents              → 46.4% accuracy, worst measured
```

---

## Study Performance (measured 2026-07-28)

From 6,037 reviews across 2,108 cards. Accuracy is Bayesian-blended (prior 70% @ 5 reviews) so thin categories are not over-read.

**Weakest subjects, weighted by share of recent-game clue volume:**

| Subject             | Accuracy  | Share of 2020+ clues |
| ------------------- | --------- | -------------------- |
| Wordplay & Language | 82.9%     | **22.0%**            |
| Literature          | **70.5%** | 8.6%                 |
| Film & TV           | **72.4%** | 7.8%                 |
| History             | 76.1%     | 8.2%                 |
| Music               | 75.4%     | 6.0%                 |
| Science             | 86.6%     | 5.5%                 |

**The dominant pattern is people-based recall.** The worst sub-categories cluster hard: U.S. Presidents 46.4%, Americans 47.7%, Politicians 40.9%, People 50.0%, European Royalty 55.0%, American Women 54.2%, Historical Figures 58.7%, Biblical Characters 53.1%. Naming _who did a thing_ is the weak spot, not the thing itself.

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
- [x] **Day-category grouping, value order, Final Jeopardy interspersion** — fixed 2026-07-29: groups were keyed on `(Show number, category)`, but Show number is blank on 18% of the deck (the jwolle1 post-2019 import has no show-number column), collapsing every reused broad category name into one group spanning years of games. Regrouped on `(air_date, round, category)` — verified 0 of 90,465 groups now exceed 5 cards or span more than one category/date. Also added descending-value sort within each group and even interspersion of Final Jeopardy cards (`interleave_blocks()`). See _Study Queue Ordering_ above. New one-time script `configure_deck_options.py` sets deck options so new cards show before reviews.

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

- [x] **Fix the `Unclassified` bucket** — it is 11.1% of recent-game clue volume and the largest single group in your review history (392 reviews), yet those cards earn no topic credit in scoring and cannot be targeted by tag. Worth a focused classification pass over the highest-volume unclassified categories.

  > ⚠️ **Discrepancy found 2026-09-24: they _do_ earn topic credit today.** `score_notes()` zeroes the sub-category component only when the label is `"Miscellaneous"` (`_DEFAULT_SUBCAT`), but `consolidate_taxonomy.py` renames every catch-all to `"Unclassified"`, so the zeroing matches 1 card instead of ~30,000. The `unclassified` sub-category is therefore the **second-largest bucket in the deck** (16,645 recency-weighted vs 1,905 for the third), and every card in it gets a near-top sub-category percentile (the `A NOVEL PASSAGE` cards, for one, showed 88–93 while `Other`). Measured on the current deck after the Literature fallback (29,858 cards, 7.3%): mean priority **53.1**, with 8,817 (30%) in `freq:high`. If the bucket earned no credit, as the code comment at `score_notes()` intends, those figures would be **12.8** and **22 (0%)**, and **73,667 cards (18.0% of the deck) would change tier** once everything is re-ranked. The fix is one line (compare against both labels), but it reorders the new-card queue, so it was deliberately **not** made alongside the classification change — decide first whether you want it.

  **Fixed 2026-09-25** (with the evidence reclassification, at the user's request): `score_notes()` now zeroes both labels (`_NO_TOPIC_SUBCATS`). The bucket is 26,806 cards (6.5%); their mean score fell from 52.9 to 12.3 and `freq:high` from 7,720 to 19. 66,372 cards (16.2%) changed tier on top of the reclassification: 24,724 `Unclassified` cards moved down, and 41,166 other cards moved up into the room they left. Against the previous live state, the whole refresh changed 67,303 tiers (16.4%).

- [ ] **Label the rest of `Other` with an LLM.** About 23,900 `Other` cards still show `Other` after the per-card rule, although (blind hand labels) about 77% of `Other` cards have a clear content subject. Independent readers labeling clues one by one agreed with my labels 86–90% exactly (97% within two subjects), so LLM labels for these cards would be about as good as the category taxonomy's. It is 80 batches of 300 clues in the style of `classify_categories.py`. The labels would have to be stored (a table keyed by note id, applied like the per-card layer) and refreshed for each new season, which turns a computed rule into a maintained table. Not done.

- [ ] **Match classifier output on a normalized name** — `classify_batch()` looks each result up by exact name and silently files any miss as `Other / Miscellaneous` (see _Why `CAPITAL "C"` showed Other_). Strip the TSV's `\"` escape or compare normalized keys, log every item the model skips, then re-classify the 827 backslash categories.

- [ ] **Strip the TSV's `\"` from answers** — 1,225 cards (964 distinct answers, e.g. `"1999\"`) keep the backslash after `normalize_answer`, so they never match the same answer elsewhere in the deck. Fixing it changes answer-frequency scores, so quantify it first.

- [ ] **Consolidate "Other" subject** — currently 3,875 categories land in `Other` (mostly obscure one-off categories). Null out their `sub_category` so they fall back to answer-only scoring rather than dragging down the Other subject percentile.

- [ ] **Season 42 gap** — jwolle1 dataset ends July 2025. When Season 42 data becomes available, re-run `update_collection.py` and re-score.

### Study Experience

- [ ] **Filtered deck presets** — document recommended Anki filtered deck queries for targeted sessions (e.g., Final Jeopardy practice, recent high-frequency, subject deep-dives)

- [ ] **Anki add-on for dynamic scheduling** — optional: an add-on that shortens intervals for `freq:high` cards (0.7× multiplier) and lengthens for `freq:rare` (1.5×), so high-frequency answers get proportionally more review time without manual filtered decks

- [ ] **Category coverage report** — after full classification + consolidation, print which subjects have the most cards and which sub-categories drive the most `freq:high` hits; use this to prioritize weak-area study

- [ ] **Wagering simulator** — interactive CLI or Anki card that presents a game state (your score, 2nd place score, category) and asks for the optimal bet; validates against the FJ math formulas in JEOPARDY_STRATEGY.md
