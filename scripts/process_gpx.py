import json
import os
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv

from gps_utils import calculate_destination_point, haversine_distance

# XML Namespace for GPX 1.1
NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
ET.register_namespace("", NS["gpx"])


def process_gpx(
    input_file: str | Path, sensitive_config: dict[str, Any]
) -> ET.Element:
    print(f"Reading GPX: {input_file}")
    tree = ET.parse(input_file)
    root = tree.getroot()

    # Store transformation rules: (original_lat, original_lon) -> {'new_pos': (lat, lon), 'radius': r}
    point_transformations = {}

    # 0. Pre-Pass: Handle Explicit "Ghost" Coordinates from JSON (those not matching a waypoint)
    for name, config in sensitive_config.items():
        if "lat" in config and "lon" in config:
            lat = config["lat"]
            lon = config["lon"]

            rng = random.Random(config["seed"])

            dist = config["radius"]
            # Randomize distance between 75%-100% of radius to guarantee meaningful
            # displacement while avoiding a predictable exact-radius displacement.
            random_distance = rng.uniform(dist * 0.75, dist)
            random_bearing = rng.uniform(0, 360)

            new_lat, new_lon = calculate_destination_point(
                lat, lon, random_distance, random_bearing
            )

            # Register this as a sensitive zone
            point_transformations[(lat, lon)] = {
                "new_pos": (new_lat, new_lon),
                "radius": dist,
            }
            print(
                f"  [Ghost Point] '{name}': Moved {random_distance:.2f}km @ {random_bearing:.0f}°"
            )

    # 1. Process Waypoints (<wpt>)
    for wpt in root.findall("gpx:wpt", NS):
        name_tag = wpt.find("gpx:name", NS)
        if name_tag is not None and name_tag.text in sensitive_config:
            name = name_tag.text
            config = sensitive_config[name]

            lat = float(wpt.get("lat"))
            lon = float(wpt.get("lon"))
            original_key = (lat, lon)

            # Seed based on the config to ensure consistency across the name.
            # Use a local RNG instance to avoid polluting global state
            rng = random.Random(config["seed"])

            dist = config["radius"]
            # Randomize distance between 75%-100% of radius to guarantee meaningful
            # displacement while avoiding a predictable exact-radius displacement.
            random_distance = rng.uniform(dist * 0.75, dist)
            random_bearing = rng.uniform(0, 360)

            new_lat, new_lon = calculate_destination_point(
                lat, lon, random_distance, random_bearing
            )

            # Update the waypoint in the GPX
            wpt.set("lat", str(new_lat))
            wpt.set("lon", str(new_lon))

            # Log this transformation for the track editing phase
            point_transformations[original_key] = {
                "new_pos": (new_lat, new_lon),
                "radius": dist,
            }

            print(f"  Obfuscating '{name}': Moved {random_distance:.2f}km @ {random_bearing:.0f}°")

    # 2. Process Tracks (<trk>)
    count_deleted = 0
    count_moved = 0

    for trk in root.findall("gpx:trk", NS):
        for trkseg in trk.findall("gpx:trkseg", NS):
            points_to_keep = []

            for trkpt in trkseg.findall("gpx:trkpt", NS):
                pt_lat = float(trkpt.get("lat"))
                pt_lon = float(trkpt.get("lon"))

                should_delete = False
                matched_transformation = False

                # Check against all sensitive original locations
                for original_coords, rules in point_transformations.items():
                    orig_lat, orig_lon = original_coords
                    dist = haversine_distance(pt_lat, pt_lon, orig_lat, orig_lon)

                    # 1 meter tolerance to identify the "original sensitive waypoint" in the track
                    is_original_point = dist < 0.001

                    if is_original_point:
                        # Move exact matches
                        new_lat, new_lon = rules["new_pos"]
                        trkpt.set("lat", str(new_lat))
                        trkpt.set("lon", str(new_lon))
                        matched_transformation = True
                        count_moved += 1
                        break

                    elif dist <= rules["radius"]:
                        # Delete nearby points
                        should_delete = True

                if matched_transformation:
                    points_to_keep.append(trkpt)
                elif not should_delete:
                    points_to_keep.append(trkpt)
                else:
                    count_deleted += 1

            # Rebuild the segment with only kept points
            trkseg[:] = points_to_keep

    print("Processing complete.")
    print(f"  - Track points moved: {count_moved}")
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
                        runs.append((current_mode, current_coords))
                        # Share the transition point so adjacent LineStrings connect
                        current_coords = [current_coords[-1]]
                    current_mode = mode

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
    "-w",
    "--waypoints",
    type=click.Path(exists=True, path_type=str),
    default=None,
    help="Path to JSON waypoints config. Default: PRIVATE_DATA_DIR/sensitive_waypoints.json",
)
@click.option(
    "--deploy-path",
    type=click.Path(path_type=str),
    default=None,
    help='Folder to copy output files to for deployment. Default: DEPLOY_TARGET/gpx. Pass "" to disable.',
)
def run(input_gpx: str, waypoints: str | None, deploy_path: str | None) -> None:
    """
    Obfuscate sensitive waypoints in a GPX file and generate a transport-segmented GeoJSON.

    Obfuscates coordinates in memory (no GPX file written) and outputs a .geojson file where
    the track is split into LineString features by transport mode. Input can be a single GPX
    file or a directory (processes all *.gpx files). Output is written to FINAL_DATA_DIR.

    Waypoints config JSON example:
      [
        {"name": "My House", "seed": 103, "radius": 8},
        {"name": "Not A Waypoint", "seed": 83, "radius": 10, "lat": 40.56789, "lon": "-70.23456}
      ]

    INPUT_GPX: Path to the input GPX file or directory containing *.gpx files.
    """
    load_dotenv()

    default_output_path = os.getenv("FINAL_DATA_DIR")
    default_waypoints = os.path.join(
        os.getenv("PRIVATE_DATA_DIR"), "sensitive_waypoints.json"
    )
    default_deploy_path = os.path.join(os.getenv("DEPLOY_TARGET"), "gpx")

    waypoints_path = waypoints if waypoints is not None else default_waypoints
    if deploy_path is None:
        deploy_path = default_deploy_path
    elif deploy_path == "":
        deploy_path = None

    if deploy_path and not os.path.exists(deploy_path):
        raise SystemExit(f"[ERROR] Deploy path not found: {deploy_path}")

    with open(waypoints_path, "r") as f:
        data = json.load(f)
    config = {item["name"]: item for item in data}

    if os.path.isdir(input_gpx):
        inputs = list(Path(input_gpx).glob("*.gpx"))
        print(f"{inputs}")
    else:
        inputs = [input_gpx]

    for gpx_path in inputs:
        print(f"Processing file {gpx_path}")
        stem = Path(gpx_path).stem
        geojson_output = os.path.join(default_output_path, stem + ".geojson")

        root = process_gpx(gpx_path, config)

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
