#!/usr/bin/env python3
"""Tune deck options: new-cards-before-reviews, and learn/relearn step timing.

study_optimizer.py packs each day's up-to-5-clue category into consecutive
`due` positions, but Anki's default deck option ("New/review order" = mix with
reviews) interleaves new and review cards freely — a due review card can land
in the middle of a 5-card group regardless of `due` order. This sets the
"New/review order" (and the equivalent setting for interday learning/
relearning cards) to "before reviews" for every deck options group in the
collection, so new cards — and therefore whole category groups — are always
shown before that day's reviews.

It also sets the learn/relearn step timings (`--learn-steps`/`--relearn-steps`,
in minutes) — these don't affect grouping, but live in the same protobuf blob
and are convenient to tune alongside it.

This is a one-time setting, not part of the regular refresh pipeline; re-running
it is harmless (idempotent). Changing step timings only affects how long a
step takes the next time a card reaches it — it does not reset any card's
current progress (interval, due date, lapses, review history).

What the review-mix change does NOT fix: a card you mark Again/Hard earlier in
the SAME session re-enters the queue at its learning-step timer regardless of
this setting — that's Anki's short-term relearning behavior, working as
intended, and isn't controlled by deck options at all. Only cross-session
reviews and multi-day relearning are deferred by this change.

Usage:
  python configure_deck_options.py [--db PATH] [--dry-run] [--no-backup]
                                    [--learn-steps M [M ...]]
                                    [--relearn-steps M [M ...]]
"""

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from jeopardy_consts import (
    ANKI_COLLECTION_PATH,
    DECKCONFIG_FIELD_INTERDAY_LEARNING_MIX,
    DECKCONFIG_FIELD_LEARN_STEPS,
    DECKCONFIG_FIELD_NEW_CARD_GATHER_PRIORITY,
    DECKCONFIG_FIELD_NEW_CARD_SORT_ORDER,
    DECKCONFIG_FIELD_NEW_MIX,
    DECKCONFIG_FIELD_RELEARN_STEPS,
    DECKCONFIG_LEARN_STEPS_MINUTES,
    DECKCONFIG_RELEARN_STEPS_MINUTES,
    DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS,
    DECKCONFIG_SAFE_GATHER_PRIORITIES,
    DECKCONFIG_SAFE_SORT_ORDERS,
    USN_PENDING,
)
from jeopardy_db_helpers import (
    connect_anki,
    encode_packed_floats,
    protobuf_get_packed_float_field,
    protobuf_get_varint_field,
    protobuf_replace_fields,
    protobuf_set_varint_field,
    require_anki_closed,
)

logger = logging.getLogger(__name__)

_REVIEW_MIX_NAMES: dict[int | None, str] = {
    None: "mix with reviews (default)",
    0: "mix with reviews",
    1: "after reviews",
    2: "before reviews",
}


def review_mix_name(value: int | None) -> str:
    """Human-readable label for a ReviewMix enum value (or None = unset/default)."""
    return _REVIEW_MIX_NAMES.get(value, f"unknown ({value})")


def update_review_mix(config: bytes, *, dry_run: bool) -> tuple[bytes, bool]:
    """Check/report one deck_config row's ordering settings; fix the mix fields.

    Args:
        config: The deck_config row's serialized `Config` protobuf blob
        dry_run: If True, report only — never modify `config`

    Returns:
        (possibly-updated config bytes, whether a change was [or would be] made)
    """
    gather_priority = protobuf_get_varint_field(
        config, DECKCONFIG_FIELD_NEW_CARD_GATHER_PRIORITY
    )
    sort_order = protobuf_get_varint_field(config, DECKCONFIG_FIELD_NEW_CARD_SORT_ORDER)
    new_mix = protobuf_get_varint_field(config, DECKCONFIG_FIELD_NEW_MIX)
    interday_mix = protobuf_get_varint_field(
        config, DECKCONFIG_FIELD_INTERDAY_LEARNING_MIX
    )

    logger.info("  new card gather priority: %s", gather_priority)
    logger.info("  new card sort order:      %s", sort_order)
    logger.info("  new/review order:         %s", review_mix_name(new_mix))
    logger.info("  interday learning order:  %s", review_mix_name(interday_mix))

    if (gather_priority or 0) not in DECKCONFIG_SAFE_GATHER_PRIORITIES:
        logger.warning(
            "  ! new card gather priority is %s, not Deck/LowestPosition — "
            "the day-category due order study_optimizer.py writes will NOT "
            "be respected. Fix this in Anki: Deck Options -> Display Order.",
            gather_priority,
        )
    if (sort_order or 0) not in DECKCONFIG_SAFE_SORT_ORDERS:
        logger.warning(
            "  ! new card sort order is %s, not Template/NoSort — new cards "
            "may be reshuffled after gathering. Fix this in Anki: "
            "Deck Options -> Display Order.",
            sort_order,
        )

    changed = (new_mix or 0) != DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS or (
        interday_mix or 0
    ) != DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS
    if not changed:
        logger.info("  already set correctly — nothing to do")
        return config, False

    if dry_run:
        logger.info(
            "  would set new/review order and interday order to 'before reviews'"
        )
        return config, True

    config = protobuf_set_varint_field(
        config, DECKCONFIG_FIELD_NEW_MIX, DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS
    )
    config = protobuf_set_varint_field(
        config,
        DECKCONFIG_FIELD_INTERDAY_LEARNING_MIX,
        DECKCONFIG_REVIEW_MIX_BEFORE_REVIEWS,
    )
    logger.info("  -> set new/review order and interday order to 'before reviews'")
    return config, True


def update_learning_steps(
    config: bytes,
    learn_steps: list[float],
    relearn_steps: list[float],
    *,
    dry_run: bool,
) -> tuple[bytes, bool]:
    """Check/report one deck_config row's learn/relearn steps; fix if different.

    Args:
        config: The deck_config row's serialized `Config` protobuf blob
        learn_steps: Desired learning steps in minutes (new cards)
        relearn_steps: Desired relearning steps in minutes (lapsed cards)
        dry_run: If True, report only — never modify `config`

    Returns:
        (possibly-updated config bytes, whether a change was [or would be] made)
    """
    current_learn = (
        protobuf_get_packed_float_field(config, DECKCONFIG_FIELD_LEARN_STEPS) or []
    )
    current_relearn = (
        protobuf_get_packed_float_field(config, DECKCONFIG_FIELD_RELEARN_STEPS) or []
    )
    logger.info("  learn steps (minutes):    %s", current_learn)
    logger.info("  relearn steps (minutes):  %s", current_relearn)

    changed = current_learn != learn_steps or current_relearn != relearn_steps
    if not changed:
        logger.info("  learning steps already match — nothing to do")
        return config, False

    if dry_run:
        logger.info(
            "  would set learn steps -> %s, relearn steps -> %s",
            learn_steps,
            relearn_steps,
        )
        return config, True

    config = protobuf_replace_fields(
        config,
        {
            DECKCONFIG_FIELD_LEARN_STEPS: encode_packed_floats(learn_steps),
            DECKCONFIG_FIELD_RELEARN_STEPS: encode_packed_floats(relearn_steps),
        },
    )
    logger.info(
        "  -> set learn steps to %s, relearn steps to %s", learn_steps, relearn_steps
    )
    return config, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=ANKI_COLLECTION_PATH, help="collection.anki2 path"
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--no-backup", action="store_true", help="skip the .bak copy")
    parser.add_argument(
        "--learn-steps",
        nargs="+",
        type=float,
        default=list(DECKCONFIG_LEARN_STEPS_MINUTES),
        metavar="M",
        help="Learning steps in minutes for new cards (default: %(default)s)",
    )
    parser.add_argument(
        "--relearn-steps",
        nargs="+",
        type=float,
        default=list(DECKCONFIG_RELEARN_STEPS_MINUTES),
        metavar="M",
        help="Relearning steps in minutes for lapsed cards (default: %(default)s)",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)
    require_anki_closed(db_path)

    conn = connect_anki(db_path)
    rows = conn.execute("SELECT id, name, config FROM deck_config").fetchall()
    logger.info("%d deck options group(s) found", len(rows))

    updates: list[tuple[bytes, int, int]] = []
    for deck_config_id, name, config in rows:
        logger.info("--- deck options group: %r (id=%d) ---", name, deck_config_id)
        config, mix_changed = update_review_mix(config, dry_run=args.dry_run)
        config, steps_changed = update_learning_steps(
            config, args.learn_steps, args.relearn_steps, dry_run=args.dry_run
        )
        if (mix_changed or steps_changed) and not args.dry_run:
            updates.append((config, int(time.time()), deck_config_id))

    if args.dry_run:
        logger.info("Dry run — no changes written.")
        conn.close()
        return

    if not updates:
        logger.info("Nothing to change.")
        conn.close()
        return

    if not args.no_backup:
        backup = db_path.with_name(db_path.name + ".bak")
        shutil.copy2(db_path, backup)
        logger.info("Backed up to %s", backup)

    with conn:
        conn.executemany(
            "UPDATE deck_config SET config = ?, mtime_secs = ?, usn = ? WHERE id = ?",
            [(cfg, mtime, USN_PENDING, dcid) for cfg, mtime, dcid in updates],
        )
        conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))
    conn.close()

    logger.info("✓ Updated %d deck options group(s)", len(updates))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
