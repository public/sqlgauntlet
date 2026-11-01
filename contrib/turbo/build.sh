#!/bin/sh
# contrib/turbo/build.sh <turbo-checkout> [corpus.c]: cursed-sqlite-turbo (a specialising layer over SQLite) as the
# backend "turbo", an example of an engine built outside this repository: its qc/patch.py over the same SQLite as
# backends/sqlite, qc.c appended, then make sqlite-api. It serves statements inside sqlite3_step when QC_SERVE=1, which
# its arm defaults set. With corpus.c (turbo's own bench driver, which prints the same lines), it is also built over
# both amalgamations as build/ref/corpus-sqlite and build/ref/corpus-turbo, exe arms for tools/identity.py.
set -e
T=${1:?usage: contrib/turbo/build.sh <turbo-checkout> [corpus.c]}; T=$(cd "$T" && pwd)
R=$(cd "$(dirname "$0")/../.." && pwd); O=$R/build/turbo
cd "$R"; make -s third_party/sqlite/sqlite3.c
mkdir -p "$O"
python3 "$T/qc/patch.py" third_party/sqlite/sqlite3.c "$O/sqlite3.c.tmp"
python3 "$T/qc/gen_decoders.py" "$O/qc_dec.h"
printf '\n#include "qc.c"   /* cursed-sqlite-turbo */\n' >> "$O/sqlite3.c.tmp"; mv "$O/sqlite3.c.tmp" "$O/sqlite3.c"
cp third_party/sqlite/sqlite3.h "$O/"
git -C "$T" rev-parse HEAD > "$O/COMMIT"
make -s sqlite-api NAME=turbo SRC="$O" ENV="QC_SERVE=1" \
  EXTRA_CFLAGS="-I$T/qc -DSG_VERSION_NOTE='\" + turbo $(cut -c1-10 "$O/COMMIT")\"'"
echo "contrib/turbo: build/backends/turbo.so at $(cut -c1-10 "$O/COMMIT")"
[ -n "${2:-}" ] || exit 0
OPTS=$(make -s -p -n 2>/dev/null | sed -n 's/^SQLITE_OPTS := //p' | head -1)
mkdir -p build/ref
# shellcheck disable=SC2086  # (OPTS and CFLAGS are option lists)
cc ${CFLAGS:--O2} $OPTS -w -Ithird_party/sqlite "$2" third_party/sqlite/sqlite3.c -o build/ref/corpus-sqlite -lm
# shellcheck disable=SC2086
cc ${CFLAGS:--O2} $OPTS -w -I"$O" -I"$T/qc" "$2" "$O/sqlite3.c" -o build/ref/corpus-turbo -lm
echo "contrib/turbo: build/ref/corpus-sqlite, build/ref/corpus-turbo"
