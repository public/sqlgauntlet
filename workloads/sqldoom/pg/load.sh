#!/bin/sh
# workloads/sqldoom/pg/load.sh [map] [skill]: SQLDoom's WAD loader against Postgres 16.3 in Docker, Freedoom's
# freedoom1.wad, one map (default E1M1, skill 2). Leaves build/workloads/sqldoom/work/pg/<map>.dump (pg_dump) and
# work/<map>.db (the renderer's tables in SQLite, export.py). DOCKER_CPUS=<list> pins the container's CPUs.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd); B=$(cd "$HERE/../.." && pwd)/build/workloads/sqldoom
MAP=${1:-E1M1}; SKILL=${2:-2}
IMG=sqldoom-pg:16.3
docker build -q -t $IMG "$HERE/pg" > /dev/null
mkdir -p "$B/work/pg" && rm -f "$B/work/$MAP.db"
docker run --rm --name sqldoom-load ${DOCKER_CPUS:+--cpuset-cpus "$DOCKER_CPUS"} --memory 4g -v "$HERE:/sd:ro" -v "$B/src:/sd/src:ro" -v "$B/work:/out" $IMG sh -c '
set -e
export PGDATA=/tmp/pgdata PGHOST=/var/run/postgresql PGUSER=postgres PGDATABASE=postgres
su postgres -c "initdb --locale=C --encoding=UTF8 -D $PGDATA" > /dev/null
su postgres -c "pg_ctl -s -D $PGDATA -o \"-c fsync=off -c listen_addresses=\" -w start" > /dev/null
cd /tmp && python3 -I /sd/pg/pgload.py /sd/src/cedardb-sqldoom /sd/src/freedoom/freedoom1.wad '"$MAP $SKILL"'
pg_dump -Fc -f /out/pg/'"$MAP"'.dump
python3 -I /sd/pg/export.py /out/'"$MAP"'.db
chmod a+rw /out/pg/'"$MAP"'.dump /out/'"$MAP"'.db
'
