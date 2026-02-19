package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
)

func main() {
	_ = godotenv.Load() // load .env if present; ignore error

	connStr := os.Getenv("DATABASE_URL")
	if connStr == "" {
		connStr = os.Getenv("DATABASE_CONFIG")
	}
	if connStr == "" {
		connStr = buildConnStr(
			getEnv("DATABASE_HOST", "localhost"),
			getEnv("DATABASE_PORT", "5432"),
			getEnv("DATABASE_USER", "admin"),
			getEnv("DATABASE_PASSWORD", "password"),
			getEnv("DATABASE_NAME", "postgres"),
		)
	}

	pool, err := pgxpool.New(context.Background(), connStr)
	if err != nil {
		log.Fatalf("database: %v", err)
	}
	defer pool.Close()

	if err := pool.Ping(context.Background()); err != nil {
		log.Fatalf("database ping: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", health)
	mux.HandleFunc("GET /waypoints/count", waypointsCount(pool))

	addr := getEnv("SERVER_ADDR", ":8081")
	log.Printf("listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func buildConnStr(host, port, user, password, dbname string) string {
	return "postgres://" + user + ":" + password + "@" + host + ":" + port + "/" + dbname + "?sslmode=disable"
}

func health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
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
