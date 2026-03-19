"""Upload photos from the local data directory to Cloudflare R2.

Walks $PRIVATE_DATA_DIR/photos/ and uploads each YYYY/MM/*.jpg file to the
configured R2 bucket, preserving the YYYY/MM/filename.jpg key structure
(matching the `filename` column in the photos DB table).

Skips files that already exist in the bucket (by key) unless --force is set.

Required env vars (add to .env):
    R2_ACCOUNT_ID       — Cloudflare account ID
    R2_ACCESS_KEY_ID    — R2 API token access key
    R2_SECRET_ACCESS_KEY — R2 API token secret key
    R2_BUCKET_NAME      — Target bucket name
    PRIVATE_DATA_DIR    — Local data root (photos live under photos/)
"""

import os
import sys
from pathlib import Path

import boto3
import click
from dotenv import load_dotenv

load_dotenv()

PHOTOS_SUBDIR = "photos"


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


def collect_local_photos(photos_dir: Path) -> list[tuple[Path, str]]:
    """Return (local_path, r2_key) pairs for all JPGs under photos_dir.

    R2 keys match the DB filename format: YYYY/MM/filename.jpg
    """
    results = []
    for year_dir in sorted(photos_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for photo in sorted(month_dir.iterdir()):
                if photo.suffix.lower() in (".jpg", ".jpeg"):
                    key = f"{year_dir.name}/{month_dir.name}/{photo.name}"
                    results.append((photo, key))
    return results


@click.command()
@click.option("--force", is_flag=True, help="Re-upload files that already exist in R2.")
@click.option("--dry-run", is_flag=True, help="List what would be uploaded without uploading.")
def upload(force: bool, dry_run: bool) -> None:
    """Upload local photos to Cloudflare R2."""
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

    local_photos = collect_local_photos(photos_dir)
    click.echo(f"Found {len(local_photos)} local photos")

    if force:
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

    uploaded = 0
    errors = 0
    for path, key in to_upload:
        try:
            client.upload_file(
                str(path),
                bucket,
                key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            uploaded += 1
            if uploaded % 100 == 0:
                click.echo(f"  {uploaded}/{len(to_upload)}")
        except Exception as e:
            click.echo(f"  Error uploading {key}: {e}", err=True)
            errors += 1

    click.echo(f"Done: {uploaded} uploaded, {errors} errors")


if __name__ == "__main__":
    upload()
