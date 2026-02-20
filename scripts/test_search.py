import os

import psycopg2
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

### IN PROGRESS ###

# --- Config ---
load_dotenv()
DB_CONFIG = os.getenv("DATABASE_CONFIG")

# Load the SAME model you used for populating
model = SentenceTransformer('BAAI/bge-small-en-v1.5')

def search_waypoints(query_text):
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
        cur.close()
        conn.close()

if __name__ == "__main__":
    # Try different types of queries to test semantic understanding
    search_waypoints("ancient temples and history")
    search_waypoints("relaxing beaches with clear water")
    search_waypoints("busy city streets and markets")
