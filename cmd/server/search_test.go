package main

import (
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func tp(t time.Time) *time.Time { return &t }

var base = time.Date(2024, 12, 1, 10, 0, 0, 0, time.UTC)

func TestDiversifyPhotos_FewerThanN(t *testing.T) {
	candidates := []photoMatch{
		{ID: 1, Distance: 0.1, TimeTaken: tp(base)},
		{ID: 2, Distance: 0.2, TimeTaken: tp(base.Add(1 * time.Hour))},
	}
	got := diversifyPhotos(candidates, 5, 5*24*time.Hour)
	assert.Equal(t, candidates, got, "should return all candidates when fewer than n")
}

func TestDiversifyPhotos_ExactlyN(t *testing.T) {
	candidates := make([]photoMatch, 5)
	for i := range candidates {
		candidates[i] = photoMatch{ID: i, Distance: float64(i) * 0.1, TimeTaken: tp(base.Add(time.Duration(i) * time.Hour))}
	}
	got := diversifyPhotos(candidates, 5, 5*24*time.Hour)
	assert.Equal(t, candidates, got, "should return all candidates when exactly n")
}

func TestDiversifyPhotos_SkipsDuplicateTimes(t *testing.T) {
	// 4 photos within minutes, 1 photo 10 days later.
	// Should pick the best match (ID 1), skip the cluster, grab the distant one (ID 5).
	gap := 5 * 24 * time.Hour
	candidates := []photoMatch{
		{ID: 1, Distance: 0.10, TimeTaken: tp(base)},
		{ID: 2, Distance: 0.12, TimeTaken: tp(base.Add(5 * time.Minute))},
		{ID: 3, Distance: 0.13, TimeTaken: tp(base.Add(10 * time.Minute))},
		{ID: 4, Distance: 0.14, TimeTaken: tp(base.Add(15 * time.Minute))},
		{ID: 5, Distance: 0.50, TimeTaken: tp(base.Add(10 * 24 * time.Hour))},
	}

	got := diversifyPhotos(candidates, 2, gap)
	assert.Len(t, got, 2)
	assert.Equal(t, 1, got[0].ID, "first pick: best match")
	assert.Equal(t, 5, got[1].ID, "second pick: skips cluster for distant photo")
}

func TestDiversifyPhotos_FallsBackToMostDistant(t *testing.T) {
	// All photos are within the 5-day gap, so the algorithm falls back to
	// picking the candidate with the largest minimum time gap from selected.
	gap := 5 * 24 * time.Hour
	candidates := []photoMatch{
		{ID: 1, Distance: 0.10, TimeTaken: tp(base)},                       // t+0h
		{ID: 2, Distance: 0.15, TimeTaken: tp(base.Add(1 * time.Hour))},    // t+1h
		{ID: 3, Distance: 0.20, TimeTaken: tp(base.Add(3 * time.Hour))},    // t+3h
		{ID: 4, Distance: 0.25, TimeTaken: tp(base.Add(2 * time.Hour))},    // t+2h
	}

	got := diversifyPhotos(candidates, 3, gap)
	assert.Len(t, got, 3)
	assert.Equal(t, 1, got[0].ID, "first: best match (t+0h)")
	assert.Equal(t, 3, got[1].ID, "second: fallback picks most distant from selected (t+3h, gap=3h)")
	assert.Equal(t, 2, got[2].ID, "third: ID 2 and ID 4 both have 1h min gap; ID 2 wins by candidate order")
}

func TestDiversifyPhotos_NilTimestampsAlwaysEligible(t *testing.T) {
	gap := 5 * 24 * time.Hour
	candidates := []photoMatch{
		{ID: 1, Distance: 0.10, TimeTaken: tp(base)},
		{ID: 2, Distance: 0.12, TimeTaken: tp(base.Add(1 * time.Minute))},
		{ID: 3, Distance: 0.15, TimeTaken: nil},
		{ID: 4, Distance: 0.20, TimeTaken: tp(base.Add(10 * 24 * time.Hour))},
	}

	got := diversifyPhotos(candidates, 3, gap)
	assert.Len(t, got, 3)
	assert.Equal(t, 1, got[0].ID, "first: best match")
	assert.Equal(t, 3, got[1].ID, "second: nil timestamp is always eligible")
	assert.Equal(t, 4, got[2].ID, "third: distant photo beats cluster member")
}

func TestDiversifyPhotos_MultipleClusters(t *testing.T) {
	// Three clusters separated by 7+ days. Should pick one from each cluster
	// first (best match per cluster), then fill remaining slots via fallback.
	gap := 5 * 24 * time.Hour
	day1 := base
	day8 := base.Add(7 * 24 * time.Hour)
	day16 := base.Add(15 * 24 * time.Hour)
	candidates := []photoMatch{
		{ID: 1, Distance: 0.10, TimeTaken: tp(day1)},
		{ID: 2, Distance: 0.11, TimeTaken: tp(day1.Add(10 * time.Minute))},
		{ID: 3, Distance: 0.12, TimeTaken: tp(day1.Add(20 * time.Minute))},
		{ID: 4, Distance: 0.13, TimeTaken: tp(day1.Add(30 * time.Minute))},
		{ID: 5, Distance: 0.20, TimeTaken: tp(day8)},
		{ID: 6, Distance: 0.21, TimeTaken: tp(day8.Add(10 * time.Minute))},
		{ID: 7, Distance: 0.22, TimeTaken: tp(day8.Add(20 * time.Minute))},
		{ID: 8, Distance: 0.30, TimeTaken: tp(day16)},
		{ID: 9, Distance: 0.31, TimeTaken: tp(day16.Add(10 * time.Minute))},
		{ID: 10, Distance: 0.32, TimeTaken: tp(day16.Add(20 * time.Minute))},
	}

	got := diversifyPhotos(candidates, 5, gap)
	assert.Len(t, got, 5)
	assert.Equal(t, 1, got[0].ID, "best match from day 1 cluster")
	assert.Equal(t, 5, got[1].ID, "best match from day 8 cluster")
	assert.Equal(t, 8, got[2].ID, "best match from day 16 cluster")
	// Remaining slots: all candidates are within 30m of a selected photo.
	// ID 4 (day1+30m) has the largest min gap (30m from ID 1).
	assert.Equal(t, 4, got[3].ID, "fallback: largest min gap among remaining")
}

func TestDiversifyPhotos_AllSameTime(t *testing.T) {
	// All identical timestamps — gap is always 0 so every pick is a fallback.
	// All fallback candidates tie, so order follows cosine distance.
	gap := 5 * 24 * time.Hour
	candidates := make([]photoMatch, 8)
	for i := range candidates {
		candidates[i] = photoMatch{ID: i + 1, Distance: float64(i) * 0.05, TimeTaken: tp(base)}
	}

	got := diversifyPhotos(candidates, 5, gap)
	assert.Len(t, got, 5)
	for i, p := range got {
		assert.Equal(t, i+1, p.ID, "same-time fallback preserves cosine distance order")
	}
}

func TestMinTimeGap_NilTimestamp(t *testing.T) {
	photo := photoMatch{ID: 1, TimeTaken: nil}
	selected := []photoMatch{{ID: 2, TimeTaken: tp(base)}}
	assert.Equal(t, time.Duration(-1), minTimeGap(photo, selected))
}

func TestMinTimeGap_SelectedHasNilTimestamp(t *testing.T) {
	// Should skip the nil-timestamp member and only compare against ID 3.
	photo := photoMatch{ID: 1, TimeTaken: tp(base)}
	selected := []photoMatch{
		{ID: 2, TimeTaken: nil},
		{ID: 3, TimeTaken: tp(base.Add(48 * time.Hour))},
	}
	assert.Equal(t, 48*time.Hour, minTimeGap(photo, selected))
}
