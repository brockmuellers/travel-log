import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo

# EXIF-style timestamp: "2024:07:27 07:38:52" (local time at photo location)
PHOTO_TIMESTAMP_FMT = "%Y:%m:%d %H:%M:%S"
# Filename timestamp e.g. "2024-08-04 17.02.35.jpg"
PHOTO_FILENAME_TIMESTAMP_FMT = "%Y-%m-%d %H.%M.%S"

# Global TimezoneFinder instance reused for all photos to avoid per-photo initialization cost.
_timezone_finder = TimezoneFinder()

def _timestamp_str_from_filename(filename: str | None) -> str | None:
    """
    Extract local timestamp from a filename like "2024-08-04 17.02.35.jpg".
    Returns a string in PHOTO_TIMESTAMP_FMT for use with _parse_photo_time, or None.
    """
    if not filename:
        return None
    base = Path(filename).stem  # e.g. "2024-08-04 17.02.35"
    try:
        dt = datetime.strptime(base, PHOTO_FILENAME_TIMESTAMP_FMT)
        return dt.strftime(PHOTO_TIMESTAMP_FMT)
    except ValueError:
        return None


def _parse_photo_time(
    local_ts_str: str | None, lat: float | None, lon: float | None
) -> tuple[datetime | None, datetime | None, str | None]:
    """
    Parse EXIF-style local timestamp and resolve timezone at (lat, lon).

    Returns (time_taken_utc, time_taken_local, time_taken_local_tz) where:
      - time_taken_utc: timezone-aware datetime in UTC (for time_taken column)
      - time_taken_local: naive datetime of the original local time (for time_taken_local column)
      - time_taken_local_tz: IANA timezone name e.g. "America/Los_Angeles" (for time_taken_local_tz column)
    On failure returns (None, None, None).
    """
    if not local_ts_str or lat is None or lon is None:
        return (None, None, None)
    try:
        dt_naive = datetime.strptime(local_ts_str.strip(), PHOTO_TIMESTAMP_FMT)
    except ValueError:
        return (None, None, None)
    tz_name = _timezone_finder.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        return (None, None, None)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return (None, None, None)
    utc_dt = dt_naive.replace(tzinfo=tz).astimezone(timezone.utc)
    assert utc_dt.tzinfo is timezone.utc, "must return UTC timezone-aware datetime"
    return (utc_dt, dt_naive, tz_name)


def connect_to_database(db_params: dict[str, Any]) -> psycopg2.extensions.connection | None:
    """ Connect to the PostgreSQL database server and return a connection object. """
    conn = None
    try:
        print('Connecting to the PostgreSQL database...')
        conn = psycopg2.connect(**db_params)
        print('Connection successful.')
        return conn
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error connecting to the database: {error}")
        if conn:
            conn.close()
        return None

def _latest_jsonl_photos_files(paths: list[Path]) -> list[Path]:
    """
    From jsonl paths like captions_2024-07_2026-02-25.jsonl, keep only the latest
    file per content year-month (by the file date suffix). Returns a sorted list.
    Paths not matching captions_YYYY-MM_YYYY-MM-DD.jsonl are ignored.
    """
    latest_per_ym: dict[str, Path] = {}
    for path in paths:
        stem = path.stem  # e.g. "captions_2024-07_2026-02-25"
        parts = stem.split("_")
        if len(parts) != 3 or parts[0] != "captions":
            continue
        content_ym, file_date = parts[1], parts[2]
        if len(content_ym) != 7 or len(file_date) != 10:  # YYYY-MM, YYYY-MM-DD
            continue
        if content_ym not in latest_per_ym or file_date > latest_per_ym[content_ym].stem.split("_")[2]:
            latest_per_ym[content_ym] = path
    return sorted(latest_per_ym.values(), key=lambda p: p.name)

def run_photos_etl(conn: psycopg2.extensions.connection, photos_dir: str | Path) -> None:
    """
    Populate the photos table from JSONL files in photos_dir.
    Each line is a JSON object with filename, caption, timestamp, and location.
    Only the most recent file per year-month is processed (e.g. captions_2024-07_2026-02-28.jsonl
    is used and captions_2024-07_2026-02-25.jsonl is ignored).
    """
    photos_dir = Path(photos_dir)
    if not photos_dir.is_dir():
        raise FileNotFoundError(f"Photos directory not found: {photos_dir}")

    jsonl_files = _latest_jsonl_photos_files(list(photos_dir.glob("*.jsonl")))
    if not jsonl_files:
        print(f"No .jsonl files found in {photos_dir} (or none matching captions_YYYY-MM_YYYY-MM-DD.jsonl)")
        return

    # -------------------------------------------------------------------------
    # TODO: Waypoint timestamps (start_time / end_time) are not trustworthy.
    #       Prefer exploring assignment by location (e.g. nearest waypoint by
    #       distance, or point-in-polygon if we had regions) instead of time
    #       windows. This time-based match is a stopgap.
    # -------------------------------------------------------------------------
    cur = conn.cursor()
    cur.execute("SELECT id, start_time, end_time FROM waypoints ORDER BY start_time NULLS LAST")
    waypoint_windows = cur.fetchall()  # list of (id, start_time, end_time)
    if not waypoint_windows:
        print("ERROR: No waypoints found. Populate waypoints before running photos ETL.", file=sys.stderr)
        raise RuntimeError("waypoints table is empty")

    def waypoint_id_for_time(t: datetime | None) -> int | None:
        if t is None:
            return None
        for i, (wp_id, start, end) in enumerate(waypoint_windows):
            if start is None:
                continue
            if t < start:
                continue
            if end is not None:
                if t < end:
                    return wp_id
                continue
            # end is None (last waypoint of a trip): only match if no later waypoint has start <= t
            next_start = waypoint_windows[i + 1][1] if i + 1 < len(waypoint_windows) else None
            if next_start is None or t < next_start:
                return wp_id
        return None

    inserted_count = 0
    for path in sorted(jsonl_files):
        print(f"Processing {path}...")
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Skipping invalid JSON in {path}: {e}")
                    continue
                filename = data.get("filename")
                caption = data.get("caption")
                location = data.get("location") or {}
                # location_metadata stores the full "location" object from the input
                location_metadata = json.dumps(location) if location else None
                lat = location.get("latitude")
                lon = location.get("longitude")
                # Only set geography point when we have valid lat/lon
                if lat is not None and lon is not None:
                    location_wkt = f"POINT({lon} {lat})"
                else:
                    location_wkt = None
                # Timestamp is in local time at photo location; resolve timezone from lat/lon.
                # Fall back to parsing filename (e.g. "2024-08-04 17.02.35.jpg") when missing.
                ts_str = data.get("timestamp") or _timestamp_str_from_filename(filename)
                time_taken, time_taken_local, time_taken_local_tz = _parse_photo_time(
                    ts_str, lat, lon
                )
                waypoint_id = waypoint_id_for_time(time_taken)
                if location_wkt:
                    cur.execute("""
                        INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromText(%s), 4326), %s::jsonb, %s)
                    """, (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location_wkt, location_metadata, None))
                else:
                    cur.execute("""
                        INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
                    """, (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location_metadata, None))
                inserted_count += 1

    if inserted_count == 0:
        print("No photo records to insert.")
        return

    conn.commit()
    print(f"Inserted {inserted_count} photo(s).")


if __name__ == "__main__":
    load_dotenv()

    db_params = {
        "host": os.getenv("DATABASE_HOST"),
        "database": os.getenv("DATABASE_NAME"),
        "user": os.getenv("DATABASE_USER"),
        "password": os.getenv("DATABASE_PASSWORD"),
        "port": os.getenv("DATABASE_PORT")
    }
    connection = connect_to_database(db_params)
    if connection is None:
        sys.exit(1)

    # Populate photos with descriptions
    photos_dir = os.path.join(os.getenv("INTERIM_DATA_DIR"), "photos")
    print(f"Populating photos from {photos_dir}...")
    try:
        run_photos_etl(connection, photos_dir)
        print("Success!")
    except Exception:
        connection.close()
        print("Failed to process")
        raise

    connection.close()
