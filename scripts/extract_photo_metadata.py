"""
Extract EXIF metadata from all screened photo directories and write one
metadata_YYYY-MM.jsonl file per directory to $INTERIM_DATA_DIR/photos/.

By default, skips directories that already have a metadata file (incremental).
Use --overwrite to re-extract all directories.

Output format (one JSON object per line):
  {"filename": "2024/07/photo.jpg", "timestamp": "2024:07:27 07:38:52", "location": {...}}
"""

import json
import os
from pathlib import Path

import click
from dotenv import load_dotenv

from lib.photo_metadata import get_image_metadata

VALID_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def has_not_screened_marker(directory: Path) -> bool:
    """Return True if a NOT_SCREENED marker file exists in the directory."""
    return (directory / "NOT_SCREENED").is_file()


def extract_directory(image_dir: Path, year_month: str, output_path: Path) -> None:
    """Extract metadata from all images in image_dir and write to output_path."""
    image_files = sorted(
        f for f in image_dir.iterdir() if f.suffix.lower() in VALID_EXTENSIONS
    )
    total = len(image_files)
    print(f"  Extracting metadata for {total} photos...")

    with open(output_path, "w", encoding="utf-8") as out:
        for i, image_path in enumerate(image_files):
            metadata = get_image_metadata(str(image_path))
            entry = {
                "filename": f"{year_month}/{image_path.name}",
                "timestamp": metadata["timestamp"],
                "location": metadata["location"],
            }
            out.write(json.dumps(entry) + "\n")
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{total}...")

    print(f"  Written to {output_path.name}")


@click.command()
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Re-extract metadata even for directories that already have a metadata file.",
)
def run(overwrite: bool) -> None:
    """
    Extract EXIF metadata from all screened photo directories.

    Processes every YYYY/MM subdirectory of PRIVATE_DATA_DIR/photos/ that does not
    contain a NOT_SCREENED marker. Skips directories that already have a metadata
    file unless --overwrite is passed.
    """
    load_dotenv()

    private_dir = os.getenv("PRIVATE_DATA_DIR")
    interim_dir = os.getenv("INTERIM_DATA_DIR")
    if not private_dir or not interim_dir:
        raise SystemExit("Error: PRIVATE_DATA_DIR and INTERIM_DATA_DIR must be set.")

    photos_source = Path(private_dir) / "photos"
    photos_output = Path(interim_dir) / "photos"
    photos_output.mkdir(parents=True, exist_ok=True)

    if not photos_source.is_dir():
        raise SystemExit(f"Error: photos directory not found: {photos_source}")

    # Discover YYYY/MM subdirectories
    dirs: list[tuple[str, Path]] = []
    for year_dir in sorted(photos_source.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            year_month = f"{year_dir.name}/{month_dir.name}"
            dirs.append((year_month, month_dir))

    if not dirs:
        raise SystemExit(f"No YYYY/MM subdirectories found in {photos_source}")

    processed = 0
    skipped_screened = 0
    skipped_exists = 0

    for year_month, image_dir in dirs:
        ym_slug = year_month.replace("/", "-")  # e.g. "2024-07"
        output_path = photos_output / f"metadata_{ym_slug}.jsonl"

        if has_not_screened_marker(image_dir):
            print(f"Skipping {year_month} (NOT_SCREENED)")
            skipped_screened += 1
            continue

        if not overwrite and output_path.exists():
            print(f"Skipping {year_month} (metadata file already exists)")
            skipped_exists += 1
            continue

        print(f"Processing {year_month}...")
        extract_directory(image_dir, year_month, output_path)
        processed += 1

    print(
        f"\nDone. Processed: {processed}, skipped (exists): {skipped_exists}, "
        f"skipped (not screened): {skipped_screened}"
    )


if __name__ == "__main__":
    run()
