import json
import os
from pathlib import Path

import psycopg2
from dateutil import \
    parser  # distinct from standard datetime, helps parsing ISO strings
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

"""
Populate embeddings on waypoints, along with descriptions, from a json file.
"""

# --- Configuration ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")
INPUT_DIR = os.path.join(os.getenv("INTERIM_DATA_DIR"), "robinblog")

# Load the free model (downloads automatically on first run)
# This model outputs 384-dimensional vectors
print("Loading model (this may take a moment first time)...")
model = SentenceTransformer('BAAI/bge-small-en-v1.5')

def get_embedding(text):
    """Generates a 384-dim embedding locally."""
    # sentence-transformers handles newlines fine, but cleaning is good practice
    text = text.replace("\n", " ")
    return model.encode(text).tolist()

def populate_embeddings(json_file_path):
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"CRITICAL: Could not connect to database. {e}")
        return

    try:
        with open(json_file_path, 'r') as f:
            waypoints_data = json.load(f)
    except FileNotFoundError:
        print(f"CRITICAL: JSON file not found at {json_file_path}")
        return

    print(f"Processing {len(waypoints_data)} waypoints from {json_file_path}...")

    try:
        for entry in waypoints_data:
            name = entry.get('name')
            raw_start_time = entry.get('time')
            description = entry.get('description')

            if not description:
                print(f"Skipping '{name}': No description text found.")
                continue

            try:
                start_time_dt = parser.parse(raw_start_time)
            except (ValueError, TypeError):
                raise ValueError(f"CRITICAL ERROR: Invalid timestamp for '{name}'")

            # --- NOISY CHECK ---
            check_query = """
                SELECT id FROM waypoints 
                WHERE name = %s AND start_time = %s;
            """
            cur.execute(check_query, (name, start_time_dt))
            result = cur.fetchone()

            if result is None:
                error_msg = (
                    f"\n{'!'*50}\n"
                    f"DATA MISMATCH ERROR:\n"
                    f"Waypoint '{name}' ({raw_start_time})\n"
                    f"found in JSON but NOT in DB.\n"
                    f"{'!'*50}\n"
                )
                raise LookupError(error_msg)
            
            waypoint_id = result[0]

            # --- Generate Embedding Locally ---
            # No API call, no cost.
            embedding_vector = get_embedding(description)

            update_query = """
                UPDATE waypoints 
                SET embedding = %s::vector 
                WHERE id = %s;
            """
            cur.execute(update_query, (embedding_vector, waypoint_id))

            update_query = """
                UPDATE waypoints 
                SET description = %s 
                WHERE id = %s;
            """
            cur.execute(update_query, (description, waypoint_id))

        conn.commit()
        print("\nSUCCESS: All embeddings populated and committed.")

    except Exception as e:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK.")
        raise e

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":

    inputs = list(Path(INPUT_DIR).glob("*.json"))

    for infile in inputs:
        populate_embeddings(infile)
