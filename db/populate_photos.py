import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv
from lib.gps_utils import haversine_distance
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo

# EXIF-style timestamp: "2024:07:27 07:38:52" (local time at photo location)
PHOTO_TIMESTAMP_FMT = "%Y:%m:%d %H:%M:%S"
# Filename timestamp e.g. "2024-08-04 17.02.35.jpg"
PHOTO_FILENAME_TIMESTAMP_FMT = "%Y-%m-%d %H.%M.%S"

PHOTO_WAYPOINT_DIST_THRESHOLD = 0.1

# Photos taken at or before each timestamp (matched against time_taken_local) are pinned
# to waypoint A on a transition day, regardless of GPS distance. Use this when you briefly
# left the area around A and returned before actually departing to B — set the end time to
# the last photo taken before your real departure.
# Format: list of "YYYY-MM-DD HH.MM.SS" end-time strings (local photo time).
# If this list grows unwieldy, move it to a JSON file in $PRIVATE_DATA_DIR.
TRANSITION_EXEMPTIONS: list[str] = [
    "2024-11-30 14.03.38",  # Prek Toal
    "2024-12-30 13.41.54", # Vientiane
]

# Global TimezoneFinder instance reused for all photos to avoid per-photo initialization cost.
_timezone_finder = TimezoneFinder()

logger = logging.getLogger(__name__)


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
    # TODO: Photos without GPS (lat/lon=None) end up with time_taken=None entirely,
    #       even when a valid timestamp can be parsed from the filename. This means
    #       they can't be assigned to waypoints and are invisible to the transition
    #       correction pass. Two possible fixes:
    #       1. Parse and store time_taken_local (the naive local time) even without
    #          GPS, leaving time_taken (UTC) and time_taken_local_tz as None.
    #       2. Infer the timezone from adjacent photos in the same JSONL file that
    #          DO have GPS — if surrounding photos share a timezone, use it for
    #          the GPS-less ones. This is more complex but would also produce a
    #          valid UTC time_taken, enabling waypoint assignment.
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


def connect_to_database(
    db_params: dict[str, Any],
) -> psycopg2.extensions.connection | None:
    """Connect to the PostgreSQL database server and return a connection object."""
    conn = None
    try:
        print("Connecting to the PostgreSQL database...")
        conn = psycopg2.connect(**db_params)
        print("Connection successful.")
        return conn
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error connecting to the database: {error}")
        if conn:
            conn.close()
        return None


def _latest_jsonl_captions_files(paths: list[Path]) -> list[Path]:
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
        if (
            content_ym not in latest_per_ym
            or file_date > latest_per_ym[content_ym].stem.split("_")[2]
        ):
            latest_per_ym[content_ym] = path
    return sorted(latest_per_ym.values(), key=lambda p: p.name)


def _load_captions(photos_dir: Path) -> dict[str, str]:
    """
    Load all caption JSONL files (latest per year-month) and return a mapping
    of filename → caption. Only the caption field is read; other fields are ignored.
    """
    captions: dict[str, str] = {}
    caption_files = _latest_jsonl_captions_files(list(photos_dir.glob("captions_*.jsonl")))
    for path in caption_files:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                filename = data.get("filename")
                caption = data.get("caption")
                if filename and caption:
                    captions[filename] = caption
    return captions


def _apply_transition_corrections(
    photos: list[dict[str, Any]],
    waypoints: list[dict[str, Any]],
) -> None:
    """
    Apply location-based tiebreaking for photos on waypoint transition days.

    The base time-window assignment is unreliable on transition days because waypoint
    start/end times are often midnight boundaries. This function re-examines photos
    whose local date matches a transition date using GPS proximity:

    For each consecutive waypoint pair (A→B) within a trip, photos on the transition
    date are walked chronologically. The first photo beyond 10% of the A→B distance
    from A triggers a reassignment: it and all subsequent photos that day go to B.
    This monotonic rule prevents back-and-forth assignments; a WARN is logged if a
    post-transition photo is near A.

    NOTE: Validate the monotonic rule and threshold against real-world data.

    TODO (big): Revisit multi-transition-day logic. When 3+ waypoints fall on the same
    calendar day within a trip, only the first and last waypoints are used — intermediate
    stops are ignored. This is a simplification that may produce incorrect results.

    TODO: Validate behavior for trans-continental flights where local time at the photo
    location may span different calendar dates than the waypoint's UTC transition date.
    """
    # Parse TRANSITION_EXEMPTIONS once. Each entry is an end-time; any photo whose
    # time_taken_local is at or before it is pinned to waypoint A and excluded from
    # both the transition search and the monotonic reassignment.
    parsed_exemption_ends: list[datetime] = [
        datetime.strptime(e, PHOTO_FILENAME_TIMESTAMP_FMT) for e in TRANSITION_EXEMPTIONS
    ]

    def _is_exempted(photo: dict[str, Any]) -> bool:
        t = photo["time_taken_local"]
        if t is None:
            return False
        # Match on date too so an exemption for 2024-11-30 doesn't accidentally
        # exempt photos from earlier dates that sort as "before" the end timestamp.
        return any(t.date() == end.date() and t <= end for end in parsed_exemption_ends)

    # Group waypoints by trip_id, sorted by start_time
    by_trip: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for wp in waypoints:
        by_trip[wp["trip_id"]].append(wp)

    # Build consecutive pairs (A→B) within each trip
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for trip_wps in by_trip.values():
        trip_wps.sort(
            key=lambda w: w["start_time"] or datetime.min.replace(tzinfo=timezone.utc)
        )
        for i in range(len(trip_wps) - 1):
            all_pairs.append((trip_wps[i], trip_wps[i + 1]))

    # Group pairs by (trip_id, transition_date) — the UTC calendar date of B's start_time
    pairs_by_trip_date: dict[tuple[Any, date], list[tuple]] = defaultdict(list)
    for wp_a, wp_b in all_pairs:
        if wp_b["start_time"] is None:
            continue
        transition_date = wp_b["start_time"].date()
        pairs_by_trip_date[(wp_a["trip_id"], transition_date)].append((wp_a, wp_b))

    warned_no_gps: set[str] = set()

    for (trip_id, transition_date), day_pairs in sorted(pairs_by_trip_date.items()):
        if len(day_pairs) > 1:
            logger.warning(
                "Multiple waypoint transitions on %s for trip %s (%d transitions). "
                "Only using first and last waypoint of the day for photo assignment.",
                transition_date,
                trip_id,
                len(day_pairs),
            )
            effective_pairs = [(day_pairs[0][0], day_pairs[-1][1])]
        else:
            effective_pairs = day_pairs

        for wp_a, wp_b in effective_pairs:
            if (
                wp_a["lat"] is None
                or wp_a["lon"] is None
                or wp_b["lat"] is None
                or wp_b["lon"] is None
            ):
                continue
            ab_dist = haversine_distance(wp_a["lat"], wp_a["lon"], wp_b["lat"], wp_b["lon"])
            if ab_dist == 0:
                continue
            threshold = ab_dist * PHOTO_WAYPOINT_DIST_THRESHOLD

            # Gather photos whose local date equals the transition date
            candidates: list[dict[str, Any]] = []
            for photo in photos:
                if photo["time_taken_local"] is None:
                    continue
                if photo["time_taken_local"].date() != transition_date:
                    continue
                if photo["lat"] is None or photo["lon"] is None:
                    filename = photo["filename"]
                    if filename not in warned_no_gps:
                        logger.warning(
                            "Photo %s has no GPS, skipping transition correction", filename
                        )
                        warned_no_gps.add(filename)
                    continue
                candidates.append(photo)

            if not candidates:
                continue

            candidates.sort(
                key=lambda p: p["time_taken"] or datetime.min.replace(tzinfo=timezone.utc)
            )

            # Find first non-exempted photo beyond threshold distance from A.
            # Exempted photos are skipped here so a temporary excursion away from A
            # doesn't prematurely trigger the transition.
            transition_idx = None
            for i, photo in enumerate(candidates):
                if _is_exempted(photo):
                    continue
                dist_to_a = haversine_distance(
                    photo["lat"], photo["lon"], wp_a["lat"], wp_a["lon"]
                )
                if dist_to_a >= threshold:
                    transition_idx = i
                    break

            if transition_idx is None:
                continue

            # Reassign from transition point onward to B (monotonic).
            # Exempted photos are skipped — they stay assigned to A.
            for photo in candidates[transition_idx:]:
                if _is_exempted(photo):
                    continue
                photo["waypoint_id"] = wp_b["id"]
                dist_to_a = haversine_distance(
                    photo["lat"], photo["lon"], wp_a["lat"], wp_a["lon"]
                )
                if dist_to_a < threshold:
                    logger.warning(
                        "Photo %s is near '%s' (%.1f km, threshold %.1f km) but was taken "
                        "after transition point toward '%s' — keeping assignment to '%s'",
                        photo["filename"],
                        wp_a["name"],
                        dist_to_a,
                        threshold,
                        wp_b["name"],
                        wp_b["name"],
                    )


def run_photos_etl(
    conn: psycopg2.extensions.connection, photos_dir: str | Path
) -> None:
    """
    Populate the photos table from metadata and caption JSONL files in photos_dir.

    Metadata files (metadata_YYYY-MM.jsonl) are the source of truth for which photos
    exist and their EXIF data (timestamp, location). Caption files (captions_*.jsonl,
    latest per year-month) are loaded as an optional lookup: photos without a caption
    entry are inserted with caption=NULL.

    Photos are first assigned to waypoints by time window (start_time <= photo < end_time).
    A transition correction pass then uses GPS proximity to fix misassignments on days
    where the waypoint boundary falls at midnight. See _apply_transition_corrections.
    """
    photos_dir = Path(photos_dir)
    if not photos_dir.is_dir():
        raise FileNotFoundError(f"Photos directory not found: {photos_dir}")

    metadata_files = sorted(photos_dir.glob("metadata_*.jsonl"))
    if not metadata_files:
        print(f"No metadata_*.jsonl files found in {photos_dir}")
        return

    captions = _load_captions(photos_dir)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, trip_id, name, start_time, end_time,
               ST_Y(location::geometry) AS lat,
               ST_X(location::geometry) AS lon
        FROM waypoints
        ORDER BY start_time NULLS LAST
        """
    )
    rows = cur.fetchall()
    if not rows:
        print(
            "ERROR: No waypoints found. Populate waypoints before running photos ETL.",
            file=sys.stderr,
        )
        raise RuntimeError("waypoints table is empty")

    # (id, start_time, end_time) tuples for time-window matching
    waypoint_windows = [(row[0], row[3], row[4]) for row in rows]

    # Full waypoint info dicts for transition correction
    waypoints_for_correction = [
        {
            "id": row[0],
            "trip_id": row[1],
            "name": row[2],
            "start_time": row[3],
            "end_time": row[4],
            "lat": row[5],
            "lon": row[6],
        }
        for row in rows
    ]

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
            next_start = (
                waypoint_windows[i + 1][1] if i + 1 < len(waypoint_windows) else None
            )
            if next_start is None or t < next_start:
                return wp_id
        return None

    # --- Step 1: Parse all photos and apply time-window assignment ---
    photos: list[dict[str, Any]] = []
    for path in metadata_files:
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
                caption = captions.get(filename) if filename else None
                location = data.get("location") or {}
                # location_metadata stores the full "location" object from the input
                location_metadata = json.dumps(location) if location else None
                lat = location.get("latitude")
                lon = location.get("longitude")
                # Only set geography point when we have valid lat/lon
                location_wkt = (
                    f"POINT({lon} {lat})" if lat is not None and lon is not None else None
                )
                # Timestamp is in local time at photo location; resolve timezone from lat/lon.
                # Fall back to parsing filename (e.g. "2024-08-04 17.02.35.jpg") when missing.
                ts_str = data.get("timestamp") or _timestamp_str_from_filename(filename)
                time_taken, time_taken_local, time_taken_local_tz = _parse_photo_time(
                    ts_str, lat, lon
                )

                waypoint_id = waypoint_id_for_time(time_taken)
                if waypoint_id is None and time_taken is not None:
                    logger.warning(
                        "Photo %s (taken %s UTC) falls in a waypoint-less time chunk, leaving unlinked",
                        filename,
                        time_taken,
                    )

                photos.append(
                    {
                        "filename": filename,
                        "caption": caption,
                        "time_taken": time_taken,
                        "time_taken_local": time_taken_local,
                        "time_taken_local_tz": time_taken_local_tz,
                        "lat": lat,
                        "lon": lon,
                        "location_wkt": location_wkt,
                        "location_metadata": location_metadata,
                        "waypoint_id": waypoint_id,
                    }
                )

    if not photos:
        print("No photo records to insert.")
        return

    # --- Step 2: Apply GPS-based transition corrections ---
    _apply_transition_corrections(photos, waypoints_for_correction)

    # --- Step 3: Insert all photos ---
    for photo in photos:
        if photo["location_wkt"]:
            cur.execute(
                """
                INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromText(%s), 4326), %s::jsonb, %s)
            """,
                (
                    photo["waypoint_id"],
                    photo["filename"],
                    photo["caption"],
                    photo["time_taken"],
                    photo["time_taken_local"],
                    photo["time_taken_local_tz"],
                    photo["location_wkt"],
                    photo["location_metadata"],
                    None,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
            """,
                (
                    photo["waypoint_id"],
                    photo["filename"],
                    photo["caption"],
                    photo["time_taken"],
                    photo["time_taken_local"],
                    photo["time_taken_local_tz"],
                    photo["location_metadata"],
                    None,
                ),
            )

    conn.commit()
    print(f"Inserted {len(photos)} photo(s).")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv()

    db_params = {
        "host": os.getenv("DATABASE_HOST"),
        "database": os.getenv("DATABASE_NAME"),
        "user": os.getenv("DATABASE_USER"),
        "password": os.getenv("DATABASE_PASSWORD"),
        "port": os.getenv("DATABASE_PORT"),
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
