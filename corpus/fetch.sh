#!/bin/sh
# corpus/fetch.sh [repo]: the corpus's "fetch" applications (licences.tsv: GPL, AGPL, EUPL, Elastic-2.0 and n8n's
# Sustainable Use License; not committed here) from the upstream corpus repository at the pinned commit (UPSTREAM:
# url, commit, subdir), into corpus/projects/<app>/ with their LICENSE and NOTICE.md, and their manifest lines into
# manifest.fetched.jsonl. repo: a local clone, or another URL for the same history (default: UPSTREAM's url, or
# $SQLGAUNTLET_CORPUS_REPO).
set -e
C=$(cd "$(dirname "$0")" && pwd)
URL=$(sed -n 1p "$C/UPSTREAM"); PIN=$(sed -n 2p "$C/UPSTREAM"); SUB=$(sed -n 3p "$C/UPSTREAM")
SRC=${1:-${SQLGAUNTLET_CORPUS_REPO:-$URL}}
if [ -d "$SRC/.git" ] || [ -f "$SRC/.git" ]; then G=$SRC
else G=$C/fetched/upstream.git; [ -d "$G" ] || git clone -q --bare --filter=blob:none "$SRC" "$G"; git -C "$G" fetch -q origin "$PIN" 2>/dev/null || true; fi
git -C "$G" cat-file -e "$PIN^{commit}" || { echo "fetch.sh: $SRC has no commit $PIN" >&2; exit 1; }
APPS=$(awk -F'\t' '!/^#/ && $3 == "fetch" { print $1 }' "$C/licences.tsv")
P=""; for a in $APPS; do P="$P $SUB/projects/$a"; done
T=$C/fetched/x; rm -rf "$T"; mkdir -p "$T"
# shellcheck disable=SC2086  # (one path per app: split on purpose)
git -C "$G" archive "$PIN" $P | tar -x -C "$T"
for a in $APPS; do rm -rf "$C/projects/$a"; mv "$T/$SUB/projects/$a" "$C/projects/$a"; rm -f "$C/projects/$a/expected.json" "$C/projects/$a/timings.json"; done
git -C "$G" show "$PIN:$SUB/manifest.jsonl" | python3 -c "
import json, sys; keep = set(sys.argv[1].split())
for l in sys.stdin:
    if l.strip() and json.loads(l)['project'] in keep: sys.stdout.write(l)" "$APPS" > "$C/manifest.fetched.jsonl"
rm -rf "$T"
echo "fetch.sh: $(echo "$APPS" | wc -w | tr -d ' ') apps, $(wc -l < "$C/manifest.fetched.jsonl" | tr -d ' ') statements at $(echo "$PIN" | cut -c1-10)"
