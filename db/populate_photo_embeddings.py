import os

import psycopg2
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

"""
Populate embeddings on photos from their captions (DB already has photos with captions).
"""

# --- Configuration ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")

# Same 384-dim model as waypoints for consistency
print("Loading model (this may take a moment first time)...")
model = SentenceTransformer("BAAI/bge-small-en-v1.5")


def strip_nul(s):
    """Remove NUL (0x00) characters. PostgreSQL and some libs reject them."""
    if s is None:
        return s
    return s.replace("\x00", "")


def get_embedding(text):
    """Generates a 384-dim embedding locally."""
    text = text.replace("\n", " ")
    return model.encode(text).tolist()


def populate_photo_embeddings():
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"CRITICAL: Could not connect to database. {e}")
        return

    cur.execute("""
        SELECT id, caption FROM photos
        WHERE caption IS NOT NULL AND TRIM(caption) != '' AND embedding IS NULL;
    """)
    rows = cur.fetchall()

    if not rows:
        print("No photos with caption and missing embedding. Nothing to do.")
        cur.close()
        conn.close()
        return

    print(f"Processing {len(rows)} photo(s) with caption and missing embedding...")

    try:
        for photo_id, caption in rows:
            caption_clean = strip_nul(caption)
            if not caption_clean or not caption_clean.strip():
                continue

            embedding_vector = get_embedding(caption_clean)

            cur.execute("""
                UPDATE photos SET embedding = %s::vector WHERE id = %s;
            """, (embedding_vector, photo_id))

        conn.commit()
        print("\nSUCCESS: Photo embeddings populated and committed.")

    except Exception as e:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK.")
        raise e

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    populate_photo_embeddings()
