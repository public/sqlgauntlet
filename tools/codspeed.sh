#!/bin/sh
# tools/codspeed.sh prepare|run: corpus statements as CodSpeed CPU-simulation benchmarks (deterministic instructions and
# simulated caches), one benchmark per arm, statement and mode: "<project>::<arm>::<id>[hot|prep]".
#   tools/codspeed.sh prepare   natively, outside the runner: builds the CLI, build/codspeed/sqlgauntlet (make codspeed),
#                               the projects' databases and .q files (sqlgauntlet-run check), and each arm's environment
#   codspeed run -m simulation -- tools/codspeed.sh run      (or CodSpeedHQ/action, run: tools/codspeed.sh run)
#                               every arm over every project, whole projects, in both modes; only the statements in
#                               CODSPEED_IDS are measured, the rest run uncounted, so plan and page caches see the
#                               whole project
# Environment (the same for both steps):
#   CODSPEED_ARMS      arms as sqlgauntlet-run takes them, "name" or "label=backend:K=V:K=V" (default: sqlite)
#   CODSPEED_PROJECTS  corpus projects (default: the ones CODSPEED_IDS come from)
#   CODSPEED_IDS       statements to measure, space-separated (default: the list below)
#   CODSPEED_REPS      timed executions per statement and mode (default 5; executions are fixed, the budget unlimited);
#                      an arm can carry its own CODSPEED_REPS, CODSPEED_PROJECTS and CODSPEED_IDS (comma lists), e.g.
#                      "turbo-park=turbo:QC_ADAPT_MARGIN=1e-9:CODSPEED_REPS=30:CODSPEED_PROJECTS=trilium,grafana"
#   CODSPEED_OUT       work directory (default build/codspeed-run)
# Adaptive engines time themselves, and valgrind's clock is ~50x slow: pin their decisions in the arm so the counts
# measure one known path. turbo: QC_NOADAPT=1 serves every compiled plan; QC_ADAPT_MARGIN=1e-9 parks every plan adapt
# samples (the path of the statements turbo sends back to SQLite's VM), with enough executions to reach the verdict.
set -eu
R=$(cd "$(dirname "$0")/.." && pwd)
OUT=${CODSPEED_OUT:-$R/build/codspeed-run}
ARMS=${CODSPEED_ARMS:-sqlite}
REPS=${CODSPEED_REPS:-5}
IDS=${CODSPEED_IDS:-"technitium.querylogs.page.last_page technitium.querylogs.export.all_page
  trilium.sync.pending_push_exists trilium.becca.get_attachment ha.statistics.find_latest_short_term_id
  syncthing.local.list_devices_for_folder grafana.star.is_starred dagster.runs.count_in_progress atuin.store.status
  firefox.autofill.origin_history_bookmark"}
PROJECTS=${CODSPEED_PROJECTS:-}
if [ -z "$PROJECTS" ]; then # (the projects whose queries.sql defines one of the ids)
  for i in $IDS; do
    PROJECTS="$PROJECTS $(grep -l -- "^-- @id $i\$" "$R"/corpus/projects/*/queries.sql | sed 's,.*/projects/,,; s,/queries.sql,,')"
  done
  PROJECTS=$(echo "$PROJECTS" | tr ' ' '\n' | sort -u | tr '\n' ' ')
fi
case "${1:-}" in
prepare)
  make -s -C "$R" all codspeed
  set --
  for a in $ARMS; do set -- "$@" --arm "$a"; done
  # shellcheck disable=SC2086 # (PROJECTS: a word list)
  (cd "$R/python" && python3 -m sqlgauntlet.run check $PROJECTS --out "$OUT" --no-report "$@") # (projects: right after the mode)
  mkdir -p "$OUT/codspeed"
  for a in $ARMS; do # one line per arm: label, backend .so, then K=V words (the backend's json env, then the arm's)
    python3 - "$a" "$R/build/backends" >>"$OUT/codspeed/arms.tmp" <<'PY'
import json, sys
spec, d = sys.argv[1], sys.argv[2]
label, _, rest = spec.partition('=') if '=' in spec.split(':')[0] else (spec.split(':')[0], '', spec)
parts = (rest or spec).split(':')
env = json.load(open(f'{d}/{parts[0]}.json')).get('env', {})
env.update(kv.split('=', 1) for kv in parts[1:] if kv)
print(label, f'{d}/{parts[0]}.so', *(f'{k}={v}' for k, v in env.items()))
PY
  done
  mv "$OUT/codspeed/arms.tmp" "$OUT/codspeed/arms"
  echo "$PROJECTS" >"$OUT/codspeed/projects"
  ;;
run)
  [ -f "$OUT/codspeed/arms" ] || {
    echo "tools/codspeed.sh: run tools/codspeed.sh prepare first" >&2
    exit 2
  }
  ONLY=$(echo "$IDS" | tr -s ' \t\n' ',' | sed 's/^,//; s/,$//')
  PROJS=$(cat "$OUT/codspeed/projects")
  while read -r label so env; do
    reps=$REPS projs=$PROJS only=$ONLY # (an arm's own CODSPEED_REPS / CODSPEED_PROJECTS / CODSPEED_IDS win, commas)
    for kv in $env; do
      case $kv in
        CODSPEED_REPS=*) reps=${kv#CODSPEED_REPS=} ;;
        CODSPEED_PROJECTS=*) projs=$(echo "${kv#CODSPEED_PROJECTS=}" | tr ',' ' ') ;;
        CODSPEED_IDS=*) only=${kv#CODSPEED_IDS=} ;;
      esac
    done
    for p in $projs; do
      [ -f "$OUT/db/$p.db" ] || continue
      for m in hot prep; do
        cp "$OUT/db/$p.db" "$OUT/codspeed/c.db" # (a fresh copy per arm and mode: writes roll back)
        # shellcheck disable=SC2086 # (env: K=V words)
        env $env TZ=UTC SG_CODSPEED_ARM="$label" SG_CODSPEED_ONLY="$only" SG_MIN_REPS="$reps" \
          "$R/build/codspeed/sqlgauntlet" --backend "$so" "$m" "$OUT/codspeed/c.db" "$OUT/q/$p.q" "$reps" 1e12 >/dev/null
      done
    done
  done <"$OUT/codspeed/arms"
  ;;
*)
  sed -n '2,/^set -eu/p' "$0" | sed '$d'
  exit 2
  ;;
esac
