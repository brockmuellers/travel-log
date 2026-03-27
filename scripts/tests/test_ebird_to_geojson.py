import json
import tempfile
from pathlib import Path

import pytest

from lib.gps_utils import calculate_destination_point, compute_obfuscated_location
from scripts.ebird_to_geojson import (
    apply_obfuscation,
    build_sensitive_zones,
    convert_ebird_csv_to_geojson,
)

SENSITIVE_CONFIG = [
    {"key": "My House", "displacement": 3.5, "bearing": 45.0, "radius": 5, "lat": 40.0, "lon": -75.0}
]

EBIRD_FIELDNAMES = [
    "Submission ID", "Common Name", "Scientific Name", "Taxonomic Order",
    "Count", "State/Province", "County", "Location ID", "Location",
    "Latitude", "Longitude", "Date", "Time", "Protocol", "Duration (Min)",
    "All Obs Reported", "Distance Traveled (km)", "Area Covered (ha)",
    "Number of Observers", "Breeding Code", "Observation Details",
    "Checklist Comments", "ML Catalog Numbers",
]


def make_csv(rows: list[dict]) -> str:
    """Build a minimal eBird CSV string."""
    lines = [",".join(EBIRD_FIELDNAMES)]
    for row in rows:
        lines.append(",".join(str(row.get(f, "")) for f in EBIRD_FIELDNAMES))
    return "\n".join(lines)


def run_convert(csv_content: str, zones=None) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "ebird.csv"
        output_path = Path(tmpdir) / "out.geojson"
        input_path.write_text(csv_content, encoding="utf-8")
        convert_ebird_csv_to_geojson(str(input_path), str(output_path), zones)
        return json.loads(output_path.read_text())["features"]


# --- build_sensitive_zones ---


def test_build_sensitive_zones_returns_one_zone():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    assert len(zones) == 1
    z = zones[0]
    assert z["lat"] == 40.0
    assert z["lon"] == -75.0
    assert z["radius"] == 5


# --- apply_obfuscation ---


def test_apply_obfuscation_moves_point_inside_radius():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    result_lat, result_lon = apply_obfuscation(near_lat, near_lon, zones)
    expected_lat, expected_lon = compute_obfuscated_location(zones[0], near_lat, near_lon)
    assert result_lat == pytest.approx(expected_lat)
    assert result_lon == pytest.approx(expected_lon)


def test_apply_obfuscation_leaves_point_outside_radius():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 20.0, 90.0)
    result_lat, result_lon = apply_obfuscation(far_lat, far_lon, zones)
    assert result_lat == pytest.approx(far_lat)
    assert result_lon == pytest.approx(far_lon)


# --- convert_ebird_csv_to_geojson ---


def test_single_checklist_single_species():
    csv = make_csv([{
        "Submission ID": "S123", "Common Name": "Robin", "Location ID": "L1",
        "Location": "City Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01",
    }])
    features = run_convert(csv)
    assert len(features) == 1
    f = features[0]
    assert f["properties"]["title"] == "City Park"
    assert f["properties"]["location_id"] == "L1"
    assert f["properties"]["hotspot_url"] == "https://ebird.org/hotspot/L1"
    assert f["properties"]["checklists"] == [
        {
            "id": "S123", "url": "https://ebird.org/checklist/S123",
            "date": "2025-01-01", "start_time": "", "duration_min": None,
            "individual_count": 0, "species_count": 1,
        }
    ]
    assert f["properties"]["species_count"] == 1
    assert f["properties"]["species"] == [{"common_name": "Robin", "scientific_name": ""}]


def test_multiple_species_at_same_hotspot_aggregated():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
        {"Submission ID": "S1", "Common Name": "Sparrow", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
    ])
    features = run_convert(csv)
    assert len(features) == 1
    assert features[0]["properties"]["species_count"] == 2
    assert len(features[0]["properties"]["checklists"]) == 1
    assert features[0]["properties"]["checklists"][0]["id"] == "S1"
    assert features[0]["properties"]["checklists"][0]["species_count"] == 2


def test_two_distinct_hotspots_produce_two_features():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
         "Location": "Park A", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
        {"Submission ID": "S2", "Common Name": "Eagle", "Location ID": "L2",
         "Location": "Park B", "Latitude": 46.0, "Longitude": -74.0, "Date": "2025-02-01"},
    ])
    features = run_convert(csv)
    assert len(features) == 2


def test_min_max_date_single_checklist():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-03-01",
    }])
    features = run_convert(csv)
    assert features[0]["properties"]["min_date"] == "2025-03-01"
    assert features[0]["properties"]["max_date"] == "2025-03-01"


def test_min_max_date_multiple_checklists():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
        {"Submission ID": "S2", "Common Name": "Sparrow", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-06-15"},
    ])
    features = run_convert(csv)
    assert features[0]["properties"]["min_date"] == "2025-01-01"
    assert features[0]["properties"]["max_date"] == "2025-06-15"


def test_multiple_checklists_at_same_hotspot_merged():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
        {"Submission ID": "S2", "Common Name": "Sparrow", "Location ID": "L1",
         "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-02-01"},
    ])
    features = run_convert(csv)
    assert len(features) == 1
    checklists = features[0]["properties"]["checklists"]
    assert {c["id"] for c in checklists} == {"S1", "S2"}
    assert features[0]["properties"]["species_count"] == 2
    assert {c["date"] for c in checklists} == {"2025-01-01", "2025-02-01"}


def test_checklist_fields_duration_count_time_scientific_name():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Scientific Name": "Turdus migratorius",
         "Location ID": "L1", "Location": "Park", "Latitude": 45.0, "Longitude": -73.0,
         "Date": "2025-01-01", "Time": "07:30", "Duration (Min)": "45", "Count": "3"},
        {"Submission ID": "S1", "Common Name": "Sparrow", "Scientific Name": "Passer domesticus",
         "Location ID": "L1", "Location": "Park", "Latitude": 45.0, "Longitude": -73.0,
         "Date": "2025-01-01", "Time": "07:30", "Duration (Min)": "45", "Count": "X"},
    ])
    features = run_convert(csv)
    cl = features[0]["properties"]["checklists"][0]
    assert cl["start_time"] == "07:30"
    assert cl["duration_min"] == 45
    assert cl["individual_count"] == 3  # "X" count skipped
    assert cl["species_count"] == 2
    species = features[0]["properties"]["species"]
    assert {"common_name": "Robin", "scientific_name": "Turdus migratorius"} in species
    assert {"common_name": "Sparrow", "scientific_name": "Passer domesticus"} in species


def test_hotspot_inside_zone_is_moved():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    near_lat, near_lon = calculate_destination_point(40.0, -75.0, 1.0, 0.0)
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Secret Spot", "Latitude": near_lat, "Longitude": near_lon, "Date": "2025-01-01",
    }])
    features = run_convert(csv, zones)
    out_lon, out_lat = features[0]["geometry"]["coordinates"]
    expected_lat, expected_lon = compute_obfuscated_location(zones[0], near_lat, near_lon)
    assert out_lat == pytest.approx(expected_lat)
    assert out_lon == pytest.approx(expected_lon)


def test_hotspot_outside_zone_is_unchanged():
    zones = build_sensitive_zones(SENSITIVE_CONFIG)
    far_lat, far_lon = calculate_destination_point(40.0, -75.0, 20.0, 90.0)
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Far Park", "Latitude": far_lat, "Longitude": far_lon, "Date": "2025-01-01",
    }])
    features = run_convert(csv, zones)
    out_lon, out_lat = features[0]["geometry"]["coordinates"]
    assert out_lat == pytest.approx(far_lat)
    assert out_lon == pytest.approx(far_lon)


def test_row_with_missing_coordinates_is_skipped():
    csv = make_csv([
        {"Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
         "Location": "Park", "Latitude": "", "Longitude": "", "Date": "2025-01-01"},
        {"Submission ID": "S2", "Common Name": "Eagle", "Location ID": "L2",
         "Location": "Lake", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-01-01"},
    ])
    features = run_convert(csv)
    assert len(features) == 1
    assert features[0]["properties"]["location_id"] == "L2"


def test_row_with_null_island_is_skipped():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Null Island", "Latitude": 0.0, "Longitude": 0.0, "Date": "2025-01-01",
    }])
    features = run_convert(csv)
    assert len(features) == 0


# --- date filtering ---


def test_checklist_within_date_range_is_included():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2024-07-20",
    }])
    features = run_convert(csv)
    assert len(features) == 1


def test_checklist_before_date_range_is_excluded():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2024-07-19",
    }])
    features = run_convert(csv)
    assert len(features) == 0


def test_checklist_after_date_range_is_excluded():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-12-02",
    }])
    features = run_convert(csv)
    assert len(features) == 0


def test_checklist_on_last_day_of_range_is_included():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2025-12-01",
    }])
    features = run_convert(csv)
    assert len(features) == 1


def test_hotspot_with_all_checklists_outside_range_produces_no_feature():
    csv = make_csv([{
        "Submission ID": "S1", "Common Name": "Robin", "Location ID": "L1",
        "Location": "Park", "Latitude": 45.0, "Longitude": -73.0, "Date": "2023-01-01",
    }])
    features = run_convert(csv)
    assert len(features) == 0
