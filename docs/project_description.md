This is an AI-generated description of the current state of the project as of commit `e9972aa`. It is designed for ingestion by other LLMs.

---

## Context, purpose, and goals

This project is a **travel-log**: an enriched, map-based visualization and insight tool built from 18 months of sabbatical travel data. The author has collected a large amount of heterogeneous data (GPX tracks, geotagged photos, eBird/iNaturalist observations, Garmin activities, travel notes, and a spouse’s travel blog) and wants to integrate it into a single experience. The primary purpose is to produce a useful, visually engaging keepsake and exploration surface; a secondary purpose is to learn modern ML workflows (vector embeddings, RAG) and spatial data handling. Goals include: (1) a “Hello World” travel map with processed GPX and eBird data and obfuscated sensitive locations; (2) a local database and ETL (Postgres with PostGIS and pgvector) for flexible data models and vector embeddings; (3) a basic server and visualization layer (Go backend, map-based UI); and (4) a low-cost public deployment with privacy and authorization in mind. A stretch goal is a flythrough video that follows the main GPX track and surfaces the most interesting data or insights per location.

The codebase is in an early but functional phase: the database schema and ETL populate **trips**, **waypoints**, **tracks**, and **track_points** from FindPenguins GPX; waypoint **descriptions** are generated from the travel blog (via Gemini) and stored; **embeddings** (384-dim, BAAI/bge-small-en-v1.5) are computed for waypoints and used for semantic search. A Go HTTP server exposes waypoint count and semantic search; in development it calls a local Python embedding service, and in production it uses the Hugging Face Inference API. The frontend is a static GitHub Pages site that can call this API; deployment uses Neon (Postgres), Render (Go server), and Cloudflare (DNS/bot mitigation). Known challenges include timestamp and timezone consistency across sources, geotag precision, privacy handling, and scaling RAG/vector workflows; see the README and docs for data sources, risks, and phased roadmap.

---

## Functionality and architecture

### Data sources and pipeline

- **Primary:** FindPenguins GPX (simplified route between major destinations, timestamps at destination level); geotagged photos; eBird checklists and iNaturalist observations; Garmin data (activities, steps, sleep, HRV); patchy travel notes.
- **Secondary:** Spouse’s travel blog (one post per country, used for waypoint descriptions and LLM context); Google location history; public sources (eBird/iNaturalist global data, weather, OpenStreetMap POIs, Wikivoyage, etc.). Sensitive or private locations are obfuscated (e.g. script `scripts/obfuscate_points.py`).
- **Data layout:** Scripts assume env-driven directories such as `PRIVATE_DATA_DIR`, `INTERIM_DATA_DIR` (e.g. for FindPenguins extracts, blog-derived JSON). Database credentials use `DATABASE_URL` or `DATABASE_CONFIG` (or `DATABASE_HOST`, `DATABASE_USER`, etc.).

### Database

- **Engine:** PostgreSQL with **PostGIS** (spatial types, SRID 4326) and **pgvector** (vector type). Local dev runs via Docker Compose; `Dockerfile` builds PostGIS 17 + pgvector v0.8.1.
- **Schema** (`db/init.sql`):
  - **trips** — One row per “trip” (e.g. Southeast Asia, South America); `name`, `start_date`, `end_date`, `route` (MultiLineString), optional `embedding vector(1536)`.
  - **waypoints** — High-level stops (nights spent), from FindPenguins; `trip_id`, `name`, `description` (from blog/LLM), `start_time`/`end_time` (TIMESTAMPTZ), `location` (POINT), `embedding vector(384)` (from description text). Descriptions and embeddings are populated by separate scripts.
  - **tracks** — Segment between two waypoints; `trip_id`, `name`, `start_time`, `end_time_incl`, `start_waypoint_id`, `end_waypoint_id`, `source` (e.g. FindPenguins), `route` (LINESTRING), `metadata` (JSONB), optional `embedding vector(1536)`.
  - **track_points** — Individual points from tracks; `track_id`, `recorded_at`, `location`, `elevation_meters`.
- **Indexes:** GIST on `trips.route`, `waypoints.location`, `tracks.route`, `track_points.location`; B-tree on `track_points.recorded_at`. Vector columns are used in queries (e.g. cosine distance on `waypoints.embedding`) but the schema does not yet add an explicit vector index in init.sql.

### ETL and embeddings

- **GPX → DB:** `db/populate_db.py` parses FindPenguins GPX (waypoints + track points grouped by timestamp), inserts one trip per file, waypoints with start/end times and location, tracks as LINESTRINGs between waypoints, and track_points with optional SRTM elevation. Uses `psycopg2` and env for DB connection.
- **Waypoint descriptions:** `scripts/describe_waypoints.py` uses Gemini (e.g. `gemini-3-flash-preview`) to generate first-person descriptions per waypoint from the travel blog (PDF/post content). Output is JSON (waypoint name, date, description); data is written to `INTERIM_DATA_DIR` (e.g. `robinblog`).
- **Waypoint embeddings:** `db/populate_waypoint_embeddings.py` reads the blog-derived JSON, computes 384-dim vectors with **BAAI/bge-small-en-v1.5** (SentenceTransformers), and updates `waypoints.description` and `waypoints.embedding` in Postgres. Same model is used for semantic search so dimensions must stay 384 (enforced by `tests/test_embedding_dimension.py`).
- **Other scripts:** e.g. `scripts/list_waypoints.py`, `scripts/test_search.py`, `scripts/obfuscate_points.py`, `scripts/load_inaturalist_counts.py`, `scripts/describe_photos.py` (photo descriptions are on the roadmap). `db/reload-db.sh` wipes and reinitializes the DB and can repopulate (including embeddings).

### Embedding service (local dev)

- **Role:** So the Go server can run semantic search without embedding logic in Go; production uses Hugging Face instead.
- **Implementation:** `embedding_service/main.py` — small HTTP server; `POST /embed` body `{"text": "..."}` returns `{"embedding": [384 floats]}`. Uses SentenceTransformers with `BAAI/bge-small-en-v1.5`. Env: `EMBEDDING_SERVICE_PORT` (default 5001), `EMBEDDING_SERVICE_HOST` (default 127.0.0.1). Dependencies in `embedding_service/requirements.txt`.

### Go server

- **Entrypoint:** `cmd/server/main.go`. Requires env: `DATABASE_URL` or `DATABASE_CONFIG`, `SERVER_ADDR`, `SITE_TOKEN`. Optional: `EMBEDDING_SERVICE_URL` (default `http://127.0.0.1:5001`), `CORS_ORIGINS`, `ENV` (dev vs prod).
- **Routes:**
  - `GET /health` — JSON `{"status":"ok"}`; no auth.
  - `GET /waypoints/count` — JSON `{"count": N}`; requires site token.
  - `GET /waypoints/search?q=<query>` — Semantic search over waypoints; requires site token. Query is embedded (see below), then the server runs a pgvector cosine-distance query on `waypoints.embedding`, returns up to 3 results with `name`, `description`, `distance`, and a 0–100 `score` derived from distance.
- **Auth:** Middleware `requireSiteToken` checks `X-Site-Token` header against `SITE_TOKEN`; `/health` is excluded. Not intended as strong security; mainly to limit abuse and control who can hit the API.
- **CORS:** If `CORS_ORIGINS` is set (comma-separated), responses include `Access-Control-Allow-Origin` for those origins; OPTIONS is handled.
- **Semantic search flow:** (1) Read `q`. (2) **Prod (`ENV=prod`):** POST to Hugging Face Inference API (`BAAI/bge-small-en-v1.5`), using `HUGGING_FACE_TOKEN`. (3) **Dev:** POST to `EMBEDDING_SERVICE_URL/embed` with `{"text": q}`. (4) Expect 384-dim vector; then `SELECT name, description, (embedding <=> $1) AS distance FROM waypoints WHERE embedding IS NOT NULL ORDER BY distance ASC LIMIT 3`.

### Testing

- **Go:** `cmd/server/integration_test.go` (build tag `integration`). Tests require a real Postgres with the app schema (e.g. `docker compose up -d db`) and `DATABASE_URL` (or `DATABASE_CONFIG`) set; otherwise tests are skipped. A mock HTTP server simulates the embedding service (returns zero vector) so no Python service is needed. Run: `go test -tags=integration ./cmd/server/ -count=1`.
- **Python:** `tests/test_embedding_dimension.py` asserts that the waypoint embedding pipeline still outputs 384 dimensions (for compatibility with server and DB). Run from repo root: `pytest tests/ -v` or `python tests/test_embedding_dimension.py`.

### Deployment and infrastructure

- **Database:** Neon (hosted Postgres); schema/data are applied manually (e.g. dump from local and restore to Neon; see README).
- **Backend:** Render (Go server); sleeps after inactivity; cold starts noted in devlog. API example: `https://api.travel-log.brockmuellers.com`.
- **Frontend:** Static site on GitHub Pages; same-origin or CORS to the API.
- **DNS / protection:** Cloudflare (DNS, optional bot blocking). Site token is sent from the frontend to the API to stay within free-tier and rate limits.

### Environment variables (summary)

- **Server:** `DATABASE_URL` or `DATABASE_CONFIG`, `SERVER_ADDR`, `SITE_TOKEN`; optional `ENV`, `EMBEDDING_SERVICE_URL`, `CORS_ORIGINS`; prod only: `HUGGING_FACE_TOKEN`.
- **ETL / scripts:** `PRIVATE_DATA_DIR`, `INTERIM_DATA_DIR`, `DATABASE_*` or `DATABASE_CONFIG`; Gemini scripts use their own API keys (e.g. Google/Gemini).
- **Embedding service:** `EMBEDDING_SERVICE_HOST`, `EMBEDDING_SERVICE_PORT`.

### Planned and in-progress (from docs/TODO.md and devlog)

- **Product:** eBird download/processing; travel mode on GPX and map line coloring; photo descriptions; waypoint summaries from embeddings; map legend; filter search by trip; link search results to map.
- **Internal:** Refactor Go server; split frontend logic; local test DB; Python tests for data transforms; embedding service exposed for public use (to avoid Hugging Face when author’s machine is on).
- **Data:** Fix track in Japan/Vietnam; fix waypoint names with special characters.
- **Infra:** Cold-start handling (e.g. health pings); Cloudflare worker for pausing traffic (version-controlled); CI (e.g. GitHub Actions with service container).

This description is intended to give another LLM enough context to contribute to the project without needing access to the full codebase or private data.
