import contextlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
import srtm
from dateutil import parser
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo

# Initialize elevation data (files will be cached in a local directory)
elevation_data = srtm.get_data()

# For resolving timezone from (lat, lon) in photos ETL
_timezone_finder = TimezoneFinder()

# EXIF-style timestamp: "2024:07:27 07:38:52" (local time at photo location)
PHOTO_TIMESTAMP_FMT = "%Y:%m:%d %H:%M:%S"
# Filename timestamp e.g. "2024-08-04 17.02.35.jpg"
PHOTO_FILENAME_TIMESTAMP_FMT = "%Y-%m-%d %H.%M.%S"

NS = {'gpx': 'http://www.topografix.com/GPX/1/1'}


def _strip_nul(s: str | None) -> str | None:
    """Remove NUL (0x00) characters. PostgreSQL and some libs reject them."""
    if s is None:
        return s
    return s.replace("\x00", "")


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


def _local_to_utc(local_ts_str: str | None, lat: float | None, lon: float | None) -> datetime | None:
    """Return UTC timezone-aware datetime for the photo timestamp, or None."""
    utc_dt, _, _ = _parse_photo_time(local_ts_str, lat, lon)
    return utc_dt

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

def get_text(elem: ET.Element, tag: str) -> str | None:
    item = elem.find(tag, NS)
    return item.text if item is not None else None


def run_find_penguins_description_etl(
    conn: psycopg2.extensions.connection, json_file_path: str | Path
) -> None:
    """
    Populate waypoint descriptions (and related text-only fields) from a JSON file.
    - matches waypoints by (name, start_time)
    - skips entries with missing time (e.g. "_general_" waypoint)

    TODO: handle "_general_" waypoint description
    """
    json_file_path = Path(json_file_path)

    try:
        with open(json_file_path, "r") as f:
            waypoints_data = json.load(f)
    except FileNotFoundError:
        print(f"CRITICAL: JSON file not found at {json_file_path}")
        return

    print(f"Processing {len(waypoints_data)} waypoints from {json_file_path}...")

    cur = conn.cursor()

    try:
        for entry in waypoints_data:
            name = _strip_nul(entry.get("name"))
            raw_start_time = _strip_nul(entry.get("time"))
            description = _strip_nul(entry.get("description"))

            if not description:
                print(f"Skipping '{name}': No description text found.")
                continue

            if description.strip().strip(".").lower() == "no mention":
                # Specifically directed LLM to use this string when it couldn't find info
                # on a particular waypoint. Not the most robust. Consider using blank instead.
                print(f"Skipping '{name}': Description was 'No mention.'")
                continue

            if not raw_start_time:
                # Note that this may happen for the "_general_" waypoint
                print(f"Skipping '{name}': No time (or empty time) found.")
                continue

            try:
                start_time_dt = parser.parse(raw_start_time)
            except (ValueError, TypeError):
                raise ValueError(f"CRITICAL ERROR: Invalid timestamp for '{name}'")

            check_query = """
                SELECT id FROM waypoints
                WHERE name = %s AND start_time = %s;
            """
            cur.execute(check_query, (name, start_time_dt))
            result = cur.fetchone()

            if result is None:
                error_msg = (
                    f"\n{'!'*50}\n"
                    f"DATA MISMATCH ERROR:\n"
                    f"Waypoint '{name}' ({raw_start_time})\n"
                    f"found in JSON but NOT in DB.\n"
                    f"{'!'*50}\n"
                )
                raise LookupError(error_msg)

            waypoint_id = result[0]

            update_query = """
                UPDATE waypoints
                SET description = %s
                WHERE id = %s;
            """
            cur.execute(update_query, (description, waypoint_id))

        conn.commit()
        print("\nSUCCESS: All waypoint descriptions populated and committed.")

    except Exception:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK.")
        raise
    finally:
        cur.close()

def run_findpenguins_gpx_etl(conn: psycopg2.extensions.connection, file_path: str | Path) -> None:
    """ Import data from FindPenguins GPX file given its path and a DB connection object """
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

    # 1. Parse Waypoints (already ordered by time in the input)
    waypoints = []
    for wpt in root.findall('gpx:wpt', NS):
        waypoints.append({
            'name': get_text(wpt, 'gpx:name'),
            'desc': get_text(wpt, 'gpx:desc'),
            'time': get_text(wpt, 'gpx:time'),
            'lat': float(wpt.get('lat')),
            'lon': float(wpt.get('lon'))
        })

    # Sort waypoints by time to ensure we can find the "previous" one easily
    # UNNECESSARY, they are already ordered
    #waypoints.sort(key=lambda x: x['time'])

    # 2. Parse Tracks (Grouped by Timestamp)
    raw_points = root.findall('.//gpx:trkpt', NS)
    grouped_tracks = defaultdict(list)

    for pt in raw_points:
        timestamp = get_text(pt, 'gpx:time')
        grouped_tracks[timestamp].append({
            'time': timestamp,
            'lat': float(pt.get('lat')),
            'lon': float(pt.get('lon')),
            'ele': get_text(pt, 'gpx:ele')
        })

    sorted_timestamps = sorted(grouped_tracks.keys())

    # --- DATABASE INSERTION ---
    cur = conn.cursor()

    # A. Insert Trip
    # TODO verify times
    trip_name = root.find('gpx:metadata', NS).find('gpx:name', NS).text
    print(f"Inserting Trip {trip_name}...")
    cur.execute("""
        INSERT INTO trips (name, start_date, end_date)
        VALUES (%s, %s, %s) RETURNING id
    """, (trip_name, waypoints[0]['time'], waypoints[-1]['time']))
    trip_id = cur.fetchone()[0]

    # B. Insert Waypoints & Build Lookup Map
    # Lookup map will be used to figure out which start/end waypoints correspond to a track
    print("Inserting Waypoints...")
    # Map: timestamp_string -> database_id
    time_to_wp_id = {}

    # Also keep a list of (timestamp, id) tuples to look up the "previous" waypoint
    wp_timeline = []

    # A waypoint's end time should be the start time of the next waypoint
    waypoint_end_times = [None] * len(waypoints) # last waypoint has no end time
    for i, val in enumerate(waypoints):
        if i != len(waypoints) - 1:
            waypoint_end_times[i] = waypoints[i+1]['time']

    for i, wp in enumerate(waypoints):
        cur.execute("""
            INSERT INTO waypoints (trip_id, name, description, start_time, end_time, location)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            RETURNING id
        """, (trip_id, wp['name'], wp['desc'], wp['time'], waypoint_end_times[i], wp['lon'], wp['lat']))

        wp_id = cur.fetchone()[0]
        time_to_wp_id[wp['time']] = wp_id
        wp_timeline.append((wp['time'], wp_id))

    # C. Insert Tracks linked to Waypoints, as well as Points
    print("Inserting Tracks...")
    for i, ts in enumerate(sorted_timestamps):
        points = grouped_tracks[ts]

        # Determine Waypoint Links
        # The track ends at the waypoint with the matching timestamp
        end_wp_id = time_to_wp_id.get(ts)

        # The track starts at the previous waypoint in the timeline
        # If this is the first track segment, start_wp might be None or the first waypoint itself
        start_wp_id = None

        # Find the index of the current timestamp in our waypoint timeline
        # We iterate to find where 'ts' fits.
        # (In your file, track_time usually equals waypoint_time, so we look for exact match)
        current_wp_index = next((idx for idx, val in enumerate(wp_timeline) if val[0] == ts), None)

        if current_wp_index is not None and current_wp_index > 0:
            start_wp_id = wp_timeline[current_wp_index - 1][1]

        # Construct Geometry
        if len(points) > 1:
            coords = ", ".join([f"{p['lon']} {p['lat']}" for p in points])
            wkt = f"LINESTRING({coords})"
        else:
            p = points[0]
            wkt = f"LINESTRING({p['lon']} {p['lat']}, {p['lon']} {p['lat']})"

        # Insert Track
        cur.execute("""
            INSERT INTO tracks
            (trip_id, start_waypoint_id, end_waypoint_id, source, start_time, end_time_incl, route)
            VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            RETURNING id
        """, (
            trip_id,
            start_wp_id,
            end_wp_id,
            'FindPenguins',
            points[0]['time'],
            points[-1]['time'],
            wkt
        ))
        track_id = cur.fetchone()[0]

        # Insert Points
        # Hydrate elevation data
        for p in points:
            try:
                # Redirect stdout to devnull to silence the library's print statements
                with contextlib.redirect_stdout(open(os.devnull, 'w')):
                    p['ele'] = elevation_data.get_elevation(p['lat'], p['lon'])
            except Exception as e:
                print(f"failed to load elevation: {e}")
                continue

        db_points = [(track_id, p['time'], p['lon'], p['lat'], p['ele']) for p in points]
        execute_values(cur, """
            INSERT INTO track_points (track_id, recorded_at, location, elevation_meters)
            VALUES %s
        """, db_points, template="(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)")

    conn.commit()


def run_photos_etl(conn: psycopg2.extensions.connection, photos_dir: str | Path) -> None:
    """
    Populate the photos table from JSONL files in photos_dir.
    Each line is a JSON object with filename, caption, timestamp, and location.
    """
    photos_dir = Path(photos_dir)
    if not photos_dir.is_dir():
        raise FileNotFoundError(f"Photos directory not found: {photos_dir}")

    jsonl_files = list(photos_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"No .jsonl files found in {photos_dir}")
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

    rows = []
    for path in sorted(jsonl_files):
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
                rows.append((
                    waypoint_id,
                    filename,
                    caption,
                    time_taken,
                    time_taken_local,
                    time_taken_local_tz,
                    location_wkt,
                    location_metadata,
                    None,  # embedding
                ))

    if not rows:
        print("No photo records to insert.")
        return

    cur = conn.cursor()
    for row in rows:
        (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz,
         location_wkt, location_metadata, embedding) = row
        if location_wkt:
            cur.execute("""
                INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromText(%s), 4326), %s::jsonb, %s)
            """, (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location_wkt, location_metadata, embedding))
        else:
            cur.execute("""
                INSERT INTO photos (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location, location_metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
            """, (waypoint_id, filename, caption, time_taken, time_taken_local, time_taken_local_tz, location_metadata, embedding))

    conn.commit()
    print(f"Inserted {len(rows)} photo(s).")


if __name__ == "__main__":
    load_dotenv()


    # TODO remove this debug line
    #waypoint_files_list = waypoint_files_list[0:1]

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

    # Populate waypoints from GPX
    # NOTE: USING RAW UN-OBFUSCATED GPX FILES FOR NOW
    gpx_dir = os.path.join(os.getenv("PRIVATE_DATA_DIR"),"findpenguins")
    waypoint_files_list = list(Path(gpx_dir).glob("*.gpx"))

    print(f"Importing {len(waypoint_files_list)} files")
    for f in waypoint_files_list:
        print(f"Processing {f}...")
        try:
            run_findpenguins_gpx_etl(connection, f)
            print("Success!")
        except Exception:
            connection.close()
            print("Failed to process")
            raise

    # Populate waypoint descriptions
    # If there are multiple files for the same trip, with different models,
    # we just process them in alphabetical order.
    # Do I want more consistency? gemini3pro comes after gemini3fp
    waypoint_description_path = os.path.join(os.getenv("INTERIM_DATA_DIR"), "robinblog")
    waypoint_description_files = sorted(Path(waypoint_description_path).glob("*.json"))
    print(f"Populating waypoint descriptions from {waypoint_description_path}...")
    for f in waypoint_description_files:
        try:
            run_find_penguins_description_etl(connection, f)
            print("Success!")
        except Exception:
            connection.close()
            print("Failed to process")
            raise

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
