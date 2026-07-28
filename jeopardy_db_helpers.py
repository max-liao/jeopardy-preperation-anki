"""Helpers for working with Anki .colpkg files and SQLite databases."""

import hashlib
import logging
import re
import shutil
import sqlite3
import zipfile
from pathlib import Path

import zstandard

logger = logging.getLogger(__name__)

# Matches HTML tags and [sound:...] media references for checksum stripping.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")


def extract_colpkg(src: Path, tmp_dir: Path) -> Path:
    """Extract and decompress .colpkg file to SQLite database.

    Args:
        src: Path to source .colpkg file
        tmp_dir: Temporary directory to extract into

    Returns:
        Path to decompressed collection.anki2 database
    """
    extract_dir = tmp_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)

    # Unzip .colpkg
    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(extract_dir)

    # Decompress collection.anki21b
    compressed_path = extract_dir / "collection.anki21b"
    if not compressed_path.exists():
        raise FileNotFoundError("collection.anki21b not found in .colpkg")

    decompressed_path = extract_dir / "collection.anki2"
    dctx = zstandard.ZstdDecompressor()
    with open(compressed_path, "rb") as f_in:
        with open(decompressed_path, "wb") as f_out:
            dctx.copy_stream(f_in, f_out)

    logger.debug(f"Extracted {src} to {extract_dir}")
    return decompressed_path


def repack_colpkg(db_path: Path, extract_dir: Path, output: Path) -> None:
    """Recompress and repackage .colpkg from modified database.

    Args:
        db_path: Path to modified collection.anki2 database
        extract_dir: Extraction directory (contains meta, media files, etc.)
        output: Output .colpkg file path
    """
    # Recompress to .anki21b
    compressed_path = extract_dir / "collection.anki21b"
    if compressed_path.exists():
        compressed_path.unlink()

    cctx = zstandard.ZstdCompressor(level=10)
    with open(db_path, "rb") as f_in:
        with open(compressed_path, "wb") as f_out:
            cctx.copy_stream(f_in, f_out)

    # Repackage as .colpkg ZIP
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add meta
        meta_path = extract_dir / "meta"
        if meta_path.exists():
            zf.write(meta_path, arcname="meta")

        # Add collection.anki21b
        zf.write(compressed_path, arcname="collection.anki21b")

        # Add media manifest
        media_path = extract_dir / "media"
        if media_path.exists():
            zf.write(media_path, arcname="media")

        # Add numbered media files (0, 1, 2, ...)
        for item in extract_dir.iterdir():
            if item.name.isdigit():
                zf.write(item, arcname=item.name)

    logger.debug(f"Repacked to {output}")


def _unicase(a: str, b: str) -> int:
    """Case-insensitive comparison approximating Anki's `unicase` collation."""
    af, bf = a.casefold(), b.casefold()
    if af < bf:
        return -1
    if af > bf:
        return 1
    return 0


def connect_anki(db_path: Path) -> sqlite3.Connection:
    """Open an Anki collection DB with the custom `unicase` collation registered.

    Anki declares `COLLATE unicase` on several name/tag columns; a bare sqlite3
    connection lacks it and fails on writes/queries that touch those columns.
    The affected tables use integer primary keys, so the exact collation behavior
    does not affect stored structure — a casefold-based comparison is sufficient.

    Args:
        db_path: Path to a decompressed collection.anki2

    Returns:
        An open connection with `unicase` registered
    """
    conn = sqlite3.connect(db_path)
    conn.create_collation("unicase", _unicase)
    return conn


def require_anki_closed(db_path: Path) -> None:
    """Exit if Anki still holds the collection open.

    Anki keeps a non-empty write-ahead log while running; writing underneath it
    corrupts the collection. Every script that mutates the live DB calls this
    first.

    Args:
        db_path: Path to the live collection.anki2

    Raises:
        SystemExit: If a non-empty WAL file is present
    """
    wal_path = db_path.with_name(db_path.name + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        logger.error(
            "Anki appears to be running (WAL present: %s). Close Anki first.", wal_path
        )
        raise SystemExit(1)


def get_deck_id(conn: sqlite3.Connection, deck_name: str = "Jeopardy") -> int:
    """Get the deck ID for a named deck from the collection.

    Modern Anki collections (schema v18+) store decks in a dedicated `decks`
    table rather than the `col.decks` JSON blob. This reads the `decks` table,
    preferring an exact name match for `deck_name`, then a case-insensitive
    substring match, and finally the first non-default deck.

    Args:
        conn: SQLite connection to collection.anki2
        deck_name: Preferred deck name to match (default: "Jeopardy")

    Returns:
        Deck ID (integer)

    Raises:
        ValueError: If no usable deck is found
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM decks")
    decks: list[tuple[int, str]] = [(int(did), name) for did, name in cursor.fetchall()]
    if not decks:
        raise ValueError("No decks found in collection")

    # Exact name match
    for did, name in decks:
        if name == deck_name:
            return did

    # Case-insensitive substring match
    target = deck_name.lower()
    for did, name in decks:
        if target in name.lower():
            return did

    # First non-default deck (deck id 1 is Anki's built-in default)
    for did, name in decks:
        if did != 1:
            return did

    return decks[0][0]


def extract_field(flds: str, field_index: int) -> str:
    """Extract a field from pipe-delimited flds string.

    Args:
        flds: Field data delimited by \x1f
        field_index: 0-based field index

    Returns:
        Field value as string (empty string if not found)
    """
    if not flds:
        return ""
    fields = flds.split("\x1f")
    if field_index < len(fields):
        return fields[field_index]
    return ""


def pack_fields(field_values: list[str]) -> str:
    """Pack field values into \x1f-delimited string.

    Args:
        field_values: List of field values

    Returns:
        Packed field string
    """
    return "\x1f".join(field_values)


def strip_html_media(text: str) -> str:
    """Strip HTML tags and [sound:...] media refs (Anki's sort/checksum form).

    Args:
        text: Raw field text

    Returns:
        Text with HTML tags and sound references removed
    """
    return _SOUND_RE.sub("", _HTML_TAG_RE.sub("", text))


_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")
_PUNCT_ONLY_RE = re.compile(r"^[^\w]*$")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_answer(text: str) -> str:
    """Reduce an answer to a comparable topic key.

    The same subject is written many ways across 42 seasons — "talons" vs
    "talon", "porpoise (dolphin)" vs "porpoise", "the Nile" vs "Nile". Treating
    those as unrelated makes a live topic look retired, so recurrence and
    liveness are computed on this normalized form.

    Deliberately conservative: it strips parentheticals, a leading article, and
    surrounding punctuation only. Plural folding is NOT done here because
    blindly dropping a trailing "s" would corrupt words like "Paris"; see
    `merge_plural_variants`, which folds them only when both forms genuinely
    occur.

    Args:
        text: Raw answer field text

    Returns:
        Normalized key (empty string if nothing usable remains)
    """
    stripped = strip_html_media(text).casefold().strip()
    stripped = _PARENTHETICAL_RE.sub("", stripped)
    stripped = _LEADING_ARTICLE_RE.sub("", stripped)
    stripped = _WHITESPACE_RE.sub(" ", stripped).strip(" \"'.,!?;:")
    return "" if _PUNCT_ONLY_RE.match(stripped) else stripped


def is_malformed_answer(text: str) -> bool:
    """True when an answer carries no usable content (empty or punctuation only).

    Short answers are explicitly NOT malformed: "9", "H" and "4" are all real
    Jeopardy responses.
    """
    return not normalize_answer(text)


def merge_plural_variants(years_by_key: dict[str, list[int]]) -> dict[str, list[int]]:
    """Fold "<key>s" into "<key>" when BOTH spellings occur in the corpus.

    Requiring both forms to be present is what makes this safe: "talon" and
    "talons" both appear so they merge, while "paris" never merges into a
    "pari" that nobody ever answered.

    Args:
        years_by_key: normalized answer -> air years it appeared in

    Returns:
        A new mapping with plural forms folded into their singular
    """
    merged: dict[str, list[int]] = {k: list(v) for k, v in years_by_key.items()}
    for key in list(merged):
        if key.endswith("s") and len(key) > 3:
            singular = key[:-1]
            if singular in merged:
                merged[singular].extend(merged.pop(key))
    return merged


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a base-128 varint from `data` at `pos`. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _encode_varint(value: int) -> bytes:
    """Encode a non-negative int as a base-128 varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def protobuf_prepend_to_field1(config: bytes, prefix: str) -> bytes:
    """Prepend `prefix` to the string in protobuf field #1, preserving all else.

    Anki's CardTemplateConfig stores the front template (q_format) as protobuf
    field 1 (length-delimited string). This walks the top-level message, prepends
    `prefix` to field 1's value, and re-serializes every field byte-for-byte
    otherwise. Avoids a protobuf dependency for a single, well-understood edit.

    Args:
        config: The serialized CardTemplateConfig blob
        prefix: Text to prepend to the front-template string

    Returns:
        The modified blob

    Raises:
        ValueError: On an unsupported wire type
    """
    out = bytearray()
    pos = 0
    n = len(config)
    while pos < n:
        tag, pos = _read_varint(config, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 2:  # length-delimited
            length, pos = _read_varint(config, pos)
            value = config[pos : pos + length]
            pos += length
            if field_num == 1:
                value = prefix.encode("utf-8") + value
            out += _encode_varint(tag)
            out += _encode_varint(len(value))
            out += value
        elif wire_type == 0:  # varint
            value_int, pos = _read_varint(config, pos)
            out += _encode_varint(tag)
            out += _encode_varint(value_int)
        elif wire_type == 5:  # 32-bit
            out += _encode_varint(tag)
            out += config[pos : pos + 4]
            pos += 4
        elif wire_type == 1:  # 64-bit
            out += _encode_varint(tag)
            out += config[pos : pos + 8]
            pos += 8
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
    return bytes(out)


def field_checksum(first_field: str) -> int:
    """Compute Anki's field checksum (csum) for a note's first field.

    Anki stores `csum = int(sha1(strip_html_media(field0))[:8], 16)` and uses
    it for duplicate detection on the first field.

    Args:
        first_field: The note's first field value (raw)

    Returns:
        Integer checksum
    """
    digest = hashlib.sha1(strip_html_media(first_field).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def get_next_id(conn: sqlite3.Connection) -> int:
    """Get the next available note ID.

    Args:
        conn: SQLite connection to collection.anki2

    Returns:
        Next ID to use
    """
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM notes")
    row = cursor.fetchone()
    max_id = row[0] if row[0] else 0
    return max_id + 1


def get_next_card_id(conn: sqlite3.Connection) -> int:
    """Get the next available card ID.

    Card IDs live in a separate id space from note IDs, so they must be
    derived from the cards table to guarantee uniqueness.

    Args:
        conn: SQLite connection to collection.anki2

    Returns:
        Next card ID to use
    """
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM cards")
    row = cursor.fetchone()
    max_id = row[0] if row[0] else 0
    return max_id + 1


def get_max_new_due(conn: sqlite3.Connection) -> int:
    """Get the maximum 'due' value for new cards.

    Args:
        conn: SQLite connection to collection.anki2

    Returns:
        Max due value, or 0 if no cards
    """
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(due) FROM cards WHERE type = 0")
    row = cursor.fetchone()
    max_due = row[0] if row[0] else 0
    return max_due


def strip_foreign_decks(conn: sqlite3.Connection, keep_deck_id: int) -> None:
    """Delete all decks except keep_deck_id and their cards, revlog, and orphaned notes.

    Args:
        conn: SQLite connection (unicase collation must be registered)
        keep_deck_id: The deck ID to preserve
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM cards WHERE did != ?", (keep_deck_id,))
    foreign_cids = [row[0] for row in cursor.fetchall()]
    if foreign_cids:
        cursor.executemany(
            "DELETE FROM revlog WHERE cid = ?", [(c,) for c in foreign_cids]
        )
        cursor.execute("DELETE FROM cards WHERE did != ?", (keep_deck_id,))
    cursor.execute("DELETE FROM notes WHERE id NOT IN (SELECT nid FROM cards)")
    cursor.execute("DELETE FROM decks WHERE id != ?", (keep_deck_id,))
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM notes")
    note_count = cursor.fetchone()[0]
    logger.info(f"Stripped foreign decks — {note_count} notes remain")


def rename_deck(conn: sqlite3.Connection, deck_id: int, new_name: str) -> None:
    """Rename a deck by ID.

    Args:
        conn: SQLite connection (unicase collation must be registered)
        deck_id: Deck to rename
        new_name: New deck name
    """
    conn.execute("UPDATE decks SET name = ? WHERE id = ?", (new_name, deck_id))
    conn.commit()
    logger.info(f"Renamed deck {deck_id} → '{new_name}'")


def pack_apkg(db_path: Path, output: Path) -> None:
    """Package a plain SQLite database as an importable .apkg file.

    The .apkg format (no 'meta' file, collection.anki2 entry) causes Anki to
    merge the package into the existing collection instead of replacing it.

    Args:
        db_path: Path to the plain (uncompressed) collection.anki2 database
        output: Output .apkg path
    """
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_path, arcname="collection.anki2")
        zf.writestr("media", "{}")
    logger.debug(f"Packed .apkg to {output}")


def cleanup_tmpdir(tmp_dir: Path) -> None:
    """Remove temporary directory.

    Args:
        tmp_dir: Path to temporary directory
    """
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.debug(f"Cleaned up {tmp_dir}")
