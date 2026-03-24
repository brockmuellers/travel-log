package main

import (
	"context"
	"encoding/json"
	"log"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
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

	var presigner *r2Presigner
	if r2AccountID := os.Getenv("R2_ACCOUNT_ID"); r2AccountID != "" {
		presigner = newR2Presigner(
			r2AccountID,
			os.Getenv("R2_ACCESS_KEY_ID"),
			os.Getenv("R2_SECRET_ACCESS_KEY"),
			os.Getenv("R2_BUCKET_NAME"),
		)
		log.Printf("R2 presigner configured (bucket: %s)", os.Getenv("R2_BUCKET_NAME"))
	}

	handler := NewHandler(ServerConfig{
		Pool:                pool,
		Env:                 env,
		EmbeddingServiceURL: embeddingServiceURL,
		SiteToken:           siteToken,
		CORSOrigins:         os.Getenv("CORS_ORIGINS"),
		R2Presigner:         presigner,
		PhotoBaseURL:        os.Getenv("PHOTO_BASE_URL"),
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
	R2Presigner          *r2Presigner
	PhotoBaseURL         string
}

// r2Presigner generates time-limited presigned URLs for objects in an R2 bucket.
//
// The R2 bucket is kept private (no public access). Instead of exposing a public
// URL that bots could scrape (racking up Class B read operations against the free
// tier), the Go server mints short-lived presigned URLs on demand. Since every
// non-health endpoint already requires X-Site-Token auth, only legitimate frontend
// requests can obtain photo URLs, and each URL expires after presignedURLExpiry.
// This avoids needing Cloudflare WAF/rate-limit rules or a custom domain in front
// of the bucket.
type r2Presigner struct {
	client *s3.PresignClient
	bucket string
}

// presignedURLExpiry is how long presigned photo URLs remain valid.
const presignedURLExpiry = 1 * time.Hour

func newR2Presigner(accountID, accessKeyID, secretAccessKey, bucket string) *r2Presigner {
	s3Client := s3.New(s3.Options{
		BaseEndpoint: aws.String("https://" + accountID + ".r2.cloudflarestorage.com"),
		Region:       "auto",
		Credentials:  credentials.NewStaticCredentialsProvider(accessKeyID, secretAccessKey, ""),
	})
	return &r2Presigner{
		client: s3.NewPresignClient(s3Client),
		bucket: bucket,
	}
}

// URL returns a presigned GET URL for the given object key, valid for presignedURLExpiry.
func (p *r2Presigner) URL(ctx context.Context, key string) (string, error) {
	result, err := p.client.PresignGetObject(ctx, &s3.GetObjectInput{
		Bucket: &p.bucket,
		Key:    &key,
	}, s3.WithPresignExpires(presignedURLExpiry))
	if err != nil {
		return "", err
	}
	return result.URL, nil
}

// NewHandler builds the HTTP handler from config. Used by main and integration tests.
func NewHandler(cfg ServerConfig) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", health)
	mux.HandleFunc("GET /waypoints", waypointsList(cfg.Pool, cfg.R2Presigner, cfg.PhotoBaseURL))
	mux.HandleFunc("GET /waypoints/count", waypointsCount(cfg.Pool))
	mux.HandleFunc("GET /waypoints/search", waypointsSearchHybrid(cfg.Pool, cfg.Env, cfg.EmbeddingServiceURL, cfg.R2Presigner, cfg.PhotoBaseURL))

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

// httpError logs the error and sends a JSON error response. Every error
// returned to the client goes through this function so nothing is silently
// swallowed. Pass nil for err when there is no underlying error (e.g. bad
// user input).
func httpError(w http.ResponseWriter, msg string, code int, err error) {
	if err != nil {
		log.Printf("HTTP %d: %s: %v", code, msg, err)
	} else {
		log.Printf("HTTP %d: %s", code, msg)
	}
	http.Error(w, `{"error":"`+msg+`"}`, code)
}

func waypointsList(pool *pgxpool.Pool, presigner *r2Presigner, photoBaseURL string) http.HandlerFunc {
	type waypointItem struct {
		ID          int         `json:"id"`
		Name        string      `json:"name"`
		Description string      `json:"description"`
		Coordinates *[2]float64 `json:"coordinates"`  // [lon, lat]; nil if no location
		PhotoURL    *string     `json:"photo_url"`    // nil if no photos
		Trip        *string     `json:"trip"`         // trip key; nil if no trip
	}
	return func(w http.ResponseWriter, r *http.Request) {
		rows, err := pool.Query(r.Context(), `
			SELECT w.id, w.name, COALESCE(w.description, '') AS description,
			       ST_X(w.location_public::geometry) AS lon, ST_Y(w.location_public::geometry) AS lat,
			       t.key AS trip_key
			FROM waypoints w
			LEFT JOIN trips t ON t.id = w.trip_id
			ORDER BY w.id
		`)
		if err != nil {
			httpError(w, "database query failed", http.StatusInternalServerError, err)
			return
		}
		defer rows.Close()

		results := []waypointItem{}
		for rows.Next() {
			var item waypointItem
			var lon, lat *float64
			if err := rows.Scan(&item.ID, &item.Name, &item.Description, &lon, &lat, &item.Trip); err != nil {
				httpError(w, "database scan failed", http.StatusInternalServerError, err)
				return
			}
			if lon != nil && lat != nil {
				item.Coordinates = &[2]float64{*lon, *lat}
			}
			results = append(results, item)
		}
		if err := rows.Err(); err != nil {
			httpError(w, "database query failed", http.StatusInternalServerError, err)
			return
		}

		// Fetch all photo filenames for the returned waypoints, then pick one
		// per waypoint using a seeded RNG (seed = waypoint ID) so the choice
		// is deterministic across requests.
		if len(results) > 0 {
			wpIDs := make([]int, len(results))
			for i, item := range results {
				wpIDs[i] = item.ID
			}
			photoRows, err := pool.Query(r.Context(),
				`SELECT waypoint_id, filename FROM photos WHERE waypoint_id = ANY($1) AND filename IS NOT NULL`,
				wpIDs,
			)
			if err != nil {
				log.Printf("photo fetch error: %v", err)
				// Non-fatal: return waypoints without photos.
			} else {
				defer photoRows.Close()
				photosByWaypoint := map[int][]string{}
				for photoRows.Next() {
					var wpID int
					var filename string
					if err := photoRows.Scan(&wpID, &filename); err != nil {
						log.Printf("photo scan error: %v", err)
						break
					}
					photosByWaypoint[wpID] = append(photosByWaypoint[wpID], filename)
				}
				for i, item := range results {
					filenames := photosByWaypoint[item.ID]
					if len(filenames) == 0 {
						continue
					}
					chosen := filenames[rand.New(rand.NewSource(int64(item.ID))).Intn(len(filenames))]
					var url string
					if photoBaseURL != "" {
						url = photoBaseURL + "/" + chosen
					} else if presigner != nil {
						url, err = presigner.URL(r.Context(), chosen)
						if err != nil {
							log.Printf("presign error for %s: %v", chosen, err)
							continue
						}
					}
					if url != "" {
						results[i].PhotoURL = &url
					}
				}
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(results)
	}
}

func waypointsCount(pool *pgxpool.Pool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var count int
		err := pool.QueryRow(r.Context(), "SELECT count(*) FROM waypoints").Scan(&count)
		if err != nil {
			httpError(w, "database query failed", http.StatusInternalServerError, err)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]int{"count": count})
	}
}

