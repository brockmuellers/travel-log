import contextlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extensions
import srtm
from dateutil import parser
from dotenv import load_dotenv
from psycopg2.extras import execute_values

# Initialize elevation data (files will be cached in a local directory)
elevation_data = srtm.get_data()

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def _first_waypoint_time(path: Path) -> str:
    """Return the timestamp of the first waypoint in a GPX file (for sorting)."""
    tree = ET.parse(path)
    root = tree.getroot()
    return root.find("gpx:wpt", NS).find("gpx:time", NS).text


def _strip_nul(s: str | None) -> str | None:
    """Remove NUL (0x00) characters. PostgreSQL and some libs reject them."""
    if s is None:
        return s
    return s.replace("\x00", "")


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
                    f"\n{'!' * 50}\n"
                    f"DATA MISMATCH ERROR:\n"
                    f"Waypoint '{name}' ({raw_start_time})\n"
                    f"found in JSON but NOT in DB.\n"
                    f"{'!' * 50}\n"
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


def run_findpenguins_gpx_etl(
    conn: psycopg2.extensions.connection, file_path: str | Path
) -> None:
    """Import data from FindPenguins GPX file given its path and a DB connection object"""
    tree = ET.parse(file_path)
    root = tree.getroot()
    # 1. Parse Waypoints (already ordered by time in the input)
    waypoints = []
    for wpt in root.findall("gpx:wpt", NS):
        waypoints.append(
            {
                "name": get_text(wpt, "gpx:name"),
                "desc": get_text(wpt, "gpx:desc"),
                "time": get_text(wpt, "gpx:time"),
                "lat": float(wpt.get("lat")),
                "lon": float(wpt.get("lon")),
            }
        )

    # Sort waypoints by time to ensure we can find the "previous" one easily
    # UNNECESSARY, they are already ordered
    # waypoints.sort(key=lambda x: x['time'])

    # 2. Parse Tracks (Grouped by Timestamp)
    raw_points = root.findall(".//gpx:trkpt", NS)
    grouped_tracks = defaultdict(list)

    for pt in raw_points:
        timestamp = get_text(pt, "gpx:time")
        grouped_tracks[timestamp].append(
            {
                "time": timestamp,
                "lat": float(pt.get("lat")),
                "lon": float(pt.get("lon")),
                "ele": get_text(pt, "gpx:ele"),
            }
        )

    sorted_timestamps = sorted(grouped_tracks.keys())

    # --- DATABASE INSERTION ---
    cur = conn.cursor()

    # A. Insert Trip
    # TODO verify times
    trip_name = root.find("gpx:metadata", NS).find("gpx:name", NS).text
    print(f"Inserting Trip {trip_name}...")
    cur.execute(
        """
        INSERT INTO trips (name, start_date, end_date, source)
        VALUES (%s, %s, %s, %s) RETURNING id
    """,
        (trip_name, waypoints[0]["time"], waypoints[-1]["time"], "findpenguins"),
    )
    trip_id = cur.fetchone()[0]

    # B. Insert Waypoints & Build Lookup Map
    # Lookup map will be used to figure out which start/end waypoints correspond to a track
    print("Inserting Waypoints...")
    # Map: timestamp_string -> database_id
    time_to_wp_id = {}

    # Also keep a list of (timestamp, id) tuples to look up the "previous" waypoint
    wp_timeline = []

    # A waypoint's end time should be the start time of the next waypoint
    waypoint_end_times = [None] * len(waypoints)  # last waypoint has no end time
    for i, val in enumerate(waypoints):
        if i != len(waypoints) - 1:
            waypoint_end_times[i] = waypoints[i + 1]["time"]

    for i, wp in enumerate(waypoints):
        cur.execute(
            """
            INSERT INTO waypoints (trip_id, name, description, start_time, end_time, location)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            RETURNING id
        """,
            (
                trip_id,
                wp["name"],
                wp["desc"],
                wp["time"],
                waypoint_end_times[i],
                wp["lon"],
                wp["lat"],
            ),
        )

        wp_id = cur.fetchone()[0]
        time_to_wp_id[wp["time"]] = wp_id
        wp_timeline.append((wp["time"], wp_id))

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
        current_wp_index = next(
            (idx for idx, val in enumerate(wp_timeline) if val[0] == ts), None
        )

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
        cur.execute(
            """
            INSERT INTO tracks
            (trip_id, start_waypoint_id, end_waypoint_id, source, start_time, end_time_incl, route)
            VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            RETURNING id
        """,
            (
                trip_id,
                start_wp_id,
                end_wp_id,
                "FindPenguins",
                points[0]["time"],
                points[-1]["time"],
                wkt,
            ),
        )
        track_id = cur.fetchone()[0]

        # Insert Points
        # Hydrate elevation data
        for p in points:
            try:
                # Redirect stdout to devnull to silence the library's print statements
                with contextlib.redirect_stdout(open(os.devnull, "w")):
                    p["ele"] = elevation_data.get_elevation(p["lat"], p["lon"])
            except Exception as e:
                print(f"failed to load elevation: {e}")
                continue

        db_points = [
            (track_id, p["time"], p["lon"], p["lat"], p["ele"]) for p in points
        ]
        execute_values(
            cur,
            """
            INSERT INTO track_points (track_id, recorded_at, location, elevation_meters)
            VALUES %s
        """,
            db_points,
            template="(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)",
        )

    conn.commit()


def run_manual_trips_etl(
    conn: psycopg2.extensions.connection, json_path: str | Path
) -> None:
    """Import manual trips from JSON to fill gaps between FindPenguins trips.

    Expected JSON schema (list of trips):
    [
      {
        "name": "Trip Name",
        "waypoints": [
          {
            "name": "City",
            "lat": 37.77,
            "lon": -122.42,
            "start_time": "2024-07-15T00:00:00Z",
            "end_time": "2024-07-20T00:00:00Z",  // optional
            "track_to_here": [                     // optional, omit for first wp
              {"lat": 37.77, "lon": -122.42},
              {"lat": 40.0, "lon": -122.5, "transport": "car"},  // transport ignored
              {"lat": 45.52, "lon": -122.68}
            ]
          }
        ]
      }
    ]
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"Manual trips file not found at {json_path}")

    with open(json_path, "r") as f:
        manual_trips = json.load(f)

    if not manual_trips:
        print("No manual trips to import.")
        return

    cur = conn.cursor()
    try:
        # Get existing FP trips ordered by start_date
        cur.execute(
            "SELECT id, start_date, end_date FROM trips WHERE source = 'findpenguins' ORDER BY start_date"
        )
        fp_trips = cur.fetchall()

        expected_count = len(fp_trips) + 1
        if len(manual_trips) != expected_count:
            raise ValueError(
                f"Expected {expected_count} manual trips for {len(fp_trips)} FP trips, "
                f"got {len(manual_trips)}"
            )

        for i, mt in enumerate(manual_trips):
            # Determine trip start/end dates from FP trip boundaries
            start_date = fp_trips[i - 1][2] if i > 0 else None
            end_date = fp_trips[i][1] if i < len(fp_trips) else None

            cur.execute(
                """
                INSERT INTO trips (name, start_date, end_date, source)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (mt["name"], start_date, end_date, "manual"),
            )
            trip_id = cur.fetchone()[0]
            print(f"Inserted manual trip: {mt['name']} (id={trip_id})")

            # Backfill end_time on the last FP waypoint from the preceding trip.
            # FP trips always leave their final waypoint with end_time=NULL.
            waypoints = mt["waypoints"]
            if i > 0 and waypoints:
                first_manual_start = waypoints[0]["start_time"]
                fp_trip_id = fp_trips[i - 1][0]
                cur.execute(
                    """
                    UPDATE waypoints
                    SET end_time = %s
                    WHERE trip_id = %s AND end_time IS NULL
                    """,
                    (first_manual_start, fp_trip_id),
                )
                updated = cur.rowcount
                if updated:
                    print(f"  Backfilled end_time on {updated} FP waypoint(s) from trip {fp_trip_id}")

            wp_ids: list[int] = []

            for j, wp in enumerate(waypoints):
                # Use explicit end_time if provided, else next wp's start_time, else trip end_date
                end_time = wp.get("end_time")
                if end_time is None:
                    if j < len(waypoints) - 1:
                        end_time = waypoints[j + 1]["start_time"]
                    else:
                        end_time = end_date

                cur.execute(
                    """
                    INSERT INTO waypoints (trip_id, name, start_time, end_time, location)
                    VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                    RETURNING id
                    """,
                    (trip_id, wp["name"], wp["start_time"], end_time, wp["lon"], wp["lat"]),
                )
                wp_id = cur.fetchone()[0]
                wp_ids.append(wp_id)

            # Insert tracks and track points
            for j, wp in enumerate(waypoints):
                track_points = wp.get("track_to_here")
                if not track_points or j == 0:
                    continue

                # Build LINESTRING
                coords = ", ".join(
                    f"{p['lon']} {p['lat']}" for p in track_points
                )
                if len(track_points) < 2:
                    p = track_points[0]
                    wkt = f"LINESTRING({p['lon']} {p['lat']}, {p['lon']} {p['lat']})"
                else:
                    wkt = f"LINESTRING({coords})"

                prev_wp = waypoints[j - 1]
                cur.execute(
                    """
                    INSERT INTO tracks
                    (trip_id, start_waypoint_id, end_waypoint_id, source, start_time, end_time_incl, route)
                    VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                    RETURNING id
                    """,
                    (
                        trip_id,
                        wp_ids[j - 1],
                        wp_ids[j],
                        "manual",
                        prev_wp["start_time"],
                        wp["start_time"],
                        wkt,
                    ),
                )
                track_id = cur.fetchone()[0]

                # Hydrate elevation and insert track points
                for p in track_points:
                    try:
                        with contextlib.redirect_stdout(open(os.devnull, "w")):
                            p["ele"] = elevation_data.get_elevation(p["lat"], p["lon"])
                    except Exception as e:
                        print(f"failed to load elevation: {e}")
                        p["ele"] = None

                db_points = [
                    (track_id, wp["start_time"], p["lon"], p["lat"], p.get("ele"))
                    for p in track_points
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO track_points (track_id, recorded_at, location, elevation_meters)
                    VALUES %s
                    """,
                    db_points,
                    template="(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)",
                )

        conn.commit()
        print(f"SUCCESS: Imported {len(manual_trips)} manual trips.")

    except Exception:
        conn.rollback()
        print("TRANSACTION ROLLED BACK (manual trips).")
        raise
    finally:
        cur.close()


if __name__ == "__main__":
    load_dotenv()

    # TODO remove this debug line
    # waypoint_files_list = waypoint_files_list[0:1]

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

    # Populate waypoints from GPX
    # NOTE: USING RAW UN-OBFUSCATED GPX FILES FOR NOW
    gpx_dir = os.path.join(os.getenv("PRIVATE_DATA_DIR"), "findpenguins")
    waypoint_files_list = sorted(Path(gpx_dir).glob("*.gpx"), key=_first_waypoint_time)

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

    # Populate manual trips (gap-filling between FP trips)
    manual_trips_path = os.path.join(os.getenv("PRIVATE_DATA_DIR"), "manual", "trips.json")
    run_manual_trips_etl(connection, manual_trips_path)

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

    connection.close()
