"""Tests for the parsing and transform functions in populate_waypoints.py."""

import json
import textwrap
from pathlib import Path

import pytest

from db.populate_waypoints import backfill_end_times, parse_fp_gpx, parse_manual_trips


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_GPX = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8" standalone="no" ?>
    <gpx version="1.1"
         xmlns="http://www.topografix.com/GPX/1/1"
         creator="FindPenguins">
      <metadata><name>Test Trip</name></metadata>
      <wpt lat="45.0" lon="-79.0">
        <name>Place A</name>
        <desc>First stop</desc>
        <time>2024-10-01T12:00:00+00:00</time>
      </wpt>
      <wpt lat="46.0" lon="-80.0">
        <name>Place B</name>
        <time>2024-10-05T12:00:00+00:00</time>
      </wpt>
      <wpt lat="47.0" lon="-81.0">
        <name>Place C</name>
        <time>2024-10-10T12:00:00+00:00</time>
      </wpt>
      <trk><trkseg>
        <trkpt lat="45.0" lon="-79.0"><time>2024-10-05T12:00:00+00:00</time></trkpt>
        <trkpt lat="45.5" lon="-79.5"><time>2024-10-05T12:00:00+00:00</time></trkpt>
        <trkpt lat="46.0" lon="-80.0"><time>2024-10-05T12:00:00+00:00</time></trkpt>
      </trkseg></trk>
      <trk><trkseg>
        <trkpt lat="46.0" lon="-80.0"><time>2024-10-10T12:00:00+00:00</time></trkpt>
        <trkpt lat="47.0" lon="-81.0"><time>2024-10-10T12:00:00+00:00</time></trkpt>
      </trkseg></trk>
    </gpx>
""")

MINIMAL_MANUAL_JSON = [
    {
        "name": "Pre-trip",
        "waypoints": [
            {
                "name": "Home",
                "lat": 37.77,
                "lon": -122.42,
                "start_time": "2024-09-01T00:00:00Z",
            },
            {
                "name": "Nearby",
                "lat": 37.80,
                "lon": -122.40,
                "start_time": "2024-09-15T00:00:00Z",
                "track_to_here": [
                    {"lat": 37.77, "lon": -122.42},
                    {"lat": 37.80, "lon": -122.40},
                ],
            },
        ],
    }
]


# ---------------------------------------------------------------------------
# parse_fp_gpx
# ---------------------------------------------------------------------------


class TestParseFpGpx:
    def test_trip_name_and_source(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)

        assert trip["name"] == "Test Trip"
        assert trip["source"] == "findpenguins"

    def test_trip_key_slugified(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)

        assert trip["key"] == "test-trip"

    def test_waypoint_count_and_fields(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)
        wps = trip["waypoints"]

        assert len(wps) == 3
        assert wps[0]["name"] == "Place A"
        assert wps[0]["lat"] == 45.0
        assert wps[0]["lon"] == -79.0
        assert wps[0]["start_time"] == "2024-10-01T12:00:00+00:00"

    def test_description_preserved(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)

        assert trip["waypoints"][0]["description"] == "First stop"
        assert trip["waypoints"][1]["description"] is None

    def test_intra_trip_end_times(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)
        wps = trip["waypoints"]

        assert wps[0]["end_time"] == "2024-10-05T12:00:00+00:00"
        assert wps[1]["end_time"] == "2024-10-10T12:00:00+00:00"
        assert wps[2]["end_time"] is None  # last waypoint

    def test_track_assignment(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)
        wps = trip["waypoints"]

        # First waypoint has no track
        assert wps[0]["track_to_here"] is None

        # Second waypoint gets the 3-point track (keyed by its timestamp)
        assert wps[1]["track_to_here"] is not None
        assert len(wps[1]["track_to_here"]) == 3
        assert wps[1]["track_to_here"][0]["lat"] == 45.0

        # Third waypoint gets the 2-point track
        assert wps[2]["track_to_here"] is not None
        assert len(wps[2]["track_to_here"]) == 2

    def test_track_points_have_time(self, tmp_path: Path) -> None:
        gpx_file = tmp_path / "test.gpx"
        gpx_file.write_text(MINIMAL_GPX)

        trip = parse_fp_gpx(gpx_file)
        point = trip["waypoints"][1]["track_to_here"][0]

        assert "time" in point
        assert point["time"] == "2024-10-05T12:00:00+00:00"


# ---------------------------------------------------------------------------
# parse_manual_trips
# ---------------------------------------------------------------------------


class TestParseManualTrips:
    def test_basic_parsing(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(MINIMAL_MANUAL_JSON))

        trips = parse_manual_trips(json_file)

        assert len(trips) == 1
        assert trips[0]["name"] == "Pre-trip"
        assert trips[0]["source"] == "manual"

    def test_trip_key_auto_generated(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(MINIMAL_MANUAL_JSON))

        trips = parse_manual_trips(json_file)

        assert trips[0]["key"] == "pre-trip"

    def test_trip_key_explicit(self, tmp_path: Path) -> None:
        data = [{"name": "West Coast", "key": "west-coast", "waypoints": MINIMAL_MANUAL_JSON[0]["waypoints"]}]
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(data))

        trips = parse_manual_trips(json_file)

        assert trips[0]["key"] == "west-coast"

    def test_waypoint_fields(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(MINIMAL_MANUAL_JSON))

        wps = parse_manual_trips(json_file)[0]["waypoints"]

        assert len(wps) == 2
        assert wps[0]["name"] == "Home"
        assert wps[0]["lat"] == 37.77
        assert wps[0]["description"] is None

    def test_intra_trip_end_times(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(MINIMAL_MANUAL_JSON))

        wps = parse_manual_trips(json_file)[0]["waypoints"]

        # First wp end_time = second wp start_time
        assert wps[0]["end_time"] == "2024-09-15T00:00:00Z"
        # Last wp end_time is None (no next wp)
        assert wps[1]["end_time"] is None

    def test_explicit_end_time_honored(self, tmp_path: Path) -> None:
        data = [
            {
                "name": "Trip",
                "waypoints": [
                    {
                        "name": "A",
                        "lat": 1.0,
                        "lon": 2.0,
                        "start_time": "2024-01-01T00:00:00Z",
                        "end_time": "2024-01-03T00:00:00Z",
                    },
                    {
                        "name": "B",
                        "lat": 3.0,
                        "lon": 4.0,
                        "start_time": "2024-01-05T00:00:00Z",
                    },
                ],
            }
        ]
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(data))

        wps = parse_manual_trips(json_file)[0]["waypoints"]

        # Explicit end_time takes precedence over next wp's start_time
        assert wps[0]["end_time"] == "2024-01-03T00:00:00Z"

    def test_track_to_here_preserved(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text(json.dumps(MINIMAL_MANUAL_JSON))

        wps = parse_manual_trips(json_file)[0]["waypoints"]

        assert wps[0].get("track_to_here") is None
        assert wps[1]["track_to_here"] is not None
        assert len(wps[1]["track_to_here"]) == 2

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_manual_trips(tmp_path / "nonexistent.json")

    def test_empty_json(self, tmp_path: Path) -> None:
        json_file = tmp_path / "trips.json"
        json_file.write_text("[]")

        assert parse_manual_trips(json_file) == []


# ---------------------------------------------------------------------------
# backfill_end_times
# ---------------------------------------------------------------------------


class TestBackfillEndTimes:
    @staticmethod
    def _make_trip(name: str, wps: list[dict]) -> dict:
        return {"name": name, "source": "test", "waypoints": wps}

    @staticmethod
    def _make_wp(start: str, end: str | None = None) -> dict:
        return {
            "name": "wp",
            "lat": 0.0,
            "lon": 0.0,
            "start_time": start,
            "end_time": end,
        }

    def test_fills_none_from_next_trip(self) -> None:
        trips = [
            self._make_trip("A", [self._make_wp("2024-01-01", None)]),
            self._make_trip("B", [self._make_wp("2024-02-01")]),
        ]

        backfill_end_times(trips)

        assert trips[0]["waypoints"][-1]["end_time"] == "2024-02-01"

    def test_does_not_overwrite_existing(self) -> None:
        trips = [
            self._make_trip("A", [self._make_wp("2024-01-01", "2024-01-15")]),
            self._make_trip("B", [self._make_wp("2024-02-01")]),
        ]

        backfill_end_times(trips)

        assert trips[0]["waypoints"][-1]["end_time"] == "2024-01-15"

    def test_last_trip_stays_none(self) -> None:
        trips = [
            self._make_trip("A", [self._make_wp("2024-01-01", None)]),
            self._make_trip("B", [self._make_wp("2024-02-01", None)]),
        ]

        backfill_end_times(trips)

        assert trips[0]["waypoints"][-1]["end_time"] == "2024-02-01"
        assert trips[1]["waypoints"][-1]["end_time"] is None

    def test_empty_list(self) -> None:
        backfill_end_times([])  # should not raise

    def test_single_trip(self) -> None:
        trips = [self._make_trip("A", [self._make_wp("2024-01-01", None)])]

        backfill_end_times(trips)

        assert trips[0]["waypoints"][-1]["end_time"] is None

    def test_multi_waypoint_only_fills_last(self) -> None:
        trips = [
            self._make_trip(
                "A",
                [
                    self._make_wp("2024-01-01", "2024-01-10"),
                    self._make_wp("2024-01-10", None),
                ],
            ),
            self._make_trip("B", [self._make_wp("2024-02-01")]),
        ]

        backfill_end_times(trips)

        assert trips[0]["waypoints"][0]["end_time"] == "2024-01-10"  # unchanged
        assert trips[0]["waypoints"][1]["end_time"] == "2024-02-01"  # filled
