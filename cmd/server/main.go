package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	pgxvec "github.com/pgvector/pgvector-go/pgx"
)

func main() {
	_ = godotenv.Load()

	connStr := os.Getenv("DATABASE_URL")
	if connStr == "" {
		connStr = os.Getenv("DATABASE_CONFIG")
	}
	if connStr == "" {
		log.Fatal("could not find DATABASE_URL or DATABASE_CONFIG in environment variables")
	}

	serverAddr := os.Getenv("SERVER_ADDR")
	if serverAddr == "" {
		log.Fatal("could not find SERVER_ADDR in environment variables")
	}

	embeddingServiceURL := os.Getenv("EMBEDDING_SERVICE_URL")
	if embeddingServiceURL == "" {
		embeddingServiceURL = "http://127.0.0.1:5001"
	}

	config, err := pgxpool.ParseConfig(connStr)
	if err != nil {
		log.Fatalf("database config: %v", err)
	}
	config.AfterConnect = func(ctx context.Context, conn *pgx.Conn) error {
		return pgxvec.RegisterTypes(ctx, conn)
	}

	// This might take a few extra seconds while DB starts up but there's no timeout so it's fine.
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		log.Fatalf("database: %v", err)
	}
	defer pool.Close()

	if err := pool.Ping(context.Background()); err != nil {
		log.Fatalf("database ping: %v", err)
	}

	env := os.Getenv("ENV")
	if env == "" {
		env = "dev"
	}

	// Site token is used to authenticate requests to the server.
	// It is not meant to be super secure - just an extra layer of protection.
	siteToken := os.Getenv("SITE_TOKEN")
	if siteToken == "" {
		log.Fatal("SITE_TOKEN must be set in environment (.env)")
	}

	handler := NewHandler(ServerConfig{
		Pool:                pool,
		Env:                 env,
		EmbeddingServiceURL: embeddingServiceURL,
		SiteToken:           siteToken,
		CORSOrigins:         os.Getenv("CORS_ORIGINS"),
	})

	log.Printf("listening on %s", serverAddr)
	if err := http.ListenAndServe(serverAddr, handler); err != nil {
		log.Fatal(err)
	}
}

// ServerConfig holds the configuration needed to build the HTTP handler (used by main and tests).
type ServerConfig struct {
	Pool                 *pgxpool.Pool
	Env                  string
	EmbeddingServiceURL  string
	SiteToken            string
	CORSOrigins          string
}

// NewHandler builds the HTTP handler from config. Used by main and integration tests.
func NewHandler(cfg ServerConfig) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", health)
	mux.HandleFunc("GET /waypoints/count", waypointsCount(cfg.Pool))
	mux.HandleFunc("GET /waypoints/search", waypointsSearchHybrid(cfg.Pool, cfg.Env, cfg.EmbeddingServiceURL))

	noAuthPaths := []string{"/health"}
	handler := requireSiteToken(cfg.SiteToken, mux, noAuthPaths)
	if cfg.CORSOrigins != "" {
		handler = corsMiddleware(handler, cfg.CORSOrigins)
	}
	return handler
}

func health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// requireSiteToken returns a middleware that rejects requests without a valid SITE_TOKEN.
// The key may be sent via the X-Site-Token header.
// Requests whose path is in skipPaths are passed through without checking the token.
func requireSiteToken(expectedToken string, next http.Handler, skipPaths []string) http.Handler {
	skip := make(map[string]struct{}, len(skipPaths))
	for _, p := range skipPaths {
		skip[p] = struct{}{}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, ok := skip[r.URL.Path]; ok {
			next.ServeHTTP(w, r)
			return
		}
		token := r.Header.Get("X-Site-Token")
		if token != expectedToken {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid or missing site token"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

// corsMiddleware sets CORS headers when Origin is in the comma-separated allowed list and responds to OPTIONS. Empty entries are ignored.
func corsMiddleware(next http.Handler, allowedOriginsStr string) http.Handler {
	originsList := strings.Split(allowedOriginsStr, ",")
	allowedOrigins := make(map[string]bool, len(originsList))
	for _, o := range originsList {
		if o := strings.TrimSpace(o); o != "" {
			allowedOrigins[o] = true
		}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if allowedOrigins[origin] {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Site-Token")
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func waypointsCount(pool *pgxpool.Pool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var count int
		err := pool.QueryRow(r.Context(), "SELECT count(*) FROM waypoints").Scan(&count)
		if err != nil {
			http.Error(w, `{"error":"database query failed"}`, http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]int{"count": count})
	}
}

