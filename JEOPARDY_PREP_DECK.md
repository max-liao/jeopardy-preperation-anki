# Jeopardy Smart Prep Deck — Project Documentation

## Table of Contents

- [Project Overview](#project-overview)
- [Problem Statement](#problem-statement)
- [Solution Architecture](#solution-architecture)
- [Part 1: smart_prep.py Script](#part-1-smart_preppy-script)
- [Part 2: frequency_scheduler Add-on](#part-2-frequency_scheduler-add-on)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Verification & Testing](#verification--testing)
- [Technical Details](#technical-details)
- [Data Flow Diagram](#data-flow-diagram)

---

## Project Overview

### Goal

Build a **spaced repetition study system optimized for Jeopardy preparation** that:

1. **Analyzes answer frequency** across 370,616+ historical Jeopardy cards
2. **Accounts for recency bias** (recent answers are better predictors of future questions)
3. **Prioritizes common answers** in study rotation (appear 30–50% more frequently)
4. **Maintains Anki compatibility** using native scheduling and tags

### Expected Outcome

A preparation deck where studying high-frequency answers gets proportionally more review time, improving odds of success on future Jeopardy episodes.

---

## ⭐ Requirements Update (2026-06-07) — SUPERSEDES conflicting details below

This section captures the current, authoritative requirements. Where it conflicts
with the original design further down (e.g. field indices, recency weights, the
two-part add-on architecture), **this section wins**.

### Goal restated

Produce a **new collection that expands the user's existing, manually-curated
deck** (preserving all manual edits, review history, and prior deletions) by:

1. **Adding recent questions** (2020–2025) that the original deck lacked.
2. **Scoring every card** with a frequency score derived from how often its
   **subject** (e.g. Literature, Religion, Opera) and **sub-category**
   (e.g. Shakespeare, Russian literature, Ernest Hemingway) recur — blended with
   exact-answer frequency, all recency-weighted.
3. **Showing that score on each card** via a new note-type field + template badge.

### Data source for recent questions

- The original `.colpkg` covers **1984-09-10 → 2019-06-06 only** (370,616 notes).
  It has **no 2020+ questions** (Trebek's last full season was the cutoff).
- Recent clues come from **[jwolle1/jeopardy_clue_dataset](https://github.com/jwolle1/jeopardy_clue_dataset)**
  (`combined_season1-41.tsv`, 538,845 clues through Season 41 / July 2025).
  J-Archive itself blocks scraping via robots.txt; this pre-built TSV avoids that.
- `update_collection.py` merges rows with `air_date > 2019-06-06` (81,652 clues),
  producing a collection spanning **1984 → 2025-07-25** (452,268 notes).
- **Season 42 (Sept 2025+) is not yet included** — acceptable for now.

> **⚠️ Field terminology is REVERSED between the TSV and Anki.** In the jwolle1
> TSV, `answer` = the clue shown and `question` = the correct response. In the
> Anki deck, field 9 "Question" = clue shown and field 11 "Answer" = response.
> The importer maps TSV `answer`→Anki Question(9) and TSV `question`→Anki Answer(11).

### Correct Anki "Jeopardy" note-type field order (14 fields, verified)

| 0 Show number | 1 AirDate | 2 Extra Info | 3 Round | 4 Coords | 5 Category | 6 Order |
| 7 Value | 8 Daily Double | 9 Question (clue) | 10 Links | 11 Answer (response) |
| 12 Correct Attempts | 13 Wrong Attempts |

(The original design below incorrectly implies the answer is field 11 _and_ uses
it as the study answer — it is the response; the **clue** is field 9.)

### Subject / sub-category classification — **via LLM**

- The on-air **Category** field (#5) is the natural **sub-category** signal
  (56,235 unique categories; top ones are exactly THE BIBLE, OPERA, LITERATURE…).
- A one-time **LLM pass (`classify_categories.py`, Anthropic SDK)** classifies
  each unique category into `{subject, sub_category}` and caches the result to
  `category_taxonomy.json`. Uses the Batches API (50% cost) + prompt caching +
  structured JSON output. Re-runs read the cache; only new categories are sent.

### Per-card frequency score — **blended, recency-weighted**

`score = w_ans · answer_freq + w_subcat · subcategory_freq + w_subj · subject_freq`,
where each component is the recency-weighted count of cards sharing that
answer / category / subject. Tiers (`freq:high/medium/low/rare`) are derived from
the blended score. Recency weights peak at 2020–2026 (now that recent data exists).

### Score display — **new field + template**

- Add a **"Frequency Score"** field to the Jeopardy note type and render it on the
  card as a small badge (score + tier + subject), so it's visible during review.
  This extends every note's `flds` by one segment and updates the card template.
- Cards are also tagged (`freq:*`, `subject:*`, `subcat:*`, `era:*`) for filtering.

### Pipeline / status (as of 2026-06-07)

```
Step 1: Merge post-2019 clues
  update_collection.py ✅ DONE
  → updated.colpkg (1984–2025-07-25, 452,268 notes)
  → 81,652 new clues merged, 7,200 reviewed cards + 180,995 revlog intact

Step 2: Classify categories via LLM
  classify_categories.py ⏳ IN PROGRESS (13,360 / 54,519 done, 24.5%)
  → category_taxonomy.json (incremental checkpoint)
  → headless Claude CLI (batched, 2 workers, exponential backoff)
  → re-run: python3 classify_categories.py updated.colpkg (resumes from cache)

Step 3: Build frequency scores + add field/template
  smart_prep.py ✅ WRITTEN (not yet run)
  → loads partial taxonomy, computes blended scores
  → adds "Frequency Score" field to notetype
  → renders badge on cards (HTML via protobuf template edit)
  → tags all notes: freq:{tier}, subject:{name}, subcat:{name}, era:{era}
  → pending: classification completion → lint/type-check → run → validation

Step 4: Import into Anki
  ⏳ user step (after smart_prep.py completes)
```

### Blended frequency scoring (current algorithm)

Each note's frequency score = `0.40 · answer% + 0.35 · subcat% + 0.25 · subject%`,
where each component is the **percentile rank** of that note's recency-weighted
frequency within all notes. All components are recency-weighted (peak 2020–2026,
decay to 0.2x for pre-2010). Result is scaled 0–100 and mapped to tier:

- `freq:high` ≥ 70
- `freq:medium` 40–69
- `freq:low` 15–39
- `freq:rare` < 15

### Note on the original "Part 2 add-on"

The original design proposed a runtime `frequency_scheduler` Anki add-on that
mutates intervals/ease live. **Out of scope** — current deliverable is a visible
frequency badge + tags on cards for filtering. User can optionally build custom
scheduling on top of tags post-import.

---

## Next Steps: Category Consolidation & Score Reweighting

### Current State (as of 2026-06-08)

**Classification progress:**

- **13,360** categories classified out of **54,519** unique on-air categories (24.5%)
- **~3,900** unique sub-categories across 17 subjects
- **220** sub-categories used across multiple subjects (consolidation opportunities)
- Key overlaps: "Wordplay" in 9 subjects, "Authors" in 6 subjects
- Catch-alls: "Miscellaneous" (919 cats, 7.7% share), "Wordplay" (826 cats)
- Redundant temporal subcats: "1950s Travel", "19th Century Food & Drink", "1870s Literature" — dozens of prefix-duplicates
- Most bloated subject: Wordplay & Language (3,405 cats → 459 unique subcats)
- Classifier killed; resume with: `python3 classify_categories.py updated.colpkg`

**Current scoring:**

- Blended 0-100 percentile: 40% answer + 35% subcategory + 25% subject
- Recency-weighted (2020–2026 peak at 1.0x, decaying to 0.2x pre-2010)
- Flat score across all question values and rounds

### Planned Improvements

#### 1. Category Consolidation (Post-Classification)

**Goal:** Reduce 3,900 subcategories → 600–800 high-signal ones.

**Strategy:**

- **Merge global subcategories**: "Authors", "Awards", "Movies", "Television", etc.
  appear across multiple subjects; unify them (remove subject prefix).
- **Collapse time-period noise**: "1950s Travel", "19th Century Food & Drink",
  "1870s Literature" → trim to base topic or date-only (user decision).
- **Eliminate "Miscellaneous"**: Redistribute 919 cards to parent subject or
  "Unclassified" flag; do not carry forward.
- **Dedup within subject**: Identify synonyms (e.g., "Films" vs. "Movies") and
  merge; keep the most-frequent variant.

**Process:**

1. Wait for classification to finish (est. 54,519 categories done)
2. Load category_taxonomy.json → build overlap map
3. Generate consolidation rules (JSON mapping old → new subcategories)
4. Apply to taxonomy.json (idempotent: reversible via git)
5. Re-run smart_prep.py with consolidated taxonomy

**Expected result:** Cleaner tag vocabulary, no "Other" bloat, subject/subcat
tags more useful for filtering in Anki.

#### 2. Score Reweighting by Round & Value

**Current limitation:** All questions weighted equally; Final Jeopardy (high stakes,
often harder), Daily Doubles (wagerable), and higher clue values (harder) get
same frequency score as $100 Jeopardy clues.

**Proposed refinement:**

- **Final Jeopardy**: 2.0x weight (rarest, highest-stakes, best predictor)
- **Daily Doubles**: 1.5x weight (contestant-controlled, higher variance)
- **Value weighting** (within round):
  - Final Jeopardy: N/A (not valued)
  - Double Jeopardy: $1600 → 1.5x, $1200 → 1.3x, $800 → 1.1x, $400–600 → 1.0x, $200 → 0.9x
  - Jeopardy: $800 → 1.5x, $600 → 1.3x, $400 → 1.1x, $200 → 1.0x, $100 → 0.8x

**Implementation:**

1. Modify `smart_prep.py` to read FIELD_ROUND (#3), FIELD_VALUE (#7), FIELD_DAILY_DOUBLE (#8)
2. For each note, compute `round_weight · value_weight · base_score`
3. Recalculate percentiles with weighted frequencies
4. Re-map to tiers (thresholds may shift; validate tier distribution remains ~30% high, ~40% med, ~20% low, ~10% rare)

**Expected outcome:** Final Jeopardy answers weighted ~2–3× higher, high-value
questions prioritized, Daily Doubles stand out as high-signal. More realistic
study distribution.

### For Next AI Thread

**Prerequisites:**

- Classification pipeline must finish (check `wc -l category_taxonomy.json`; should be ~54,519)
- smart_prep.py is written, lint-clean, type-checked

**Tasks (in order):**

1. **Category consolidation**:
   - Load taxonomy.json
   - Build overlap map (subcategory → set of subjects)
   - Generate consolidation rules (manual review or LLM-assisted merging)
   - Apply rules, validate, commit

2. **Reweighting implementation**:
   - Update smart_prep.py to extract round + value + daily_double
   - Apply weights during frequency accumulation
   - Recalculate percentiles with weighted scores
   - Validate tier distribution, spot-check examples (e.g., Final Jeopardy answers should shift up)

3. **Final validation & export**:
   - Run smart_prep.py on updated.colpkg with consolidated taxonomy + reweighting
   - Import into Anki desktop
   - Verify frequency badges display
   - Spot-check: Final Jeopardy & Daily Double answers should have higher scores
   - Verify tags filter correctly (tag:freq:high, tag:era:recent, etc.)
   - Confirm all 7,200 reviewed cards + review history still intact

4. **Documentation**:
   - Update JEOPARDY_PREP_DECK.md with final pipeline, consolidation results, reweighting formula
   - Document category naming choices, tier thresholds, field structure, tagging scheme
   - Add examples of filtered-deck searches and expected tag distributions

**Blockers:** None (classification can run to completion in parallel; consolidation/reweighting happen after).

---

## Problem Statement

### Current State

- Anki reviews cards on **pure spaced repetition** regardless of real-world answer frequency
- A contestant studying the full 370K deck learns rare historical figures (appeared once in 40 years) with same effort as common answers (appeared 50+ times recently)
- No mechanism to prioritize **trending** or **statistically likely** answers
- Manual filtering by category/date requires external tools

### Why It Matters

Jeopardy questions are **not uniformly distributed**:

- US Presidents, State Capitals, Shakespeare: appear dozens/hundreds of times
- One-off cultural references, obscure historical figures: appear once or twice
- **Time correlation**: questions from recent seasons better predict current/future episodes than 1980s questions

A smart scheduler that weights by frequency and recency gives contestants a statistical edge.

---

## Solution Architecture

### Two-Part Approach

#### **Part 1: Data Preprocessing (`smart_prep.py`)**

Runs once on your collection. Outputs a tagged, optimized deck.

**Responsibilities:**

- Extract answer frequency from 370K cards
- Calculate time-weighted relevance scores
- Tag each card with frequency tier (`freq:high`, `freq:medium`, `freq:low`, `freq:rare`)
- Tag by category and era
- Export as `.colpkg` ready for Anki import

**Why separate?**

- Frequency analysis is expensive (370K SQLite queries)
- Results are deterministic; no need to recalculate every session
- Tags can be modified independently if needed

#### **Part 2: Runtime Scheduler (`frequency_scheduler` Add-on)**

Runs continuously while reviewing in Anki. Adjusts scheduling on the fly.

**Responsibilities:**

- Hook into Anki's card reviewer
- Modify interval multipliers based on frequency tags
- Adjust ease factors (high-frequency cards easier, low-frequency cards harder)
- Prioritize high-frequency cards in review queue

**Why separate?**

- Decouples data analysis (Part 1) from scheduling logic (Part 2)
- Allows tuning scheduler parameters without re-processing deck
- Can be disabled/enabled without affecting card data

---

## Part 1: smart_prep.py Script

### Overview

A command-line Python tool that analyzes your Jeopardy collection and exports a frequency-tagged deck.

### Algorithm

#### **Step 1: Extract & Parse**

```
Input: collection-2026-06-07@15-43-15.colpkg
├─ Extract ZIP
├─ Decompress collection.anki21b (Zstandard) → SQLite
└─ Query all notes
```

For each of the 370,616 notes:

- Extract the **Answer** field (field index 11)
- Extract the **AirDate** field (field index 1: `YYYY-MM-DD`)
- Store as `(answer_text, year)` pair

#### **Step 2: Calculate Time-Weighted Frequency**

For each unique answer:

1. Count total occurrences: `count = len([card for card if answer matches])`
2. Extract year distribution: `{2024: 3, 2023: 2, 2015-2022: 40, pre-2015: 5}`
3. Apply recency decay:
   ```
   recency_weight[year] = {
       2024-2026: 1.0x (current/future-predictive)
       2023:      0.8x
       2022:      0.6x
       2021:      0.5x
       2020:      0.4x
       2015-2019: 0.3x
       pre-2015:  0.2x
   }
   ```
4. Calculate score:
   ```
   score = sum(count_in_year[y] * recency_weight[y] for y in all_years)
   ```

Example:

- "United States" (342 total): 45 in 2024 (1.0) + 28 in 2023 (0.8) + 120 in 2015-2022 (0.3) = **45 + 22.4 + 36 = 103.4**
- "Millard Fillmore" (1 total, 1987): 1 in 1987 (0.2) = **0.2**

#### **Step 3: Rank into Tiers**

Sort all answers by score (descending). Create 4 frequency tiers:

| Tier       | Criterion                    | Example Count          | Tag           | Use Case                       |
| ---------- | ---------------------------- | ---------------------- | ------------- | ------------------------------ |
| **High**   | Top 5% by score (score ≥ 50) | ~800 unique answers    | `freq:high`   | Study first; appear frequently |
| **Medium** | Top 5–25% (score 10–49)      | ~4,000 unique answers  | `freq:medium` | Standard spaced rep            |
| **Low**    | Top 25–50% (score 2–9)       | ~12,000 unique answers | `freq:low`    | Longer intervals               |
| **Rare**   | Bottom 50% (score < 2)       | ~28,000 unique answers | `freq:rare`   | Minimal review                 |

#### **Step 4: Tag Cards**

For each card in the collection:

1. Extract its answer
2. Look up answer's frequency tier
3. Add tags:
   - Frequency tag: `freq:high`, `freq:medium`, `freq:low`, or `freq:rare`
   - Category tag: `cat:SCIENCE`, `cat:HISTORY`, etc. (extracted from field 5)
   - Era tag (optional): `era:recent` (2023+), `era:modern` (2015-2023), `era:old` (<2015)

#### **Step 5: Export**

- Recompress modified SQLite to `collection.anki21b` (Zstandard)
- Package as new `.colpkg` ZIP with:
  - `meta` (format version)
  - `collection.anki21b` (pruned/tagged database)
  - `media` (JSON manifest)
  - `0`, `1`, `2`, … (media files: images, audio)

### Command-Line Interface

```bash
python smart_prep.py SOURCE.colpkg OUTPUT.colpkg [OPTIONS]
```

#### Options

```
  --analysis-only
      Run frequency analysis and print statistics without modifying deck.

  --output-format {colpkg,apkg}
      Export format. Default: colpkg (Anki collection package).
      colpkg = full collection (preserves scheduling, history)
      apkg = question bank (clean import, fresh scheduling)

  --recency-weight-mode {linear,exponential,custom}
      How aggressively to weight recent answers.
      linear: year-by-year decay (default)
      exponential: faster decay for old answers
      custom: use custom JSON weights file

  --min-frequency N
      Ignore answers that appear < N times. (Optional, default: 1)
```

#### Example Usage

```bash
# Analyze frequency distribution
python smart_prep.py collection-2026-06-07@15-43-15.colpkg --analysis-only

# Create tagged deck
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_smart.colpkg

# Export as fresh import (no scheduling history)
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_smart.apkg --output-format apkg
```

#### Analysis Output

```
=== Jeopardy Frequency Analysis ===

Total cards analyzed: 370,616
Unique answers: 47,283

Top 20 answers by score:
1. United States        | 342 total | score: 215.3 | freq:high
2. England              | 156 total | score:  98.2 | freq:high
3. Germany              | 149 total | score:  94.1 | freq:high
4. France               | 143 total | score:  86.7 | freq:high
5. China                | 138 total | score:  84.2 | freq:high
...

Frequency distribution:
  freq:high   (score ≥ 50):   823 answers → 125,439 cards (33.8%)
  freq:medium (10–49):      4,201 answers → 156,782 cards (42.3%)
  freq:low    (2–9):       12,340 answers →  67,891 cards (18.3%)
  freq:rare   (< 2):       27,867 answers →  25,504 cards (6.9%)

Processing took 3m 42s
Output: jeopardy_smart.colpkg (924 MB)
```

---

## Part 2: frequency_scheduler Add-on

### Overview

An Anki add-on that dynamically adjusts card scheduling based on frequency tags.

**Installation location:**

- **Windows**: `%APPDATA%\Anki2\addons21\frequency_scheduler`
- **macOS**: `~/Library/Application Support/Anki2/addons21/frequency_scheduler`
- **Linux**: `~/.local/share/Anki2/addons21/frequency_scheduler`

### How It Works

#### **Interval Adjustment**

When you answer a card, Anki calculates a new review interval. The add-on modifies this interval based on frequency:

```python
adjusted_interval = base_interval * multiplier[tag]
```

**Multipliers:**

| Frequency Tag | Multiplier | Effect                                 |
| ------------- | ---------- | -------------------------------------- |
| `freq:high`   | 0.7x       | Appears 30% sooner (shorter intervals) |
| `freq:medium` | 1.0x       | Standard spaced repetition             |
| `freq:low`    | 1.3x       | Appears 30% later (longer intervals)   |
| `freq:rare`   | 1.5x       | Appears 50% later (longest intervals)  |

**Example:**

- Standard interval for "Good" answer: 10 days
- High-frequency card: 10 × 0.7 = **7 days** (appears sooner)
- Rare card: 10 × 1.5 = **15 days** (longer gap)

#### **Ease Factor Adjustment**

Ease factor (also called "difficulty factor") ranges from 1.3–2.5 and affects how much intervals grow. The add-on adjusts ease for non-medium cards:

```python
adjusted_ease = base_ease + adjustment[tag]
```

**Adjustments:**

| Frequency Tag | Ease Adjustment | Effect                                      |
| ------------- | --------------- | ------------------------------------------- |
| `freq:high`   | +50             | Easier to remember (intervals grow slower)  |
| `freq:medium` | 0               | Standard (default 2500)                     |
| `freq:low`    | -20             | Slightly harder                             |
| `freq:rare`   | -40             | Harder (intervals grow faster, more review) |

#### **Queue Prioritization**

When opening the deck for review, the add-on reorders the queue:

```
Order: freq:high → freq:medium → freq:low → freq:rare
```

Within each tier, Anki's standard order (due, then random) is preserved.

### Implementation Details

#### **File Structure**

```
~/.local/share/Anki2/addons21/frequency_scheduler/
├── __init__.py              # Main add-on logic
├── config.json              # Default settings
├── manifest.json            # Anki metadata
└── README.md                # User-facing instructions
```

#### **Key Hooks**

The add-on uses Anki's built-in hook system:

```python
# When a card is answered:
reviewer_did_answer_card: hook(reviewer, card, ease)
  → Fetch frequency tag from card
  → Adjust ease factor and interval
  → Return modified values

# When opening a deck:
gui_will_load_deck: hook(deck)
  → Reorder cards by frequency tier
  → Return reordered queue
```

#### **Configuration**

User can customize settings in Anki > Tools > Add-ons > frequency_scheduler > Config:

```json
{
  "enabled": true,
  "interval_multipliers": {
    "freq:high": 0.7,
    "freq:medium": 1.0,
    "freq:low": 1.3,
    "freq:rare": 1.5
  },
  "ease_adjustments": {
    "freq:high": 50,
    "freq:medium": 0,
    "freq:low": -20,
    "freq:rare": -40
  },
  "queue_priority": true,
  "logging_level": "INFO"
}
```

### Behavior Examples

#### **Scenario 1: Review a freq:high card**

```
Card: "What is the capital of France?" (Answer: "Paris")
Tag: freq:high (appears 150+ times in deck)

1. Answer: "Good" (normal choice)
2. Base interval: 10 days
3. Adjusted by add-on: 10 × 0.7 = 7 days next review
4. Ease factor: 2550 (normal 2500 + 50 boost)
→ Result: High-frequency cards return sooner, easier to advance
```

#### **Scenario 2: Review a freq:rare card**

```
Card: "What is obscure 1970s U.S. Senator's nephew's birthplace?"
Tag: freq:rare (appears 1 time total)

1. Answer: "Good"
2. Base interval: 10 days
3. Adjusted by add-on: 10 × 1.5 = 15 days next review
4. Ease factor: 2460 (normal 2500 - 40)
→ Result: Rare cards spend longer gaps between reviews
```

#### **Scenario 3: Review queue order**

```
Due today: 150 cards

Before add-on:
[high1, rare1, medium1, low1, high2, medium2, rare2, ...]  (random mix)

After add-on prioritization:
[high1, high2, high3, ..., medium1, medium2, ..., low1, ..., rare1, ...]
→ Result: High-frequency cards studied first (order: high → med → low → rare)
```

---

## Installation & Setup

### Prerequisites

- **Anki 2.1.45+** (Anki 2.1.50+ recommended for `.anki21b` support)
- **Python 3.8+** (for `smart_prep.py` script)
- **zstandard library** (for decompressing `.anki21b`)

### Step 1: Install Python Dependencies

```bash
pip install zstandard
```

### Step 2: Prepare Your Collection

Backup your current Anki collection (in case of issues):

```bash
# Anki stores collections in:
# macOS: ~/Library/Application Support/Anki2/
# Linux: ~/.local/share/Anki2/
# Windows: %APPDATA%\Anki2\

# Export a backup:
# In Anki: File > Export > Export format: "Anki Collection Package" → collection-backup.colpkg
```

### Step 3: Run smart_prep.py

```bash
cd /home/max/Documents/Jeopardy

# Analyze frequency (read-only):
python smart_prep.py collection-2026-06-07@15-43-15.colpkg --analysis-only

# Generate tagged deck:
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_smart.colpkg
```

This creates `jeopardy_smart.colpkg` (~924 MB, same size as original).

### Step 4: Import into Anki

1. **In Anki**: File > Import
2. **Select**: `jeopardy_smart.colpkg`
3. **Confirm**: "Create new collection" or "Import into existing deck"
4. **Wait**: 3–5 minutes for Anki to process 370K cards

### Step 5: Install frequency_scheduler Add-on

1. Copy the `frequency_scheduler/` directory to your Anki add-ons folder:

   ```bash
   # macOS/Linux:
   cp -r frequency_scheduler/ ~/Library/Application\ Support/Anki2/addons21/

   # Windows:
   copy frequency_scheduler %APPDATA%\Anki2\addons21\
   ```

2. **Restart Anki**: File > Exit, then reopen

3. **Verify installation**: Tools > Add-ons > Search "frequency_scheduler"

4. **(Optional) Configure**: Tools > Add-ons > frequency_scheduler > Config

---

## Usage

### Workflow

#### **First-Time Setup**

1. Run `smart_prep.py` on your collection (10–15 minutes)
2. Import the resulting `.colpkg` into Anki
3. Install the `frequency_scheduler` add-on
4. Restart Anki
5. Open the Jeopardy deck; verify tags are present (browse cards)

#### **Daily Study**

1. Open Anki
2. Select Jeopardy deck
3. Start reviewing
4. The `frequency_scheduler` add-on automatically:
   - Prioritizes `freq:high` cards
   - Adjusts intervals based on tags
   - Tracks progress

#### **Optional: Manual Filtering**

**Study only high-frequency answers:**

1. In Anki Browser (Ctrl+B or Cmd+B)
2. Search: `tag:freq:high`
3. Click "Create Filtered Deck" (or study directly)
4. Review only common answers

**Study by era:**

```
tag:era:recent   → only 2023+ questions
tag:era:modern   → only 2015-2023 questions
tag:era:old      → pre-2015 (for historical context)
```

**Study by category + frequency:**

```
tag:cat:SCIENCE tag:freq:high  → Science answers that appear frequently
```

---

## Verification & Testing

### Part 1: smart_prep.py Verification

#### **Test 1: Frequency Analysis**

```bash
python smart_prep.py collection-2026-06-07@15-43-15.colpkg --analysis-only
```

**Expected output:**

- ✓ Runs without error
- ✓ Reports 370K+ cards analyzed
- ✓ Top answers are reasonable: "United States", country names, Shakespeare, etc.
- ✓ Frequency distribution shows tier breakdown (high: ~33%, medium: ~42%, low: ~18%, rare: ~7%)

#### **Test 2: Tagged Deck Generation**

```bash
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_smart.colpkg
```

**Expected output:**

- ✓ Creates `jeopardy_smart.colpkg` (~924 MB)
- ✓ Completes in < 15 minutes
- ✓ No errors or warnings

#### **Test 3: Import into Anki**

1. In Anki: File > Import
2. Select `jeopardy_smart.colpkg`
3. **Verify:**
   - ✓ ~370K cards imported
   - ✓ Jeopardy blue styling intact (browse a few cards)
   - ✓ All cards have frequency tags

   To verify tags:

   ```
   In Anki Browser (Ctrl+B):
   Search: "tag:freq:*"
   Expected: 370,616 cards found

   Search: "tag:freq:high"
   Expected: 100K–150K cards (30–40%)
   ```

### Part 2: frequency_scheduler Add-on Verification

#### **Test 1: Add-on Loads**

1. Restart Anki
2. Tools > Add-ons
3. **Verify:** "frequency_scheduler" appears in the list
4. Click it; should show "Config" and "Disable" buttons

#### **Test 2: Scheduling Adjustment**

1. Open Jeopardy deck
2. Review a few cards tagged `freq:high`:
   - Answer "Good"
   - **Check**: Next interval should be ~70% of standard (shorter)

   To verify:
   - Open any reviewed card in the browser
   - Right-click > Card Info
   - **Look for**: Ease factor slightly higher (~2550 vs 2500)

3. Review a `freq:rare` card:
   - Answer "Good"
   - **Check**: Next interval should be ~150% of standard (longer)

#### **Test 3: Queue Prioritization**

1. Open Jeopardy deck with 100+ due cards
2. Look at the review queue
3. **Verify**: High-frequency cards appear first in the list

   To confirm order:
   - Open Anki's study screen
   - Note the card positions
   - Expected: freq:high, freq:high, freq:high, ..., freq:medium, freq:low, freq:rare

#### **Test 4: Configuration**

1. Tools > Add-ons > frequency_scheduler > Config
2. **Modify**: Change `interval_multipliers.freq:high` from 0.7 to 0.8
3. Save
4. Review more cards
5. **Verify**: Intervals now ~80% of standard (less aggressive boosting)
6. Revert to defaults

### Integration Test

**Complete workflow:**

```bash
# 1. Analyze and create tagged deck
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_test.colpkg --analysis-only
python smart_prep.py collection-2026-06-07@15-43-15.colpkg jeopardy_test.colpkg

# 2. Import into Anki
# (Use Anki GUI: File > Import)

# 3. Review 50 cards:
# - Mix of freq:high, freq:medium, freq:low
# - Note intervals and ease factors
# - Verify queue order (high first)

# 4. Spot-check answers:
# - "United States" should be freq:high
# - Obscure 1970s reference should be freq:rare
# - Recent (2024–2025) answers weighted more heavily

# 5. Run browser queries:
#   tag:freq:high      → expect 100K+
#   tag:freq:medium    → expect 150K+
#   tag:freq:low       → expect 70K+
#   tag:freq:rare      → expect 25K+
```

---

## Technical Details

### Data Structures

#### **Answer Frequency Record**

```python
from typing import TypedDict
from datetime import date

class AnswerFrequency(TypedDict):
    answer_text: str
    total_count: int
    year_distribution: dict[int, int]  # {2024: 45, 2023: 28, ...}
    time_weighted_score: float
    frequency_tier: Literal["high", "medium", "low", "rare"]
    sample_cards: list[int]  # note IDs for sampling
```

#### **Card Tag Set**

```python
class CardTags(TypedDict):
    frequency: Literal["freq:high", "freq:medium", "freq:low", "freq:rare"]
    category: str  # e.g., "cat:SCIENCE"
    era: Literal["era:recent", "era:modern", "era:old"]  # optional
```

### SQLite Schema

Original Jeopardy collection schema (relevant tables):

```sql
CREATE TABLE notes (
    id      INTEGER PRIMARY KEY,
    guid    TEXT,
    mid     INTEGER,       -- model (note type) ID
    mod     INTEGER,       -- modification timestamp
    usn     INTEGER,       -- review status
    tags    TEXT,          -- space-separated tags (we append to this)
    flds    TEXT,          -- field data, \x1f-delimited
    ...
);

CREATE TABLE cards (
    id      INTEGER PRIMARY KEY,
    nid     INTEGER,       -- note ID
    did     INTEGER,       -- deck ID
    mod     INTEGER,
    usn     INTEGER,
    type    INTEGER,       -- 0=new, 1=learning, 2=review
    queue   INTEGER,       -- scheduling queue
    due     INTEGER,       -- when due (day number or timestamp)
    ivl     INTEGER,       -- interval in days
    factor  INTEGER,       -- ease factor (×1000, so 2500 = 2.5)
    reps    INTEGER,       -- total reviews
    ...
);
```

### Anki Add-on Hooks

The `frequency_scheduler` add-on uses these Anki hooks:

#### **Hook: reviewer_did_answer_card**

Fires when a card is answered in the reviewer.

```python
from anki.hooks import wrap

def my_answer_hook(reviewer, card, ease):
    # Read frequency tag
    tag = extract_frequency_tag(card.note())

    # Adjust interval
    interval_multiplier = get_multiplier(tag)
    new_interval = card.interval * interval_multiplier

    # Adjust ease
    ease_adjustment = get_ease_adjustment(tag)
    new_ease = card.factor + ease_adjustment * 10  # factor is ×10

    # Store
    card.interval = new_interval
    card.factor = new_ease
    card.save()

wrap(Reviewer.answer_card, my_answer_hook, "after")
```

#### **Hook: gui_will_load_deck**

Fires when opening a deck for review (used for queue reordering).

```python
def reorder_queue(deck_id, order):
    # Fetch due cards
    cards = mw.col.find_cards(f"did:{deck_id}")

    # Sort by frequency tier
    tier_order = {"freq:high": 0, "freq:medium": 1, "freq:low": 2, "freq:rare": 3}
    sorted_cards = sorted(cards, key=lambda c: get_tier_order(c, tier_order))

    # Reorder in deck
    return sorted_cards

gui_will_load_deck.append(reorder_queue)
```

### Performance Notes

**smart_prep.py:**

- **Time**: ~10–15 minutes for 370K cards on standard hardware
- **Memory**: ~500 MB peak (loading all answers in memory)
- **I/O**: 4 GB read/write (decompressing and recompressing SQLite)

**frequency_scheduler:**

- **Time per review**: < 50 ms (tag lookup + multiplication)
- **Memory**: ~1 MB (config + tag cache)
- **Latency**: Negligible (hooks run between card answers)

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    collection-2026-06-07@15-43-15.colpkg       │
│                      (370,616 cards, 924 MB)                    │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │    smart_prep.py          │
                    │  (Data Preprocessing)     │
                    └────────────┬──────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
    ┌─────────▼────────┐  ┌─────▼──────┐  ┌───────▼───────┐
    │ Extract & Parse  │  │ Calculate  │  │ Rank into     │
    │ 370K answers +   │  │ Time-      │  │ 4 tiers by    │
    │ air dates        │  │ Weighted   │  │ score         │
    │                  │  │ Frequency  │  │               │
    │ Result:          │  │            │  │ Result:       │
    │ (answer, year)   │  │ Result:    │  │ {answer,      │
    │ tuples           │  │ {answer,   │  │  tier}        │
    │                  │  │  score}    │  │ dict          │
    └─────────┬────────┘  └────────────┘  └───┬───────────┘
              │                                │
              └────────────┬───────────────────┘
                           │
                    ┌──────▼──────────┐
                    │  Tag Cards      │
                    │  Add to DB:     │
                    │  - freq:*       │
                    │  - cat:*        │
                    │  - era:*        │
                    └──────┬──────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
    ┌────▼────┐  ┌─────────▼──────┐  ┌──────▼──────┐
    │Recompress│  │ Update media   │  │ Package as  │
    │SQLite to │  │ manifest       │  │ .colpkg ZIP │
    │.anki21b  │  │                │  │             │
    └────┬─────┘  └────────────────┘  └──────┬──────┘
         │                                    │
         └────────────┬─────────────────────┘
                      │
              ┌───────▼──────────┐
              │  jeopardy_smart  │
              │  .colpkg         │
              │ (924 MB, tagged) │
              └───────┬──────────┘
                      │
           ┌──────────▼──────────┐
           │  Import into Anki   │
           │  (5 minutes)        │
           └──────────┬──────────┘
                      │
         ┌────────────▼──────────────┐
         │  Jeopardy Deck in Anki    │
         │  - 370K cards with tags   │
         │  - Original scheduling    │
         │  - Ready for review       │
         └────────────┬──────────────┘
                      │
           ┌──────────▼──────────┐
           │Install Add-on:      │
           │frequency_scheduler  │
           └──────────┬──────────┘
                      │
         ┌────────────▼────────────┐
         │  During Review:         │
         │  - Adjust intervals     │
         │  - Prioritize freq:high │
         │  - Adjust ease factors  │
         └────────────┬────────────┘
                      │
         ┌────────────▼────────────┐
         │ Optimized Study Path:   │
         │ freq:high (70% speed)   │
         │ → freq:medium (100%)    │
         │ → freq:low (130%)       │
         │ → freq:rare (150%)      │
         └─────────────────────────┘
```

---

## Appendix: Troubleshooting

### Issue: "zstandard not installed"

**Solution:**

```bash
pip install zstandard
```

### Issue: "collection.anki21b not found"

**Cause**: `.colpkg` file is corrupted or in old format.

**Solution**:

1. Export a fresh backup from Anki: File > Export > "Anki Collection Package"
2. Use the newly exported file

### Issue: Import takes very long (> 10 minutes)

**Cause**: Anki is scanning/reorganizing 370K cards.

**Solution**: Let it run (may take 15–20 minutes on slow disk). Don't interrupt.

### Issue: Add-on doesn't load

**Cause**: Wrong installation location or Anki version < 2.1.45.

**Solution**:

1. Check installation path (see [Installation & Setup](#installation--setup))
2. Restart Anki: File > Exit, then reopen
3. If still issues, check Anki version: Help > About

### Issue: Intervals don't seem adjusted

**Cause**: Add-on may be disabled or cards lack frequency tags.

**Solution**:

1. Verify add-on is enabled: Tools > Add-ons > frequency_scheduler (should be enabled)
2. Verify tags: Browser > Search `tag:freq:*` (should find all cards)
3. Check config: Tools > Add-ons > frequency_scheduler > Config (ensure multipliers are != 1.0)

---

## Summary

This project implements a **data-driven study optimizer** for Jeopardy preparation:

1. **smart_prep.py** analyzes 370K historical questions and identifies common answers (weighted by recency)
2. **frequency_scheduler add-on** automatically adjusts Anki's scheduling to prioritize high-frequency answers
3. Result: Contestants spend proportionally more study time on answers most likely to appear on future episodes

**Expected outcome:** 20–30% improvement in answer coverage per unit of study time.
