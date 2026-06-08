"""
Post-processor that consolidates category_taxonomy.json after LLM classification.

Run AFTER classify_categories.py finishes:
    python3 consolidate_taxonomy.py [--dry-run] [--input category_taxonomy.json]

Goals:
  - Merge cross-subject duplicate sub-categories into single canonical labels
  - Strip temporal prefixes ("1950s Travel" → "Travel", "19th Century Art" → "Art")
  - Apply synonym renames ("Films" → "Movies", "TV Shows" → "Television", etc.)
  - Null out pure catch-alls ("Miscellaneous", "Potpourri") → "Unclassified"
  - Report before/after subcat count so the user can review merges before committing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Final

from jeopardy_consts import SUBJECT_OTHER

# ---------------------------------------------------------------------------
# Canonical sub-category names that should be unified across subjects
# ---------------------------------------------------------------------------

# When a sub-cat has the same name in multiple subjects, keep it exactly as-is
# (subject remains the primary distinction). Only listed here so we DON'T strip
# temporal prefixes from them — "1980s Presidents" stays distinct from "Presidents".
GLOBAL_SUBCATS: Final[frozenset[str]] = frozenset(
    {
        "Academy Awards",
        "Actors/Actresses",
        "Airlines",
        "Animals",
        "Art",
        "Authors",
        "Awards",
        "Baseball",
        "Bible",
        "Books",
        "Business",
        "Capitals",
        "Cinema",
        "Composers",
        "Directors",
        "Films",
        "Geography",
        "Grammar",
        "History",
        "Holidays",
        "Literature",
        "Movies",
        "Music",
        "Mythology",
        "Novels",
        "Opera",
        "Paintings",
        "Plays",
        "Poetry",
        "Poets",
        "Presidents",
        "Science",
        "Shakespeare",
        "Songs",
        "Sports",
        "Television",
        "Theater",
        "TV Shows",
    }
)

# ---------------------------------------------------------------------------
# Temporal prefix pattern — strip these prefixes to collapse time-era variants
# ---------------------------------------------------------------------------

_TEMPORAL_PREFIXES: Final[list[str]] = [
    r"\d{3}0s",  # "1950s", "1980s" — decades only (not bare years like "1812")
    r"(?:1[0-9]|20)th Century",  # "19th Century", "20th Century"
    r"(?:Ancient|Modern|Contemporary|Early|Late|Mid(?:-century)?|Pre-\w+)",
    r"(?:Classic(?:al)?|Victorian|Medieval|Renaissance)",
]

_TEMPORAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:" + "|".join(_TEMPORAL_PREFIXES) + r")\s+",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Synonym / rename table — maps any variant to the canonical label
# ---------------------------------------------------------------------------

SYNONYM_MAP: Final[dict[str, str]] = {
    # Film/TV
    "Cinema": "Movies",
    "Films": "Movies",
    "Film": "Movies",
    "TV": "Television",
    "TV Shows": "Television",
    "TV Series": "Television",
    "Television Series": "Television",
    # Literature
    "Books": "Novels",
    "Book": "Novels",
    "Novels & Books": "Novels",
    "Short Stories": "Fiction",
    # Performing arts
    "Theater": "Theatre",
    "Plays": "Theatre",
    "Stage": "Theatre",
    # Visual art
    "Paintings": "Visual Art",
    "Sculpture": "Visual Art",
    "Photography": "Visual Art",
    # Music
    "Classical Music": "Classical",
    "Rock Music": "Rock",
    "Pop Music": "Pop",
    "Jazz Music": "Jazz",
    # People / biography
    "Biographical": "Biography",
    "Biographies": "Biography",
    # Sports
    "Athletics": "Sports",
    # Geography
    "Capitals": "Capital Cities",
    # Word games
    "Word Games": "Wordplay",
    "Word Puzzles": "Wordplay",
    "Word Patterns": "Wordplay",
    "Puns": "Wordplay",
    "Homophones": "Wordplay",
    "Wordplay Puns": "Wordplay",
    "Puns & Wordplay": "Wordplay",
    "Letter Replacement Puns": "Wordplay",
    "Anagrams": "Wordplay",
    "Rhymes": "Wordplay",
    "Abbreviations": "Wordplay",
    "Acronyms": "Wordplay",
    "Crossword": "Wordplay",
    # Catch-alls — eliminated below, listed here for synonym merge pass too
    "Other": "Unclassified",
    "General": "Unclassified",
    "Potpourri": "Unclassified",
    "Miscellaneous": "Unclassified",
    "Miscellanea": "Unclassified",
    "Variety": "Unclassified",
}

# Sub-categories whose entries should be nullified (set to "Unclassified").
# These carry no meaningful signal and inflate the subcat vocabulary.
ELIMINATE: Final[frozenset[str]] = frozenset(
    {
        "Miscellaneous",
        "Miscellanea",
        "Potpourri",
        "General",
        "Other",
        "Variety",
        "Unclassified",  # idempotent — already nullified entries stay nullified
    }
)


# Case-insensitive lookup table (pre-built so we don't reconstruct per call)
_SYNONYM_LOWER: Final[dict[str, str]] = {k.lower(): v for k, v in SYNONYM_MAP.items()}
_ELIMINATE_LOWER: Final[frozenset[str]] = frozenset(s.lower() for s in ELIMINATE)


# ---------------------------------------------------------------------------
# Core consolidation logic
# ---------------------------------------------------------------------------


def _strip_temporal(label: str) -> str:
    """Return label with leading temporal prefix removed (if any)."""
    return _TEMPORAL_RE.sub("", label).strip()


def _lookup_synonym(label: str) -> str:
    """Case-insensitive synonym lookup; returns original if no match."""
    return _SYNONYM_LOWER.get(label.strip().lower(), label.strip())


def consolidate(
    taxonomy: dict[str, dict[str, str]],
    *,
    strip_temporal: bool = True,
    apply_synonyms: bool = True,
    eliminate_catch_alls: bool = True,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """
    Return (consolidated_taxonomy, merge_log).

    merge_log maps original subcat label → canonical label for every rename.
    The taxonomy dict maps CATEGORY_UPPER → {subject, sub_category}.
    """
    merge_log: dict[str, str] = {}
    result: dict[str, dict[str, str]] = {}

    for category, info in taxonomy.items():
        subj = info.get("subject", SUBJECT_OTHER)
        raw_subcat = info.get("sub_category", "Unclassified") or "Unclassified"

        subcat = raw_subcat

        # 1. Eliminate catch-alls before anything else
        if eliminate_catch_alls and subcat.strip().lower() in _ELIMINATE_LOWER:
            canonical = "Unclassified"
            if raw_subcat != canonical:
                merge_log[raw_subcat] = canonical
            result[category] = {"subject": subj, "sub_category": canonical}
            continue

        # 2. Apply synonym map
        if apply_synonyms:
            matched = _lookup_synonym(subcat)
            if matched != subcat:
                merge_log[subcat] = matched
                subcat = matched

        # 3. Strip temporal prefix — but only when the stripped label is NOT in
        #    GLOBAL_SUBCATS (those keep era context, e.g. "1980s Presidents" stays)
        if strip_temporal:
            stripped = _strip_temporal(subcat)
            if stripped and stripped.lower() != subcat.lower():
                if stripped.lower() not in {g.lower() for g in GLOBAL_SUBCATS}:
                    merge_log[subcat] = stripped
                    subcat = stripped

        # 4. Final synonym pass after temporal strip (e.g. "Films" after stripping
        #    "1930s Films" → "Films" → "Movies")
        if apply_synonyms:
            matched = _lookup_synonym(subcat)
            if matched != subcat:
                merge_log[subcat] = matched
                subcat = matched

        result[category] = {"subject": subj, "sub_category": subcat.strip()}

    return result, merge_log


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _count_subcats(taxonomy: dict[str, dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for info in taxonomy.values():
        counts[info.get("sub_category", "Unclassified")] += 1
    return dict(counts)


def print_report(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    merge_log: dict[str, str],
) -> None:
    before_counts = _count_subcats(before)
    after_counts = _count_subcats(after)

    before_unique = len(before_counts)
    after_unique = len(after_counts)
    reduction = before_unique - after_unique
    pct = 100.0 * reduction / before_unique if before_unique else 0.0

    print("\n=== Category Taxonomy Consolidation Report ===")
    print(f"  Categories processed : {len(before):,}")
    print(f"  Unique subcats BEFORE: {before_unique:,}")
    print(f"  Unique subcats AFTER : {after_unique:,}")
    print(f"  Reduction            : {reduction:,} ({pct:.1f}%)")

    # Show by subject
    by_subject_before: dict[str, set[str]] = defaultdict(set)
    by_subject_after: dict[str, set[str]] = defaultdict(set)
    for info in before.values():
        by_subject_before[info.get("subject", "Other")].add(
            info.get("sub_category", "Unclassified")
        )
    for info in after.values():
        by_subject_after[info.get("subject", "Other")].add(
            info.get("sub_category", "Unclassified")
        )

    print("\n  Per-subject subcategory counts:")
    for subj in sorted(by_subject_before):
        b = len(by_subject_before[subj])
        a = len(by_subject_after.get(subj, set()))
        marker = "✓" if a < b else " "
        print(f"  {marker} {subj:30}  {b:4} → {a:4}")

    # Show top merges
    print(f"\n  Top merges applied ({len(merge_log)} total):")
    shown = 0
    for orig, canonical in sorted(merge_log.items()):
        print(f"    '{orig}' → '{canonical}'")
        shown += 1
        if shown >= 30:
            print(f"    ... and {len(merge_log) - shown} more")
            break

    # Show remaining top subcats
    print("\n  Top 20 subcats after consolidation:")
    for sc, cnt in sorted(after_counts.items(), key=lambda x: -x[1])[:20]:
        pct_share = 100.0 * cnt / len(after) if after else 0.0
        print(f"    {sc:40} {cnt:5} ({pct_share:5.1f}%)")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate category_taxonomy.json after LLM classification."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("category_taxonomy.json"),
        help="Input taxonomy JSON (default: category_taxonomy.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: overwrite input)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report without writing output",
    )
    parser.add_argument(
        "--no-temporal-strip",
        action="store_true",
        help="Disable temporal prefix stripping",
    )
    parser.add_argument(
        "--no-synonyms",
        action="store_true",
        help="Disable synonym renaming",
    )
    parser.add_argument(
        "--no-eliminate",
        action="store_true",
        help="Disable elimination of catch-all sub-categories",
    )
    args = parser.parse_args()

    input_path: Path = args.input
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    with input_path.open() as f:
        taxonomy: dict[str, dict[str, str]] = json.load(f)

    if not taxonomy:
        print("Taxonomy is empty — nothing to consolidate.", file=sys.stderr)
        sys.exit(0)

    consolidated, merge_log = consolidate(
        taxonomy,
        strip_temporal=not args.no_temporal_strip,
        apply_synonyms=not args.no_synonyms,
        eliminate_catch_alls=not args.no_eliminate,
    )

    print_report(taxonomy, consolidated, merge_log)

    if args.dry_run:
        print("Dry-run mode — no files written.")
        return

    output_path: Path = args.output if args.output else input_path
    with output_path.open("w") as f:
        json.dump(consolidated, f, indent=2, ensure_ascii=False)
    print(f"Written to: {output_path}")


if __name__ == "__main__":
    main()
