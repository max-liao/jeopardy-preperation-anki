"""
Post-processor that consolidates category_taxonomy.json after LLM classification.

Run AFTER classify_categories.py finishes:
    python3 consolidate_taxonomy.py [--dry-run] [--input category_taxonomy.json]

Goals:
  - Apply hand-coded overrides for the top ~300 highest-frequency on-air categories
    (these cover ~30%+ of all cards; LLM often skips them for the long tail)
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
from typing import Final

from jeopardy_consts import SUBJECT_OTHER

# ---------------------------------------------------------------------------
# Manual overrides — hand-coded mappings for high-frequency on-air categories.
# Applied FIRST; they win over any LLM output. Sorted by historical card count.
# Format: "ON-AIR CATEGORY UPPER": ("Subject", "Sub-category")
# ---------------------------------------------------------------------------

MANUAL_OVERRIDES: Final[dict[str, tuple[str, str]]] = {
    # ── Science ─────────────────────────────────────────────────────────────
    "SCIENCE":                    ("Science", "Science"),
    "SCIENCE & NATURE":           ("Science", "Science"),
    "GENERAL SCIENCE":            ("Science", "Science"),
    "SCIENCE CLASS":              ("Science", "Science"),
    "PHYSICS":                    ("Science", "Physics"),
    "PHYSICAL SCIENCE":           ("Science", "Physics"),
    "BIOLOGY":                    ("Science", "Biology"),
    "CHEMISTRY":                  ("Science", "Chemistry"),
    "THE ELEMENTS":               ("Science", "Chemistry"),
    "MEDICINE":                   ("Science", "Medicine"),
    "HEALTH & MEDICINE":          ("Science", "Medicine"),
    "SICKNESS & HEALTH":          ("Science", "Medicine"),
    "THE HUMAN BODY":             ("Science", "Biology"),
    "THE BODY HUMAN":             ("Science", "Biology"),
    "ASTRONOMY":                  ("Science", "Astronomy"),
    "THE PLANETS":                ("Science", "Astronomy"),
    "CONSTELLATIONS":             ("Science", "Astronomy"),
    "GEOLOGY":                    ("Science", "Geology"),
    "EARTH SCIENCE":              ("Science", "Geology"),
    "ROCKS & MINERALS":           ("Science", "Geology"),
    "GEMS":                       ("Science", "Geology"),
    "WEATHER":                    ("Science", "Meteorology"),
    "TECHNOLOGY":                 ("Science", "Technology"),
    "INVENTORS":                  ("Science", "Inventors"),
    "INVENTORS & INVENTIONS":     ("Science", "Inventors"),
    "INVENTIONS":                 ("Science", "Inventors"),
    "SCIENTISTS":                 ("Science", "Scientists"),
    "MATH":                       ("Science", "Mathematics"),
    "MATHEMATICS":                ("Science", "Mathematics"),
    "GEOMETRY":                   ("Science", "Mathematics"),
    "ZOOLOGY":                    ("Science", "Biology"),
    "MARINE BIOLOGY":             ("Science", "Marine Biology"),
    "PSYCHOLOGY":                 ("Science", "Psychology"),
    "THE ENVIRONMENT":            ("Science", "Environment"),
    "NUTRITION":                  ("Food & Drink", "Nutrition"),
    "WEIGHTS & MEASURES":         ("Science", "Measurement"),
    "TIME":                       ("Science", "Measurement"),
    # ── History ─────────────────────────────────────────────────────────────
    "HISTORY":                    ("History", "World History"),
    "WORLD HISTORY":              ("History", "World History"),
    "AMERICAN HISTORY":           ("History", "US History"),
    "U.S. HISTORY":               ("History", "US History"),
    "THE AMERICAN REVOLUTION":    ("History", "US History"),
    "THE CIVIL WAR":              ("History", "Civil War"),
    "WORLD WAR II":               ("History", "World War II"),
    "WORLD WAR I":                ("History", "World War I"),
    "EUROPEAN HISTORY":           ("History", "European History"),
    "FRENCH HISTORY":             ("History", "European History"),
    "THE MIDDLE AGES":            ("History", "Medieval History"),
    "THE 19TH CENTURY":          ("History", "19th Century"),
    "THE 20TH CENTURY":          ("History", "20th Century"),
    "THE 18TH CENTURY":          ("History", "18th Century"),
    "HISTORIC NAMES":             ("History", "Historical Figures"),
    "PEOPLE IN HISTORY":          ("History", "Historical Figures"),
    "HISTORIC AMERICANS":         ("History", "US History"),
    "THE OLD WEST":               ("History", "US History"),
    "EXPLORERS":                  ("History", "Exploration"),
    "EXPLORERS & EXPLORATION":    ("History", "Exploration"),
    "NATIVE AMERICANS":           ("History", "US History"),
    "ROYALTY":                    ("History", "Royalty"),
    "RULERS":                     ("History", "Royalty"),
    "THE 1930S":                  ("History", "1930s"),
    "WOMEN IN HISTORY":           ("History", "Women in History"),
    "WEAPONS":                    ("History", "Military History"),
    "SHIPS":                      ("History", "Naval History"),
    "RANKS & TITLES":             ("History", "Historical Figures"),
    # ── Geography ───────────────────────────────────────────────────────────
    "GEOGRAPHY":                  ("Geography", "Geography"),
    "WORLD GEOGRAPHY":            ("Geography", "World Geography"),
    "U.S. GEOGRAPHY":             ("Geography", "US Geography"),
    "U.S. CITIES":                ("Geography", "US Cities"),
    "WORLD CAPITALS":             ("Geography", "Capital Cities"),
    "STATE CAPITALS":             ("Geography", "Capital Cities"),
    "COUNTRIES OF THE WORLD":     ("Geography", "Countries"),
    "EUROPE":                     ("Geography", "Europe"),
    "EUROPEAN GEOGRAPHY":         ("Geography", "Europe"),
    "EUROPEAN CITIES":            ("Geography", "Europe"),
    "SOUTH AMERICA":              ("Geography", "South America"),
    "ISLANDS":                    ("Geography", "Islands"),
    "MOUNTAINS":                  ("Geography", "Mountains"),
    "LAKES & RIVERS":             ("Geography", "Rivers & Lakes"),
    "RIVERS":                     ("Geography", "Rivers & Lakes"),
    "BODIES OF WATER":            ("Geography", "Rivers & Lakes"),
    "WORLD CITIES":               ("Geography", "Cities"),
    "LANDMARKS":                  ("Geography", "Landmarks"),
    "TRAVEL & TOURISM":           ("Geography", "Travel"),
    "WORLD TRAVEL":               ("Geography", "Travel"),
    "TRAVEL U.S.A.":              ("Geography", "US Geography"),
    "AROUND THE WORLD":           ("Geography", "World Travel"),
    "U.S. STATES":                ("Geography", "US States"),
    "THE 50 STATES":              ("Geography", "US States"),
    "STATE FLAGS":                ("Geography", "US States"),
    "STATE NICKNAMES":            ("Geography", "US States"),
    "STATE FACTS":                ("Geography", "US States"),
    "OFFICIAL STATE STUFF":       ("Geography", "US States"),
    "NATIONAL PARKS":             ("Geography", "US Geography"),
    "U.S.A.":                     ("Geography", "US Geography"),
    "FLAGS":                      ("Geography", "National Symbols"),
    "OFFICIAL LANGUAGES":         ("Geography", "Countries"),
    "WHERE AM I?":                ("Geography", "Geography"),
    "ON THE MAP":                 ("Geography", "Geography"),
    "PEOPLE & PLACES":            ("Geography", "Geography"),
    "WORLD FACTS":                ("Geography", "Geography"),
    "THE WESTERN HEMISPHERE":     ("Geography", "Americas"),
    "HIGHWAYS & BYWAYS":          ("Geography", "Travel"),
    # ── Literature ──────────────────────────────────────────────────────────
    "LITERATURE":                 ("Literature", "Literature"),
    "BOOKS & AUTHORS":            ("Literature", "Books & Authors"),
    "AUTHORS":                    ("Literature", "Authors"),
    "WOMEN AUTHORS":              ("Literature", "Authors"),
    "PEN NAMES":                  ("Literature", "Authors"),
    "SHAKESPEARE":                ("Literature", "Shakespeare"),
    "SHAKESPEAREAN CHARACTERS":   ("Literature", "Shakespeare"),
    "NONFICTION":                 ("Literature", "Nonfiction"),
    "FICTION":                    ("Literature", "Fiction"),
    "NOVELS":                     ("Literature", "Novels"),
    "SHORT STORIES":              ("Literature", "Fiction"),
    "IN THE BOOKSTORE":           ("Literature", "Literature"),
    "LITERARY CHARACTERS":        ("Literature", "Fictional Characters"),
    "FICTIONAL CHARACTERS":       ("Literature", "Fictional Characters"),
    "POETS & POETRY":             ("Literature", "Poetry"),
    "POETRY":                     ("Literature", "Poetry"),
    "POETS":                      ("Literature", "Poetry"),
    "PLAYWRIGHTS":                ("Literature", "Playwrights"),
    "PLAYS & PLAYWRIGHTS":        ("Literature", "Theatre"),
    "DRAMA":                      ("Literature", "Theatre"),
    "PLAYS":                      ("Literature", "Theatre"),
    "ENGLISH LITERATURE":         ("Literature", "British Literature"),
    "ENGLISH LIT":                ("Literature", "British Literature"),
    "WORLD LITERATURE":           ("Literature", "World Literature"),
    "FRENCH LITERATURE":          ("Literature", "World Literature"),
    "KIDDY LIT":                  ("Literature", "Children's Literature"),
    "NURSERY RHYMES":             ("Literature", "Children's Literature"),
    "QUOTATIONS":                 ("Literature", "Quotations"),
    "QUOTES":                     ("Literature", "Quotations"),
    "FAMOUS QUOTES":              ("Literature", "Quotations"),
    "LITERARY QUOTES":            ("Literature", "Quotations"),
    "LITERARY TERMS":             ("Literature", "Literary Terms"),
    "LITERARY HODGEPODGE":        ("Literature", "Literature"),
    # ── Music ───────────────────────────────────────────────────────────────
    "MUSIC":                      ("Music", "Music"),
    "CLASSICAL MUSIC":            ("Music", "Classical"),
    "MUSIC APPRECIATION":         ("Music", "Classical"),
    "COMPOSERS":                  ("Music", "Composers"),
    "OPERA":                      ("Music", "Opera"),
    "POP MUSIC":                  ("Music", "Pop"),
    "COUNTRY MUSIC":              ("Music", "Country Music"),
    "SINGERS":                    ("Music", "Musicians"),
    "MUSICAL INSTRUMENTS":        ("Music", "Instruments"),
    "NATIONAL ANTHEMS":           ("Music", "National Anthems"),
    # ── Art ─────────────────────────────────────────────────────────────────
    "ART":                        ("Art", "Art"),
    "ART & ARTISTS":              ("Art", "Artists"),
    "THE ARTS":                   ("Art", "Art"),
    "SCULPTURE":                  ("Art", "Sculpture"),
    "MUSEUMS":                    ("Art", "Museums"),
    "FASHION":                    ("Art", "Fashion"),
    "FASHION HISTORY":            ("Art", "Fashion"),
    "FASHION DESIGNERS":          ("Art", "Fashion"),
    "BALLET":                     ("Art", "Ballet"),
    "DANCE":                      ("Art", "Dance"),
    "THEATRE":                    ("Art", "Theatre"),
    "THEATER":                    ("Art", "Theatre"),
    "MUSICAL THEATRE":            ("Art", "Theatre"),
    "MUSICAL THEATER":            ("Art", "Theatre"),
    "PHOTOGRAPHY":                ("Art", "Photography"),
    "GEMS & JEWELRY":             ("Art", "Decorative Arts"),
    "FURNITURE":                  ("Art", "Decorative Arts"),
    # ── Film & TV ────────────────────────────────────────────────────────────
    "TELEVISION":                 ("Film & TV", "Television"),
    "TV":                         ("Film & TV", "Television"),
    "TV TRIVIA":                  ("Film & TV", "Television"),
    "TV CHARACTERS":              ("Film & TV", "Television"),
    "THE MOVIES":                 ("Film & TV", "Movies"),
    "MOVIES":                     ("Film & TV", "Movies"),
    "RECENT MOVIES":              ("Film & TV", "Movies"),
    "MOVIE QUOTES":               ("Film & TV", "Movies"),
    "MOVIE TRIVIA":               ("Film & TV", "Movies"),
    "MOVIE TAGLINES":             ("Film & TV", "Movies"),
    "THE OSCARS":                 ("Film & TV", "Awards"),
    "WHO PLAYED 'EM?":            ("Film & TV", "Actors"),
    "FROM PAGE TO SCREEN":        ("Film & TV", "Adaptations"),
    "DOCUMENTARIES":              ("Film & TV", "Documentaries"),
    # ── Religion & Mythology ────────────────────────────────────────────────
    "RELIGION":                   ("Religion & Mythology", "Religion"),
    "WORLD RELIGION":             ("Religion & Mythology", "Religion"),
    "THE BIBLE":                  ("Religion & Mythology", "Bible"),
    "THE OLD TESTAMENT":          ("Religion & Mythology", "Bible"),
    "THE NEW TESTAMENT":          ("Religion & Mythology", "Bible"),
    "MYTHOLOGY":                  ("Religion & Mythology", "Mythology"),
    "MYTHS & LEGENDS":            ("Religion & Mythology", "Mythology"),
    "SAINTS":                     ("Religion & Mythology", "Saints"),
    "PHILOSOPHY":                 ("Religion & Mythology", "Philosophy"),
    # ── People ──────────────────────────────────────────────────────────────
    "PEOPLE":                     ("People", "Biography"),
    "FAMOUS NAMES":               ("People", "Biography"),
    "NOTABLE NAMES":              ("People", "Biography"),
    "FAMOUS AMERICANS":           ("People", "Americans"),
    "FAMOUS WOMEN":               ("People", "Women"),
    "NOTABLE WOMEN":              ("People", "Women"),
    "WOMEN":                      ("People", "Women"),
    "WOMEN IN SPORTS":            ("Sports", "Women in Sports"),
    "NICKNAMES":                  ("People", "Biography"),
    "NOTORIOUS":                  ("People", "Biography"),
    "FAMOUS PAIRS":               ("People", "Biography"),
    "CONTEMPORARIES":             ("People", "Biography"),
    "LESSER-KNOWN NAMES":         ("People", "Biography"),
    "OCCUPATIONS":                ("People", "Occupations"),
    "EDUCATION":                  ("People", "Education"),
    "MIDDLE NAMES":               ("People", "Biography"),
    # ── Politics & Government ────────────────────────────────────────────────
    "GOVERNMENT & POLITICS":      ("Politics & Government", "Government"),
    "GOVERNMENT":                 ("Politics & Government", "Government"),
    "U.S. GOVERNMENT":            ("Politics & Government", "US Government"),
    "THE CONSTITUTION":           ("Politics & Government", "US Government"),
    "THE CABINET":                ("Politics & Government", "US Government"),
    "U.S. PRESIDENTS":            ("Politics & Government", "Presidents"),
    "PRESIDENTS":                 ("Politics & Government", "Presidents"),
    "HAIL TO THE CHIEF":          ("Politics & Government", "Presidents"),
    "PRESIDENTIAL TRIVIA":        ("Politics & Government", "Presidents"),
    "PRESIDENTIAL NICKNAMES":     ("Politics & Government", "Presidents"),
    "VICE PRESIDENTS":            ("Politics & Government", "Presidents"),
    "FIRST LADIES":               ("Politics & Government", "Presidents"),
    "POLITICIANS":                ("Politics & Government", "Politicians"),
    "WORLD LEADERS":              ("Politics & Government", "World Leaders"),
    "POLITICS":                   ("Politics & Government", "Politics"),
    "LAW":                        ("Politics & Government", "Law"),
    "THE LAW":                    ("Politics & Government", "Law"),
    "THE SUPREME COURT":          ("Politics & Government", "Law"),
    "ORGANIZATIONS":              ("Business & Economics", "Organizations"),
    "NATIONAL PARKS":             ("Geography", "US Geography"),
    # ── Business & Economics ─────────────────────────────────────────────────
    "ECONOMICS":                  ("Business & Economics", "Economics"),
    "MONEY":                      ("Business & Economics", "Finance"),
    "STOCK SYMBOLS":              ("Business & Economics", "Finance"),
    "COMMUNICATION":              ("Business & Economics", "Media"),
    "NEWSPAPERS":                 ("Pop Culture", "Media"),
    "MAGAZINES":                  ("Pop Culture", "Media"),
    # ── Sports ──────────────────────────────────────────────────────────────
    "SPORTS":                     ("Sports", "Sports"),
    "THE OLYMPICS":               ("Sports", "Olympics"),
    "FOOTBALL":                   ("Sports", "Football"),
    "SPORTS STARS":               ("Sports", "Athletes"),
    "SPORTS NICKNAMES":           ("Sports", "Athletes"),
    # ── Nature & Animals ────────────────────────────────────────────────────
    "ANIMALS":                    ("Nature & Animals", "Animals"),
    "THE ANIMAL KINGDOM":         ("Nature & Animals", "Animals"),
    "NATURE":                     ("Nature & Animals", "Nature"),
    "MAMMALS":                    ("Nature & Animals", "Mammals"),
    "INSECTS":                    ("Nature & Animals", "Insects"),
    "FISH":                       ("Nature & Animals", "Fish"),
    "DOGS":                       ("Nature & Animals", "Animals"),
    "FLOWERS":                    ("Nature & Animals", "Plants"),
    "TREES":                      ("Nature & Animals", "Plants"),
    "PLANTS":                     ("Nature & Animals", "Plants"),
    "PLANTS & TREES":             ("Nature & Animals", "Plants"),
    "GARDENING":                  ("Nature & Animals", "Plants"),
    "HERBS & SPICES":             ("Food & Drink", "Cooking"),
    "FRUITS & VEGETABLES":        ("Food & Drink", "Produce"),
    # ── Food & Drink ─────────────────────────────────────────────────────────
    "FOOD":                       ("Food & Drink", "Food"),
    "FOOD & DRINK":               ("Food & Drink", "Food & Drink"),
    "FOOD FACTS":                 ("Food & Drink", "Food"),
    "COOKING":                    ("Food & Drink", "Cooking"),
    "INTERNATIONAL CUISINE":      ("Food & Drink", "International Cuisine"),
    "POTENT POTABLES":            ("Food & Drink", "Beverages"),
    # ── Pop Culture ──────────────────────────────────────────────────────────
    "POP CULTURE":                ("Pop Culture", "General"),
    "AMERICANA":                  ("Pop Culture", "Americana"),
    "HOLIDAYS & OBSERVANCES":     ("Pop Culture", "Holidays"),
    "HOLIDAYS":                   ("Pop Culture", "Holidays"),
    "TOYS & GAMES":               ("Pop Culture", "Games"),
    "GAMES":                      ("Pop Culture", "Games"),
    "IN THE NEWS":                ("Pop Culture", "Current Events"),
    "THE 1980S":                  ("Pop Culture", "1980s"),
    "THE 1970S":                  ("Pop Culture", "1970s"),
    "ETIQUETTE":                  ("Pop Culture", "Social Customs"),
    "WEBSITES":                   ("Pop Culture", "Technology"),
    "GUINNESS RECORDS":           ("Pop Culture", "Records"),
    # ── Wordplay & Language ──────────────────────────────────────────────────
    "WORD ORIGINS":               ("Wordplay & Language", "Etymology"),
    "WORD & PHRASE ORIGINS":      ("Wordplay & Language", "Etymology"),
    "FROM THE LATIN":             ("Wordplay & Language", "Etymology"),
    "FROM THE GREEK":             ("Wordplay & Language", "Etymology"),
    "FROM THE FRENCH":            ("Wordplay & Language", "Etymology"),
    "EPONYMS":                    ("Wordplay & Language", "Etymology"),
    "LANGUAGES":                  ("Wordplay & Language", "Languages"),
    "FOREIGN WORDS & PHRASES":    ("Wordplay & Language", "Foreign Language"),
    "VOCABULARY":                 ("Wordplay & Language", "Vocabulary"),
    "IN THE DICTIONARY":          ("Wordplay & Language", "Vocabulary"),
    "ODD WORDS":                  ("Wordplay & Language", "Vocabulary"),
    "MORE THAN ONE MEANING":      ("Wordplay & Language", "Vocabulary"),
    "IN OTHER WORDS...":          ("Wordplay & Language", "Vocabulary"),
    "NEW TO THE OED":             ("Wordplay & Language", "Vocabulary"),
    "LITERARY TERMS":             ("Literature", "Literary Terms"),
    "RHYME TIME":                 ("Wordplay & Language", "Wordplay"),
    "HOMOPHONES":                 ("Wordplay & Language", "Wordplay"),
    "HOMOPHONIC PAIRS":           ("Wordplay & Language", "Wordplay"),
    "WORD PUZZLES":               ("Wordplay & Language", "Wordplay"),
    "COMPOUND WORDS":             ("Wordplay & Language", "Wordplay"),
    "WORDS WITHIN WORDS":         ("Wordplay & Language", "Wordplay"),
    "WORDS THAT SHOULD RHYME":    ("Wordplay & Language", "Wordplay"),
    "SAME FIRST & LAST LETTER":   ("Wordplay & Language", "Wordplay"),
    "DOUBLE DOUBLE LETTERS":      ("Wordplay & Language", "Wordplay"),
    "TRIPLE RHYME TIME":          ("Wordplay & Language", "Wordplay"),
    "LETTER PERFECT":             ("Wordplay & Language", "Wordplay"),
    "DOUBLE TALK":                ("Wordplay & Language", "Wordplay"),
    "QUASI-RELATED PAIRS":        ("Wordplay & Language", "Wordplay"),
    "PARTS OF THE WHOLE":         ("Wordplay & Language", "Wordplay"),
    "UNREAL ESTATE":              ("Wordplay & Language", "Wordplay"),
    "NUMBER, PLEASE":             ("Wordplay & Language", "Wordplay"),
    "STUPID ANSWERS":             ("Wordplay & Language", "Wordplay"),
    "FAMILIAR PHRASES":           ("Wordplay & Language", "Phrases"),
    "PROVERBS":                   ("Wordplay & Language", "Phrases"),
    "SIGNS & SYMBOLS":            ("Wordplay & Language", "Symbols"),
    "THE QUEEN'S ENGLISH":        ("Wordplay & Language", "Language"),
    "COMMON BONDS":               ("Wordplay & Language", "Word Patterns"),
    "LAST NAME'S THE SAME":       ("Wordplay & Language", "Wordplay"),
    "FIRST NAME'S THE SAME":      ("Wordplay & Language", "Wordplay"),
    "FAMOUS PAIRS":               ("People", "Biography"),
    # ── Other / Unclassified ─────────────────────────────────────────────────
    "POTPOURRI":                  ("Other", "Unclassified"),
    "HODGEPODGE":                 ("Other", "Unclassified"),
    "ODDS & ENDS":                ("Other", "Unclassified"),
    "THIS & THAT":                ("Other", "Unclassified"),
    "LEFTOVERS":                  ("Other", "Unclassified"),
    "FACTS & FIGURES":            ("Other", "Unclassified"),
    "POT LUCK":                   ("Other", "Unclassified"),
    "MISCELLANEOUS":              ("Other", "Unclassified"),
    "WORLD FACTS":                ("Geography", "Geography"),
}

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
        # 0. Manual override wins unconditionally over LLM output
        if category in MANUAL_OVERRIDES:
            subj, subcat = MANUAL_OVERRIDES[category]
            result[category] = {"subject": subj, "sub_category": subcat}
            continue

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

    # Inject manual overrides for categories not yet reached by the LLM classifier
    injected = 0
    for cat, (subj, sc) in MANUAL_OVERRIDES.items():
        if cat not in taxonomy:
            taxonomy[cat] = {"subject": subj, "sub_category": sc}
            injected += 1
    if injected:
        print(f"  Injected {injected} manual overrides for uncategorized entries.")

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
