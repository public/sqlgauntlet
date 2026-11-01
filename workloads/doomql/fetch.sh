#!/bin/sh
# workloads/doomql/fetch.sh: the two DOOMQL games, each at a pinned commit, into build/workloads/doomql/src (both MIT;
# nothing of them is committed here: port/ is this repository's SQLite port of the first, and NOTICE.md says how).
#   cedardb-doomql   DOOMQL, Lukas Vogel (CedarDB), written for CedarDB/Postgres: ported by port/ and mkport.py
#   petergpt-doomql  DOOMQL, Peter Gostev, written for SQLite: runs unchanged (mkpeter.py)
set -eu
B=$(cd "$(dirname "$0")/../.." && pwd)/build/workloads/doomql; mkdir -p "$B/src"
get() {   # get <dir> <url> <commit>
  [ -d "$B/src/$1/.git" ] || git clone -q "$2" "$B/src/$1"
  git -C "$B/src/$1" cat-file -e "$3^{commit}" 2>/dev/null || git -C "$B/src/$1" fetch -q origin
  git -C "$B/src/$1" checkout -q "$3"
}
get cedardb-doomql https://github.com/cedardb/DOOMQL 8d6c2fc
get petergpt-doomql https://github.com/petergpt/doomql a2b40b6
