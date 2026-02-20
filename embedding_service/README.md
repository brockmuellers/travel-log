# Embedding service

Small HTTP service that encodes text to 384-dim vectors using `BAAI/bge-small-en-v1.5` (same model as waypoint embeddings). The Go server calls it for semantic search.

## Endpoints

- **POST /embed** — Body: `{"text": "your query"}` → `{"embedding": [0.1, -0.2, ...]}`
- **GET /health** — Returns `{"status":"ok"}`

## Run locally

```bash
# From repo root
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r embedding_service/requirements.txt
EMBEDDING_SERVICE_PORT=5001 python embedding_service/main.py
```

Defaults: host `127.0.0.1`, port `5001`. Override with `EMBEDDING_SERVICE_HOST` and `EMBEDDING_SERVICE_PORT`.

## Usage with the Go server

Start this service first, then start the Go server. Set `EMBEDDING_SERVICE_URL=http://127.0.0.1:5001` (or your URL) in `.env`. Search:

```bash
curl "http://localhost:8081/waypoints/search?q=ancient%20temples"
```
