import sys
from pathlib import Path

from db import populate_embeddings


def test_embedding_dimension_is_384() -> None:
    """Server and DB expect 384-dim vectors; this catches model/script changes."""
    embedding = populate_embeddings.get_embedding("test query")
    assert len(embedding) == 384, (
        f"expected embedding dimension 384 (server and db/init.sql use vector(384)), got {len(embedding)}"
    )


if __name__ == "__main__":
    test_embedding_dimension_is_384()
    print("OK: embedding dimension is 384")
