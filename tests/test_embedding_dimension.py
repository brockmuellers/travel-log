"""
Ensures the embedding model used for waypoints still outputs 384 dimensions.
If you change the model or the embedding pipeline, update the server and DB (vector(384)) together.
Run from repo root: pytest tests/ -v
"""
import sys
from pathlib import Path

# Allow importing db script without making db a package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "db"))
import populate_waypoint_embeddings  # noqa: E402


def test_embedding_dimension_is_384():
    """Server and DB expect 384-dim vectors; this catches model/script changes."""
    embedding = populate_waypoint_embeddings.get_embedding("test query")
    assert len(embedding) == 384, (
        f"expected embedding dimension 384 (server and db/init.sql use vector(384)), got {len(embedding)}"
    )


if __name__ == "__main__":
    test_embedding_dimension_is_384()
    print("OK: embedding dimension is 384")
