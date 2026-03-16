import os

import psycopg2
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

"""
Populate embeddings on waypoints and photos from text fields already stored in the DB.

- Waypoints: use the `description` column
- Photos: use the `caption` column
"""

# --- Configuration ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")

# Load the free model (downloads automatically on first run)
# This model outputs 384-dimensional vectors
print("Loading model (this may take a moment first time)...")
model = SentenceTransformer("BAAI/bge-small-en-v1.5")


def strip_nul(s: str | None) -> str | None:
    """Remove NUL (0x00) characters. PostgreSQL and some libs reject them."""
    if s is None:
        return s
    return s.replace("\x00", "")


def get_embedding(text: str) -> list[float]:
    """Generates a 384-dim embedding locally."""
    text = text.replace("\n", " ")
    return model.encode(text).tolist()


def populate_waypoint_embeddings() -> None:
    """Populate embeddings on waypoints from their descriptions."""
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"CRITICAL: Could not connect to database. {e}")
        return

    cur.execute(
        """
        SELECT id, description FROM waypoints
        WHERE description IS NOT NULL AND TRIM(description) != '' AND embedding IS NULL;
        """
    )
    rows = cur.fetchall()

    if not rows:
        print("No waypoints with description and missing embedding. Nothing to do.")
        cur.close()
        conn.close()
        return

    print(f"Processing {len(rows)} waypoint(s) with description and missing embedding...")

    try:
        for waypoint_id, description in rows:
            description_clean = strip_nul(description)
            if not description_clean or not description_clean.strip():
                continue

            embedding_vector = get_embedding(description_clean)

            cur.execute(
                """
                UPDATE waypoints
                SET embedding = %s::vector
                WHERE id = %s;
                """,
                (embedding_vector, waypoint_id),
            )

        conn.commit()
        print("\nSUCCESS: Waypoint embeddings populated and committed.")

    except Exception as e:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK (waypoints).")
        raise e

    finally:
        cur.close()
        conn.close()


def populate_photo_embeddings() -> None:
    """Populate embeddings on photos from their captions."""
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"CRITICAL: Could not connect to database. {e}")
        return

    cur.execute(
        """
        SELECT id, caption FROM photos
        WHERE caption IS NOT NULL AND TRIM(caption) != '' AND embedding IS NULL;
        """
    )
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

            cur.execute(
                """
                UPDATE photos
                SET embedding = %s::vector
                WHERE id = %s;
                """,
                (embedding_vector, photo_id),
            )

        conn.commit()
        print("\nSUCCESS: Photo embeddings populated and committed.")

    except Exception as e:
        conn.rollback()
        print("\nTRANSACTION ROLLED BACK (photos).")
        raise e

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    # Run both waypoint and photo embedding population.
    populate_waypoint_embeddings()
    populate_photo_embeddings()

