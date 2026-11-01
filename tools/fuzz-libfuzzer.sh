#!/bin/sh
# tools/fuzz-libfuzzer.sh <backend> [seconds] [jobs]: fuzz/sqlfuzz.c, libFuzzer over a SQLite-API backend's engine
# (its sqlite3.c and compile options: build/backends/<backend>.src, .so.cflags) under ASan and UBSan, in Docker
# (ubuntu:24.04, clang: Apple's clang has no libFuzzer), for <seconds> (default 60) with <jobs> processes (default 1).
# Seeds: every read statement of the corpus, plus fuzz/dict.txt's tokens as libFuzzer's dictionary. Kept between runs:
# build/fuzz/libfuzzer/<backend>/corpus (what libFuzzer found interesting) and artifacts/ (crash-*, leak-*,
# timeout-*: each is a finding: replay it with the binary, minimise it with -minimize_crash=1, then turn it into a
# tests/regressions case once confirmed). The source tree and the backend's directories are mounted read-only.
set -eu
B=${1:?usage: tools/fuzz-libfuzzer.sh <backend> [seconds] [jobs]}; SECS=${2:-60}; JOBS=${3:-1}
R=$(cd "$(dirname "$0")/.." && pwd); O=$R/build/fuzz/libfuzzer/$B
SRC=$(cat "$R/build/backends/$B.src" 2>/dev/null) || { echo "fuzz-libfuzzer: $B is not a SQLite-API backend (make $B)" >&2; exit 2; }
CF=$(sed -e 's/-mcpu=native//; s/-march=native//; s/^[^ ]* //' -e "s/-DSG_VERSION_NOTE='[^']*'//" "$R/build/backends/$B.so.cflags")   # (the
# compiler is the container's; the version note, a quoted string, isn't needed)
mkdir -p "$O/corpus" "$O/artifacts" "$R/build/fuzz/seeds"
python3 - "$R" <<'PY'
import json, sys
from pathlib import Path
R = Path(sys.argv[1]); out = R / 'build' / 'fuzz' / 'seeds'; n = 0
for m in sorted((R / 'corpus').glob('manifest*.jsonl')):
    for line in open(m):
        r = json.loads(line)
        if 'read' in r.get('tags', []) and len(r['sql']) < 4000:
            (out / f"{r['id']}.sql").write_text(r['sql']); n += 1
print(f'fuzz-libfuzzer: {n} seeds', file=sys.stderr)
PY
MOUNTS=""; for d in $(echo "$CF" | tr ' ' '\n' | sed -n 's/^-I\(\/.*\)/\1/p'); do MOUNTS="$MOUNTS -v $d:$d:ro"; done   # (the -I directories)
# shellcheck disable=SC2086  # (CF: compile options; MOUNTS: -v options)
docker run --rm $MOUNTS -v "$R:$R:ro" -v "$SRC:$SRC:ro" -v "$O:$O" -v "$R/build/fuzz/seeds:/seeds:ro" ubuntu:24.04 sh -c "
  set -e; export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq > /dev/null && apt-get install -y -qq clang > /dev/null
  cd $R && clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined $CF -w -Iinclude -Ibackends/sqlite -I$SRC \
    fuzz/sqlfuzz.c backends/sqlite/adapter.c $SRC/sqlite3.c -o /tmp/sqlfuzz -lm
  cd $O && /tmp/sqlfuzz -max_total_time=$SECS -timeout=10 -rss_limit_mb=2048 -jobs=$JOBS -workers=$JOBS \
    -dict=$R/fuzz/dict.txt -artifact_prefix=$O/artifacts/ $O/corpus /seeds 2>&1 | tail -25
"
echo "fuzz-libfuzzer: $B: $(find "$O/artifacts" -type f | wc -l | tr -d ' ') artifacts in $O/artifacts"
