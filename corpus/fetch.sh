#!/bin/sh
# corpus/fetch.sh [turbo-repo]: the corpus's "fetch" applications (licences.tsv: GPL, AGPL, EUPL, Elastic-2.0 and
# n8n's Sustainable Use License; not committed here) from cursed-sqlite-turbo at the pinned commit (PIN), into
# corpus/projects/<app>/ with their LICENSE and NOTICE.md, and their manifest lines into manifest.fetched.jsonl.
# turbo-repo: a local checkout or a git URL (default $TURBO_REPO, else https://github.com/public/cursed-sqlite-turbo).
set -e
C=$(cd "$(dirname "$0")" && pwd); PIN=$(cat "$C/PIN")
SRC=${1:-${TURBO_REPO:-https://github.com/public/cursed-sqlite-turbo}}
if [ -d "$SRC/.git" ] || [ -f "$SRC/.git" ]; then G=$SRC
else G=$C/fetched/turbo.git; [ -d "$G" ] || git clone -q --bare --filter=blob:none "$SRC" "$G"; git -C "$G" fetch -q origin "$PIN" 2>/dev/null || true; fi
git -C "$G" cat-file -e "$PIN^{commit}" || { echo "fetch.sh: $SRC has no commit $PIN" >&2; exit 1; }
APPS=$(awk -F'\t' '!/^#/ && $3 == "fetch" { print $1 }' "$C/licences.tsv")
P=""; for a in $APPS; do P="$P bench/corpus/projects/$a"; done
T=$C/fetched/x; rm -rf "$T"; mkdir -p "$T"
git -C "$G" archive "$PIN" $P | tar -x -C "$T"
for a in $APPS; do rm -rf "$C/projects/$a"; mv "$T/bench/corpus/projects/$a" "$C/projects/$a"; rm -f "$C/projects/$a/expected.json" "$C/projects/$a/timings.json"; done
git -C "$G" show "$PIN:bench/corpus/manifest.jsonl" | python3 -c "
import json, sys; keep = set(sys.argv[1].split())
for l in sys.stdin:
    if l.strip() and json.loads(l)['project'] in keep: sys.stdout.write(l)" "$APPS" > "$C/manifest.fetched.jsonl"
rm -rf "$T"
echo "fetch.sh: $(echo $APPS | wc -w | tr -d ' ') apps, $(wc -l < "$C/manifest.fetched.jsonl" | tr -d ' ') statements at ${PIN%????????????????????????????????}"
