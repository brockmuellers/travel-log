import csv
import json
import os
import shutil
from typing import Any

import click
from dotenv import load_dotenv
from lib.gps_utils import (compute_obfuscated_location, haversine_distance,
                           load_sensitive_zones)

# Trip date range filter (inclusive)
DATE_MIN = "2024-07-20"
DATE_MAX = "2025-12-01"


def build_sensitive_zones(sensitive_zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build sensitive zone dicts from config entries."""
    zones = []
    for config in sensitive_zones:
        zones.append({"lat": config["lat"], "lon": config["lon"], "radius": config["radius"],
                      "displacement": config["displacement"], "bearing": config["bearing"]})
        print(f"  [Sensitive zone] '{config['key']}': radius={config['radius']}km, displacement=({config['displacement']:.2f}km @ {config['bearing']:.1f}°)")
    return zones


def apply_obfuscation(
    lat: float, lon: float, zones: list[dict[str, Any]]
) -> tuple[float, float]:
    """If (lat, lon) falls within any sensitive zone's radius, displace it by that zone's vector."""
    for zone in zones:
        if haversine_distance(lat, lon, zone["lat"], zone["lon"]) <= zone["radius"]:
            return compute_obfuscated_location(zone, lat, lon)
    return lat, lon


def convert_inat_csv_to_geojson(
    input_csv: str,
    output_geojson: str,
    taxa_json: str,
    sensitive_zones: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Converts an iNaturalist CSV export into a GeoJSON FeatureCollection.

    Args:
        input_csv (str): Path to the input CSV file.
        output_geojson (str): Path where the output GeoJSON file will be saved.
        taxa_json (str): Path to the json file containing inaturalist taxon data
    """

    # Load taxa data for global observation count lookup
    print(f"Loading taxon data from {taxa_json}...")
    taxa_lookup = {}
    try:
        with open(taxa_json, "r", encoding="utf-8") as f:
            raw_taxa = json.load(f)
            # Create a dictionary where Key = ID (as string) and Value = observations_count
            # We convert ID to string because CSV DictReader reads all columns as strings
            for item in raw_taxa:
                t_id = str(item.get("id", ""))
                if t_id:
                    taxa_lookup[t_id] = item.get("observations_count", 0)

    except FileNotFoundError:
        print(f"Warning: {taxa_json} not found. Global counts will be 0.")

    features = []

    print(f"Reading {input_csv}...")

    with open(input_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # 1. Parse Coordinates
            try:
                lat = float(row.get("latitude", 0))
                lon = float(row.get("longitude", 0))
            except ValueError:
                # Skip rows with invalid or missing coordinates
                continue

            # Skip if coordinates are 0,0 (unless you actually went to Null Island)
            if lat == 0 and lon == 0:
                continue

            date = row.get("observed_on", "").strip()
            if not (DATE_MIN <= date <= DATE_MAX):
                continue

            if sensitive_zones:
                lat, lon = apply_obfuscation(lat, lon, sensitive_zones)

            # 2. Determine Title (Fallback Strategy)
            # Try Common Name -> Scientific Name -> Species Guess -> "Observation"
            title = row.get("common_name", "").strip()
            if not title:
                title = row.get("scientific_name", "").strip()
            if not title:
                title = row.get("species_guess", "").strip()
            if not title:
                title = "Observation"

            # 2.1 Lookup global count from taxon data
            taxon_id = row.get("taxon_id", "")
            global_obs_count = taxa_lookup.get(taxon_id, 0)  # Default to 0 if not found

            # 3. Construct GeoJSON Feature
            feature = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "title": title,
                    "image_url": row.get("image_url", ""),
                    "obs_url": row.get("url", ""),
                    "date": row.get("observed_on", ""),
                    "global_count": global_obs_count,
                },
            }
            features.append(feature)

    # 4. Construct FeatureCollection
    geojson_data = {"type": "FeatureCollection", "features": features}

    # 5. Write to File
    with open(output_geojson, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, indent=2)

    print(f"Successfully wrote {len(features)} points to {output_geojson}")
    return True


@click.command()
@click.argument("input_csv", type=click.Path(exists=True, path_type=str))
@click.option(
    "--deploy-path",
    type=click.Path(path_type=str),
    default=None,
    help='Folder to copy the output GeoJSON to for deployment. Default: FINAL_DATA_DIR/../DEPLOY_TARGET/observations. Pass "" to disable.',
)
def run(input_csv: str, deploy_path: str | None) -> None:
    """
    Convert iNaturalist CSV export to a GeoJSON FeatureCollection for map view.

    Uses taxon data from PUBLIC_DATA_DIR for global observation counts.
    Obfuscates observations near sensitive locations using sensitive_locations.json.
    Output is written to FINAL_DATA_DIR/inaturalist.geojson.

    INPUT_CSV: Path to the input CSV file.
    """
    load_dotenv()

    output_file = os.path.join(os.getenv("FINAL_DATA_DIR"), "inaturalist.geojson")
    taxa_json_file = os.path.join(os.getenv("PUBLIC_DATA_DIR"), "inaturalist_taxa.json")
    default_deploy_path = os.path.join(os.getenv("DEPLOY_TARGET"), "observations")

    if deploy_path is None:
        deploy_path = default_deploy_path
    elif deploy_path == "":
        deploy_path = None

    if deploy_path and not os.path.exists(deploy_path):
        raise SystemExit(f"[ERROR] Deploy path not found: {deploy_path}")

    if not os.path.exists(input_csv):
        raise SystemExit(f"Error: Could not find input file '{input_csv}'")

    print("Building sensitive zones for obfuscation...")
    sensitive_zones = build_sensitive_zones(load_sensitive_zones())

    success = convert_inat_csv_to_geojson(input_csv, output_file, taxa_json_file, sensitive_zones)

    if success and deploy_path:
        try:
            shutil.copy(output_file, deploy_path)
            print(f"  [SUCCESS] Copied {output_file} -> {deploy_path}")
        except Exception as e:
            raise SystemExit(f"  [ERROR] Copy failed: {e}")


if __name__ == "__main__":
    run()
