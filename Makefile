# Declare phony targets (targets that aren't actual files)
.PHONY: help install-deps run-server run-embedding reload-db test-python test-go test

# The default target runs when you just type 'make'
help:
	@echo "Available commands:"
	@echo "  make install-deps   - Install Go and Python dependencies"
	@echo "  make run-server     - Run the Go server"
	@echo "  make run-embedding  - Run the Python embedding service"
	@echo "  make reload-db      - Drop database and run the database population scripts"
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

test-python:
	@echo "Running Python tests..."
	# PYTHONPATH=. ensures pytest can find imports relative to the project root
	PYTHONPATH=. pytest db/tests/ embedding_service/tests/ scripts/tests/

test-go:
	@echo "Running Go tests..."
	export DATABASE_URL="postgres://admin:password@localhost:5432/postgres?sslmode=disable"; \
	go test  -tags=integration ./cmd/server/

test: test-go test-python