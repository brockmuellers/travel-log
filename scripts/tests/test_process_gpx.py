import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from lib.gps_utils import calculate_destination_point, haversine_distance
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


# --- process_gpx fixtures ---

# Zone center used for radius calculations
ZONE_LAT, ZONE_LON = 10.0, 20.0
RADIUS = 1.0  # km

# The waypoint sits at the zone center; this is also the coord that must
# appear as a <trkpt> for the exact-match to work.
WPT_LAT, WPT_LON = ZONE_LAT, ZONE_LON

# Precomputed points at known distances from the zone center
INSIDE_LAT, INSIDE_LON = calculate_destination_point(ZONE_LAT, ZONE_LON, 0.5, 0)  # 0.5km — inside
OUTSIDE_LAT, OUTSIDE_LON = calculate_destination_point(ZONE_LAT, ZONE_LON, 2.0, 0)  # 2.0km — outside

NAMED_ZONE = {
    "key": "Test Location",
    "name": "Test Location",
    "lat": ZONE_LAT,
    "lon": ZONE_LON,
    "radius": RADIUS,
    "displacement": 5.0,
    "bearing": 90,
}

GHOST_ZONE = {
    "key": "Ghost Zone",
    # no "name" — this is a ghost zone
    "lat": ZONE_LAT,
    "lon": ZONE_LON,
    "radius": RADIUS,
    "displacement": 5.0,
    "bearing": 90,
}


# --- process_gpx: waypoint obfuscation ---


def test_named_waypoint_is_obfuscated():
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[{"lat": WPT_LAT, "lon": WPT_LON}],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    wpts = get_waypoints(root)
    assert len(wpts) == 1
    assert wpts[0]["lat"] != WPT_LAT or wpts[0]["lon"] != WPT_LON


def test_unnamed_waypoint_is_unchanged():
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Somewhere Else"}],
        track_points=[],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    wpts = get_waypoints(root)
    assert wpts[0]["lat"] == WPT_LAT
    assert wpts[0]["lon"] == WPT_LON


def test_waypoint_obfuscation_applies_zone_displacement_and_bearing():
    from lib.gps_utils import compute_obfuscated_location

    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[{"lat": WPT_LAT, "lon": WPT_LON}],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    expected_lat, expected_lon = compute_obfuscated_location(NAMED_ZONE, WPT_LAT, WPT_LON)
    wpt = get_waypoints(root)[0]
    assert wpt["lat"] == expected_lat
    assert wpt["lon"] == expected_lon


# --- process_gpx: track point handling ---


def test_matching_track_point_is_transformed():
    from lib.gps_utils import compute_obfuscated_location

    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    expected_lat, expected_lon = compute_obfuscated_location(NAMED_ZONE, WPT_LAT, WPT_LON)
    trkpts = get_track_points(root)
    assert (expected_lat, expected_lon) in trkpts


def test_points_inside_radius_are_deleted():
    # All points within the radius (before and after the match) are removed.
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": INSIDE_LAT, "lon": INSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": INSIDE_LAT, "lon": INSIDE_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    trkpts = get_track_points(root)
    assert (INSIDE_LAT, INSIDE_LON) not in trkpts


def test_non_adjacent_points_inside_radius_are_also_deleted():
    # A point inside the radius separated from the match by an outside point
    # is still deleted — we scan all points, not just adjacent ones.
    far_inside_lat, far_inside_lon = calculate_destination_point(ZONE_LAT, ZONE_LON, 0.9, 180)
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": far_inside_lat, "lon": far_inside_lon},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
        ],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    trkpts = get_track_points(root)
    assert (far_inside_lat, far_inside_lon) not in trkpts


def test_multi_visit_waypoints_are_not_deleted_by_each_other():
    # When the same named zone is visited twice, both <wpt> entries have slightly
    # different GPS coords but both fall within the zone radius. The radius scan
    # for visit 1 must not delete the track point belonging to visit 2, and vice versa.
    wpt2_lat, wpt2_lon = calculate_destination_point(ZONE_LAT, ZONE_LON, 0.2, 45)
    zone = {**NAMED_ZONE}  # same zone handles both visits

    gpx = make_gpx(
        waypoints=[
            {"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"},
            {"lat": wpt2_lat, "lon": wpt2_lon, "name": "Test Location"},
        ],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": wpt2_lat, "lon": wpt2_lon},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    # Should not raise "no matching track point"
    root = run_process_gpx(gpx, [zone])
    from lib.gps_utils import compute_obfuscated_location
    obf1 = compute_obfuscated_location(zone, WPT_LAT, WPT_LON)
    obf2 = compute_obfuscated_location(zone, wpt2_lat, wpt2_lon)
    trkpts = get_track_points(root)
    assert obf1 in trkpts
    assert obf2 in trkpts


def test_points_outside_radius_are_preserved():
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [NAMED_ZONE])
    trkpts = get_track_points(root)
    assert trkpts.count((OUTSIDE_LAT, OUTSIDE_LON)) == 2


def test_error_on_multiple_matching_track_points():
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
        ],
    )
    with pytest.raises(SystemExit, match="Multiple track points match waypoint"):
        run_process_gpx(gpx, [NAMED_ZONE])


def test_error_on_unmatched_waypoint():
    # Waypoint is in the sensitive zones, but no track point has those exact coords.
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[{"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON}],
    )
    with pytest.raises(SystemExit, match="no matching track point"):
        run_process_gpx(gpx, [NAMED_ZONE])


def test_error_on_transport_mode_in_adjacent_point():
    # A transport-tagged point inside the radius should abort — we don't know
    # how to safely relocate it.
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Test Location"}],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": INSIDE_LAT, "lon": INSIDE_LON, "transport": "flight"},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    with pytest.raises(SystemExit, match="transport modes is not yet supported"):
        run_process_gpx(gpx, [NAMED_ZONE])


# --- process_gpx: ghost zones ---


def test_ghost_zone_deletes_points_inside_radius():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": INSIDE_LAT, "lon": INSIDE_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [GHOST_ZONE])
    trkpts = get_track_points(root)
    assert (INSIDE_LAT, INSIDE_LON) not in trkpts


def test_ghost_zone_preserves_points_outside_radius():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": INSIDE_LAT, "lon": INSIDE_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [GHOST_ZONE])
    trkpts = get_track_points(root)
    assert trkpts.count((OUTSIDE_LAT, OUTSIDE_LON)) == 2


def test_ghost_zone_overlapping_waypoint_warns_not_errors(capsys):
    # Ghost zone overlapping a waypoint is a misconfiguration, but we warn
    # rather than abort so we can still test while the data is being fixed.
    # The waypoint track point must NOT be deleted.
    gpx = make_gpx(
        waypoints=[{"lat": WPT_LAT, "lon": WPT_LON, "name": "Somewhere Else"}],
        track_points=[
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
            {"lat": WPT_LAT, "lon": WPT_LON},
            {"lat": OUTSIDE_LAT, "lon": OUTSIDE_LON},
        ],
    )
    root = run_process_gpx(gpx, [GHOST_ZONE])
    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out
    trkpts = get_track_points(root)
    assert (WPT_LAT, WPT_LON) in trkpts


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
    assert features[1]["properties"]["transport"] is None
    assert len(features) == 3


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


def test_geojson_single_point_run_is_not_skipped():
    gpx = make_gpx(
        waypoints=[],
        track_points=[
            {"lat": 1.0, "lon": 10.0, "transport": "flight"},
            {"lat": 2.0, "lon": 11.0, "transport": "train"},
            {"lat": 3.0, "lon": 12.0, "transport": "train"},
        ],
    )
    features = gpx_to_geojson(parse_gpx(gpx))["features"]
    # flight: [point1, point2(shared)] = 2 coords
    # train:  [point2(shared), point3] = 2 coords
    assert features[0]["properties"]["transport"] == "flight"
    assert len(features[0]["geometry"]["coordinates"]) == 2
    assert features[1]["properties"]["transport"] == "train"
    assert len(features[1]["geometry"]["coordinates"]) == 2
