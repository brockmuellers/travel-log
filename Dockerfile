# 1. Use the latest PostGIS image based on Postgres 17
FROM postgis/postgis:17-3.5

# 2. Install build dependencies
# We need git, make, and the postgres server headers (postgresql-server-dev-17)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       git \
       postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*

# 3. Clone and install pgvector v0.8.1
# We pin the version (v0.8.1) to ensure stability
RUN cd /tmp \
    && git clone --branch v0.8.1 https://github.com/pgvector/pgvector.git \
    && cd pgvector \
    && make \
    && make install \
    && cd .. \
    && rm -rf pgvector

# 4. (Optional) Tuning
# Vectors are heavy. This sets a higher default memory limit for maintenance
# tasks like building the HNSW index.
# You can also set this in docker-compose or postgresql.conf later.
CMD ["postgres", "-c", "maintenance_work_mem=512MB"]
