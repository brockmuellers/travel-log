import csv
import json
import tempfile
from pathlib import Path

import pytest

from scripts.gps_utils import calculate_destination_point, haversine_distance
from scripts.inaturalist_to_geojson import (
    apply_obfuscation,
    build_sensitive_zones,
    convert_inat_csv_to_geojson,
)

# --- Helpers ---

SENSITIVE_CONFIG = {
    "My House": {"name": "My House", "seed": 42, "radius": 5, "lat": 40.0, "lon": -75.0}
}


def make_csv(rows: list[dict]) -> str:
    """Build a minimal iNaturalist CSV string."""
    fieldnames = ["latitude", "longitude", "common_name", "scientific_name",
                  "species_guess", "taxon_id", "image_url", "url", "observed_on"]
    lines = [",".join(fieldnames)]
    for row in rows:
        lines.append(",".join(str(row.get(f, "")) for f in fieldnames))
    return "\n".join(lines)


def run_convert(csv_content: str, zones=None) -> list[dict]:
    """Write csv_content to a temp file, run conversion, return features list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "obs.csv"
        output_path = Path(tmpdir) / "out.geojson"
        taxa_path = Path(tmpdir) / "taxa.json"
        input_path.write_text(csv_content, encoding="utf-8")
        taxa_path.write_text("[]", encoding="utf-8")
        convert_inat_csv_to_geojson(str(input_path), str(output_path), str(taxa_path), zones)
        return json.loads(output_path.read_text())["features"]


# --- build_sensitive_zones ---


def test_build_sensitive_zones_with_explicit_coords():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    assert len(zones) == 1
    z = zones[0]
    assert z["lat"] == 40.0
    assert z["lon"] == -75.0
    assert z["radius"] == 5


def test_build_sensitive_zones_skips_entries_without_coords():
    config = {"Named Only": {"name": "Named Only", "seed": 1, "radius": 5}}
    zones = build_sensitive_zones(config)
    assert zones == []


def test_build_sensitive_zones_fake_location_within_range():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    z = zones[0]
    dist = haversine_distance(40.0, -75.0, z["fake_lat"], z["fake_lon"])
    assert 5 * 0.75 <= dist <= 5 + 0.001


def test_build_sensitive_zones_is_deterministic():
    zones1 = build_sensitive_zones(SENSITIVE_CONFIG)
    zones2 = build_sensitive_zones(SENSITIVE_CONFIG)
    assert zones1 == zones2


# --- apply_obfuscation ---


def test_apply_obfuscation_moves_point_inside_radius():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    # A point 1 km from the sensitive location (well within 5 km radius)
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    result_lat, result_lon = apply_obfuscation(near_lat, near_lon, zones)
    assert (result_lat, result_lon) == (zones[0]["fake_lat"], zones[0]["fake_lon"])


def test_apply_obfuscation_leaves_point_outside_radius():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 20.0, 90.0)
    result_lat, result_lon = apply_obfuscation(far_lat, far_lon, zones)
    assert result_lat == pytest.approx(far_lat)
    assert result_lon == pytest.approx(far_lon)


def test_apply_obfuscation_all_points_in_zone_get_same_fake_location():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    pt1_lat, pt1_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    pt2_lat, pt2_lon = calculate_destination_point(40.0, -75.0, 2.0, 180.0)
    result1 = apply_obfuscation(pt1_lat, pt1_lon, zones)
    result2 = apply_obfuscation(pt2_lat, pt2_lon, zones)
    assert result1 == result2


# --- convert_inat_csv_to_geojson ---


def test_observation_inside_zone_is_moved():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    csv = make_csv([{"latitude": near_lat, "longitude": near_lon, "common_name": "Robin"}])
    features = run_convert(csv, zones)
    assert len(features) == 1
    out_lon, out_lat = features[0]["geometry"]["coordinates"]
    assert out_lat == pytest.approx(zones[0]["fake_lat"])
    assert out_lon == pytest.approx(zones[0]["fake_lon"])


def test_observation_outside_zone_is_unchanged():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 20.0, 90.0)
    csv = make_csv([{"latitude": far_lat, "longitude": far_lon, "common_name": "Robin"}])
    features = run_convert(csv, zones)
    out_lon, out_lat = features[0]["geometry"]["coordinates"]
    assert out_lat == pytest.approx(far_lat)
    assert out_lon == pytest.approx(far_lon)


def test_no_zones_leaves_all_observations_unchanged():
    lat, lon = 40.0, -75.0
    csv = make_csv([{"latitude": lat, "longitude": lon, "common_name": "Robin"}])
    features = run_convert(csv, zones=None)
    out_lon, out_lat = features[0]["geometry"]["coordinates"]
    assert out_lat == pytest.approx(lat)
    assert out_lon == pytest.approx(lon)
