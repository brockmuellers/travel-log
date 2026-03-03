# Declare phony targets (targets that aren't actual files)
.PHONY: help install-deps run-server run-embedding reload-db deploy-db test-python test-go test

# Set shell to bash so I can use bash syntax
SHELL := /bin/bash

# The default target runs when you just type 'make'
help:
	@echo "Available commands:"
	@echo "  make install-deps   - Install Go and Python dependencies"
	@echo "  make run-server     - Run the Go server"
	@echo "  make run-embedding  - Run the Python embedding service"
	@echo "  make reload-db      - Drop database and run the database population scripts"
	@echo "  make deploy-db      - Copy local database data to remote postgres instance"
	@echo "  make test-python    - Run all Python tests (db, embedding_service, scripts)"
	@echo "  make test-go        - Run Go tests"
	@echo "  make test           - Run both Go and Python tests"

install-deps:
	@echo "Installing Go dependencies..."
	go mod tidy
	@echo "Installing Python dependencies..."
	pip install -r embedding_service/requirements.txt
	pip install -r scripts/requirements.txt

run-server:
	@echo "Starting Go server..."
	go run ./cmd/server

run-embedding:
	@echo "Starting Embedding Service..."
	python embedding_service/main.py

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