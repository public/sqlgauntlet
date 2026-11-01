#!/bin/sh
# contrib/turbo/tree.sh [repo]: build/turbo-tree, cursed-sqlite-turbo at the pinned commit (contrib/turbo/TURBO: url,
# commit) with this directory's apparatus laid over it where it used to live, so its scripts run unchanged:
#   bench/            contrib/turbo/bench (corpus driver, speedtest1, TPC-H, ClickBench, qrun, the Xeon payloads)
#   bench/corpus/     + sqlgauntlet's corpus: projects/ (a link) and manifest.jsonl (its halves joined)
#   tests/sqlancer/   contrib/turbo/sqlancer
#   third_party/sqlite/  sqlgauntlet's pinned SQLite (the same 3.54.1 turbo's scripts/fetch-sqlite.sh fetches)
# repo: a local clone or another URL for the same history (default: TURBO's url, or $SQLGAUNTLET_TURBO_REPO). The
# tree is rebuilt from scratch each time; prints its path.
set -e
C=$(cd "$(dirname "$0")" && pwd); R=$(cd "$C/../.." && pwd)
URL=$(sed -n 1p "$C/TURBO"); PIN=$(sed -n 2p "$C/TURBO")
SRC=${1:-${SQLGAUNTLET_TURBO_REPO:-$URL}}
if [ -d "$SRC/.git" ] || [ -f "$SRC/.git" ]; then G=$SRC
else G=$R/build/turbo.git; [ -d "$G" ] || git clone -q --bare --filter=blob:none "$SRC" "$G"; git -C "$G" fetch -q origin "$PIN" 2>/dev/null || true; fi
git -C "$G" cat-file -e "$PIN^{commit}" || { echo "tree.sh: $SRC has no commit $PIN" >&2; exit 1; }
T=$R/build/turbo-tree; rm -rf "$T"; mkdir -p "$T"
git -C "$G" archive "$PIN" qc scripts tests Makefile LICENSE | tar -x -C "$T"
echo "$PIN" > "$T/TURBO_COMMIT"
rm -rf "$T/bench" "$T/tests/sqlancer"; cp -R "$C/bench" "$T/bench"; cp -R "$C/sqlancer" "$T/tests/sqlancer"
ln -s "$R/corpus/projects" "$T/bench/corpus/projects"
cat "$R"/corpus/manifest*.jsonl > "$T/bench/corpus/manifest.jsonl"
(cd "$R" && make -s third_party/sqlite/sqlite3.c); mkdir -p "$T/third_party"; cp -R "$R/third_party/sqlite" "$T/third_party/sqlite"
echo "$T"
