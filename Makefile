# Declare phony targets (targets that aren't actual files)
.PHONY: help install-deps run-server run-embedding run-photos start-db reload-db deploy-db test-python test-go test prod-pause prod-unpause

# Set shell to bash so I can use bash syntax
SHELL := /bin/bash

# The default target runs when you just type 'make'
help:
	@echo "Available commands:"
	@echo "  make install-deps   - Install Go and Python dependencies"
	@echo "  make run-server     - Run the Go server"
	@echo "  make run-embedding  - Run the Python embedding service"
	@echo "  make run-photos     - Serve local photos for development"
	@echo "  make start-db       - Start up the Docker database container"
	@echo "  make reload-db      - Drop database and run the database population scripts"
	@echo "  make deploy-db      - Copy local database data to remote postgres instance"
	@echo "  make test-python    - Run all Python tests (db, embedding_service, scripts)"
	@echo "  make test-go        - Run Go tests"
	@echo "  make test           - Run both Go and Python tests"
	@echo "  make prod-pause     - Activate the Cloudflare pause worker (503 all API traffic)"
	@echo "  make prod-unpause   - Deactivate the Cloudflare pause worker"

install-deps:
	@echo "Installing Go dependencies..."
	go mod tidy
	@echo "Installing Python dependencies..."
	pip install -r requirements.txt
	pip install -r requirements-dev.txt
	pip install -r embedding_service/requirements.txt

run-server:
	@echo "Starting Go server..."
	go run ./cmd/server

run-embedding:
	@echo "Starting Embedding Service..."
	python embedding_service/main.py

run-photos:
	@. .env && \
	echo "Serving photos from $$PRIVATE_DATA_DIR/photos on :8082..." && \
	python -m http.server 8082 --directory "$$PRIVATE_DATA_DIR/photos"

start-db:
	@echo "Starting Docker database..."
	docker compose up

reload-db:
	@echo "Dropping database and running DB population scripts..."
	./db/reload-db.sh

deploy-db:
	@echo "Copying local database to remote postgres instance..."
	. .env && \
	docker exec travel_log_db pg_dump \
	-U $$DATABASE_USER -d $$DATABASE_NAME --no-owner --no-privileges --clean --if-exists | \
	psql "$$NEON_CONNECTION"

test-python:
	@echo "Running Python tests..."
	# PYTHONPATH=. ensures pytest can find imports relative to the project root
	PYTHONPATH=. pytest db/tests/ embedding_service/tests/ scripts/tests/

test-go:
	@echo "Running Go tests..."
	export DATABASE_URL="postgres://admin:password@localhost:5432/postgres?sslmode=disable"; \
	go test  -tags=integration ./cmd/server/

test: test-go test-python

CF_ZONE_ID := 3830f92fa4dc590bb7719d0c2e021910
CF_ROUTE_PATTERN := api.travel-log.brockmuellers.com/*
CF_WORKER_NAME := pause-travel-log

prod-pause:
	@. .env && \
	curl -sf -X POST "https://api.cloudflare.com/client/v4/zones/$(CF_ZONE_ID)/workers/routes" \
		-H "Authorization: Bearer $$CLOUDFLARE_API_TOKEN" \
		-H "Content-Type: application/json" \
		-d '{"pattern": "$(CF_ROUTE_PATTERN)", "script": "$(CF_WORKER_NAME)"}' | jq .
	@echo "Pause worker activated on $(CF_ROUTE_PATTERN)"

prod-unpause:
	@. .env && \
	ROUTE_ID=$$(curl -sf "https://api.cloudflare.com/client/v4/zones/$(CF_ZONE_ID)/workers/routes" \
		-H "Authorization: Bearer $$CLOUDFLARE_API_TOKEN" | \
		jq -r '.result[] | select(.pattern == "$(CF_ROUTE_PATTERN)" and .script == "$(CF_WORKER_NAME)") | .id') && \
	if [ -z "$$ROUTE_ID" ]; then echo "No active pause route found."; exit 0; fi && \
	curl -sf -X DELETE "https://api.cloudflare.com/client/v4/zones/$(CF_ZONE_ID)/workers/routes/$$ROUTE_ID" \
		-H "Authorization: Bearer $$CLOUDFLARE_API_TOKEN" | jq . && \
	echo "Pause worker deactivated."