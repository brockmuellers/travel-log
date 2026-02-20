"""
Tiny HTTP service that encodes text to 384-dim vectors using the same model
as waypoint embeddings (BAAI/bge-small-en-v1.5). Used by the Go server for
semantic search.
"""
import json
import os
import sys

from sentence_transformers import SentenceTransformer

# Same model as db/populate_waypoint_embeddings.py and scripts/test_search.py
MODEL_NAME = "BAAI/bge-small-en-v1.5"

# Load model once at startup (can take a few seconds on first run)
print("Loading model...", file=sys.stderr)
model = SentenceTransformer(MODEL_NAME)
print("Model ready.", file=sys.stderr)


def encode(text: str) -> list[float]:
    """Normalize and encode text to a 384-dim vector."""
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return [0.0] * 384  # model expects at least some input; empty -> zero vector
    return model.encode(text).tolist()


def handle_embed(body: bytes) -> tuple[int, dict]:
    """
    Parse JSON body {"text": "..."}, return (status_code, response_dict).
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, {"error": "invalid JSON"}

    text = data.get("text")
    if text is None:
        return 400, {"error": "missing field: text"}

    embedding = encode(text)
    return 200, {"embedding": embedding}


def main():
    port = int(os.environ.get("EMBEDDING_SERVICE_PORT", "5001"))
    host = os.environ.get("EMBEDDING_SERVICE_HOST", "127.0.0.1")

    from http.server import HTTPServer, BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path.rstrip("/") != "/embed":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"not found"}')
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            status, resp = handle_embed(body)

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))

        def log_message(self, format, *args):
            print(args[0], file=sys.stderr)

    server = HTTPServer((host, port), Handler)
    print(f"Embedding service listening on http://{host}:{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
