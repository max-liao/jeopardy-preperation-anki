#!/usr/bin/env python3
"""Classify Jeopardy on-air categories into a subject / sub-category taxonomy.

Drives the local Claude Code CLI in headless mode (no separate API key — uses the
user's Claude subscription). Each unique category is classified into a broad
SUBJECT (from a controlled vocabulary) and a normalized SUB_CATEGORY. Results are
cached incrementally to a JSON file so re-runs only classify new categories.

Usage:
  python classify_categories.py SOURCE.colpkg [--taxonomy category_taxonomy.json]
      [--model haiku] [--batch-size 150] [--workers 4] [--limit N]
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jeopardy_consts import (
    CLASSIFY_BATCH_SIZE,
    CLASSIFY_MAX_RETRIES,
    CLASSIFY_MODEL_DEFAULT,
    CLASSIFY_RETRY_BACKOFF_SECS,
    CLASSIFY_WORKERS,
    FIELD_CATEGORY,
    JEOPARDY_NOTETYPE_ID,
    SUBJECT_OTHER,
    SUBJECTS,
    TAXONOMY_PATH_DEFAULT,
)
from jeopardy_db_helpers import extract_colpkg
from jeopardy_types import CategoryClassification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Map of lowercased subject -> canonical subject, for snapping LLM output.
_SUBJECT_CANON: dict[str, str] = {s.lower(): s for s in SUBJECTS}
# Matches a ```json ... ``` (or bare ```) fenced block.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def get_claude_bin() -> str:
    """Resolve the Claude Code CLI executable path.

    Returns:
        Path to the claude binary (from CLAUDE_CODE_EXECPATH or "claude")
    """
    return os.environ.get("CLAUDE_CODE_EXECPATH", "claude")


def get_unique_categories(conn_db_path: Path) -> list[str]:
    """Read distinct, normalized on-air categories from the collection.

    Args:
        conn_db_path: Path to a decompressed collection.anki2

    Returns:
        Sorted list of unique uppercased category strings
    """
    conn = sqlite3.connect(conn_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT flds FROM notes WHERE mid = ?", (JEOPARDY_NOTETYPE_ID,))
    cats: set[str] = set()
    for (flds,) in cursor.fetchall():
        parts = flds.split("\x1f")
        if len(parts) > FIELD_CATEGORY:
            cat = parts[FIELD_CATEGORY].strip().upper()
            if cat:
                cats.add(cat)
    conn.close()
    return sorted(cats)


def load_taxonomy(path: Path) -> dict[str, CategoryClassification]:
    """Load the cached taxonomy, if present.

    Args:
        path: Path to the taxonomy JSON cache

    Returns:
        Mapping of category -> classification (empty if no cache)
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw: dict[str, CategoryClassification] = json.load(f)
    return raw


def save_taxonomy(path: Path, taxonomy: dict[str, CategoryClassification]) -> None:
    """Write the taxonomy cache atomically.

    Args:
        path: Path to the taxonomy JSON cache
        taxonomy: Mapping of category -> classification
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.replace(path)


def build_prompt(batch: list[str]) -> str:
    """Build the classification prompt for a batch of categories.

    Args:
        batch: Category strings to classify

    Returns:
        Prompt text
    """
    subject_list = ", ".join(SUBJECTS)
    numbered = "\n".join(f"{i + 1}. {cat}" for i, cat in enumerate(batch))
    return (
        "You are classifying Jeopardy! category names into a two-level taxonomy.\n"
        "For EACH input category, output its broad SUBJECT and a normalized "
        "SUB_CATEGORY.\n\n"
        f"SUBJECT must be EXACTLY one of this controlled list: {subject_list}.\n"
        "SUB_CATEGORY is a short, canonical, Title Case grouping label that "
        "collapses synonyms (e.g. 'RUSSIAN LIT' and 'CLASSIC RUSSIAN AUTHORS' "
        "should both map to sub_category 'Russian Literature'; categories about "
        "one person use that person, e.g. 'Ernest Hemingway'). Reuse the same "
        "sub_category label for equivalent categories so they aggregate.\n"
        "If a category is a grab-bag with no clear topic (e.g. POTPOURRI, "
        "HODGEPODGE), use subject 'Other' and sub_category 'Miscellaneous'.\n\n"
        "Return ONLY a JSON array, one object per input, in the SAME order, with "
        'keys "category", "subject", "sub_category". No prose, no code fence.\n\n'
        f"Categories:\n{numbered}"
    )


def extract_json_array(result_text: str) -> list[dict[str, str]]:
    """Extract a JSON array of objects from model output text.

    Handles bare JSON, ```json fenced blocks, and leading/trailing prose.

    Args:
        result_text: The model's response text

    Returns:
        Parsed list of dicts

    Raises:
        ValueError: If no JSON array can be parsed
    """
    candidates: list[str] = []
    fence = _FENCE_RE.search(result_text)
    if fence:
        candidates.append(fence.group(1).strip())
    # Also try the substring from first '[' to last ']'.
    start = result_text.find("[")
    end = result_text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(result_text[start : end + 1])
    candidates.append(result_text.strip())

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [obj for obj in parsed if isinstance(obj, dict)]
    raise ValueError("No JSON array found in model output")


def canonical_subject(raw_subject: str) -> str:
    """Snap an LLM subject to the controlled vocabulary.

    Args:
        raw_subject: Subject string from the model

    Returns:
        Canonical subject (falls back to SUBJECT_OTHER)
    """
    return _SUBJECT_CANON.get(raw_subject.strip().lower(), SUBJECT_OTHER)


def call_claude(prompt: str, model: str, claude_bin: str) -> str:
    """Invoke the headless Claude CLI and return the model's result text.

    Args:
        prompt: The prompt to send
        model: Model alias or ID (e.g. "haiku")
        claude_bin: Path to the claude executable

    Returns:
        The model's response text (the envelope's `result` field)

    Raises:
        RuntimeError: If the CLI fails or returns an error envelope
    """
    cmd = [
        claude_bin,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[:500]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude returned error: {envelope.get('result', '')[:500]}")
    result = envelope.get("result", "")
    if not isinstance(result, str) or not result:
        raise RuntimeError("claude returned empty result")
    return result


def classify_batch(
    batch: list[str], model: str, claude_bin: str
) -> dict[str, CategoryClassification]:
    """Classify one batch of categories, with bounded retries.

    Args:
        batch: Category strings to classify
        model: Model alias or ID
        claude_bin: Path to the claude executable

    Returns:
        Mapping of category -> classification for the items the model returned.
        On a permanent call/parse failure, returns an EMPTY dict so the batch's
        categories stay unclassified and are retried on the next run (rather than
        being poisoned with a bogus "Other"). Genuinely-missing items from an
        otherwise-successful response are filled as Other/Miscellaneous.
    """
    prompt = build_prompt(batch)
    last_err: Exception | None = None
    parsed: list[dict[str, str]] | None = None
    for attempt in range(1, CLASSIFY_MAX_RETRIES + 1):
        try:
            result = call_claude(prompt, model, claude_bin)
            parsed = extract_json_array(result)
            break
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            last_err = exc
            logger.warning(
                f"Batch attempt {attempt}/{CLASSIFY_MAX_RETRIES} failed: {exc}"
            )
            # Escalating backoff — most failures here are usage/burst limits.
            time.sleep(CLASSIFY_RETRY_BACKOFF_SECS * attempt)

    if parsed is None:
        logger.error(
            f"Batch permanently failed ({last_err}); leaving {len(batch)} "
            "categories unclassified for a later run"
        )
        return {}

    # Map results back to the input categories by normalized text.
    by_cat: dict[str, dict[str, str]] = {}
    for obj in parsed:
        cat = str(obj.get("category", "")).strip().upper()
        if cat:
            by_cat[cat] = obj

    out: dict[str, CategoryClassification] = {}
    for cat in batch:
        found = by_cat.get(cat)
        if found is None:
            # Model responded but skipped this one — treat as uncategorizable.
            out[cat] = CategoryClassification(
                category=cat, subject=SUBJECT_OTHER, sub_category="Miscellaneous"
            )
            continue
        subject = canonical_subject(str(found.get("subject", "")))
        sub_category = str(found.get("sub_category", "")).strip() or "Miscellaneous"
        out[cat] = CategoryClassification(
            category=cat, subject=subject, sub_category=sub_category
        )
    return out


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Classify Jeopardy categories into subject/sub-category via LLM"
    )
    parser.add_argument("source", help="Source .colpkg file (categories read from it)")
    parser.add_argument(
        "--taxonomy",
        default=TAXONOMY_PATH_DEFAULT,
        help=f"Taxonomy cache path (default: {TAXONOMY_PATH_DEFAULT})",
    )
    parser.add_argument(
        "--model",
        default=CLASSIFY_MODEL_DEFAULT,
        help=f"Model alias/ID (default: {CLASSIFY_MODEL_DEFAULT})",
    )
    parser.add_argument(
        "--batch-size", type=int, default=CLASSIFY_BATCH_SIZE, help="Categories/call"
    )
    parser.add_argument(
        "--workers", type=int, default=CLASSIFY_WORKERS, help="Concurrent CLI calls"
    )
    parser.add_argument(
        "--limit", type=int, help="Only classify the first N new categories (testing)"
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    taxonomy_path = Path(args.taxonomy)
    if not source_path.exists():
        logger.error(f"Source not found: {source_path}")
        sys.exit(1)

    claude_bin = get_claude_bin()
    logger.info(f"Using claude binary: {claude_bin}")

    with tempfile.TemporaryDirectory() as tmpdir:
        logger.info(f"Extracting categories from {source_path}")
        db_path = extract_colpkg(source_path, Path(tmpdir))
        all_categories = get_unique_categories(db_path)
    logger.info(f"Found {len(all_categories)} unique categories")

    taxonomy = load_taxonomy(taxonomy_path)
    logger.info(f"Loaded {len(taxonomy)} cached classifications")

    todo = [c for c in all_categories if c not in taxonomy]
    if args.limit:
        todo = todo[: args.limit]
    logger.info(f"Classifying {len(todo)} new categories")
    if not todo:
        logger.info("Nothing to do — taxonomy is up to date")
        return

    batches = [
        todo[i : i + args.batch_size] for i in range(0, len(todo), args.batch_size)
    ]
    logger.info(f"{len(batches)} batches of up to {args.batch_size}")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(classify_batch, b, args.model, claude_bin): b for b in batches
        }
        for fut in as_completed(futures):
            result = fut.result()
            taxonomy.update(result)
            done += 1
            save_taxonomy(taxonomy_path, taxonomy)  # incremental checkpoint
            logger.info(
                f"Batch {done}/{len(batches)} done "
                f"({len(taxonomy)}/{len(all_categories)} total classified)"
            )

    logger.info(f"✓ Wrote {len(taxonomy)} classifications to {taxonomy_path}")


if __name__ == "__main__":
    main()
