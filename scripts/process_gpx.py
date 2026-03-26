import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from lib.gps_utils import (calculate_destination_point,
                           compute_obfuscated_location, haversine_distance,
                           load_sensitive_zones)

# XML Namespace for GPX 1.1
NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
ET.register_namespace("", NS["gpx"])


def process_gpx(
    input_file: str | Path, sensitive_zones: list[dict[str, Any]]
) -> ET.Element:
    print(f"Reading GPX: {input_file}")
    tree = ET.parse(input_file)
    root = tree.getroot()

    # Sensitive zones come in two flavors:
    # - Named zones: matched to <wpt> elements by name, so both the waypoint and
    #   its corresponding track point get transformed to the obfuscated location.
    # - Ghost zones: no corresponding waypoint — the track just passes through a
    #   sensitive area. Track points inside the radius are deleted entirely.
    named_zones = {z["name"]: z for z in sensitive_zones if "name" in z}
    ghost_zones = [z for z in sensitive_zones if "name" not in z]

    # --- Step 1: Process waypoints (<wpt>) ---
    # Transform each named waypoint and record its original coordinates so we can
    # find the corresponding track point later (the track still has the original coords).
    waypoint_configs: dict[tuple[float, float], dict[str, Any]] = {}

    for wpt in root.findall("gpx:wpt", NS):
        name_el = wpt.find("gpx:name", NS)
        if name_el is None or name_el.text not in named_zones:
            continue

        zone = named_zones[name_el.text]
        orig_lat = float(wpt.get("lat"))
        orig_lon = float(wpt.get("lon"))

        waypoint_configs[(orig_lat, orig_lon)] = zone

        new_lat, new_lon = compute_obfuscated_location(zone, orig_lat, orig_lon)
        wpt.set("lat", str(new_lat))
        wpt.set("lon", str(new_lon))
        print(
            f"  Obfuscated waypoint '{name_el.text}': "
            f"({orig_lat}, {orig_lon}) -> ({new_lat}, {new_lon})"
        )

    # --- Step 2: Process track ---
    # For each sensitive waypoint we: (A) find its exact match in the track,
    # delete nearby track points that reveal the real location, and move the
    # match to the obfuscated coords. Then (B) scrub any ghost zones.
    matched_waypoints: set[tuple[float, float]] = set()

    for trk in root.findall("gpx:trk", NS):
        for trkseg in trk.findall("gpx:trkseg", NS):
            # Step 2A: Waypoint visits — one pass per transformed waypoint.
            for (wpt_lat, wpt_lon), zone in waypoint_configs.items():
                points = trkseg.findall("gpx:trkpt", NS)

                # Our GPX data duplicates the <wpt> coordinates as a <trkpt>,
                # so we can find the waypoint's position in the track by exact match.
                matching_indices = [
                    i
                    for i, pt in enumerate(points)
                    if float(pt.get("lat")) == wpt_lat
                    and float(pt.get("lon")) == wpt_lon
                ]

                if len(matching_indices) > 1:
                    raise SystemExit(
                        f"[ERROR] Multiple track points match waypoint at "
                        f"({wpt_lat}, {wpt_lon}). Found {len(matching_indices)} matches."
                    )

                if not matching_indices:
                    continue

                matched_waypoints.add((wpt_lat, wpt_lon))

                match_idx = matching_indices[0]
                zone_lat, zone_lon = zone["lat"], zone["lon"]
                radius = zone["radius"]

                # Delete every track point within the zone radius except the match.
                # These are approach/departure points that would reveal the real location.
                to_remove: list[int] = []

                for i, pt in enumerate(points):
                    if i == match_idx:
                        continue
                    pt_lat = float(pt.get("lat"))
                    pt_lon = float(pt.get("lon"))
                    # Don't delete track points that match other waypoints — they'll
                    # be transformed in their own pass.
                    if (pt_lat, pt_lon) in waypoint_configs:
                        continue
                    if haversine_distance(zone_lat, zone_lon, pt_lat, pt_lon) <= radius:
                        # If this error occurs with real-world data, we'll need to
                        # ensure that transport modes are correctly handled.
                        if _get_transport_mode(pt) is not None:
                            raise SystemExit(
                                f"[ERROR] Track point at ({pt_lat}, {pt_lon}) near "
                                f"waypoint ({wpt_lat}, {wpt_lon}) has transport mode "
                                f"'{_get_transport_mode(pt)}'. Transforming points with "
                                "transport modes is not yet supported."
                            )
                        to_remove.append(i)

                for i in sorted(to_remove, reverse=True):
                    trkseg.remove(points[i])

                # Move the matching track point to the obfuscated location so the
                # track connects to the transformed waypoint rather than the real one.
                points = trkseg.findall("gpx:trkpt", NS)
                earlier_removals = sum(1 for i in to_remove if i < match_idx)
                new_match_idx = match_idx - earlier_removals
                match_pt = points[new_match_idx]
                new_lat, new_lon = compute_obfuscated_location(
                    zone, wpt_lat, wpt_lon
                )
                match_pt.set("lat", str(new_lat))
                match_pt.set("lon", str(new_lon))

            # Step 2B: Ghost zones — sensitive areas with no waypoint.
            # Unlike named zones, there's no waypoint to relocate, so we simply
            # delete every track point inside the radius. We need to guard against
            # accidentally deleting a point that corresponds to a <wpt>, which
            # would leave the waypoint dangling (not connected to the track).
            all_wpt_coords: set[tuple[float, float]] = set()
            for wpt in root.findall("gpx:wpt", NS):
                all_wpt_coords.add((float(wpt.get("lat")), float(wpt.get("lon"))))
            # Include pre-transformation coords too — a waypoint that wasn't in a
            # named zone still has its original coords in the track.
            all_wpt_coords.update(waypoint_configs.keys())

            for zone in ghost_zones:
                zone_lat, zone_lon = zone["lat"], zone["lon"]
                radius = zone["radius"]

                points = trkseg.findall("gpx:trkpt", NS)
                to_remove_els: list[ET.Element] = []

                for pt in points:
                    pt_lat = float(pt.get("lat"))
                    pt_lon = float(pt.get("lon"))
                    if haversine_distance(zone_lat, zone_lon, pt_lat, pt_lon) <= radius:
                        if (pt_lat, pt_lon) in all_wpt_coords:
                            # TODO: change back to an error once input data is fixed.
                            print(
                                f"[WARNING] Ghost zone at ({zone_lat}, {zone_lon}) "
                                f"overlaps with waypoint at ({pt_lat}, {pt_lon}). "
                                "A ghost zone overlapping with a waypoint will result "
                                "in waypoints that are not connected to the track."
                            )
                            continue
                        to_remove_els.append(pt)

                for pt in to_remove_els:
                    trkseg.remove(pt)

    # Every transformed waypoint must have a corresponding track point — if one
    # is missing it means the obfuscated <wpt> has no anchor in the track, which
    # will produce a dangling waypoint on the map.
    unmatched = set(waypoint_configs.keys()) - matched_waypoints
    if unmatched:
        coords = ", ".join(f"({lat}, {lon})" for lat, lon in unmatched)
        raise SystemExit(
            f"[ERROR] Transformed waypoint(s) have no matching track point: {coords}. "
            "The obfuscated waypoint(s) will not be connected to the track."
        )

    return root


def _get_transport_mode(trkpt: ET.Element) -> str | None:
    """Extract transport mode from <extension><transport>, or None if absent."""
    ext = trkpt.find("gpx:extension/gpx:transport", NS)
    if ext is not None and ext.text:
        return ext.text
    return None


def gpx_to_geojson(root: ET.Element) -> dict:
    """
    Convert a GPX track to a GeoJSON FeatureCollection segmented by transport mode.

    Each feature is a LineString representing a continuous run of track points sharing
    the same transport mode. The transition point between two runs is duplicated so that
    adjacent LineStrings connect seamlessly on the map. Points with no transport tag
    (e.g. waypoint-arrival points) form their own short segment with transport=null.

    Each <trkseg> is processed independently.
    """
    features = []

    for trk in root.findall("gpx:trk", NS):
        for trkseg in trk.findall("gpx:trkseg", NS):
            points = trkseg.findall("gpx:trkpt", NS)
            if not points:
                continue

            runs: list[tuple[str | None, list[list[float]]]] = []
            current_mode: str | None = None
            current_coords: list[list[float]] = []

            for pt in points:
                lat = float(pt.get("lat"))
                lon = float(pt.get("lon"))
                mode = _get_transport_mode(pt)  # None if no transport tag

                if mode != current_mode:
                    if current_coords:
                        # The transport tag is on the departure point, so include
                        # this point in the closing segment, then share it as the
                        # start of the new segment.
                        current_coords.append([lon, lat])
                        runs.append((current_mode, current_coords))
                        current_coords = [[lon, lat]]
                    else:
                        current_coords = [[lon, lat]]
                    current_mode = mode
                else:
                    current_coords.append([lon, lat])

            if current_coords:
                runs.append((current_mode, current_coords))

            for mode, coords in runs:
                if len(coords) >= 2:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": coords},
                            "properties": {"transport": mode},
                        }
                    )

    return {"type": "FeatureCollection", "features": features}


@click.command()
@click.argument(
    "input_gpx",
    type=click.Path(exists=True, path_type=str),
)
@click.option(
    "--deploy-path",
    type=click.Path(path_type=str),
    default=None,
    help='Folder to copy output files to for deployment. Default: DEPLOY_TARGET/gpx. Pass "" to disable.',
)
def run(input_gpx: str, deploy_path: str | None) -> None:
    """
    Obfuscate sensitive waypoints in a GPX file and generate a transport-segmented GeoJSON.

    Obfuscates coordinates in memory (no GPX file written) and outputs a .geojson file where
    the track is split into LineString features by transport mode. Input can be a single GPX
    file or a directory (processes all *.gpx files). Output is written to FINAL_DATA_DIR.

    INPUT_GPX: Path to the input GPX file or directory containing *.gpx files.
    """
    load_dotenv()

    default_output_path = os.getenv("FINAL_DATA_DIR")
    default_deploy_path = os.path.join(os.getenv("DEPLOY_TARGET"), "gpx")

    if deploy_path is None:
        deploy_path = default_deploy_path
    elif deploy_path == "":
        deploy_path = None

    if deploy_path and not os.path.exists(deploy_path):
        raise SystemExit(f"[ERROR] Deploy path not found: {deploy_path}")

    sensitive_zones = load_sensitive_zones()
    print(f"Loaded {len(sensitive_zones)} sensitive zones")

    if os.path.isdir(input_gpx):
        inputs = list(Path(input_gpx).glob("*.gpx"))
        print(f"{inputs}")
    else:
        inputs = [input_gpx]

    for gpx_path in inputs:
        print(f"Processing file {gpx_path}")
        stem = Path(gpx_path).stem
        geojson_output = os.path.join(default_output_path, stem + ".geojson")

        root = process_gpx(gpx_path, sensitive_zones)

        geojson = gpx_to_geojson(root)
        with open(geojson_output, "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2)
        print(f"  - GeoJSON saved to: {geojson_output} ({len(geojson['features'])} features)")

        if deploy_path:
            try:
                shutil.copy(geojson_output, deploy_path)
                print(f"  [SUCCESS] Copied {geojson_output} -> {deploy_path}")
            except Exception as e:
                raise SystemExit(f"  [ERROR] Copy failed: {e}")


if __name__ == "__main__":
    run()
