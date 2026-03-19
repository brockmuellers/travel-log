"""Upload photos from the local data directory to Cloudflare R2.

Walks $PRIVATE_DATA_DIR/photos/ and uploads each YYYY/MM/*.jpg file to the
configured R2 bucket, preserving the YYYY/MM/filename.jpg key structure
(matching the `filename` column in the photos DB table).

Skips files that already exist in the bucket (by key) unless --overwrite is set.
Skips directories containing a NOT_SCREENED marker file.
Strips GPS/location EXIF data before uploading (orientation is preserved).

Required env vars (add to .env):
    R2_ACCOUNT_ID       — Cloudflare account ID
    R2_ACCESS_KEY_ID    — R2 API token access key
    R2_SECRET_ACCESS_KEY — R2 API token secret key
    R2_BUCKET_NAME      — Target bucket name
    PRIVATE_DATA_DIR    — Local data root (photos live under photos/)
"""

import os
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import boto3
import click
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

PHOTOS_SUBDIR = "photos"

# EXIF tag IDs to preserve (everything else is stripped).
EXIF_TAG_ORIENTATION = 0x0112

# GPS IFD pointer — must be removed to strip location data.
EXIF_TAG_GPS_IFD = 0x8825


def strip_location_exif(path: Path) -> BytesIO:
    """Return JPEG bytes with GPS/location EXIF stripped but orientation kept.

    Uses quality="keep" to preserve original DCT coefficients (no re-encoding loss).
    """
    img = Image.open(path)
    exif = img.getexif()

    # Remove GPS IFD
    if EXIF_TAG_GPS_IFD in exif:
        del exif[EXIF_TAG_GPS_IFD]

    buf = BytesIO()
    img.save(buf, format="JPEG", quality="keep", exif=exif.tobytes())
    buf.seek(0)
    return buf


def get_r2_client() -> boto3.client:
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def list_existing_keys(client: boto3.client, bucket: str) -> set[str]:
    """Return all object keys currently in the bucket."""
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


NOT_SCREENED_MARKER = "NOT_SCREENED"


def collect_local_photos(photos_dir: Path, year_month: str | None = None) -> list[tuple[Path, str]]:
    """Return (local_path, r2_key) pairs for all JPGs under photos_dir.

    R2 keys match the DB filename format: YYYY/MM/filename.jpg

    If year_month is given (e.g. "2024/07"), only that subdirectory is scanned.
    Directories containing a NOT_SCREENED marker file are skipped.
    """
    results = []

    if year_month:
        # Single directory mode
        target = photos_dir / year_month
        if not target.is_dir():
            click.echo(f"Error: {target} is not a directory", err=True)
            sys.exit(1)
        if (target / NOT_SCREENED_MARKER).exists():
            click.echo(f"Skipping {year_month}: NOT_SCREENED marker present")
            return results
        for photo in sorted(target.iterdir()):
            if photo.suffix.lower() in (".jpg", ".jpeg"):
                key = f"{year_month}/{photo.name}"
                results.append((photo, key))
        return results

    # Walk all YYYY/MM directories
    for year_dir in sorted(photos_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            if (month_dir / NOT_SCREENED_MARKER).exists():
                click.echo(f"Skipping {year_dir.name}/{month_dir.name}: NOT_SCREENED marker present")
                continue
            for photo in sorted(month_dir.iterdir()):
                if photo.suffix.lower() in (".jpg", ".jpeg"):
                    key = f"{year_dir.name}/{month_dir.name}/{photo.name}"
                    results.append((photo, key))
    return results


@click.command()
@click.option("--year-month", default=None, help="Upload only this subdirectory, e.g. '2024/07'.")
@click.option("--overwrite", is_flag=True, help="Re-upload files that already exist in R2.")
@click.option("--dry-run", is_flag=True, help="List what would be uploaded without uploading.")
def upload(year_month: str | None, overwrite: bool, dry_run: bool) -> None:
    """Upload local photos to Cloudflare R2.

    Optionally pass YEAR_MONTH (e.g. '2024/07') to upload only that subdirectory.
    """
    data_dir = os.environ.get("PRIVATE_DATA_DIR")
    if not data_dir:
        click.echo("Error: PRIVATE_DATA_DIR not set", err=True)
        sys.exit(1)

    photos_dir = Path(data_dir) / PHOTOS_SUBDIR
    if not photos_dir.is_dir():
        click.echo(f"Error: {photos_dir} is not a directory", err=True)
        sys.exit(1)

    bucket = os.environ["R2_BUCKET_NAME"]
    client = get_r2_client()

    local_photos = collect_local_photos(photos_dir, year_month)
    click.echo(f"Found {len(local_photos)} local photos")

    if overwrite:
        existing_keys: set[str] = set()
    else:
        click.echo("Listing existing R2 objects...")
        existing_keys = list_existing_keys(client, bucket)
        click.echo(f"Found {len(existing_keys)} existing objects in R2")

    to_upload = [
        (path, key) for path, key in local_photos if key not in existing_keys
    ]
    skipped = len(local_photos) - len(to_upload)
    if skipped:
        click.echo(f"Skipping {skipped} already-uploaded photos")

    if not to_upload:
        click.echo("Nothing to upload.")
        return

    click.echo(f"{'Would upload' if dry_run else 'Uploading'} {len(to_upload)} photos")

    if dry_run:
        for _, key in to_upload[:20]:
            click.echo(f"  {key}")
        if len(to_upload) > 20:
            click.echo(f"  ... and {len(to_upload) - 20} more")
        return

    # Group by subdirectory for per-directory progress bars.
    by_subdir: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for path, key in to_upload:
        subdir = "/".join(key.split("/")[:2])  # e.g. "2024/12"
        by_subdir[subdir].append((path, key))

    uploaded = 0
    errors = 0
    for subdir in sorted(by_subdir):
        items = by_subdir[subdir]
        with click.progressbar(items, label=subdir, show_pos=True) as bar:
            for path, key in bar:
                try:
                    buf = strip_location_exif(path)
                    client.upload_fileobj(
                        buf,
                        bucket,
                        key,
                        ExtraArgs={"ContentType": "image/jpeg"},
                    )
                    uploaded += 1
                except Exception as e:
                    click.echo(f"\n  Error uploading {key}: {e}", err=True)
                    errors += 1

    click.echo(f"Done: {uploaded} uploaded, {errors} errors")


if __name__ == "__main__":
    upload()
