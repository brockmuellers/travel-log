import json
import math
import os
from typing import Any

ROUND_TO = 6  # Round new lat/lon values to make obfuscation less obvious


def normalize_longitude(lon: float) -> float:
    """
    Wraps longitude to -180 to 180 degrees.
    Ex: 181.0 -> -179.0
    """
    return (lon + 180) % 360 - 180


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees) in kilometers.
    """
    R = 6371  # Earth radius in km

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_destination_point(
    lat: float, lon: float, distance_km: float, bearing_degrees: float
) -> tuple[float, float]:
    """
    Calculates a new coordinate given a start point, distance (km), and bearing (degrees).
    """
    R = 6371  # Earth radius in km

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

    final_lat = math.degrees(new_lat_rad)
    final_lon = normalize_longitude(math.degrees(new_lon_rad))

    return round(final_lat, ROUND_TO), round(final_lon, ROUND_TO)


def compute_obfuscated_location(
    config: dict[str, Any], lat: float, lon: float
) -> tuple[float, float]:
    """
    Apply the configured displacement (km) and bearing (degrees) to produce an
    obfuscated coordinate. Config must have "displacement" and "bearing" keys.
    """
    return calculate_destination_point(lat, lon, config["displacement"], config["bearing"])


def load_sensitive_zones() -> list[dict[str, Any]]:
    """
    Load sensitive zones from $PRIVATE_DATA_DIR/sensitive_locations.json.

    FORMAT
    ------
    The file is a JSON array. Each entry represents one sensitive location:

      {
        "key": "My House",           -- human-readable label (used in log output only)
        "name": "San Francisco",     -- optional waypoint name that can be matched to one
                                        or more waypoints
        "lat": 40.56789,             -- zone center latitude  (decimal degrees)
        "lon": -70.23456,            -- zone center longitude (decimal degrees)
        "radius": 8,                 -- zone radius in km; any GPS point within this
                                        distance is considered sensitive
        "displacement": 6.2,         -- how far to move obfuscated points, in km
        "bearing": 137               -- direction to move them (0 = N, 90 = E, 180 = S, ...)
      }

    One entry per sensitive location, regardless of how many times it was visited.
    Each "key" and "name" will be unique across entries.
    The fake location is fully determined by (lat, lon, displacement, bearing) —
    there is no randomness.

    MULTI-VISIT LOCATIONS (e.g. visited San Francisco twice)
    ---------------------------------------------------
    Use a single entry. The lat/lon should be set to the zone center — typically
    the GPS coordinates of the first visit's waypoint, or a representative point
    for the area. The coordinates of each matching waypoint may be slightly
    different due to GPS drift.

    GHOST ZONES (sensitive area with no GPX waypoint)
    --------------------------------------------------
    If a sensitive zone has no corresponding <wpt> in the GPX (e.g. a location
    that appears only as track points, not as a named stop), still add an entry
    here, but omit the "name" field. The absence of "name" is how ghost zones
    are identified — their track points are deleted rather than relocated.
    """
    path = os.path.join(os.environ["PRIVATE_DATA_DIR"], "sensitive_locations.json")
    with open(path) as f:
        return json.load(f)
