#!/bin/sh
# workloads/sqlancer/build.sh <backend>...: SQLancer (pinned commit, built with Maven; needs a JDK and mvn) and
# sqlite-jdbc's JNI library rebuilt once per SQLite-API backend (backends/sqlite: make sqlite, make sqlite-api), from
# the sqlite3.c that backend was built from (build/backends/<name>.src), so SQLancer's sqlite3 provider runs on the
# engine under test:
#   build/sqlancer/sqlancer.jar, build/sqlancer/lib/*.jar   SQLancer and its dependencies (sqlite-jdbc 3.49.1.0's Java)
#   build/sqlancer/<backend>/libsqlitejdbc.so                 NativeDB.c (sqlite-jdbc 3.49.1.0) + the backend's sqlite3.c,
#                                                            with sqlite-jdbc's own compile options and edits
# sqlite-jdbc loads it from -Dorg.sqlite.lib.path/-Dorg.sqlite.lib.name instead of the one in its jar (run.sh).
# Tarballs are SHA-256-checked; Maven runs with strict checksums and its repository in build/m2.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); O=$R/build/sqlancer; DL=$R/build/dl; mkdir -p "$O" "$DL"
SQLANCER=1d797ee53631df2fe574468e2be0425985f39867; SQLANCER_SHA=320472ab18c98c037ec245a5dc6cc1d76670cc1c0a9b73e64096dde84ac9b3a0
JDBC=3.49.1.0; JDBC_SHA=97b53f39ed68a30ca5a005af8dc515f21762c4aab80bb349b3a206301d56b612
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
get() {   # get <url> <file> <sha256>
  [ -f "$2" ] && [ "$(sha "$2")" = "$3" ] && return 0
  curl -fsSL "$1" -o "$2.part"; [ "$(sha "$2.part")" = "$3" ] || { echo "build: checksum mismatch for $1" >&2; rm -f "$2.part"; exit 1; }
  mv "$2.part" "$2"
}
get "https://codeload.github.com/sqlancer/sqlancer/tar.gz/$SQLANCER" "$DL/sqlancer-$SQLANCER.tgz" $SQLANCER_SHA
get "https://codeload.github.com/xerial/sqlite-jdbc/tar.gz/refs/tags/$JDBC" "$DL/sqlite-jdbc-$JDBC.tgz" $JDBC_SHA

# SQLancer
if [ ! -f "$O/sqlancer.jar" ] || [ "$(cat "$O/SQLANCER" 2>/dev/null)" != $SQLANCER ]; then
  rm -rf "${O:?}/src" "${O:?}/lib" && mkdir -p "$O/src" && tar xzf "$DL/sqlancer-$SQLANCER.tgz" -C "$O/src" --strip-components 1
  (cd "$O/src" && mvn -B -q -C -Dmaven.repo.local="$R/build/m2" package -DskipTests -Dcheckstyle.skip -Dspotbugs.skip -Dpmd.skip -Djacoco.skip -Dmaven.javadoc.skip)
  cp "$O"/src/target/sqlancer-*.jar "$O/sqlancer.jar"; cp -r "$O/src/target/lib" "$O/lib"
  echo $SQLANCER > "$O/SQLANCER"
fi

# sqlite-jdbc's JNI glue, and its header from its Java class
J=$O/jdbc; rm -rf "$J" && mkdir -p "$J" && tar xzf "$DL/sqlite-jdbc-$JDBC.tgz" -C "$J" --strip-components 1
JAR=$(ls "$O"/lib/sqlite-jdbc-*.jar); [ "$(basename "$JAR")" = "sqlite-jdbc-$JDBC.jar" ] || { echo "build: SQLancer uses $(basename "$JAR"), not $JDBC" >&2; exit 1; }
javac -h "$J/h" -cp "$JAR" -d "$J/classes" "$J/src/main/java/org/sqlite/core/NativeDB.java"
cp "$J/h/org_sqlite_core_NativeDB.h" "$J/h/NativeDB.h"
JH=${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}
OPTS="-DSQLITE_ENABLE_LOAD_EXTENSION=1 -DSQLITE_HAVE_ISNAN -DHAVE_USLEEP=1 -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_CORE
 -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_STAT4
 -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_THREADSAFE=1 -DSQLITE_DEFAULT_MEMSTATUS=0
 -DSQLITE_DEFAULT_FILE_PERMISSIONS=0666 -DSQLITE_MAX_VARIABLE_NUMBER=250000 -DSQLITE_MAX_MMAP_SIZE=1099511627776
 -DSQLITE_MAX_LENGTH=2147483647 -DSQLITE_MAX_COLUMN=32767 -DSQLITE_MAX_SQL_LENGTH=1073741824 -DSQLITE_MAX_FUNCTION_ARG=127
 -DSQLITE_MAX_ATTACHED=125 -DSQLITE_MAX_PAGE_COUNT=4294967294 -DSQLITE_DISABLE_PAGECACHE_OVERFLOW_STATS"
[ $# -gt 0 ] || { echo "usage: workloads/sqlancer/build.sh <backend>..." >&2; exit 2; }
for b in "$@"; do
  S=$(cat "$R/build/backends/$b.src" 2>/dev/null) || { echo "build: $b is not a SQLite-API backend (make $b)" >&2; exit 2; }
  mkdir -p "$O/$b"
  # sqlite-jdbc's edits to the amalgamation (its Makefile): extension functions registered on open, JDBC_EXTENSIONS
  # among the compile options, src/main/ext/*.c appended
  perl -p -e 's/^opendb_out:/  if(!db->mallocFailed && rc==SQLITE_OK){ rc = RegisterExtensionFunctions(db); }\nopendb_out:/;' \
    "$S/sqlite3.c" > "$O/$b/sqlite3.c.tmp"
  perl -p -e 's/^(static const char \* const sqlite3azCompileOpt.+)$/$1\n\n\/* This has been automatically added by sqlite-jdbc *\/\n  "JDBC_EXTENSIONS",/;' "$O/$b/sqlite3.c.tmp" > "$O/$b/sqlite3.c"
  cat "$J"/src/main/ext/*.c >> "$O/$b/sqlite3.c"; rm "$O/$b/sqlite3.c.tmp"
  # shellcheck disable=SC2086  # (option lists)
  ${CC:-cc} ${SQLANCER_CFLAGS:--O2 -g} -fPIC -shared $OPTS -w -I"$S" -I"$J/h" -I"$JH/include" -I"$JH/include/linux" -I"$JH/include/darwin" \
    "$J/src/main/java/org/sqlite/core/NativeDB.c" "$O/$b/sqlite3.c" -o "$O/$b/libsqlitejdbc.so" -lm -lpthread
  echo "build: $O/$b/libsqlitejdbc.so"
done
