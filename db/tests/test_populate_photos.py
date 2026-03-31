"""Tests for _apply_transition_corrections in populate_photos.py."""

from datetime import datetime, timezone

import pytest

import db.populate_photos as populate_photos
from db.populate_photos import _apply_transition_corrections


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_waypoint(id, trip_id, name, start_ymd, lat, lon, end_ymd=None):
    """Build a waypoint dict. start_ymd / end_ymd are (year, month, day) tuples (UTC)."""

    def to_utc(ymd):
        return datetime(*ymd, tzinfo=timezone.utc) if ymd else None

    return {
        "id": id,
        "trip_id": trip_id,
        "name": name,
        "start_time": to_utc(start_ymd),
        "end_time": to_utc(end_ymd),
        "lat": lat,
        "lon": lon,
    }


def make_photo(filename, local_dt, lat, lon, waypoint_id):
    """Build a photo dict. local_dt is a naive datetime (local time at photo location)."""
    return {
        "filename": filename,
        "caption": None,
        "time_taken": local_dt.replace(tzinfo=timezone.utc) if local_dt else None,
        "time_taken_local": local_dt,
        "time_taken_local_tz": "UTC",
        "lat": lat,
        "lon": lon,
        "location_wkt": f"POINT({lon} {lat})" if lat is not None else None,
        "location_metadata": None,
        "waypoint_id": waypoint_id,
    }


# ---------------------------------------------------------------------------
# Standard test fixtures
#
# WP_A is at (0.0, 0.0), WP_B is at (1.0, 0.0).
# 1 degree latitude ≈ 111 km, so A→B ≈ 111 km and threshold ≈ 11.1 km.
#
# Photo coordinates used throughout:
#   NEAR_A   lat=0.05  →  ~5.6 km from A  (below threshold, stays in A)
#   PAST_THR lat=0.15  →  ~16.7 km from A (above threshold, triggers move to B)
#   NEAR_B   lat=0.95  →  ~105 km from A  (far from A, clearly in B)
#
# The transition date is 2024-03-04 (the UTC date of WP_B's start_time).
# ---------------------------------------------------------------------------

WP_A = make_waypoint(1, 1, "Place A", (2024, 3, 3), 0.0, 0.0, end_ymd=(2024, 3, 4))
WP_B = make_waypoint(2, 1, "Place B", (2024, 3, 4), 1.0, 0.0, end_ymd=(2024, 3, 5))

NEAR_A = (0.05, 0.0)
PAST_THR = (0.15, 0.0)
NEAR_B = (0.95, 0.0)

# Shorthand: local naive datetime on the transition day
def on_transition(hour, minute=0):
    return datetime(2024, 3, 4, hour, minute)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTransitionCorrections:
    def test_photos_near_a_stay_in_a(self):
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(11), *NEAR_A, waypoint_id=1),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 1

    def test_photos_past_threshold_reassigned_to_b(self):
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *PAST_THR, waypoint_id=1),
            make_photo("p3.jpg", on_transition(17), *NEAR_B, waypoint_id=2),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 2
        assert photos[2]["waypoint_id"] == 2

    def test_monotonic_all_after_transition_go_to_b(self):
        # Once transition triggers, every subsequent photo goes to B
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *PAST_THR, waypoint_id=1),
            make_photo("p3.jpg", on_transition(15), *NEAR_B, waypoint_id=1),
            make_photo("p4.jpg", on_transition(18), *NEAR_B, waypoint_id=1),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 2
        assert photos[2]["waypoint_id"] == 2
        assert photos[3]["waypoint_id"] == 2

    def test_monotonic_near_a_after_transition_stays_b(self, caplog):
        # A post-transition photo that drifts back near A stays assigned to B
        # and triggers a warning
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *PAST_THR, waypoint_id=1),
            make_photo("p3.jpg", on_transition(16), *NEAR_A, waypoint_id=2),  # near A again
        ]
        with caplog.at_level("WARNING", logger="db.populate_photos"):
            _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[2]["waypoint_id"] == 2  # stays B despite being near A
        assert "p3.jpg" in caplog.text
        assert "after transition point" in caplog.text

    def test_no_transition_when_all_photos_near_a(self):
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *NEAR_A, waypoint_id=1),
            make_photo("p3.jpg", on_transition(18), *NEAR_A, waypoint_id=1),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert all(p["waypoint_id"] == 1 for p in photos)

    def test_non_transition_day_photos_not_affected(self):
        # Photos on the day before and after the transition date are not touched
        photos = [
            make_photo("prev.jpg", datetime(2024, 3, 3, 14), *NEAR_B, waypoint_id=1),
            make_photo("next.jpg", datetime(2024, 3, 5, 10), *NEAR_A, waypoint_id=2),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 2

    def test_photo_without_gps_is_skipped(self, caplog):
        photos = [
            make_photo("no_gps.jpg", on_transition(10), None, None, waypoint_id=1),
            make_photo("p2.jpg", on_transition(14), *PAST_THR, waypoint_id=1),
        ]
        with caplog.at_level("WARNING", logger="db.populate_photos"):
            _apply_transition_corrections(photos, [WP_A, WP_B])
        assert "no_gps.jpg" in caplog.text
        assert photos[0]["waypoint_id"] == 1  # unchanged
        assert photos[1]["waypoint_id"] == 2  # still transitions correctly

    def test_photo_without_local_time_is_skipped(self):
        # Photos with no time_taken_local are not considered candidates
        photos = [
            make_photo("no_time.jpg", None, *NEAR_B, waypoint_id=1),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1

    def test_exempted_photo_does_not_trigger_transition(self, monkeypatch):
        # Photos at or before the exemption end time are pinned to A.
        # A past-threshold photo within the exemption window should not trigger
        # the transition — the temporarily-away-from-A excursion is ignored.
        monkeypatch.setattr(
            populate_photos,
            "TRANSITION_EXEMPTIONS",
            ["2024-03-04 14.00.00"],
        )
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *PAST_THR, waypoint_id=1),  # exempted
            make_photo("p3.jpg", on_transition(16), *NEAR_A, waypoint_id=1),  # returned to A
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 1  # exempted, stays A
        assert photos[2]["waypoint_id"] == 1  # no transition was triggered

    def test_transition_fires_after_exemption_end(self, monkeypatch):
        # Photos after the exemption end time are subject to normal transition logic.
        # The transition should trigger on the first post-exemption photo past threshold.
        monkeypatch.setattr(
            populate_photos,
            "TRANSITION_EXEMPTIONS",
            ["2024-03-04 15.00.00"],
        )
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *PAST_THR, waypoint_id=1),  # exempted
            make_photo("p3.jpg", on_transition(17), *PAST_THR, waypoint_id=1),  # triggers
            make_photo("p4.jpg", on_transition(19), *NEAR_B, waypoint_id=1),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B])
        assert photos[0]["waypoint_id"] == 1
        assert photos[1]["waypoint_id"] == 1  # exempted, stays A
        assert photos[2]["waypoint_id"] == 2  # triggered transition
        assert photos[3]["waypoint_id"] == 2

    def test_multi_transition_day_uses_first_and_last(self, caplog):
        # Three waypoints A→B→C all starting on the same UTC date: only A→C is used.
        wp_c = make_waypoint(3, 1, "Place C", (2024, 3, 4), 2.0, 0.0, end_ymd=(2024, 3, 5))
        # WP_B also starts on 2024-03-04, so both A→B and B→C share the same transition date.
        # Expected: effective pair is A→C (first→last); threshold based on A→C distance.
        # A→C ≈ 222 km; threshold ≈ 22.2 km. PAST_THR (16.7 km) is now below threshold.
        # NEAR_B (105 km) is above threshold and should go to C.
        photos = [
            make_photo("p1.jpg", on_transition(9), *NEAR_A, waypoint_id=1),
            make_photo("p2.jpg", on_transition(13), *NEAR_B, waypoint_id=2),
        ]
        with caplog.at_level("WARNING", logger="db.populate_photos"):
            _apply_transition_corrections(photos, [WP_A, WP_B, wp_c])
        assert "Multiple waypoint transitions" in caplog.text
        assert photos[0]["waypoint_id"] == 1   # near A, stays A
        assert photos[1]["waypoint_id"] == 3   # near B but reassigned to C

    def test_different_trips_do_not_interfere(self):
        # Two trips with transitions on different dates — each trip's correction
        # should only affect photos on its own transition date.
        # Trip 1: A→B with transition on 2024-03-04
        # Trip 2: C→D with transition on 2024-04-10 (different date entirely)
        wp_c = make_waypoint(10, 2, "Place C", (2024, 4, 9), 0.0, 0.0, end_ymd=(2024, 4, 10))
        wp_d = make_waypoint(11, 2, "Place D", (2024, 4, 10), 1.0, 0.0, end_ymd=(2024, 4, 11))
        photos = [
            # Trip 1 photo on its transition date: should transition A→B
            make_photo("t1.jpg", on_transition(13), *PAST_THR, waypoint_id=1),
            # Trip 2 photo on its own transition date (April 10): should transition C→D
            make_photo("t2.jpg", datetime(2024, 4, 10, 13), *PAST_THR, waypoint_id=10),
        ]
        _apply_transition_corrections(photos, [WP_A, WP_B, wp_c, wp_d])
        assert photos[0]["waypoint_id"] == 2   # trip 1: transitioned to B
        assert photos[1]["waypoint_id"] == 11  # trip 2: transitioned to D

    def test_waypoints_without_gps_are_skipped(self):
        wp_no_gps = make_waypoint(3, 1, "No GPS", (2024, 3, 4), None, None)
        wp_dest = make_waypoint(4, 1, "Dest", (2024, 3, 5), 1.0, 0.0)
        photos = [
            make_photo("p1.jpg", on_transition(13), *PAST_THR, waypoint_id=3),
        ]
        # Should not raise; the no-GPS pair is skipped silently
        _apply_transition_corrections(photos, [wp_no_gps, wp_dest])
        assert photos[0]["waypoint_id"] == 3  # unchanged
