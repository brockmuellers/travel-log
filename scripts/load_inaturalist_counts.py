import pandas as pd
import requests
import json
import math
import os
import time
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()
input_csv =  os.path.join(os.getenv("PERSONAL_DATA_DIR"), "inaturalist/observations-679011.csv")
output_json = os.path.join(os.getenv("PUBLIC_DATA_DIR"), "inaturalist_taxa.json")
# ---------------------

def get_inaturalist_counts():
    print(f"Reading observations from {input_csv}...")

    try:
        # Load the CSV
        df = pd.read_csv(input_csv)

        # We only need the unique Taxon IDs from your observations
        # 'taxon_id' is the column name in your specific file
        unique_taxon_ids = df['taxon_id'].dropna().unique().astype(int).tolist()
        print(f"Found {len(unique_taxon_ids)} unique taxa to check.")

        # TODO comment out - line for testing
        #unique_taxon_ids = unique_taxon_ids[0:2]

    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # iNaturalist API allows fetching multiple IDs at once (up to 30-50 safely per call)
    batch_size = 30
    total_batches = math.ceil(len(unique_taxon_ids) / batch_size)

    results = []

    print(f"Querying iNaturalist API in {total_batches} batches...")

    # Process in chunks
    for i in range(0, len(unique_taxon_ids), batch_size):
        batch_ids = unique_taxon_ids[i:i + batch_size]

        # Convert list of ints to comma-separated string for the API URL
        ids_str = ",".join(map(str, batch_ids))
        url = f"https://api.inaturalist.org/v1/taxa/{ids_str}"

        try:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()

                # Build a simplified JSON entry
                #results.extend(data.get('results', []))
                for taxon in data.get('results', []):
                    taxon.pop('taxon_photos', '') # a big bunch of data that isn't useful to me
                    taxon.pop('default_photo', '')
                    taxon.pop('ancestors', '') # this actually could be interesting (kingdom etc) but it's MASSIVE
                    taxon.pop('conservation_statuses', '')
                    taxon.pop('listed_taxa', '')
                    taxon.pop('ancestor_ids', '')
                    taxon.pop('children', '')
                    results.append(taxon)

            else:
                print(f"Warning: Batch {i//batch_size + 1} failed with status {response.status_code}")

        except Exception as e:
            print(f"Error fetching batch: {e}")

        # Be nice to the API
        time.sleep(1.0)
        print(f"Processed batch {i//batch_size + 1}/{total_batches}", end='\r')

    # Write the final JSON
    print(f"\nWriting results to {output_json}...")
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

    print("Done!")

if __name__ == "__main__":
    get_inaturalist_counts()
