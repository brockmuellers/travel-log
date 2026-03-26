"""
Populate location_public columns for waypoints and photos.

Any waypoint or photo within the radius of a sensitive zone (from sensitive_locations.json)
has its location displaced by that zone's configured displacement and bearing.
All other rows have their real location copied as-is.
"""

import os
import sys
from typing import Any

import psycopg2
import psycopg2.extensions
from dotenv import load_dotenv
from lib.gps_utils import (compute_obfuscated_location, haversine_distance,
                           load_sensitive_zones)


def _matching_zone(lat: float, lon: float, zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    for zone in zones:
        if haversine_distance(lat, lon, zone["lat"], zone["lon"]) <= zone["radius"]:
            return zone
    return None


def run(
    conn: psycopg2.extensions.connection, sensitive_zones: list[dict[str, Any]]
) -> None:
    cur = conn.cursor()

    # --- Waypoints ---
    cur.execute(
        "SELECT id, name, ST_Y(location::geometry), ST_X(location::geometry) "
        "FROM waypoints WHERE location IS NOT NULL"
    )
    waypoints = cur.fetchall()

    updated_wp = 0
    for wp_id, name, lat, lon in waypoints:
        zone = _matching_zone(lat, lon, sensitive_zones)
        if zone:
            new_lat, new_lon = compute_obfuscated_location(zone, lat, lon)
            cur.execute(
                "UPDATE waypoints SET location_public = ST_SetSRID(ST_MakePoint(%s, %s), 4326) WHERE id = %s",
                (new_lon, new_lat, wp_id),
            )
            print(f"  Obfuscated waypoint '{name}' (id={wp_id}): {zone['displacement']:.1f}km @ {zone['bearing']:.0f}°")
        else:
            cur.execute(
                "UPDATE waypoints SET location_public = location WHERE id = %s",
                (wp_id,),
            )
        updated_wp += 1

    cur.execute("UPDATE waypoints SET location_public = NULL WHERE location IS NULL")

    # --- Photos ---
    cur.execute(
        "SELECT id, ST_Y(location::geometry), ST_X(location::geometry) "
        "FROM photos WHERE location IS NOT NULL"
    )
    photos = cur.fetchall()

    obfuscated_photos = 0
    for photo_id, lat, lon in photos:
        zone = _matching_zone(lat, lon, sensitive_zones)
        if zone:
            new_lat, new_lon = compute_obfuscated_location(zone, lat, lon)
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

    sensitive_zones = load_sensitive_zones()
    print(f"Loaded {len(sensitive_zones)} sensitive zones")
    try:
        run(conn, sensitive_zones)
    except Exception:
        conn.close()
        raise

    conn.close()
