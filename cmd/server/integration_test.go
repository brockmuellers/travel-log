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
	"github.com/stretchr/testify/assert"
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

	assert.Equal(t, http.StatusOK, rec.Code, "GET /health status")
	var out map[string]string
	err := json.NewDecoder(rec.Body).Decode(&out)
	assert.NoError(t, err, "decode health response")
	assert.Equal(t, "ok", out["status"], "health status must be ok")
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

	assert.Equal(t, http.StatusOK, rec.Code, "GET /waypoints/count status")
	var out map[string]int
	err := json.NewDecoder(rec.Body).Decode(&out)
	assert.NoError(t, err, "decode count response")
	assert.Contains(t, out, "count", "count response must have \"count\" key")
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

	// Default mode (combined) — should return 200 with the full response shape.
	req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach", nil)
	req.Header.Set("X-Site-Token", "test-token")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	assert.Equal(t, http.StatusOK, rec.Code, "GET /waypoints/search status, body: %s", rec.Body.Bytes())
	body := rec.Body.Bytes()
	assert.NotEmpty(t, body, "GET /waypoints/search body must not be empty")
	var results []map[string]interface{}
	err := json.Unmarshal(body, &results)
	assert.NoError(t, err, "decode search response")
	for i, r := range results {
		for _, key := range []string{"name", "description", "coordinates", "distance", "score", "description_distance", "photo_distance", "photos"} {
			assert.Contains(t, r, key, "search result [%d] must have key %q", i, key)
		}
	}
}

func TestIntegration_WaypointsSearch_Modes(t *testing.T) {
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

	t.Run("mode=description", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach&mode=description", nil)
		req.Header.Set("X-Site-Token", "test-token")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusOK, rec.Code)
		var results []map[string]interface{}
		assert.NoError(t, json.Unmarshal(rec.Body.Bytes(), &results))
		for i, r := range results {
			assert.NotNil(t, r["description_distance"], "result [%d] description_distance should be set", i)
			assert.Nil(t, r["photo_distance"], "result [%d] photo_distance should be nil in description mode", i)
			assert.Nil(t, r["photos"], "result [%d] photos should be nil in description mode", i)
			// coordinates should be a [lon, lat] array when present.
			if coords, ok := r["coordinates"]; ok && coords != nil {
				arr, ok := coords.([]interface{})
				assert.True(t, ok, "result [%d] coordinates should be an array", i)
				assert.Equal(t, 2, len(arr), "result [%d] coordinates should have exactly 2 elements [lon, lat]", i)
			}
		}
	})

	t.Run("mode=photo", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach&mode=photo", nil)
		req.Header.Set("X-Site-Token", "test-token")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusOK, rec.Code)
		var results []map[string]interface{}
		assert.NoError(t, json.Unmarshal(rec.Body.Bytes(), &results))
		for i, r := range results {
			assert.Nil(t, r["description_distance"], "result [%d] description_distance should be nil in photo mode", i)
			assert.NotNil(t, r["photo_distance"], "result [%d] photo_distance should be set", i)
			assert.NotNil(t, r["photos"], "result [%d] photos should be present in photo mode", i)
		}
	})

	t.Run("mode=combined explicit", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach&mode=combined", nil)
		req.Header.Set("X-Site-Token", "test-token")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusOK, rec.Code)
		var results []map[string]interface{}
		assert.NoError(t, json.Unmarshal(rec.Body.Bytes(), &results))
		// Combined mode should always include the photos array.
		for i, r := range results {
			assert.NotNil(t, r["photos"], "result [%d] photos should be present in combined mode", i)
		}
	})

	t.Run("mode=invalid returns 400", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/waypoints/search?q=beach&mode=bogus", nil)
		req.Header.Set("X-Site-Token", "test-token")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusBadRequest, rec.Code)
	})
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
	assert.NoError(t, err, "parse config")
	config.AfterConnect = func(ctx context.Context, conn *pgx.Conn) error {
		return pgxvec.RegisterTypes(ctx, conn)
	}
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	assert.NoError(t, err, "connect")
	assert.NoError(t, pool.Ping(context.Background()), "ping")
	return pool
}

// startMockEmbeddingServer starts an HTTP server that responds to POST /embed with a nested
// [[dim floats]] array (zeros) matching the HF wire format. Used so search tests don't need a real embedding service.
func startMockEmbeddingServer(t *testing.T, dim int) *httptest.Server {
	t.Helper()
	vec := make([]float64, dim)
	body, _ := json.Marshal([][]float64{vec})
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embed" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(body)
	}))
}
