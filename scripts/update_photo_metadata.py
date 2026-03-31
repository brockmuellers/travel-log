"""
Re-read EXIF metadata from the original image files and update all JSONL caption
files in-place. Captions are preserved unchanged; only `timestamp` and `location`
fields are refreshed.

Useful after fixing metadata extraction bugs (e.g. preferring DateTimeOriginal over
DateTime) without having to re-run the expensive Ollama caption generation.

For each JSONL file found in $INTERIM_DATA_DIR/photos/, a new file with today's date
suffix is written. populate_photos.py will automatically use the latest file per
year-month, so the old files can be left in place or deleted manually.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv

from lib.photo_metadata import get_image_metadata


def update_metadata_in_file(jsonl_path: Path, photos_dir: str) -> None:
    updated_lines = []
    changed = 0

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            filename = entry.get("filename", "")
            image_path = os.path.join(photos_dir, filename)

            if not os.path.exists(image_path):
                print(f"  WARNING: image not found, keeping existing metadata: {filename}")
                updated_lines.append(entry)
                continue

            fresh = get_image_metadata(image_path)
            if (
                entry.get("timestamp") != fresh["timestamp"]
                or entry.get("location") != fresh["location"]
            ):
                entry["timestamp"] = fresh["timestamp"]
                entry["location"] = fresh["location"]
                changed += 1

            updated_lines.append(entry)

    if changed == 0:
        print(f"  No changes.")
        return

    # Write a new file with today's date suffix so populate_photos.py picks it up.
    # File naming mirrors describe_photos.py: captions_YYYY-MM_YYYY-MM-DD.jsonl
    stem = jsonl_path.stem  # e.g. "captions_2024-08_2026-02-25"
    parts = stem.split("_")
    year_month = parts[1]  # e.g. "2024-08"
    today = datetime.now().strftime("%Y-%m-%d")
    new_filename = f"captions_{year_month}_{today}.jsonl"
    new_path = jsonl_path.parent / new_filename

    with open(new_path, "w") as f:
        for entry in updated_lines:
            f.write(json.dumps(entry) + "\n")

    print(f"  Updated {changed} record(s) → {new_path.name}")


@click.command()
def run() -> None:
    """
    Refresh EXIF metadata (timestamp, location) in all caption JSONL files.
    Captions are preserved. A new dated file is written for each changed JSONL.
    """
    load_dotenv()

    interim_dir = os.getenv("INTERIM_DATA_DIR")
    private_dir = os.getenv("PRIVATE_DATA_DIR")
    if not interim_dir or not private_dir:
        raise SystemExit("Error: INTERIM_DATA_DIR and PRIVATE_DATA_DIR must be set.")

    photos_output_dir = Path(interim_dir) / "photos"
    photos_source_dir = os.path.join(private_dir, "photos")

    jsonl_files = sorted(photos_output_dir.glob("captions_*.jsonl"))
    if not jsonl_files:
        raise SystemExit(f"No caption JSONL files found in {photos_output_dir}")

    for jsonl_path in jsonl_files:
        print(f"Processing {jsonl_path.name}...")
        update_metadata_in_file(jsonl_path, photos_source_dir)


if __name__ == "__main__":
    run()
