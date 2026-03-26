# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Philosophy & Scope
* **Scale:** This is a personal project. It will primarily be used by me, functioning as the backend for a small tool on my personal website. Expect occasional, low-volume traffic (a few hits a day/week).
* **Resource Constraints:** The production database runs on a strict free-tier plan. It must be protected from connection exhaustion, long-running queries, and large memory spikes.
* **Priorities:**
  * **Simplicity and developer ergonomics > Enterprise production readiness.** Maintain simplificity and ergonomics when making changes. If existing code/architecture complexity make changes difficult, suggest simplifications if possible.
  * **Architecture:** Avoid suggesting complex architectural patterns (e.g., microservices, message queues like Kafka/RabbitMQ, caching layers like Redis, or deep interface abstractions). Keep the stack strictly limited to Python, Postgres, and Go.
* **Accuracy:** Documentation may be inconsistent or inaccurate. Ask for clarification when needed.

## Technology Stack & Conventions

### 0. General Conventions
* **Curb Over-engineering:** When writing Go code, prefer flat directory structures and simple functions over deeply nested domain-driven design (DDD). Do not create interfaces unless there are immediately at least two implementations.
* **Error Handling:** For this scale, simple error logging is sufficient. Do not implement complex error wrapping, retry backoff mechanisms, or distributed tracing unless explicitly requested.

### 1. Python (ETL & DB Scripts)
* **Version:** Python 3.10.12
* **Environment:** We use `pip` for dependency management. Requirements are in `requirements.txt` and `requirements-dev.txt`. Requirements for the embedding_service are in `embedding_service/requirements.txt`.
* **Style Guidelines:**
  * Follow PEP 8.
  * Use Type Hints (`typing`) for all function signatures.
  * Formatter & Linter: `ruff`

### 2. PostgreSQL (Database)
* **Version:** PostgreSQL 17
* **Interaction:** The Go server has **READ-ONLY** access to the data. All data mutations and insertions are handled by the Python ETL scripts.
* **Migrations:** Not currently supported.

### 3. Golang (API Server)
* **Version:** Go 1.25.7
* **Style Guidelines:**
  * Strict adherence to standard `gofmt`.
  * Avoid global variables; use dependency injection for database connections and loggers.
  * Handle errors explicitly (no silent failures).

## Commands

```bash
make install-deps    # Install Go and Python dependencies (go mod tidy + pip)
make run-server      # Run the Go server (requires DB and embedding service running)
make run-embedding   # Run the Python embedding service
make run-photos      # Serve local photos on port 8082 (for frontend dev, not required by this repo)
make start-db        # Start the Docker Postgres container
make reload-db       # Wipe and re-populate the local DB from scratch
make deploy-db       # Copy local DB data to Neon (production)
make test            # Run all Go and Python tests
make test-go         # Go integration tests (requires local DB running)
make test-python     # Python tests: db/tests/, embedding_service/tests/, scripts/tests/
make prod-pause      # Activate Cloudflare pause worker (503 all prod API traffic)
make prod-unpause    # Deactivate the pause worker
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

Python dependencies are split: `requirements.txt` (all runtime deps for db/ and scripts/), `requirements-dev.txt` (pytest, ruff), `embedding_service/requirements.txt` (sentence-transformers for the embedding service).

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
- `trips` — top-level trip segments; `source TEXT` is `'findpenguins'` or `'manual'`

**Location obfuscation:** `waypoints` and `photos` each have both `location` (real) and `location_public` (obfuscated for sensitive locations, copied as-is for others). The Go server must only expose `location_public` — never `location`.

**Embedding dimension for `waypoints` and `photos` is 384** (model: `BAAI/bge-small-en-v1.5`). The Go server, pgvector schema, and Python ETL must all agree on this — `db/tests/test_embedding_dimension.py` enforces it. (`tracks` uses a different dimension; not currently used for search.)

### ETL scripts

- `db/populate_waypoints.py` — parses FindPenguins GPX + manual trips JSON (`data/private/manual/trips.json`), fetches SRTM elevation, loads waypoints/tracks/track_points
- `db/populate_photos.py` — photo metadata ingestion
- `db/populate_embeddings.py` — generates 384-dim vectors for waypoint descriptions and photo captions; `get_embedding()` is the shared function used by tests
- `scripts/describe_waypoints.py` — calls Google Gemini to generate first-person waypoint descriptions from the spouse's travel blog
- `scripts/describe_photos.py` — calls local Ollama vision model to generate photo captions
- `db/populate_public_locations.py` — sets `location_public` on waypoints and photos using configured displacement/bearing from `sensitive_locations.json`
- `scripts/process_gpx.py` — obfuscates sensitive coordinates in GPX files using configured displacement/bearing
- `scripts/upload_photos.py` — uploads photos from `$PRIVATE_DATA_DIR/photos/` to Cloudflare R2; skips already-uploaded files

`db/reload-db.sh` runs the full local repopulation sequence: docker compose down/up → populate_waypoints → populate_photos → populate_embeddings → populate_public_locations.

### Go server (`cmd/server/`)

Two files: `main.go` (routing, middleware, config, R2 presigner) and `search.go` (hybrid search handler, embedding client, SQL queries).

Endpoints:
- `GET /health` — no auth
- `GET /waypoints/count` — returns `{"count": N}`
- `GET /waypoints/search?q=<query>&mode=<mode>` — hybrid semantic search; returns top 3 waypoints

The `mode` parameter controls which embeddings are searched:
- `combined` (default) — blends waypoint description + photo caption signals (50/50 weight)
- `description` — waypoint description embeddings only
- `photo` — photo caption embeddings only, aggregated per waypoint (top-5 average cosine distance)

Auth: all non-health endpoints require `X-Site-Token` header matching `SITE_TOKEN` env var.

**Dev vs prod embedding**: `ENV=prod` calls Hugging Face Inference API directly; dev calls the local Python embedding service at `EMBEDDING_SERVICE_URL` (default `http://127.0.0.1:5001`). Both use the HF wire format. Code lives in `search.go:fetchQueryEmbedding`.

**Photo URLs**: Search results include photo URLs. In prod, the R2 bucket is private and the server generates presigned URLs (1-hour expiry) using `R2_*` env vars. In dev, `PHOTO_BASE_URL` (e.g., `http://localhost:8082`) is prepended to the filename. Photos are uploaded to R2 via `scripts/upload_photos.py`.

### Embedding service (`embedding_service/main.py`)

Tiny Python HTTP server. Used only in development to avoid Hugging Face API costs. The Go server can't run Python ML models directly, so this bridges the gap.

- `POST /embed` — `{"text": "..."}` → `{"embedding": [384 floats]}`
- `GET /health` — `{"status": "ok"}`

### Local dev setup

Requires a `.env` file with `DATABASE_URL`, `SERVER_ADDR`, `SITE_TOKEN`, `PRIVATE_DATA_DIR`, `PHOTO_BASE_URL`, and (for prod) `HUGGING_FACE_TOKEN`, `NEON_CONNECTION`, `CORS_ORIGINS`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`. Start order: `make start-db` → `make run-embedding` → `make run-server`. Optionally `make run-photos` to serve photos for the local frontend.

Go tests use the `integration` build tag and expect `DATABASE_URL` pointing at the local Docker DB.

## Directory Structure
Please respect the following separation of concerns:
* `/db/` - SQL schema, DB population scripts (`populate_*.py`), and related tests.
* `/scripts/` - Data preparation scripts (photo captioning, waypoint description generation, coordinate obfuscation, etc.).
* `/lib/` - Shared Python utilities used across `db/` and `scripts/` (e.g., GPS math in `gps_utils.py`).
* `/cmd/server` - Golang backend server code.
* `/docs/` - Any documentation.

## Ignored Files
* `docs/devlog.md` — Personal dev log. Do not read, modify, or commit this file.
* `docs/initial_plan.md` — Outdated project spec. Do not read, modify, or commit this file.
