#!/bin/sh
# contrib/turbo/build.sh [repo] [variants]: cursed-sqlite-turbo at the pinned commit (contrib/turbo/TURBO; tree.sh
# assembles it) as sqlgauntlet backends, through the generic make sqlite-api, over the same SQLite as backends/sqlite.
# variants (default "native serve diff ref"), bench/build-sqlite.sh's:
#   native  turbo         qc/patch.py, qc.c appended: qc serves read-only statements inside sqlite3_step when
#                         QC_SERVE=1, which its arm defaults set (the shipped build)
#   serve   turbo-serve   the step hook, sqlite3_reset/finalize/close wrapped, bench/qc_serve.c: qc behind SQLite's
#                         API for unmodified programs (QC_VM_ONLY=1: through qvm; QCS_STATS: per-SQL counts)
#   diff    turbo-diff    every read-only statement also run through qc on a copy and compared value by value with the
#                         interpreter's rows (QCTDIFF lines in $QCT_DIR)
#   ref     build/ref/corpus-sqlite, build/ref/corpus-turbo: turbo's own bench driver (bench/corpus/corpus.c) over
#           SQLite and the native build, exe arms for tools/identity.py
set -e
C=$(cd "$(dirname "$0")" && pwd); R=$(cd "$C/../.." && pwd); cd "$R"
T=$(sh "$C/tree.sh" "${1:-}"); PIN=$(cut -c1-10 "$T/TURBO_COMMIT")
for v in ${2:-native serve diff ref}; do
  case $v in
    native|serve|diff)
      (cd "$T" && sh bench/build-sqlite.sh "$v" "build/v-$v" > /dev/null)
      n=turbo; [ "$v" = native ] || n=turbo-$v
      env=""; [ "$v" = native ] && env="QC_SERVE=1"
      make -s sqlite-api NAME="$n" SRC="$T/build/v-$v" ENV="$env" EXTRA_CFLAGS="-DSG_VERSION_NOTE='\" + turbo $v $PIN\"'"
      echo "contrib/turbo: build/backends/$n.so ($v, turbo $PIN)" ;;
    ref)
      OPTS=$(make -s -p -n 2>/dev/null | sed -n 's/^SQLITE_OPTS := //p' | head -1); mkdir -p build/ref
      [ -f "$T/build/v-native/sqlite3.c" ] || (cd "$T" && sh bench/build-sqlite.sh native build/v-native > /dev/null)
      # shellcheck disable=SC2086  # (option lists)
      cc ${CFLAGS:--O2} $OPTS -w -Ithird_party/sqlite "$T/bench/corpus/corpus.c" third_party/sqlite/sqlite3.c -o build/ref/corpus-sqlite -lm
      # shellcheck disable=SC2086
      cc ${CFLAGS:--O2} $OPTS -w -I"$T/build/v-native" "$T/bench/corpus/corpus.c" "$T/build/v-native/sqlite3.c" -o build/ref/corpus-turbo -lm
      echo "contrib/turbo: build/ref/corpus-sqlite, build/ref/corpus-turbo" ;;
    *) echo "build.sh: unknown variant $v" >&2; exit 2 ;;
  esac
done
