//go:build integration

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	pgxvec "github.com/pgvector/pgvector-go/pgx"
)

// Integration tests require a real DB (e.g. docker-compose up db) and DATABASE_URL set.
// Run with: go test -tags=integration ./cmd/server/ -count=1

func TestIntegration_Health(t *testing.T) {
	pool := getTestPool(t)
	defer pool.Close()

	mockEmbed := startMockEmbeddingServer(t, 384)
	defer mockEmbed.Close()

	handler := NewHandler(ServerConfig{
		Pool:                pool,
		Env:                 "dev",
		EmbeddingServiceURL: mockEmbed.URL,
		SiteToken:           "test-token",
	})

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /health: status %d, want 200", rec.Code)
	}
	var out map[string]string
	if err := json.NewDecoder(rec.Body).Decode(&out); err != nil {
		t.Fatalf("decode health response: %v", err)
	}
	if out["status"] != "ok" {
		t.Errorf("health status = %q, want ok", out["status"])
	}
}

func TestIntegration_WaypointsCount(t *testing.T) {
	pool := getTestPool(t)
	defer pool.Close()

	mockEmbed := startMockEmbeddingServer(t, 384)
	defer mockEmbed.Close()

	handler := NewHandler(ServerConfig{
		Pool:                pool,
		Env:                 "dev",
		EmbeddingServiceURL: mockEmbed.URL,
		SiteToken:           "test-token",
	})

	req := httptest.NewRequest(http.MethodGet, "/waypoints/count", nil)
	req.Header.Set("X-Site-Token", "test-token")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /waypoints/count: status %d, want 200", rec.Code)
	}
	var out map[string]int
	if err := json.NewDecoder(rec.Body).Decode(&out); err != nil {
		t.Fatalf("decode count response: %v", err)
	}
	if _, ok := out["count"]; !ok {
		t.Errorf("count response missing \"count\" key: %v", out)
	}
}

func TestIntegration_WaypointsSearch(t *testing.T) {
	pool := getTestPool(t)
	defer pool.Close()

	mockEmbed := startMockEmbeddingServer(t, 384)
	defer mockEmbed.Close()

	handler := NewHandler(ServerConfig{
		Pool:                pool,
		Env:                 "dev",
		EmbeddingServiceURL: mockEmbed.URL,
		SiteToken:           "test-token",
	})

	req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach", nil)
	req.Header.Set("X-Site-Token", "test-token")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /waypoints/search: status %d, want 200. body: %s", rec.Code, rec.Body.Bytes())
	}
	body := rec.Body.Bytes()
	if len(body) == 0 {
		t.Fatalf("GET /waypoints/search: returned 200 but body is empty")
	}
	var results []map[string]interface{}
	if err := json.Unmarshal(body, &results); err != nil {
		t.Fatalf("decode search response: %v (status=%d body=%q)", err, rec.Code, body)
	}
	// Response must be a JSON array (may be empty). Each element must have the contract fields.
	for i, r := range results {
		for _, key := range []string{"name", "description", "distance", "score"} {
			if _, ok := r[key]; !ok {
				t.Errorf("search result [%d] missing key %q", i, key)
			}
		}
	}
}

func getTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	connStr := os.Getenv("DATABASE_URL")
	if connStr == "" {
		connStr = os.Getenv("DATABASE_CONFIG")
	}
	if connStr == "" {
		t.Skip("DATABASE_URL or DATABASE_CONFIG not set; skipping integration test")
	}
	config, err := pgxpool.ParseConfig(connStr)
	if err != nil {
		t.Fatalf("parse config: %v", err)
	}
	config.AfterConnect = func(ctx context.Context, conn *pgx.Conn) error {
		return pgxvec.RegisterTypes(ctx, conn)
	}
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	if err := pool.Ping(context.Background()); err != nil {
		t.Fatalf("ping: %v", err)
	}
	return pool
}

// startMockEmbeddingServer starts an HTTP server that responds to POST /embed with a JSON body
// containing an "embedding" array of dim floats (zeros). Used so search tests don't need a real embedding service.
func startMockEmbeddingServer(t *testing.T, dim int) *httptest.Server {
	t.Helper()
	vec := make([]float64, dim)
	body, _ := json.Marshal(map[string]interface{}{"embedding": vec})
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embed" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(body)
	}))
}
