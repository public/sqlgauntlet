#!/bin/sh
# workloads/sqlancer/run.sh <backend> <oracle> <seconds> [seed]: one SQLancer process (one thread) on its sqlite3
# provider, against build.sh's JNI library for <backend> (a SQLite-API backend), with the backend's environment
# (build/backends/<backend>.json). <oracle>: one of SQLancer's sqlite3 oracles (NoREC, QUERY_PARTITIONING, PQS, ...).
# Output in build/sqlancer/runs/<backend>-<oracle>-<seed>/: sqlancer.txt, SQLancer's logs/ (one file per bug it
# reports), hs_err_pid*.log after a native crash. SQLANCER_OPTS: more SQLancer options (--log-each-select true to
# replay a crash). campaign.sh runs a matrix.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); O=$R/build/sqlancer
B=$1; ORACLE=$2; SECS=$3; SEED=${4:-1}
[ -f "$O/$B/libsqlitejdbc.so" ] || { echo "run: no build/sqlancer/$B (workloads/sqlancer/build.sh $B)" >&2; exit 2; }
ENV=$(python3 -c 'import json, sys; print(" ".join(f"{k}={v}" for k, v in json.load(open(sys.argv[1])).get("env", {}).items()))' "$R/build/backends/$B.json")
W=$O/runs/$B-$ORACLE-$SEED; rm -rf "${W:?}"; mkdir -p "$W"
T=$(mktemp -d); cd "$T"   # (SQLancer writes its databases and logs/ under the working directory)
# shellcheck disable=SC2086  # (ENV: K=V words)
env $ENV timeout -k 30 $((SECS + 120)) java -Xmx1g \
  -Dorg.sqlite.lib.path="$O/$B" -Dorg.sqlite.lib.name=libsqlitejdbc.so \
  -jar "$O/sqlancer.jar" --num-threads 1 --timeout-seconds "$SECS" --random-seed "$SEED" --num-tries 100000 \
  --print-progress-summary true --print-failed false ${SQLANCER_OPTS:-} sqlite3 --oracle "$ORACLE" > "$W/sqlancer.txt" 2>&1 || true
if [ -d logs ]; then cp -r logs "$W/"; fi
cp hs_err_pid*.log "$W/" 2>/dev/null || true   # (a native crash's report)
cd / && rm -rf "${T:?}"
echo "sqlancer: $B $ORACLE seed $SEED: $(grep -c . "$W/sqlancer.txt") lines of output, $(find "$W/logs/sqlite3" -name '*.log' ! -name '*-cur.log' 2>/dev/null | wc -l | tr -d ' ') bug logs"
