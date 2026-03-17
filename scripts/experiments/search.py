import os
import subprocess
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

### IN PROGRESS ###

# --- Config ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")
PHOTOS_BASE_DIR = Path(os.getenv("PRIVATE_DATA_DIR")) / "photos"

# Load the SAME model you used for populating
model = SentenceTransformer('BAAI/bge-small-en-v1.5')

def search_waypoints(query_text: str) -> None:
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()

        # 1. Convert text to vector
        query_embedding = model.encode(query_text).tolist()

        # 2. Search in Postgres using pgvector
        # (<=>) operator means "Cosine Distance"
        sql = """
            SELECT name, description, (embedding <=> %s::vector) as distance
            FROM waypoints
            ORDER BY distance ASC
            LIMIT 3;
        """
        cur.execute(sql, (query_embedding,))
        results = cur.fetchall()

        print(f"\nSearch results for: '{query_text}'")
        print("-" * 40)
        for row in results:
            name, desc, dist = row
            # A lower distance means a better match (0 is perfect, 1 is unrelated)
            score = (1 - dist) * 100
            print(f"[{score:.1f}% Match] {name}")
            print(f"   Context: {desc[:100]}...")
            print()

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def search_photos(query_text: str) -> None:
    """Semantic search over photo captions using the same 384-dim embedding model."""
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()

        query_embedding = model.encode(query_text).tolist()

        sql = """
            SELECT p.filename, p.caption, (p.embedding <=> %s::vector) as distance, w.name as waypoint_name
            FROM photos p
            LEFT JOIN waypoints w ON p.waypoint_id = w.id
            WHERE p.embedding IS NOT NULL
            ORDER BY distance ASC
            LIMIT 3;
        """
        cur.execute(sql, (query_embedding,))
        results = cur.fetchall()

        print(f"\nPhoto search results for: '{query_text}'")
        print("-" * 40)
        photo_paths = []
        for row in results:
            filename, caption, dist, waypoint_name = row
            score = (1 - dist) * 100
            waypoint_label = waypoint_name if waypoint_name else "(no waypoint)"
            # caption_preview = (caption or "")[:100]
            # if caption and len(caption) > 100:
            #     caption_preview += "..."
            print(f"[{score:.1f}% Match] {filename} — {waypoint_label}")
            print(f"   Caption: {caption}")
            print()

            # display the photos too; don't have subdirectory offhand but can rglob search for filename
            matches = list(PHOTOS_BASE_DIR.rglob(filename))
            if matches:
                full_path = str(matches[0])
            #     subprocess.Popen(['xdg-open', full_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                print(f"   (File {filename} not found in {PHOTOS_BASE_DIR})")

            if os.path.exists(full_path):
                photo_paths.append(full_path)

        # If we found valid images, open them all in ONE window using Ubuntu's default viewer
        if photo_paths:
            # eog is the standard Ubuntu image viewer command
            subprocess.Popen(['eog'] + photo_paths, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


    except Exception as e:
        print(f"Error: {e}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    # Try different types of queries to test semantic understanding
    search_waypoints("ancient temples and history")
    search_waypoints("relaxing beaches with clear water")
    search_waypoints("busy city streets and markets")

    search_photos("ancient temples and history")
    search_photos("relaxing beaches with clear water")
    search_photos("busy city streets and markets")
