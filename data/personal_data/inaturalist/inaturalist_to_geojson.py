import argparse
import csv
import json
import os

def convert_inat_csv_to_geojson(input_csv, output_geojson):
    """
    Converts an iNaturalist CSV export into a GeoJSON FeatureCollection.

    Args:
        input_csv (str): Path to the input CSV file.
        output_geojson (str): Path where the output GeoJSON file will be saved.
    """
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
                    "date": row.get('observed_on', '')
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert raw inaturalist observations to a geojson file for map view.")
    parser.add_argument("input_csv", help="Path to the input CSV file")    
    args = parser.parse_args()
    
    OUTPUT_FILE = 'inaturalist.geojson'

    if os.path.exists(args.input_csv):
        convert_inat_csv_to_geojson(args.input_csv, OUTPUT_FILE)
    else:
        print(f"Error: Could not find input file '{args.input_csv}'")
