# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install-deps    # Install Go and Python dependencies (go mod tidy + pip)
make run-server      # Run the Go server (requires DB and embedding service running)
make run-embedding   # Run the Python embedding service
make start-db        # Start the Docker Postgres container
make reload-db       # Wipe and re-populate the local DB from scratch
make deploy-db       # Copy local DB data to Neon (production)
make test            # Run all Go and Python tests
make test-go         # Go integration tests (requires local DB running)
make test-python     # Python tests: db/tests/, embedding_service/tests/, scripts/tests/
```

Run a single Go test (requires local DB):
```bash
export DATABASE_URL="postgres://admin:password@localhost:5432/postgres?sslmode=disable"
go test -tags=integration ./cmd/server/ -run TestIntegration_Health
```

Run a single Python test:
```bash
PYTHONPATH=. pytest db/tests/test_embedding_dimension.py
```

Python dependencies are split: `embedding_service/requirements.txt` (sentence-transformers), `requirements-dev.txt` (pytest, click, timezonefinder). Scripts have their own deps listed per-file.

## Architecture

### Data flow overview

```
Raw data (GPX, photos, blog PDF, eBird, Garmin)
  → Python ETL scripts (db/, scripts/)
  → PostgreSQL + PostGIS + pgvector (local Docker / Neon in prod)
  → Go HTTP server (cmd/server/)
  → Static frontend (GitHub Pages)
```

### Database (PostgreSQL + PostGIS + pgvector)

Schema defined in `db/init.sql`. Key tables:
- `waypoints` — major stops; has `description TEXT` and `embedding vector(384)`
- `tracks` / `track_points` — GPS geometry between waypoints (PostGIS LINESTRING / POINT, SRID 4326)
- `photos` — geotagged images with `caption TEXT` and `embedding vector(384)`
- `trips` — top-level trip segments

**Embedding dimension is 384 throughout** (model: `BAAI/bge-small-en-v1.5`). The Go server, pgvector schema, and Python ETL must all agree on this — `db/tests/test_embedding_dimension.py` enforces it.

### ETL scripts

- `db/populate_waypoints.py` — parses FindPenguins GPX, fetches SRTM elevation, loads waypoints/tracks/track_points
- `db/populate_photos.py` — photo ingestion (in progress)
- `db/populate_embeddings.py` — generates 384-dim vectors for waypoint descriptions and photo captions; `get_embedding()` is the shared function used by tests
- `scripts/describe_waypoints.py` — calls Google Gemini to generate first-person waypoint descriptions from the spouse's travel blog
- `scripts/describe_photos.py` — calls Google Gemini to generate photo captions
- `scripts/obfuscate_points.py` — adds random geographic offset to sensitive coordinates before storing

`db/reload-db.sh` runs the full local repopulation sequence: docker compose down/up → populate_waypoints → populate_photos → populate_embeddings.

### Go server (`cmd/server/main.go`)

Single file. Endpoints:
- `GET /health` — no auth
- `GET /waypoints/count` — returns `{"count": N}`
- `GET /waypoints/search?q=<query>` — semantic search; returns top 3 waypoints by cosine distance

Auth: all non-health endpoints require `X-Site-Token` header matching `SITE_TOKEN` env var.

**Dev vs prod embedding**: `ENV=prod` calls Hugging Face Inference API directly (different request/response format); dev calls the local Python embedding service at `EMBEDDING_SERVICE_URL` (default `http://127.0.0.1:5001`). These are separate code paths in `waypointsSearch`.

### Embedding service (`embedding_service/main.py`)

Tiny Python HTTP server. Used only in development to avoid Hugging Face API costs. The Go server can't run Python ML models directly, so this bridges the gap.

- `POST /embed` — `{"text": "..."}` → `{"embedding": [384 floats]}`
- `GET /health` — `{"status": "ok"}`

### Local dev setup

Requires a `.env` file with `DATABASE_URL`, `SERVER_ADDR`, `SITE_TOKEN`, and (for prod) `HUGGING_FACE_TOKEN`, `NEON_CONNECTION`, `CORS_ORIGINS`. Start order: `make start-db` → `make run-embedding` → `make run-server`.

Go tests use the `integration` build tag and expect `DATABASE_URL` pointing at the local Docker DB.
