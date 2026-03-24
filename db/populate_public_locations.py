"""
Populate location_public columns for waypoints and photos.

For sensitive waypoints (listed in sensitive_waypoints.json), applies a seeded
random offset. For all other rows, copies location as-is.

Photos inherit their parent waypoint's offset: if a photo belongs to a sensitive
waypoint, its location is shifted by the same vector. Photos without a waypoint
or whose waypoint is not sensitive get their real location copied.

NOTE: The obfuscation math (calculate_destination_point, normalize_longitude) is
duplicated from scripts/obfuscate_points.py. Changes to the algorithm should be
made in both places.
"""

import json
import math
import os
import random
import sys
from typing import Any

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv

ROUND_TO = 6


def normalize_longitude(lon: float) -> float:
    return (lon + 180) % 360 - 180


def calculate_destination_point(
    lat: float, lon: float, distance_km: float, bearing_degrees: float
) -> tuple[float, float]:
    R = 6371
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_degrees)

    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(distance_km / R)
        + math.cos(lat_rad) * math.sin(distance_km / R) * math.cos(bearing_rad)
    )
    new_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(distance_km / R) * math.cos(lat_rad),
        math.cos(distance_km / R) - math.sin(lat_rad) * math.sin(new_lat_rad),
    )

    return round(math.degrees(new_lat_rad), ROUND_TO), round(
        normalize_longitude(math.degrees(new_lon_rad)), ROUND_TO
    )


def _compute_offset(config: dict[str, Any]) -> tuple[float, float]:
    """Return (dlat, dlon) offset for a sensitive waypoint config entry."""
    rng = random.Random(config["seed"])
    dist = config["radius"]
    bearing = rng.uniform(0, 360)
    # We need a reference point to compute the offset. Use the config's explicit
    # lat/lon if present (ghost points), otherwise we compute per-waypoint below.
    return dist, bearing


def run(
    conn: psycopg2.extensions.connection, sensitive_config: dict[str, dict[str, Any]]
) -> None:
    cur = conn.cursor()

    # --- Waypoints ---
    # Build a map of waypoint name -> offset (new_lat, new_lon) for sensitive ones.
    # sensitive_config keys are waypoint names.
    # For each sensitive waypoint in the DB, compute the obfuscated location.
    sensitive_offsets: dict[int, tuple[float, float]] = {}  # wp_id -> (dlat, dlon)

    cur.execute(
        "SELECT id, name, ST_Y(location::geometry), ST_X(location::geometry) "
        "FROM waypoints WHERE location IS NOT NULL"
    )
    waypoints = cur.fetchall()

    updated_wp = 0
    for wp_id, name, lat, lon in waypoints:
        if name in sensitive_config:
            config = sensitive_config[name]
            rng = random.Random(config["seed"])
            dist = config["radius"]
            # Randomize distance between 75%-100% of radius, matching process_gpx.py.
            random_distance = rng.uniform(dist * 0.75, dist)
            bearing = rng.uniform(0, 360)
            new_lat, new_lon = calculate_destination_point(lat, lon, random_distance, bearing)
            # Store the delta so photos can reuse it
            sensitive_offsets[wp_id] = (new_lat - lat, new_lon - lon)
            cur.execute(
                "UPDATE waypoints SET location_public = ST_SetSRID(ST_MakePoint(%s, %s), 4326) WHERE id = %s",
                (new_lon, new_lat, wp_id),
            )
            print(f"  Obfuscated waypoint '{name}' (id={wp_id}): {random_distance:.1f}km @ {bearing:.0f}°")
        else:
            cur.execute(
                "UPDATE waypoints SET location_public = location WHERE id = %s",
                (wp_id,),
            )
        updated_wp += 1

    # Also handle waypoints with NULL location — set location_public = NULL (already NULL, but explicit)
    cur.execute(
        "UPDATE waypoints SET location_public = NULL WHERE location IS NULL"
    )

    # --- Photos ---
    # Photos linked to a sensitive waypoint get shifted by the same delta.
    cur.execute(
        "SELECT id, waypoint_id, ST_Y(location::geometry), ST_X(location::geometry) "
        "FROM photos WHERE location IS NOT NULL"
    )
    photos = cur.fetchall()

    obfuscated_photos = 0
    for photo_id, wp_id, lat, lon in photos:
        if wp_id in sensitive_offsets:
            dlat, dlon = sensitive_offsets[wp_id]
            new_lat = round(lat + dlat, ROUND_TO)
            new_lon = round(normalize_longitude(lon + dlon), ROUND_TO)
            cur.execute(
                "UPDATE photos SET location_public = ST_SetSRID(ST_MakePoint(%s, %s), 4326) WHERE id = %s",
                (new_lon, new_lat, photo_id),
            )
            obfuscated_photos += 1
        else:
            cur.execute(
                "UPDATE photos SET location_public = location WHERE id = %s",
                (photo_id,),
            )

    cur.execute("UPDATE photos SET location_public = NULL WHERE location IS NULL")

    conn.commit()
    cur.close()
    print(
        f"Done: {updated_wp} waypoints updated, "
        f"{obfuscated_photos} photos obfuscated, "
        f"{len(photos) - obfuscated_photos} photos copied as-is."
    )


if __name__ == "__main__":
    load_dotenv()

    db_params = {
        "host": os.getenv("DATABASE_HOST"),
        "database": os.getenv("DATABASE_NAME"),
        "user": os.getenv("DATABASE_USER"),
        "password": os.getenv("DATABASE_PASSWORD"),
        "port": os.getenv("DATABASE_PORT"),
    }

    conn = None
    try:
        conn = psycopg2.connect(**db_params)
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error connecting to the database: {error}")
        sys.exit(1)

    waypoints_path = os.path.join(
        os.getenv("PRIVATE_DATA_DIR"), "sensitive_waypoints.json"
    )
    with open(waypoints_path, "r") as f:
        data = json.load(f)
    config = {item["name"]: item for item in data}

    print(f"Loaded {len(config)} sensitive waypoint configs from {waypoints_path}")
    try:
        run(conn, config)
    except Exception:
        conn.close()
        raise

    conn.close()
