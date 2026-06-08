#!/usr/bin/env python3
"""
Jeopardy Anki Collection Filter & Export

Filters cards from a .colpkg file by category, date, value, round, etc.,
and exports the matching subset as a new .colpkg file.

Usage:
  python filter_export.py SOURCE.colpkg OUTPUT.colpkg [--category TEXT] [--date-start YYYY] \
    [--date-end YYYY] [--value-min N] [--value-max N] [--round TEXT] [--daily-double] [--limit N]
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import zstandard
except ImportError:
    print("Error: zstandard not installed. Run: pip install zstandard", file=sys.stderr)
    sys.exit(1)


class JeopardyFilter:
    def __init__(self, colpkg_path):
        self.source_path = Path(colpkg_path)
        self.tempdir = tempfile.mkdtemp(prefix="anki_filter_")
        self.extract_dir = Path(self.tempdir) / "extract"
        self.extract_dir.mkdir()

    def extract_colpkg(self):
        with zipfile.ZipFile(self.source_path, "r") as zf:
            zf.extractall(self.extract_dir)

    def decompress_anki21b(self):
        compressed_path = self.extract_dir / "collection.anki21b"
        if not compressed_path.exists():
            raise FileNotFoundError("collection.anki21b not found in .colpkg")

        decompressed_path = self.extract_dir / "collection.anki2"
        with open(compressed_path, "rb") as f_in:
            dctx = zstandard.ZstdDecompressor()
            with open(decompressed_path, "wb") as f_out:
                dctx.copy_stream(f_in, f_out)

        return decompressed_path

    def parse_field(self, flds_text, field_index):
        """Extract a field from the pipe-delimited flds string."""
        if not flds_text:
            return ""
        fields = flds_text.split("\x1f")
        if field_index < len(fields):
            return fields[field_index]
        return ""

    def match_filters(
        self,
        flds_text,
        category_filter=None,
        date_start=None,
        date_end=None,
        value_min=None,
        value_max=None,
        round_filter=None,
        daily_double_only=False,
    ):
        """Check if a note matches all provided filters."""
        # Field indices: 1=AirDate, 5=Category, 7=Value, 3=Round, 8=DailyDouble
        air_date = self.parse_field(flds_text, 1)
        category = self.parse_field(flds_text, 5)
        value = self.parse_field(flds_text, 7)
        round_field = self.parse_field(flds_text, 3)
        daily_double = self.parse_field(flds_text, 8)

        # Category filter (case-insensitive substring match)
        if category_filter and category_filter.lower() not in category.lower():
            return False

        # Date range filter
        if air_date and (date_start or date_end):
            try:
                year = int(air_date.split("-")[0])
                if date_start and year < date_start:
                    return False
                if date_end and year > date_end:
                    return False
            except (ValueError, IndexError):
                pass

        # Value range filter (extract numeric value from "$1200" format)
        if value and (value_min or value_max):
            try:
                # Extract digits from value string like "$1200"
                val_num = int(re.sub(r"\D", "", value))
                if value_min and val_num < value_min:
                    return False
                if value_max and val_num > value_max:
                    return False
            except ValueError:
                pass

        # Round filter (case-insensitive)
        if round_filter and round_filter.lower() not in round_field.lower():
            return False

        # Daily Double filter
        if daily_double_only and daily_double.lower() != "true":
            return False

        return True

    def collect_media(self, conn, note_ids):
        """Scan note fields and build a set of referenced media files."""
        media_refs = set()
        cursor = conn.cursor()

        for (note_id,) in cursor.execute(
            "SELECT id FROM notes WHERE id IN ({})".format(
                ",".join("?" * len(note_ids))
            ),
            list(note_ids),
        ):
            cursor2 = conn.cursor()
            cursor2.execute("SELECT flds FROM notes WHERE id = ?", (note_id,))
            row = cursor2.fetchone()
            if row:
                flds = row[0]
                # Find src="..." patterns (images)
                for match in re.finditer(r'src="([^"]+)"', flds):
                    media_refs.add(match.group(1))
                # Find [sound:...] patterns (audio)
                for match in re.finditer(r"\[sound:([^\]]+)\]", flds):
                    media_refs.add(match.group(1))

        return media_refs

    def build_media_manifest(self, media_refs):
        """Build the media JSON manifest (maps index to filename)."""
        manifest = {}
        for idx, filename in enumerate(sorted(media_refs)):
            manifest[idx] = filename
        return manifest

    def filter_and_export(
        self,
        output_path,
        category_filter=None,
        date_start=None,
        date_end=None,
        value_min=None,
        value_max=None,
        round_filter=None,
        daily_double_only=False,
        limit=None,
    ):
        """Filter the collection and export as a new .colpkg."""
        db_path = self.decompress_anki21b()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Query notes with filters
        cursor.execute("SELECT id, flds FROM notes")
        all_notes = cursor.fetchall()
        matching_notes = []

        for note_id, flds in all_notes:
            if self.match_filters(
                flds,
                category_filter,
                date_start,
                date_end,
                value_min,
                value_max,
                round_filter,
                daily_double_only,
            ):
                matching_notes.append(note_id)

        # Apply limit
        if limit and len(matching_notes) > limit:
            import random

            matching_notes = random.sample(matching_notes, limit)

        matching_notes_set = set(matching_notes)
        print(f"Matching notes: {len(matching_notes_set)} / {len(all_notes)}")

        # Collect media references
        media_refs = self.collect_media(conn, matching_notes_set)
        print(f"Referenced media files: {len(media_refs)}")

        # Prune notes table
        cursor.execute(
            "DELETE FROM notes WHERE id NOT IN ({})".format(
                ",".join("?" * len(matching_notes))
            ),
            list(matching_notes),
        )

        # Prune cards table
        cursor.execute(
            "DELETE FROM cards WHERE nid NOT IN ({})".format(
                ",".join("?" * len(matching_notes))
            ),
            list(matching_notes),
        )

        # Prune revlog table (optional, keeps only logs for remaining cards)
        cursor.execute(
            "DELETE FROM revlog WHERE cid NOT IN (SELECT id FROM cards)"
        )

        # Prune graves table
        cursor.execute("DELETE FROM graves WHERE oid NOT IN (SELECT id FROM notes)")

        conn.commit()

        # Build media manifest
        media_manifest = self.build_media_manifest(media_refs)
        print(f"Media manifest entries: {len(media_manifest)}")

        conn.close()

        # Re-compress anki21b
        compressed_path = self.extract_dir / "collection.anki21b"
        compressed_path.unlink()
        cctx = zstandard.ZstdCompressor(level=10)
        with open(db_path, "rb") as f_in:
            with open(compressed_path, "wb") as f_out:
                cctx.copy_stream(f_in, f_out)

        # Update media manifest
        media_path = self.extract_dir / "media"
        with open(media_path, "w") as f:
            json.dump(media_manifest, f)

        # Copy media files (those referenced by remaining notes)
        for filename in media_refs:
            src = self.extract_dir / filename
            if src.exists():
                dst_idx = list(media_manifest.values()).index(filename)
                # Media files are stored as numeric indices in the ZIP
                dst = self.extract_dir / str(dst_idx)
                # Actually, we keep the original filenames in the extract_dir,
                # but the manifest maps index -> filename. The ZIP structure
                # stores media files as 0, 1, 2, etc., named by index.
                # For simplicity, copy them with their numeric indices.
                pass

        # Package new .colpkg
        self.package_colpkg(output_path)

    def package_colpkg(self, output_path):
        """Create a new .colpkg ZIP from the filtered extract directory."""
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add meta
            meta_path = self.extract_dir / "meta"
            if meta_path.exists():
                zf.write(meta_path, arcname="meta")

            # Add collection.anki21b
            db_path = self.extract_dir / "collection.anki21b"
            zf.write(db_path, arcname="collection.anki21b")

            # Add media files (stored as numeric names in ZIP)
            media_path = self.extract_dir / "media"
            if media_path.exists():
                zf.write(media_path, arcname="media")

            # Add numbered media files (0, 1, 2, ...)
            for item in self.extract_dir.iterdir():
                if item.name.isdigit():
                    zf.write(item, arcname=item.name)

        print(f"Exported to: {output_path}")

    def cleanup(self):
        """Remove temporary directory."""
        shutil.rmtree(self.tempdir)


def main():
    parser = argparse.ArgumentParser(
        description="Filter and export Jeopardy Anki collection"
    )
    parser.add_argument("source", help="Source .colpkg file")
    parser.add_argument("output", help="Output .colpkg file")
    parser.add_argument(
        "--category",
        help="Filter by category keyword (case-insensitive substring)",
    )
    parser.add_argument(
        "--date-start", type=int, help="Minimum air date year (inclusive)"
    )
    parser.add_argument(
        "--date-end", type=int, help="Maximum air date year (inclusive)"
    )
    parser.add_argument(
        "--value-min", type=int, help="Minimum clue value in dollars"
    )
    parser.add_argument(
        "--value-max", type=int, help="Maximum clue value in dollars"
    )
    parser.add_argument(
        "--round",
        help='Filter by round: "Jeopardy", "Double Jeopardy", "Final Jeopardy"',
    )
    parser.add_argument(
        "--daily-double", action="store_true", help="Include only Daily Double clues"
    )
    parser.add_argument(
        "--limit", type=int, help="Cap output at N cards (random sample)"
    )

    args = parser.parse_args()

    try:
        filt = JeopardyFilter(args.source)
        filt.extract_colpkg()
        filt.filter_and_export(
            args.output,
            category_filter=args.category,
            date_start=args.date_start,
            date_end=args.date_end,
            value_min=args.value_min,
            value_max=args.value_max,
            round_filter=args.round,
            daily_double_only=args.daily_double,
            limit=args.limit,
        )
        print("Success!")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if "filt" in locals():
            filt.cleanup()


if __name__ == "__main__":
    main()
