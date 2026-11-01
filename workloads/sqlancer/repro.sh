#!/bin/sh
# workloads/sqlancer/repro.sh <sql-file> <reference> <backend>...: replay.c linked with each SQLite-API backend's
# sqlite3.c (build/backends/<name>.src; with SQLancer's options: rtree, fts, stat4, math), the file replayed on each with
# the backend's environment, and what differs from the reference: the rows (replay prints each typed) or a crash (the
# exit status). Exit 1 if anything differs.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); B=$R/build/sqlancer/replay; mkdir -p "$B"
F=$1; shift
[ $# -ge 2 ] || { echo "usage: repro.sh <sql-file> <reference> <backend>..." >&2; exit 2; }
OPTS="-DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_STAT4 -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_MATH_FUNCTIONS"
for b in "$@"; do
  S=$(cat "$R/build/backends/$b.src")
  if [ ! -x "$B/replay-$b" ] || [ "$R/build/backends/$b.so" -nt "$B/replay-$b" ]; then
    # shellcheck disable=SC2086  # (option lists)
    ${CC:-cc} ${REPRO_CFLAGS:--O1 -g} $OPTS -w -I"$S" "$R/workloads/sqlancer/replay.c" "$S/sqlite3.c" -o "$B/replay-$b" -lm -lpthread
  fi
done
T=$(mktemp -d); bad=0; ref=$1
for b in "$@"; do
  ENV=$(python3 -c 'import json, sys; print(" ".join(f"{k}={v}" for k, v in json.load(open(sys.argv[1])).get("env", {}).items()))' "$R/build/backends/$b.json")
  rm -f "$T"/r.db*
  # shellcheck disable=SC2086  # (ENV: K=V words)
  if env $ENV REPLAY_DB="$T/r.db" "$B/replay-$b" "$F" > "$T/$b" 2>&1; then :; else echo "== $b: exit $? (crash?)"; tail -2 "$T/$b"; bad=1; fi
  [ "$b" = "$ref" ] && continue
  if cmp -s "$T/$ref" "$T/$b"; then echo "== $ref vs $b: same"; else echo "== $ref vs $b:"; diff "$T/$ref" "$T/$b" | head -20 || true; bad=1; fi
done
rm -rf "${T:?}"; exit $bad
