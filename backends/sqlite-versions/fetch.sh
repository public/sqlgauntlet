#!/bin/sh
# backends/sqlite-versions/fetch.sh X.Y.Z: an older (or newer) SQLite amalgamation into third_party/sqlite-X.Y.Z/
# (gitignored), checked against backends/sqlite-versions/sha256.tsv (a version missing from it is recorded on first fetch,
# so commit the new line). `make sqlite-version V=X.Y.Z` builds it as backend sqlite-X.Y.Z, with the same options as
# every SQLite-API backend: between two of them only the engine's version differs.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); S=$R/backends/sqlite-versions/sha256.tsv
V=${1:?usage: fetch.sh X.Y.Z}
N=$(echo "$V" | awk -F. '{ printf "%d%02d%02d00", $1, $2, $3 }')
D=$R/third_party/sqlite-$V
[ -f "$D/sqlite3.c" ] && exit 0
# the release year is in the download URL: the release log's date
Y=$(awk -F'\t' -v v="$V" '$1 == v { print $2 }' "$S" 2>/dev/null)
if [ -z "$Y" ]; then
  Y=$(curl -fsSL https://sqlite.org/chronology.html | tr '\n' ' ' | grep -oE '<tr>.{0,300}?</tr>' | sed 's/<[^>]*>/ /g' |
      awk -v v="$V" '$2 == v { print substr($1, 1, 4); exit }')
fi
[ -n "$Y" ] || { echo "fetch.sh: no SQLite release $V in the chronology" >&2; exit 1; }
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
T=$R/build/sqlite-$V.x; rm -rf "$T"; mkdir -p "$T"
U="https://www.sqlite.org/$Y/sqlite-amalgamation-$N.zip"
curl -fsSL "$U" -o "$T/a.zip" || { echo "fetch.sh: can't fetch $U" >&2; exit 1; }
H=$(sha "$T/a.zip"); W=$(awk -F'\t' -v v="$V" '$1 == v { print $3 }' "$S" 2>/dev/null)
if [ -z "$W" ]; then printf '%s\t%s\t%s\n' "$V" "$Y" "$H" >> "$S"; echo "fetch.sh: recorded $V $H in $S" >&2
elif [ "$W" != "$H" ]; then echo "fetch.sh: checksum mismatch for $U" >&2; exit 1; fi
(cd "$T" && unzip -q a.zip)
mkdir -p "$D"; cp "$T/sqlite-amalgamation-$N/sqlite3.c" "$T/sqlite-amalgamation-$N/sqlite3.h" "$D/"
echo "$V $H" > "$D/VERSION"; rm -rf "$T"
echo "fetch.sh: SQLite $V in $D"
