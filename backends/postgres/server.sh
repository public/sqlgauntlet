#!/bin/sh
# backends/postgres/server.sh [port] [image]: a throwaway PostgreSQL server in Docker for the postgres backend
# (container sqlgauntlet-pg, localhost only, trust authentication, no volume: its data goes with the container).
# Prints the URL to build the backend with: make postgres PG_URL=<url>.
set -e
P=${1:-55432}; I=${2:-postgres:17-bookworm}
docker inspect sqlgauntlet-pg > /dev/null 2>&1 || docker run -d --name sqlgauntlet-pg -p "127.0.0.1:$P:5432" -e POSTGRES_HOST_AUTH_METHOD=trust "$I" > /dev/null
until docker exec sqlgauntlet-pg pg_isready -q -U postgres; do sleep 1; done
echo "postgresql://postgres@127.0.0.1:$P"
