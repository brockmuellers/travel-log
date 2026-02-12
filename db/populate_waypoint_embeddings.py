import json
import os
import psycopg2
from dateutil import parser  # distinct from standard datetime, helps parsing ISO strings
from sentence_transformers import SentenceTransformer
import os
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")
INPUT_DIR = os.path.join(os.getenv("INTERIM_DATA_DIR"), "robinblog")

# TODO change me
JSON_FILE = "southeast-asia_gemini_02-cambodia.json"
JSON_FILE_PATH = os.path.join(INPUT_DIR, JSON_FILE)

# Load the free model (downloads automatically on first run)
# This model outputs 384-dimensional vectors
print("Loading model (this may take a moment first time)...")
model = SentenceTransformer('BAAI/bge-small-en-v1.5')

def get_embedding(text):
    """Generates a 384-dim embedding locally."""
    # sentence-transformers handles newlines fine, but cleaning is good practice
    text = text.replace("\n", " ")
    return model.encode(text).tolist()

def populate_embeddings():
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"CRITICAL: Could not connect to database. {e}")
        return

    try:
        with open(JSON_FILE_PATH, 'r') as f:
            waypoints_data = json.load(f)
    except FileNotFoundError:
        print(f"CRITICAL: JSON file not found at {JSON_FILE_PATH}")
        return

    print(f"Processing {len(waypoints_data)} waypoints...")

    try:
        for entry in waypoints_data:
            name = entry.get('name')
            raw_start_time = entry.get('time')
            summary = entry.get('summary')

            if not summary:
                print(f"Skipping '{name}': No summary text found.")
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
            embedding_vector = get_embedding(summary)

            update_query = """
                UPDATE waypoints 
                SET embedding = %s::vector 
                WHERE id = %s;
            """
            cur.execute(update_query, (embedding_vector, waypoint_id))

        conn.commit()
        print("\nSUCCESS: All embeddings populated.")

    except Exception as e:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK.")
        raise e

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    populate_embeddings()