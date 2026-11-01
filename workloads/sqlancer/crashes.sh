#!/bin/sh
# workloads/sqlancer/crashes.sh <reference> <backend> <sql-file>: exit 1 if <backend> crashes replaying the file on a
# database file and <reference> doesn't; 0 otherwise. For reduce.py. Uses repro.sh's builds (build/sqlancer/replay).
B=$(cd "$(dirname "$0")/../.." && pwd)/build/sqlancer/replay
D=$(mktemp -d)
REPLAY_DB=$D/r.db "$B/replay-$1" "$3" > /dev/null 2>&1; s=$?
rm -f "$D"/r.db*; REPLAY_DB=$D/r.db "$B/replay-$2" "$3" > /dev/null 2>&1; t=$?
rm -rf "${D:?}"
[ $s -eq 0 ] && [ $t -ge 128 ] && exit 1
exit 0
