import json
import random
import math
import xml.etree.ElementTree as ET
import argparse
import os
import shutil
import sys

"""
Obfuscates sensitive waypoints in a GPX file. It isn't perfect but it's good enough.

Changes the coordinates of each waypoint to a (seeded) random location that is `radius`
kilometers away from the original, and then removes nearby points along the route.

Semi-reviewed Gemini-generated code.

Example personal_data/sensitive_waypoints.json:
[
    {"name": "My House", "seed": 103, "radius": 8},
    {"name": "Friend's House", "seed": 32, "radius": 30},
    {"name": "Not A Waypoint", "seed": 83, "radius": 10, "lat": 40.56789, "lon": "-70.23456}
]
"""

# XML Namespace for GPX 1.1
NS = {'gpx': 'http://www.topografix.com/GPX/1/1'}
ET.register_namespace('', NS['gpx'])

# --- Configuration for Defaults ---
DEFAULT_WAYPOINTS = "private_data/sensitive_waypoints.json"
# A janky relative path on my local machine
DEFAULT_DEPLOY_PATH = "../../brockmuellers.github.io/assets/gpx"

def normalize_longitude(lon):
    """
    Wraps longitude to -180 to 180 degrees.
    Ex: 181.0 -> -179.0
    """
    return (lon + 180) % 360 - 180

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees) in kilometers.
    """
    R = 6371  # Earth radius in km

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi/2)**2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

def calculate_destination_point(lat, lon, distance_km, bearing_degrees):
    """
    Calculates a new coordinate given a start point, distance (km), and bearing (degrees).
    """
    R = 6371  # Earth radius in km

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_degrees)

    new_lat_rad = math.asin(math.sin(lat_rad) * math.cos(distance_km / R) +
                            math.cos(lat_rad) * math.sin(distance_km / R) * math.cos(bearing_rad))

    new_lon_rad = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(distance_km / R) * math.cos(lat_rad),
                                       math.cos(distance_km / R) - math.sin(lat_rad) * math.sin(new_lat_rad))

    final_lat = math.degrees(new_lat_rad) # Probably doesn't handle wrapping at poles correctly
    final_lon = normalize_longitude(math.degrees(new_lon_rad))

    return final_lat, final_lon

def process_gpx(input_file, output_file, sensitive_config):
    print(f"Reading GPX: {input_file}")
    tree = ET.parse(input_file)
    root = tree.getroot()

    # Store transformation rules: (original_lat, original_lon) -> {'new_pos': (lat, lon), 'radius': r}
    point_transformations = {}

    # 0. Pre-Pass: Handle Explicit "Ghost" Coordinates from JSON (those not matching a waypoint)
    for name, config in sensitive_config.items():
        if 'lat' in config and 'lon' in config:
            lat = config['lat']
            lon = config['lon']

            # Calculate the random "fake" location immediately
            rng = random.Random(config['seed'])

            dist = config['radius']
            random_bearing = rng.uniform(0, 360)

            new_lat, new_lon = calculate_destination_point(
                lat, lon,
                rng.uniform(0, dist),
                rng.uniform(0, 360)
            )

            # Register this as a sensitive zone
            point_transformations[(lat, lon)] = {
                'new_pos': (new_lat, new_lon),
                'radius': dist
            }
            print(f"  [Ghost Point] '{name}': Moved {dist:.2f}km @ {random_bearing:.0f}°

    # 1. Process Waypoints (<wpt>)
    for wpt in root.findall('gpx:wpt', NS):
        name_tag = wpt.find('gpx:name', NS)
        if name_tag is not None and name_tag.text in sensitive_config:
            name = name_tag.text
            config = sensitive_config[name]

            lat = float(wpt.get('lat'))
            lon = float(wpt.get('lon'))
            original_key = (lat, lon)

            # Seed based on the config to ensure consistency across the name.
            # Use a local RNG instance to avoid polluting global state
            rng = random.Random(config['seed'])

            dist = config['radius']
            random_bearing = rng.uniform(0, 360)

            new_lat, new_lon = calculate_destination_point(lat, lon, dist, random_bearing)

            # Update the waypoint in the GPX
            wpt.set('lat', str(new_lat))
            wpt.set('lon', str(new_lon))

            # Log this transformation for the track editing phase
            point_transformations[original_key] = {
                'new_pos': (new_lat, new_lon),
                'radius': dist
            }

            print(f"  Obfuscating '{name}': Moved {dist:.2f}km @ {random_bearing:.0f}°")

    # 2. Process Tracks (<trk>)
    count_deleted = 0
    count_moved = 0

    for trk in root.findall('gpx:trk', NS):
        for trkseg in trk.findall('gpx:trkseg', NS):
            points_to_keep = []

            for trkpt in trkseg.findall('gpx:trkpt', NS):
                pt_lat = float(trkpt.get('lat'))
                pt_lon = float(trkpt.get('lon'))

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
                        new_lat, new_lon = rules['new_pos']
                        trkpt.set('lat', str(new_lat))
                        trkpt.set('lon', str(new_lon))
                        matched_transformation = True
                        count_moved += 1
                        break

                    elif dist <= rules['radius']:
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

    tree.write(output_file, encoding='UTF-8', xml_declaration=True)
    print(f"Processing complete.")
    print(f"  - Track points moved: {count_moved}")
    print(f"  - Track points deleted: {count_deleted}")
    print(f"  - Saved to: {output_file}")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Obfuscate sensitive waypoints in a GPX file.")

    parser.add_argument("input_gpx", help="Path to the input GPX file")

    parser.add_argument("-o", "--output", help="Path to the output GPX file", default=None)
    parser.add_argument("-w", "--waypoints", help=f"Path to JSON waypoints config (default: {DEFAULT_WAYPOINTS})", default=DEFAULT_WAYPOINTS)
    parser.add_argument("--deploy-path",
                        help=f"Folder to copy the output file to for deployment. Default: {DEFAULT_DEPLOY_PATH}. Pass an empty string \"\" to disable.",
                        default=DEFAULT_DEPLOY_PATH)

    args = parser.parse_args()

    # Determine output filename if not provided
    if args.output is None:
        base, ext = os.path.splitext(args.input_gpx)
        args.output = f"{base}_obfuscated{ext}"

    # Resolve relative deploy path to absolute path
    # Exit early if deploy path does not exist
    if args.deploy_path:
        args.deploy_path = os.path.abspath(args.deploy_path)
        if not os.path.exists(args.deploy_path):
            print(f"\n[ERROR] Deploy path not found: {args.deploy_path}")
            sys.exit(1)

    # Load sensitive point config
    with open(args.waypoints, 'r') as f:
        data = json.load(f)
    config = {item['name']: item for item in data}

    # Obfuscate
    success = process_gpx(args.input_gpx, args.output, config)

    # Deploy obfuscated file, if deploy path provided
    # Use original file name
    if success and args.deploy_path:
        deploy_filename = os.path.basename(args.input_gpx)
        deploy_file = f"{args.deploy_path}/{deploy_filename}"
        try:
            shutil.copy(args.output, deploy_file)
            print(f"  [SUCCESS] Copied {args.output} -> {deploy_file}")
        except Exception as e:
            print(f"  [ERROR] Copy failed: {e}")
