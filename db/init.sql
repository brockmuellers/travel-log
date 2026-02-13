---INITIAL AI-GENERATED SCHEMA---

-- 1. Enable the power tools
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- corresponds to findpenguins trips, e.g. southeast asia, south america
-- storing the full trip gpx track here, just because
CREATE TABLE IF NOT EXISTS trips (
    id SERIAL PRIMARY KEY,
    name TEXT,
    start_date DATE,
    end_date DATE,
    route GEOGRAPHY(MultiLineString, 4326), -- SRID 4326 is standard GPS lat/lon
    embedding vector(1536)
);

-- Waypoints
-- The high level "Nodes", corresponding to nights spent in a place, from findpenguins
-- confirm that times map to correct dates
CREATE TABLE IF NOT EXISTS waypoints (
    id SERIAL PRIMARY KEY,
    trip_id INTEGER REFERENCES trips(id),
    name TEXT,
    description TEXT, -- populated from the blog
    start_time TIMESTAMPTZ, -- Use TIMESTAMPTZ for global travel!
    end_time TIMESTAMPTZ,
    location GEOGRAPHY(POINT, 4326),
    embedding vector(384) -- populated from the description
);

-- Bite-sized gpx tracks
-- From find penguins, it's the trip track broken into inter-waypoint sections
-- storing start and end time just for exploration
CREATE TABLE IF NOT EXISTS tracks (
    id SERIAL PRIMARY KEY,
    trip_id INTEGER REFERENCES trips(id),
    name TEXT,
    start_time TIMESTAMPTZ, -- Use TIMESTAMPTZ for global travel!
    end_time_incl TIMESTAMPTZ, -- inclusive; just the timestamp for the last of the points
    start_waypoint_id INTEGER REFERENCES waypoints(id),
    end_waypoint_id INTEGER REFERENCES waypoints(id),
    source TEXT, -- e.g., 'FindPenguins', 'Garmin'
    route GEOGRAPHY(LINESTRING, 4326),
    metadata JSONB, -- Store original GPX attributes here
    embedding vector(1536)
);

-- Individual track points, broken down from tracks
-- just because I can't decide how I'll be accessing this data
CREATE TABLE IF NOT EXISTS track_points (
    id BIGSERIAL PRIMARY KEY,
    track_id INTEGER REFERENCES tracks(id),
    recorded_at TIMESTAMPTZ NOT NULL,
    location GEOGRAPHY(POINT, 4326), -- consider using GEOMETRY for performance
    elevation_meters NUMERIC
);

-- TODO evaluate the necessity of these
CREATE INDEX idx_trips_route ON trips USING GIST (route);
CREATE INDEX idx_waypoints_loc ON waypoints USING GIST (location);
CREATE INDEX idx_tracks_route ON tracks USING GIST (route);
CREATE INDEX idx_points_loc ON track_points USING GIST (location);
CREATE INDEX idx_points_time ON track_points (recorded_at);
