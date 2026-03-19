package main

// search.go — Hybrid waypoint search handler.
//
// This file implements the /waypoints/search endpoint, which supports three
// search modes via the "mode" query parameter:
//
//   - "description" — ranks waypoints by cosine distance between the query
//     embedding and the waypoint's description embedding. This is the original
//     search behavior.
//
//   - "photo" — ranks waypoints by their best-matching photos. For each
//     waypoint, the top 5 closest photos (by cosine distance to the query) are
//     averaged into a single score. This rewards both quality and volume of
//     matches: a waypoint with many relevant photos ranks higher than one with
//     only a couple.
//
//   - "combined" (default) — blends both signals with equal weight:
//       combined = 0.5 * description_distance + 0.5 * photo_distance
//     If a waypoint has only one signal (e.g., no photos), it falls back to
//     that signal alone. This means waypoints without photos degrade gracefully
//     to description-only ranking.
//
// Design decisions:
//
//   - Top-5 average (not min, not full average) for photo scoring. Min would
//     ignore volume: a waypoint with 2 temple photos and one with 48 would
//     score the same. Full average would dilute the signal with irrelevant
//     photos. Top-5 average is the sweet spot — it captures "how many good
//     matches exist" without noise from the long tail.
//
//   - Equal 0.5/0.5 blending weight. Simple, explainable, easy to tune later.
//     Both constants (blend weight α and top-N count) are defined at the top of
//     this file.
//
//   - Photos are fetched in a second query after the main ranking query. This
//     keeps the ranking SQL clean and works identically across all three modes.
//     The second query is cheap: it only fetches photos for the 3 returned
//     waypoints.
//
//   - The embedding fetch (local dev service or Hugging Face prod) lives here
//     because search is its only consumer. If another endpoint ever needs
//     embeddings, extract it then.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pgvector/pgvector-go"
)

// ---------------------------------------------------------------------------
// Tunable constants
// ---------------------------------------------------------------------------

const (
	// blendAlpha is the weight given to description distance in combined mode.
	// Photo distance gets (1 - blendAlpha). 0.5 = equal weight.
	blendAlpha = 0.5

	// topNPhotos is how many of the closest photos per waypoint are averaged
	// to produce the photo score. See file header for rationale.
	topNPhotos = 5

	// searchLimit is the maximum number of waypoints returned.
	searchLimit = 3

	// embeddingDim is the expected vector dimension (BAAI/bge-small-en-v1.5).
	embeddingDim = 384

	// hfEmbeddingModel is the Hugging Face Inference API endpoint used in prod.
	hfEmbeddingModel = "https://router.huggingface.co/hf-inference/models/BAAI/bge-small-en-v1.5/pipeline/feature-extraction"
)

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

// hybridSearchResult is one waypoint in search results. It carries both
// per-signal distances so the frontend can show or log how ranking was derived.
type hybridSearchResult struct {
	Name                string       `json:"name"`
	Description         string       `json:"description"`
	Coordinates         *[2]float64  `json:"coordinates"`          // [lon, lat] (GeoJSON order); nil if no location
	Distance            float64      `json:"distance"`             // the distance used for ranking
	Score               float64      `json:"score"`                // 0–100, (1 - distance) * 100
	DescriptionDistance *float64     `json:"description_distance"` // nil when mode=photo
	PhotoDistance       *float64     `json:"photo_distance"`       // nil when mode=description or no photos
	Photos              []photoMatch `json:"photos"`               // top-5 matching photos; nil when mode=description
}

// photoMatch is a single photo inside a waypoint search result.
type photoMatch struct {
	ID       int     `json:"id"`
	Filename string  `json:"filename"`
	Caption  string  `json:"caption"`
	Distance float64 `json:"distance"`
	URL      string  `json:"url,omitempty"`
}

// ---------------------------------------------------------------------------
// SQL queries
// ---------------------------------------------------------------------------

// descriptionSearchSQL ranks waypoints by cosine distance between the query
// embedding and waypoints.embedding. This is the original search behavior.
const descriptionSearchSQL = `
SELECT id, name, COALESCE(description, '') AS description,
       ST_X(location::geometry) AS lon, ST_Y(location::geometry) AS lat,
       (embedding <=> $1) AS distance
FROM waypoints
WHERE embedding IS NOT NULL
ORDER BY distance ASC
LIMIT $2
`

// photoSearchSQL ranks waypoints by the average cosine distance of their top-N
// closest photos. A window function picks the N closest photos per waypoint,
// then we aggregate. Waypoints with fewer than N photos use all of them (no
// penalty).
const photoSearchSQL = `
WITH ranked_photos AS (
    SELECT
        p.waypoint_id,
        (p.embedding <=> $1) AS distance,
        ROW_NUMBER() OVER (
            PARTITION BY p.waypoint_id
            ORDER BY p.embedding <=> $1
        ) AS rn
    FROM photos p
    WHERE p.embedding IS NOT NULL
      AND p.waypoint_id IS NOT NULL
)
SELECT
    w.id,
    w.name,
    COALESCE(w.description, '') AS description,
    ST_X(w.location::geometry) AS lon,
    ST_Y(w.location::geometry) AS lat,
    AVG(rp.distance) AS photo_distance
FROM ranked_photos rp
JOIN waypoints w ON w.id = rp.waypoint_id
WHERE rp.rn <= $2
GROUP BY w.id, w.name, w.description, w.location
ORDER BY photo_distance ASC
LIMIT $3
`

// combinedSearchSQL computes both description-level and photo-level distances,
// then blends them. The CASE expression handles three fallback scenarios:
//   - Both signals present → weighted blend
//   - Only description → use description distance
//   - Only photos → use photo distance
//
// Waypoints with neither signal are excluded by the WHERE clause.
const combinedSearchSQL = `
WITH photo_ranked AS (
    SELECT
        p.waypoint_id,
        (p.embedding <=> $1) AS distance,
        ROW_NUMBER() OVER (
            PARTITION BY p.waypoint_id
            ORDER BY p.embedding <=> $1
        ) AS rn
    FROM photos p
    WHERE p.embedding IS NOT NULL
      AND p.waypoint_id IS NOT NULL
),
photo_agg AS (
    SELECT waypoint_id, AVG(distance) AS photo_distance
    FROM photo_ranked
    WHERE rn <= $2
    GROUP BY waypoint_id
)
SELECT
    w.id,
    w.name,
    COALESCE(w.description, '') AS description,
    ST_X(w.location::geometry) AS lon,
    ST_Y(w.location::geometry) AS lat,
    (w.embedding <=> $1) AS description_distance,
    pa.photo_distance,
    CASE
        WHEN w.embedding IS NOT NULL AND pa.photo_distance IS NOT NULL
            THEN $3 * (w.embedding <=> $1) + (1 - $3) * pa.photo_distance
        WHEN w.embedding IS NOT NULL
            THEN (w.embedding <=> $1)
        ELSE pa.photo_distance
    END AS combined_distance
FROM waypoints w
LEFT JOIN photo_agg pa ON pa.waypoint_id = w.id
WHERE w.embedding IS NOT NULL OR pa.photo_distance IS NOT NULL
ORDER BY combined_distance ASC
LIMIT $4
`

// topPhotosSQL fetches the top-N closest photos for a set of waypoint IDs.
// Called once after the main ranking query to populate the photos[] array in
// each result. The set of IDs is tiny (at most searchLimit), so this is cheap.
const topPhotosSQL = `
WITH ranked AS (
    SELECT
        p.waypoint_id,
        p.id,
        p.filename,
        COALESCE(p.caption, '') AS caption,
        (p.embedding <=> $1) AS distance,
        ROW_NUMBER() OVER (
            PARTITION BY p.waypoint_id
            ORDER BY p.embedding <=> $1
        ) AS rn
    FROM photos p
    WHERE p.embedding IS NOT NULL
      AND p.waypoint_id = ANY($2)
)
SELECT waypoint_id, id, filename, caption, distance
FROM ranked
WHERE rn <= $3
ORDER BY waypoint_id, distance
`

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

// waypointsSearchHybrid returns an http.HandlerFunc that performs hybrid
// waypoint search. It replaces the original waypointsSearch handler.
//
// Query parameters:
//   - q (required): the search query text
//   - mode (optional): "description", "photo", or "combined" (default)
func waypointsSearchHybrid(pool *pgxpool.Pool, env, embeddingServiceURL string, presigner *r2Presigner, photoBaseURL string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("q")
		if q == "" {
			httpError(w, "missing query parameter q", http.StatusBadRequest, nil)
			return
		}

		mode := r.URL.Query().Get("mode")
		if mode == "" {
			mode = "combined"
		}
		if mode != "description" && mode != "photo" && mode != "combined" {
			httpError(w, "invalid mode: must be description, photo, or combined", http.StatusBadRequest, nil)
			return
		}

		vec, err := fetchQueryEmbedding(r.Context(), q, env, embeddingServiceURL)
		if err != nil {
			httpError(w, "embedding service error", http.StatusBadGateway, err)
			return
		}

		pgVec := pgvector.NewVector(vec)

		// Run the appropriate ranking query.
		type waypointRow struct {
			id          int
			name        string
			description string
			lon         *float64 // from ST_X(location::geometry)
			lat         *float64 // from ST_Y(location::geometry)
			wpDist      *float64 // nil in photo-only mode
			photoDist   *float64 // nil in description-only mode
			distance    float64  // the distance used for final ranking
		}

		var rows []waypointRow

		switch mode {
		case "description":
			dbRows, err := pool.Query(r.Context(), descriptionSearchSQL, pgVec, searchLimit)
			if err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}
			defer dbRows.Close()
			for dbRows.Next() {
				var row waypointRow
				if err := dbRows.Scan(&row.id, &row.name, &row.description, &row.lon, &row.lat, &row.distance); err != nil {
					httpError(w, "database scan failed", http.StatusInternalServerError, err)
					return
				}
				d := row.distance
				row.wpDist = &d
				rows = append(rows, row)
			}
			if err := dbRows.Err(); err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}

		case "photo":
			dbRows, err := pool.Query(r.Context(), photoSearchSQL, pgVec, topNPhotos, searchLimit)
			if err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}
			defer dbRows.Close()
			for dbRows.Next() {
				var row waypointRow
				if err := dbRows.Scan(&row.id, &row.name, &row.description, &row.lon, &row.lat, &row.distance); err != nil {
					httpError(w, "database scan failed", http.StatusInternalServerError, err)
					return
				}
				d := row.distance
				row.photoDist = &d
				rows = append(rows, row)
			}
			if err := dbRows.Err(); err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}

		case "combined":
			dbRows, err := pool.Query(r.Context(), combinedSearchSQL, pgVec, topNPhotos, blendAlpha, searchLimit)
			if err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}
			defer dbRows.Close()
			for dbRows.Next() {
				var row waypointRow
				var wpDist, photoDist *float64
				if err := dbRows.Scan(&row.id, &row.name, &row.description, &row.lon, &row.lat, &wpDist, &photoDist, &row.distance); err != nil {
					httpError(w, "database scan failed", http.StatusInternalServerError, err)
					return
				}
				row.wpDist = wpDist
				row.photoDist = photoDist
				rows = append(rows, row)
			}
			if err := dbRows.Err(); err != nil {
				httpError(w, "database query failed", http.StatusInternalServerError, err)
				return
			}
		}

		// Fetch top-N photos for the returned waypoints (skip for description-only mode).
		photosByWaypoint := map[int][]photoMatch{}
		if mode != "description" && len(rows) > 0 {
			wpIDs := make([]int, len(rows))
			for i, r := range rows {
				wpIDs[i] = r.id
			}
			photoRows, err := pool.Query(r.Context(), topPhotosSQL, pgVec, wpIDs, topNPhotos)
			if err != nil {
				log.Printf("photo fetch error: %v", err)
				// Non-fatal: we still return waypoints, just without photos.
			} else {
				defer photoRows.Close()
				for photoRows.Next() {
					var wpID, photoID int
					var filename, caption string
					var dist float64
					if err := photoRows.Scan(&wpID, &photoID, &filename, &caption, &dist); err != nil {
						log.Printf("photo scan error: %v", err)
						break
					}
					photosByWaypoint[wpID] = append(photosByWaypoint[wpID], photoMatch{
						ID:       photoID,
						Filename: filename,
						Caption:  caption,
						Distance: sanitizeDistance(dist),
					})
				}
			}
		}

		// Assemble the response.
		results := make([]hybridSearchResult, 0, len(rows))
		for _, row := range rows {
			dist := sanitizeDistance(row.distance)
			score := (1 - dist) * 100
			if score < 0 || math.IsNaN(score) || math.IsInf(score, 0) {
				score = 0
			}

			result := hybridSearchResult{
				Name:        row.name,
				Description: row.description,
				Distance:    dist,
				Score:       score,
			}

			if row.lon != nil && row.lat != nil {
				result.Coordinates = &[2]float64{*row.lon, *row.lat}
			}

			if row.wpDist != nil {
				d := sanitizeDistance(*row.wpDist)
				result.DescriptionDistance = &d
			}
			if row.photoDist != nil {
				d := sanitizeDistance(*row.photoDist)
				result.PhotoDistance = &d
			}

			if mode != "description" {
				photos := photosByWaypoint[row.id]
				if photos == nil {
					photos = []photoMatch{}
				}
				for i := range photos {
					if presigner != nil {
						url, err := presigner.URL(r.Context(), photos[i].Filename)
						if err != nil {
							log.Printf("presign error for %s: %v", photos[i].Filename, err)
							continue
						}
						photos[i].URL = url
					} else if photoBaseURL != "" {
						photos[i].URL = photoBaseURL + "/" + photos[i].Filename
					}
				}
				result.Photos = photos
			}

			results = append(results, result)
		}

		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(results); err != nil {
			log.Printf("encode search results: %v", err)
		}
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// fetchQueryEmbedding calls the embedding service and returns a 384-dim vector.
// In dev, it calls the local Python embedding service. In prod, it calls the
// Hugging Face Inference API directly. Both use the same HF wire format
// (request: {"inputs": text}, response: [[384 floats]]).
func fetchQueryEmbedding(ctx context.Context, query, env, embeddingServiceURL string) ([]float32, error) {
	embURL := embeddingServiceURL + "/embed"
	if env == "prod" {
		embURL = hfEmbeddingModel
	}

	body, _ := json.Marshal(map[string]string{"inputs": query})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, embURL, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("embedding request failed")
	}
	req.Header.Set("Content-Type", "application/json")

	if env == "prod" {
		token := os.Getenv("HUGGING_FACE_TOKEN")
		if token == "" {
			return nil, fmt.Errorf("HUGGING_FACE_TOKEN not set for prod")
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embedding service unreachable")
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read embedding response body")
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embedding service error (status %d): %s", resp.StatusCode, respBody)
	}

	// HF API originally returned [[384 floats]] (nested) but appears to have
	// changed to [384 floats] (flat). We support both to be safe.
	var flat []float64
	if err := json.Unmarshal(respBody, &flat); err == nil && len(flat) == embeddingDim {
		vec := make([]float32, len(flat))
		for i, v := range flat {
			vec[i] = float32(v)
		}
		return vec, nil
	}

	var nested [][]float64
	if err := json.Unmarshal(respBody, &nested); err == nil && len(nested) > 0 && len(nested[0]) == embeddingDim {
		vec := make([]float32, len(nested[0]))
		for i, v := range nested[0] {
			vec[i] = float32(v)
		}
		return vec, nil
	}

	return nil, fmt.Errorf("unexpected embedding response: %s", respBody)
}

// sanitizeDistance clamps a cosine distance to a safe range. This guards
// against NaN/Inf from empty or zero-norm embeddings in test data.
func sanitizeDistance(d float64) float64 {
	if math.IsNaN(d) || math.IsInf(d, 0) || d < 0 {
		return 0
	}
	return d
}
