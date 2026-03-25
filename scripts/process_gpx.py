import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from lib.gps_utils import calculate_destination_point, compute_obfuscated_location, haversine_distance, load_sensitive_zones

# XML Namespace for GPX 1.1
NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
ET.register_namespace("", NS["gpx"])


def process_gpx(
    input_file: str | Path, sensitive_zones: list[dict[str, Any]]
) -> ET.Element:
    print(f"Reading GPX: {input_file}")
    tree = ET.parse(input_file)
    root = tree.getroot()

    # 1. Process Waypoints (<wpt>): move any within a sensitive zone's radius.
    # Also record the fake location for each waypoint in zone order, so the track
    # bridging pass can align each visit's bridge point with the matching waypoint.
    # GPX waypoints are assumed to be in chronological visit order.
    zone_waypoint_fakes: dict[int, list[tuple[float, float]]] = {}  # zone id -> ordered fake locs

    for wpt in root.findall("gpx:wpt", NS):
        lat = float(wpt.get("lat"))
        lon = float(wpt.get("lon"))
        for zone in sensitive_zones:
            if haversine_distance(lat, lon, zone["lat"], zone["lon"]) <= zone["radius"]:
                new_lat, new_lon = compute_obfuscated_location(zone, lat, lon)
                wpt.set("lat", str(new_lat))
                wpt.set("lon", str(new_lon))
                zone_waypoint_fakes.setdefault(id(zone), []).append((new_lat, new_lon))
                name_tag = wpt.find("gpx:name", NS)
                name = name_tag.text if name_tag is not None else "(unnamed)"
                print(f"  Obfuscating waypoint '{name}': {zone['displacement']:.2f}km @ {zone['bearing']:.0f}°")
                break

    # 2. Process Tracks (<trk>): delete points within any zone's radius, inserting one
    # bridging point per visit so the track stays connected.
    #
    # Bridge location priority:
    #   a) Use the N-th waypoint's fake location for the N-th visit (aligns the track
    #      bridge with the waypoint marker, even when visits land at slightly different
    #      GPS coordinates due to recording drift).
    #   b) Fall back to the zone-center fake location if there are more visits than
    #      waypoints (e.g. ghost zones with no <wpt> tag).
    #
    # zones_currently_inside resets on every safe point so each separate visit gets
    # its own bridge.
    count_deleted = 0
    zone_visit_counts: dict[int, int] = {}  # zone id -> number of visits seen so far

    for trk in root.findall("gpx:trk", NS):
        for trkseg in trk.findall("gpx:trkseg", NS):
            points_to_keep = []
            zones_currently_inside: set[int] = set()

            for trkpt in trkseg.findall("gpx:trkpt", NS):
                pt_lat = float(trkpt.get("lat"))
                pt_lon = float(trkpt.get("lon"))

                matched_zone = next(
                    (z for z in sensitive_zones
                     if haversine_distance(pt_lat, pt_lon, z["lat"], z["lon"]) <= z["radius"]),
                    None,
                )

                if matched_zone is None:
                    # Outside all zones — reset so future zone visits get a fresh bridge.
                    zones_currently_inside.clear()
                    points_to_keep.append(trkpt)
                else:
                    zone_id = id(matched_zone)
                    if zone_id not in zones_currently_inside:
                        # First point of a new visit — place a bridge point.
                        zones_currently_inside.add(zone_id)
                        visit_idx = zone_visit_counts.get(zone_id, 0)
                        zone_visit_counts[zone_id] = visit_idx + 1
                        fakes = zone_waypoint_fakes.get(zone_id, [])
                        if visit_idx < len(fakes):
                            fake_lat, fake_lon = fakes[visit_idx]
                        else:
                            fake_lat, fake_lon = compute_obfuscated_location(
                                matched_zone, matched_zone["lat"], matched_zone["lon"]
                            )
                        trkpt.set("lat", str(fake_lat))
                        trkpt.set("lon", str(fake_lon))
                        points_to_keep.append(trkpt)
                    else:
                        count_deleted += 1

            # Rebuild the segment with only kept points
            trkseg[:] = points_to_keep

    # Sanity check: each zone's track visit count should match its waypoint count.
    # A mismatch means the track passed through the zone without a corresponding
    # waypoint (or vice versa), which breaks the visit-to-waypoint alignment.
    for zone in sensitive_zones:
        zone_id = id(zone)
        n_visits = zone_visit_counts.get(zone_id, 0)
        n_waypoints = len(zone_waypoint_fakes.get(zone_id, []))
        if n_visits != n_waypoints:
            print(
                f"  [WARN] Zone '{zone['name']}': {n_visits} track visit(s) but "
                f"{n_waypoints} waypoint(s) — bridge/waypoint alignment may be off. "
                f"Check for pass-throughs or missing waypoints. "
                f"(If this zone has no corresponding GPX waypoint, this is expected and harmless.)"
            )

    print("Processing complete.")
    print(f"  - Track points deleted: {count_deleted}")
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
