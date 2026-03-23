import pytest

from scripts.gps_utils import (
    calculate_destination_point,
    haversine_distance,
    normalize_longitude,
)


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
