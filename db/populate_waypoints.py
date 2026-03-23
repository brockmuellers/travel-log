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


# ---------------------------------------------------------------------------
# Parsing: each source produces a list of trip dicts in a common format.
#
# Common trip format:
# {
#     "name": str,
#     "source": "findpenguins" | "manual",
#     "waypoints": [
#         {
#             "name": str,
#             "description": str | None,
#             "lat": float,
#             "lon": float,
#             "start_time": str,
#             "end_time": str | None,
#             "track_to_here": [{"lat": float, "lon": float, "time": str | None}] | None,
#         }
#     ],
# }
# ---------------------------------------------------------------------------


def parse_fp_gpx(path: Path) -> dict:
    """Parse a FindPenguins GPX file into the common trip format."""
    tree = ET.parse(path)
    root = tree.getroot()

    trip_name = root.find("gpx:metadata", NS).find("gpx:name", NS).text

    # Parse raw waypoints
    raw_wps = []
    for wpt in root.findall("gpx:wpt", NS):
        raw_wps.append(
            {
                "name": get_text(wpt, "gpx:name"),
                "desc": get_text(wpt, "gpx:desc"),
                "time": get_text(wpt, "gpx:time"),
                "lat": float(wpt.get("lat")),
                "lon": float(wpt.get("lon")),
            }
        )

    # Parse tracks grouped by timestamp (FP groups all trkpts for a waypoint
    # under the destination waypoint's timestamp)
    grouped_tracks: dict[str, list[dict]] = defaultdict(list)
    for pt in root.findall(".//gpx:trkpt", NS):
        ts = get_text(pt, "gpx:time")
        grouped_tracks[ts].append(
            {
                "lat": float(pt.get("lat")),
                "lon": float(pt.get("lon")),
                "time": ts,
            }
        )

    # Build common-format waypoints
    waypoints = []
    for i, wp in enumerate(raw_wps):
        end_time = raw_wps[i + 1]["time"] if i < len(raw_wps) - 1 else None

        # Tracks keyed by destination waypoint timestamp; skip first waypoint
        track_to_here = None
        if i > 0 and wp["time"] in grouped_tracks:
            track_to_here = grouped_tracks[wp["time"]]

        waypoints.append(
            {
                "name": wp["name"],
                "description": wp["desc"],
                "lat": wp["lat"],
                "lon": wp["lon"],
                "start_time": wp["time"],
                "end_time": end_time,
                "track_to_here": track_to_here,
            }
        )

    return {
        "name": trip_name,
        "source": "findpenguins",
        "waypoints": waypoints,
    }


def parse_manual_trips(json_path: str | Path) -> list[dict]:
    """Parse manual trips JSON into the common trip format.

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

    result = []
    for mt in manual_trips:
        waypoints = []
        raw_wps = mt["waypoints"]
        for j, wp in enumerate(raw_wps):
            end_time = wp.get("end_time")
            if end_time is None and j < len(raw_wps) - 1:
                end_time = raw_wps[j + 1]["start_time"]
            # Last waypoint: end_time stays None (filled by backfill_end_times)

            waypoints.append(
                {
                    "name": wp["name"],
                    "description": None,
                    "lat": wp["lat"],
                    "lon": wp["lon"],
                    "start_time": wp["start_time"],
                    "end_time": end_time,
                    "track_to_here": wp.get("track_to_here"),
                }
            )

        result.append(
            {
                "name": mt["name"],
                "source": "manual",
                "waypoints": waypoints,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Pre-insertion transforms
# ---------------------------------------------------------------------------


def backfill_end_times(trips: list[dict]) -> None:
    """Fill in the last waypoint's end_time for each trip from the next trip's first waypoint.

    Expects trips sorted chronologically. Mutates dicts in place.
    """
    for i in range(len(trips) - 1):
        last_wp = trips[i]["waypoints"][-1]
        if last_wp["end_time"] is None:
            last_wp["end_time"] = trips[i + 1]["waypoints"][0]["start_time"]


# ---------------------------------------------------------------------------
# Insertion: single function for all sources
# ---------------------------------------------------------------------------


def insert_trip(cur: psycopg2.extensions.cursor, trip: dict) -> int:
    """Insert a trip with its waypoints, tracks, and track_points. Returns trip_id."""
    waypoints = trip["waypoints"]

    start_date = waypoints[0]["start_time"]
    end_date = waypoints[-1].get("end_time") or waypoints[-1]["start_time"]

    cur.execute(
        """
        INSERT INTO trips (name, start_date, end_date, source)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (trip["name"], start_date, end_date, trip["source"]),
    )
    trip_id = cur.fetchone()[0]

    # Insert waypoints
    wp_ids: list[int] = []
    for wp in waypoints:
        cur.execute(
            """
            INSERT INTO waypoints (trip_id, name, description, start_time, end_time, location)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            RETURNING id
            """,
            (
                trip_id,
                wp["name"],
                wp.get("description"),
                wp["start_time"],
                wp["end_time"],
                wp["lon"],
                wp["lat"],
            ),
        )
        wp_ids.append(cur.fetchone()[0])

    # Insert tracks and track_points
    track_source = "FindPenguins" if trip["source"] == "findpenguins" else "manual"
    for j, wp in enumerate(waypoints):
        track_points = wp.get("track_to_here")
        if not track_points or j == 0:
            continue

        # Build LINESTRING
        if len(track_points) < 2:
            p = track_points[0]
            wkt = f"LINESTRING({p['lon']} {p['lat']}, {p['lon']} {p['lat']})"
        else:
            coords = ", ".join(f"{p['lon']} {p['lat']}" for p in track_points)
            wkt = f"LINESTRING({coords})"

        start_time = waypoints[j - 1]["start_time"]
        end_time = wp["start_time"]

        cur.execute(
            """
            INSERT INTO tracks
            (trip_id, start_waypoint_id, end_waypoint_id, source, start_time, end_time_incl, route)
            VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            RETURNING id
            """,
            (trip_id, wp_ids[j - 1], wp_ids[j], track_source, start_time, end_time, wkt),
        )
        track_id = cur.fetchone()[0]

        # Hydrate elevation
        for p in track_points:
            try:
                # Redirect stdout to devnull to silence the library's print statements
                with contextlib.redirect_stdout(open(os.devnull, "w")):
                    p["ele"] = elevation_data.get_elevation(p["lat"], p["lon"])
            except Exception as e:
                print(f"failed to load elevation: {e}")
                p["ele"] = None

        # Insert track points; use per-point time if available, else destination wp time
        fallback_time = wp["start_time"]
        db_points = [
            (track_id, p.get("time", fallback_time), p["lon"], p["lat"], p.get("ele"))
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

    return trip_id


# ---------------------------------------------------------------------------
# Description ETL (unchanged, only applies to FP waypoints)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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

    # 1. Parse all sources
    gpx_dir = Path(os.getenv("PRIVATE_DATA_DIR")) / "findpenguins"
    fp_trips = [
        parse_fp_gpx(p)
        for p in sorted(gpx_dir.glob("*.gpx"), key=_first_waypoint_time)
    ]
    print(f"Parsed {len(fp_trips)} FindPenguins trips")

    manual_trips_path = Path(os.getenv("PRIVATE_DATA_DIR")) / "manual" / "trips.json"
    manual_trips = parse_manual_trips(manual_trips_path)
    print(f"Parsed {len(manual_trips)} manual trips")

    # 2. Merge and sort chronologically
    all_trips = fp_trips + manual_trips
    all_trips.sort(key=lambda t: t["waypoints"][0]["start_time"])

    # 3. Backfill end-times across trip boundaries
    backfill_end_times(all_trips)

    # 4. Insert all trips in chronological order
    cur = connection.cursor()
    try:
        for trip in all_trips:
            print(f"Inserting trip: {trip['name']} ({trip['source']})...")
            insert_trip(cur, trip)
            print("  Success!")
        connection.commit()
        print(f"\nSUCCESS: Imported {len(all_trips)} trips.")
    except Exception:
        connection.rollback()
        print("TRANSACTION ROLLED BACK.")
        raise
    finally:
        cur.close()

    # 5. Populate waypoint descriptions
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
