# travel-log

Visualization and insights from my sabbatical travels.

## Goals

After spending 18 months travelling as a sabbatical, I have collected an immense amount of data. I want to create an enriched map-based travel log that integrates all of my data sources. Perhaps there will be insights available from it, but if not, it will be a cool keepsake.

I'm interested in learning more about modern ML workflows (vectorization and RAG, for example), as well as handling spatial data, so I'm shoehorning those subjects in.

## Data Sources

Primary data sources:
* A GPX track of the entire journey, including modes of transport, pulled from FindPenguins. This route is highly simplified: it is simply our major destinations connected by the routes we took between them. Since we spent multiple days at each destination, the timestamps precision is only accurate to the destination level - not to the minute or even to the day.
* An immense number of photos, mostly geotagged
* Many eBird checklists and iNaturalist observations, eBird lifelist data
* Garmin data, including activities (mostly hikes), step count, sleep data, and HRV data
* Patchy travel notes

Secondary sources:
* My spouse's travel blog, with one post per country - might be useful to provide context for an LLM
* Google location history - this is not super accurate (there are major gaps and drift)
* Data from public sources: global eBird and iNaturalist observations, other biodiversity data sources, weather + sunrise/sunset + AQI, altitude, OpenStreetMap "Points of Interest", major events (GDELT Project?), holidays, government travel advisories, opinionated travel content from Wikivoyage, Alltrails, WWF ecoregions
* Data from non-public sources (can't share it, but it would be interesting to view in a local implementation): Strava/Gaia heatmaps, Lonely Planet & Rough Guide guidebooks

## App Architecture

### Data and ETL

My primary data sources are stored in a `data` directory (gitignored). Sensitive data is stored separately to avoid accidentally exposing un-obfuscated information. I use a number of python scripts to process that data into a display-ready format. A rough diagram of the flow is found in `docs`, or [can be viewed in Excalidraw](https://excalidraw.com/#json=lA_GlfdHmcbOQ3IxK3GLw,hqk1cdvBYapBOC8g9MpFrw).

### Database

A Postgres database stores data for use by the server. Local DB starts with `docker compose up -d`. Production runs on Neon.

The database is populated by scripts in `db`, which read from the `data` directory.

### Go Server

A minimal Go server exposes queries against Postgres, for use by the frontend. Locally, requires a running postgres instance and embedding service. Production is on Render.

Note to self: don't forget to `go mod tidy` or `make install-deps` before pushing if updating `go.mod`!

### Semantic search

The Go server can run semantic search over waypoints using a small local Python embedding service (same model as waypoint embeddings: `BAAI/bge-small-en-v1.5`).

Production semantic search is handled by the Hugging Face API.

**Testing**

1. Start the servers with `make run-server` and `make run-embedding`
2. `curl -H "X-Site-Token: $SITE_TOKEN" "http://localhost:8081/waypoints/search?q=ancient%20temples"`

### Testing

Just normal go tests and pytest. Run with `make`.

### Local and remote environment

Commands for running and deploying code are found in the `Makefile`. Sensitive environment variables are stored in normal `.env` files.

A Cloudflare Worker in `cloudflare/pause-worker/` can intercept all prod API traffic with a 503 maintenance response — useful for staying within free-tier resource limits on the production database. Toggle it with `make prod-pause` and `make prod-unpause`.