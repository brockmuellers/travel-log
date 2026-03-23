import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile

import pytest

from scripts.gps_utils import calculate_destination_point, haversine_distance
from scripts.process_gpx import gpx_to_geojson, process_gpx

NS = {"gpx": "http://www.topografix.com/GPX/1/1"}

# --- Helpers ---


def make_gpx(waypoints: list[dict], track_points: list[dict]) -> str:
    """Build a minimal GPX 1.1 XML string.

    Each track_point dict has "lat", "lon", and optionally "transport".
    """
    wpt_tags = ""
    for w in waypoints:
        wpt_tags += f'  <wpt lat="{w["lat"]}" lon="{w["lon"]}"><name>{w["name"]}</name></wpt>\n'

    trkpt_tags = ""
    for p in track_points:
        transport = p.get("transport")
        ext = f"<extension><transport>{transport}</transport></extension>" if transport else ""
        trkpt_tags += f'    <trkpt lat="{p["lat"]}" lon="{p["lon"]}">{ext}</trkpt>\n'

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
    """Write gpx_content to a temp file, run process_gpx, return the modified root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.gpx"
        input_path.write_text(gpx_content, encoding="utf-8")
        return process_gpx(str(input_path), config)


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


# --- gpx_to_geojson ---


def parse_gpx(gpx_content: str) -> ET.Element:
    return ET.fromstring(gpx_content)


def test_geojson_single_mode_produces_one_feature():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "flight"},
            {"lat": 3.0, "lon": 12.0, "transport": "flight"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    assert len(features) == 1
    assert features[0]["properties"]["transport"] == "flight"
    assert features[0]["geometry"]["type"] == "LineString"
    assert len(features[0]["geometry"]["coordinates"]) == 3


def test_geojson_mode_change_splits_into_two_features():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "flight"},
            {"lat": 3.0, "lon": 12.0, "transport": "train"},
            {"lat": 4.0, "lon": 13.0, "transport": "train"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    assert len(features) == 2
    assert features[0]["properties"]["transport"] == "flight"
    assert features[1]["properties"]["transport"] == "train"


def test_geojson_transition_point_is_shared():
    # The last coord of segment N equals the first coord of segment N+1
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "flight"},
            {"lat": 3.0, "lon": 12.0, "transport": "train"},
            {"lat": 4.0, "lon": 13.0, "transport": "train"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    last_of_flight = features[0]["geometry"]["coordinates"][-1]
    first_of_train = features[1]["geometry"]["coordinates"][0]
    assert last_of_flight == first_of_train


def test_geojson_no_transport_tag_produces_null_transport_segment():
    # A point without a transport tag forms its own segment with transport=null
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "flight"},
            {"lat": 3.0, "lon": 12.0},  # no transport
            {"lat": 4.0, "lon": 13.0, "transport": "train"},
            {"lat": 5.0, "lon": 14.0, "transport": "train"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    transports = [f["properties"]["transport"] for f in features]
    assert None in transports


def test_geojson_coordinates_are_lon_lat_order():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "flight"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    first_coord = features[0]["geometry"]["coordinates"][0]
    assert first_coord == [10.0, 1.0]  # [lon, lat]


def test_geojson_empty_segment_produces_no_features():
    gpx = make_gpx(waypoints=[], track_points=[])
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    assert features == []


def test_geojson_single_point_run_is_skipped():
    # A run of only one point cannot form a LineString and is dropped
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "train"},
            {"lat": 3.0, "lon": 12.0, "transport": "train"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    # The flight run has only 1 point (+ the shared transition to train = 2 coords).
    # The train run has 2 points + the shared transition from flight.
    transports = [f["properties"]["transport"] for f in features]
    assert "train" in transports
