import argparse
import csv
import json
import os
import shutil
import sys
from dotenv import load_dotenv

"""
Converts an iNaturalist CSV export into a GeoJSON FeatureCollection.
Loads observation counts from global inaturalist taxon data to include in the geojson.
"""

def convert_inat_csv_to_geojson(input_csv, output_geojson, taxa_json):
    """
    Converts an iNaturalist CSV export into a GeoJSON FeatureCollection.

    Args:
        input_csv (str): Path to the input CSV file.
        output_geojson (str): Path where the output GeoJSON file will be saved.
        taxa_json (str): Path to the json file containing inaturalist taxon data
    """

    # --- NEW: Load Taxa Data for Lookup ---
    print(f"Loading taxon data from {taxa_json}...")
    taxa_lookup = {}
    try:
        with open(taxa_json, 'r', encoding='utf-8') as f:
            raw_taxa = json.load(f)
            # Create a dictionary where Key = ID (as string) and Value = observations_count
            # We convert ID to string because CSV DictReader reads all columns as strings
            for item in raw_taxa:
                t_id = str(item.get('id', ''))
                if t_id:
                    taxa_lookup[t_id] = item.get('observations_count', 0)

    except FileNotFoundError:
        print(f"Warning: {taxa_json} not found. Global counts will be 0.")

    features = []

    print(f"Reading {input_csv}...")

    with open(input_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            # 1. Parse Coordinates
            try:
                lat = float(row.get('latitude', 0))
                lon = float(row.get('longitude', 0))
            except ValueError:
                # Skip rows with invalid or missing coordinates
                continue

            # Skip if coordinates are 0,0 (unless you actually went to Null Island)
            if lat == 0 and lon == 0:
                continue

            # 2. Determine Title (Fallback Strategy)
            # Try Common Name -> Scientific Name -> Species Guess -> "Observation"
            title = row.get('common_name', '').strip()
            if not title:
                title = row.get('scientific_name', '').strip()
            if not title:
                title = row.get('species_guess', '').strip()
            if not title:
                title = "Observation"

            # 2.1 Lookup global count from taxon data
            taxon_id = row.get('taxon_id', '')
            global_obs_count = taxa_lookup.get(taxon_id, 0) # Default to 0 if not found

            # 3. Construct GeoJSON Feature
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "title": title,
                    "image_url": row.get('image_url', ''),
                    "obs_url": row.get('url', ''),
                    "date": row.get('observed_on', ''),
                    "global_count": global_obs_count,
                }
            }
            features.append(feature)

    # 4. Construct FeatureCollection
    geojson_data = {
        "type": "FeatureCollection",
        "features": features
    }

    # 5. Write to File
    with open(output_geojson, 'w', encoding='utf-8') as f:
        json.dump(geojson_data, f, indent=2)

    print(f"Successfully wrote {len(features)} points to {output_geojson}")
    return True

if __name__ == "__main__":
    load_dotenv()

    OUTPUT_FILE = os.path.join(os.getenv("FINAL_DATA_DIR"), "inaturalist.geojson")
    TAXON_JSON_FILE = os.path.join(os.getenv("PUBLIC_DATA_DIR"), "inaturalist_taxa.json")
    DEFAULT_DEPLOY_PATH = os.path.join(os.getenv("DEPLOY_TARGET"), "observations")

    parser = argparse.ArgumentParser(description="Convert raw inaturalist observations to a geojson file for map view.")
    parser.add_argument("input_csv", help="Path to the input CSV file")
    parser.add_argument("--deploy-path",
                        help=f"Folder to copy the output file to for deployment. Default: {DEFAULT_DEPLOY_PATH}. Pass an empty string \"\" to disable.",
                        default=DEFAULT_DEPLOY_PATH)
    args = parser.parse_args()

    if args.deploy_path:
        if not os.path.exists(args.deploy_path):
            print(f"\n[ERROR] Deploy path not found: {args.deploy_path}")
            sys.exit(1)

    if not os.path.exists(args.input_csv):
        print(f"Error: Could not find input file '{args.input_csv}'")
        os.exit(1)

    success = convert_inat_csv_to_geojson(args.input_csv, OUTPUT_FILE, TAXON_JSON_FILE)

    # Deploy generated file, if deploy path provided
    # Copied directly from obfuscate_points.py
    if success and args.deploy_path:
        deploy_file = f"{args.deploy_path}"
        try:
            shutil.copy(OUTPUT_FILE, deploy_file)
            print(f"  [SUCCESS] Copied {OUTPUT_FILE} -> {deploy_file}")
        except Exception as e:
            print(f"  [ERROR] Copy failed: {e}")

