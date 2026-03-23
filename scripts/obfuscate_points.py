import json
import math
import os
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv

# XML Namespace for GPX 1.1
NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
ET.register_namespace("", NS["gpx"])
ROUND_TO = 6  # Round new lat/lon values to make obfuscation less obvious


def normalize_longitude(lon: float) -> float:
    """
    Wraps longitude to -180 to 180 degrees.
    Ex: 181.0 -> -179.0
    """
    return (lon + 180) % 360 - 180


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees) in kilometers.
    """
    R = 6371  # Earth radius in km

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_destination_point(
    lat: float, lon: float, distance_km: float, bearing_degrees: float
) -> tuple[float, float]:
    """
    Calculates a new coordinate given a start point, distance (km), and bearing (degrees).
    """
    R = 6371  # Earth radius in km

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_degrees)

    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(distance_km / R)
        + math.cos(lat_rad) * math.sin(distance_km / R) * math.cos(bearing_rad)
    )

    new_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(distance_km / R) * math.cos(lat_rad),
        math.cos(distance_km / R) - math.sin(lat_rad) * math.sin(new_lat_rad),
    )

    final_lat = math.degrees(
        new_lat_rad
    )  # Probably doesn't handle wrapping at poles correctly
    final_lon = normalize_longitude(math.degrees(new_lon_rad))

    # Round the final values so it's less obvious that obfuscation was done
    return round(final_lat, ROUND_TO), round(final_lon, ROUND_TO)


def process_gpx(
    input_file: str | Path, output_file: str | Path, sensitive_config: dict[str, Any]
) -> bool:
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

    tree.write(output_file, encoding="UTF-8", xml_declaration=True)
    print("Processing complete.")
    print(f"  - Track points moved: {count_moved}")
    print(f"  - Track points deleted: {count_deleted}")
    print(f"  - Saved to: {output_file}")
    return True


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
    help='Folder to copy output GPX to for deployment. Default: DEPLOY_TARGET/gpx. Pass "" to disable.',
)
def run(input_gpx: str, waypoints: str | None, deploy_path: str | None) -> None:
    """
    Obfuscate sensitive waypoints in a GPX file.

    Moves each configured waypoint to a seeded random location that is a given radius (km) away
    and removes nearby track points. Input can be a single GPX file or a directory (processes
    all *.gpx files). Output is written to FINAL_DATA_DIR.

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
        output = os.path.join(default_output_path, os.path.basename(gpx_path))
        success = process_gpx(gpx_path, output, config)

        if success and deploy_path:
            try:
                shutil.copy(output, deploy_path)
                print(f"  [SUCCESS] Copied {output} -> {deploy_path}")
            except Exception as e:
                raise SystemExit(f"  [ERROR] Copy failed: {e}")


if __name__ == "__main__":
    run()
