import math

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
