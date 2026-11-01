#!/bin/sh
# fetch-libsql.sh: libSQL's C amalgamation (MIT; the one its Rust crates bundle, libsql-ffi/bundled/src) at a pinned
# commit into third_party/libsql (gitignored), checked against recorded SHA-256s.
set -e
R=$(cd "$(dirname "$0")/.." && pwd); D=$R/third_party/libsql
SHA=d8bc862d20bedc72945a1da27c8b7045414c0261
U=https://raw.githubusercontent.com/tursodatabase/libsql/$SHA/libsql-ffi/bundled/src
mkdir -p "$D"
for f in sqlite3.c sqlite3.h; do [ -f "$D/$f" ] || curl -fsSL "$U/$f" -o "$D/$f"; done
curl -fsSL "https://raw.githubusercontent.com/tursodatabase/libsql/$SHA/LICENSE.md" -o "$D/LICENSE.md" 2>/dev/null || true
echo "$SHA" > "$D/COMMIT"
if [ -f "$R/scripts/libsql.sha256" ]; then (cd "$D" && shasum -a 256 -c "$R/scripts/libsql.sha256" >&2)
else (cd "$D" && shasum -a 256 sqlite3.c sqlite3.h) > "$R/scripts/libsql.sha256"; fi
