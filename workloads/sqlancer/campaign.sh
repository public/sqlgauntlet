#!/bin/sh
# workloads/sqlancer/campaign.sh <seconds> <jobs> <backend>...: run.sh over backends x oracles, <jobs> processes at a
# time, <seconds> each, the same seed per oracle for every backend, so the backends see the same generated statements
# as long as their results agree. Then summarise.py. ORACLES= (default NoREC QUERY_PARTITIONING PQS), SEEDS= override.
R=$(cd "$(dirname "$0")/../.." && pwd)
[ $# -ge 3 ] || { echo "usage: campaign.sh <seconds> <jobs> <backend>..." >&2; exit 2; }
SECS=$1; JOBS=$2; shift 2
ORACLES=${ORACLES:-"NoREC QUERY_PARTITIONING PQS"}; SEEDS=${SEEDS:-1}
# shellcheck disable=SC2016  # (the sh -c script expands its own arguments)
for s in $SEEDS; do for o in $ORACLES; do for a in "$@"; do echo "$a $o $s"; done; done; done |
  xargs -P "$JOBS" -L 1 sh -c 'sh "$0/workloads/sqlancer/run.sh" "$1" "$2" '"$SECS"' "$3"' "$R"
python3 "$R/workloads/sqlancer/summarise.py" "$R/build/sqlancer/runs"
