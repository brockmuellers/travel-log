#!/bin/bash

# Shortcut to totally wipe and re-populate the local database
# Note that any errors in initialization will be happily swallowed.
# Try `docker logs travel_log_db` if you run into issues.

# Destroy and recreate docker
docker compose down -v  && docker compose up -d --build
# Wait for the DB to fully start
sleep 5
# Populate data
python3 db/populate_db.py
python3 db/populate_waypoint_embeddings.py