import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.obfuscate_points import (
    calculate_destination_point,
    haversine_distance,
    normalize_longitude,
    process_gpx,
)

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}

# --- Helpers ---


def make_gpx(waypoints: list[dict], track_points: list[dict]) -> str:
    """Build a minimal GPX 1.1 XML string."""
    wpt_tags = ""
    for w in waypoints:
        wpt_tags += f'  <wpt lat="{w["lat"]}" lon="{w["lon"]}"><name>{w["name"]}</name></wpt>\n'

    trkpt_tags = ""
    for p in track_points:
        trkpt_tags += f'    <trkpt lat="{p["lat"]}" lon="{p["lon"]}"/>\n'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">\n'
        + wpt_tags
        + "  <trk><trkseg>\n"
        + trkpt_tags
        + "  </trkseg></trk>\n"
        "</gpx>"
    )


def run_process_gpx(gpx_content: str, config: dict) -> ET.Element:
    """Write gpx_content to a temp file, run process_gpx, return parsed output root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.gpx"
        output_path = Path(tmpdir) / "output.gpx"
        input_path.write_text(gpx_content, encoding="utf-8")
        process_gpx(str(input_path), str(output_path), config)
        tree = ET.parse(output_path)
        return tree.getroot()


def get_waypoints(root: ET.Element) -> list[dict]:
    return [
        {
            "name": wpt.find("gpx:name", NS).text,
            "lat": float(wpt.get("lat")),
            "lon": float(wpt.get("lon")),
        }
        for wpt in root.findall("gpx:wpt", NS)
    ]


def get_track_points(root: ET.Element) -> list[tuple[float, float]]:
    return [
        (float(pt.get("lat")), float(pt.get("lon")))
        for trk in root.findall("gpx:trk", NS)
        for seg in trk.findall("gpx:trkseg", NS)
        for pt in seg.findall("gpx:trkpt", NS)
    ]


# --- Math helper tests ---


def test_haversine_same_point():
    assert haversine_distance(40.0, -75.0, 40.0, -75.0) == 0.0


def test_haversine_known_distance():
    # New York (40.7128, -74.0060) to Los Angeles (34.0522, -118.2437) ≈ 3940 km
    dist = haversine_distance(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3900 < dist < 4000


def test_normalize_longitude_wraps_east():
    assert normalize_longitude(181.0) == pytest.approx(-179.0)


def test_normalize_longitude_wraps_west():
    assert normalize_longitude(-181.0) == pytest.approx(179.0)


def test_normalize_longitude_no_wrap():
    assert normalize_longitude(90.0) == pytest.approx(90.0)


def test_calculate_destination_distance():
    # Result should be approximately the requested distance away
    target_km = 10.0
    new_lat, new_lon = calculate_destination_point(40.0, -75.0, target_km, 45.0)
    actual_dist = haversine_distance(40.0, -75.0, new_lat, new_lon)
    assert abs(actual_dist - target_km) < 0.01


def test_calculate_destination_deterministic():
    result1 = calculate_destination_point(40.0, -75.0, 5.0, 90.0)
    result2 = calculate_destination_point(40.0, -75.0, 5.0, 90.0)
    assert result1 == result2


# --- process_gpx: waypoint obfuscation ---


def test_named_waypoint_is_moved():
    config = {"My House": {"name": "My House", "seed": 42, "radius": 5}}
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[],
    )
    root = run_process_gpx(gpx, config)
    wpts = get_waypoints(root)
    assert len(wpts) == 1
    assert wpts[0]["lat"] != 40.0 or wpts[0]["lon"] != -75.0


def test_named_waypoint_moved_within_expected_range():
    # Waypoints are moved between 75%-100% of radius to guarantee meaningful
    # displacement while avoiding a predictable exact-radius displacement.
    radius = 8
    config = {"My House": {"name": "My House", "seed": 42, "radius": radius}}
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[],
    )
    root = run_process_gpx(gpx, config)
    wpt = get_waypoints(root)[0]
    dist = haversine_distance(40.0, -75.0, wpt["lat"], wpt["lon"])
    assert radius * 0.75 <= dist <= radius + 0.001


def test_named_waypoint_obfuscation_is_deterministic():
    config = {"My House": {"name": "My House", "seed": 99, "radius": 5}}
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[],
    )
    root1 = run_process_gpx(gpx, config)
    root2 = run_process_gpx(gpx, config)
    assert get_waypoints(root1) == get_waypoints(root2)


def test_non_sensitive_waypoint_untouched():
    config = {}
    gpx = make_gpx(
        waypoints=[{"name": "Safe Place", "lat": 40.0, "lon": -75.0}],
        track_points=[],
    )
    root = run_process_gpx(gpx, config)
    wpt = get_waypoints(root)[0]
    assert wpt["lat"] == pytest.approx(40.0)
    assert wpt["lon"] == pytest.approx(-75.0)


# --- process_gpx: track point handling ---


def test_track_point_at_sensitive_location_is_moved():
    # A track point at the exact sensitive location should be relocated, not deleted
    config = {"My House": {"name": "My House", "seed": 42, "radius": 5}}
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[{"lat": 40.0, "lon": -75.0}],
    )
    root = run_process_gpx(gpx, config)
    pts = get_track_points(root)
    assert len(pts) == 1
    lat, lon = pts[0]
    assert lat != 40.0 or lon != -75.0


def test_track_point_near_sensitive_location_is_deleted():
    # A point 1 km from the sensitive location (within 5 km radius) should be removed
    config = {"My House": {"name": "My House", "seed": 42, "radius": 5}}
    nearby_lat, nearby_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[{"lat": nearby_lat, "lon": nearby_lon}],
    )
    root = run_process_gpx(gpx, config)
    assert get_track_points(root) == []


def test_track_point_outside_radius_is_kept():
    # A point 20 km away (outside 5 km radius) should be untouched
    config = {"My House": {"name": "My House", "seed": 42, "radius": 5}}
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 20.0, 90.0)
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[{"lat": far_lat, "lon": far_lon}],
    )
    root = run_process_gpx(gpx, config)
    pts = get_track_points(root)
    assert len(pts) == 1
    assert pts[0] == pytest.approx((far_lat, far_lon))


def test_track_points_mixed_near_and_far():
    config = {"My House": {"name": "My House", "seed": 42, "radius": 5}}
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 2.0, 180.0)
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 50.0, 90.0)
    gpx = make_gpx(
        waypoints=[{"name": "My House", "lat": 40.0, "lon": -75.0}],
        track_points=[
            {"lat": near_lat, "lon": near_lon},
            {"lat": far_lat, "lon": far_lon},
        ],
    )
    root = run_process_gpx(gpx, config)
    pts = get_track_points(root)
    assert len(pts) == 1
    assert pts[0] == pytest.approx((far_lat, far_lon))


# --- process_gpx: ghost point (explicit lat/lon in config) ---


def test_ghost_point_creates_sensitive_zone_for_tracks():
    # A config entry with explicit lat/lon (no GPX waypoint) still protects nearby tracks
    config = {
        "Ghost": {
            "name": "Ghost",
            "seed": 7,
            "radius": 5,
            "lat": 40.0,
            "lon": -75.0,
        }
    }
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    gpx = make_gpx(
        waypoints=[],
        track_points=[{"lat": near_lat, "lon": near_lon}],
    )
    root = run_process_gpx(gpx, config)
    assert get_track_points(root) == []
