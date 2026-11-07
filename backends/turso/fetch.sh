#!/bin/sh
# backends/turso/fetch.sh [build] [TAG]: Turso (formerly Limbo, MIT) at a pinned tag into third_party/turso (the default
# tag) or third_party/turso-TAG (another one from backends/turso/tags.tsv), gitignored. With "build": its C ABI crate
# (sdk-kit/, turso_sdk_kit, header sdk-kit/turso.h) as a library in <dir>/target/release/. `make turso` builds the
# default tag as backend turso, `make turso-tag TAG=v0.9.0-pre.2` another as backend turso-v0.9.0-pre.2.
# Rust is only needed for this backend; nothing of Turso is committed here.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd)
DEF=$(awk -F'\t' '!/^#/ && $3 == "default" { print $1 }' "$R/backends/turso/tags.tsv")
TAG=${2:-$DEF}
SHA=$(awk -F'\t' -v t="$TAG" '!/^#/ && $1 == t { print $2 }' "$R/backends/turso/tags.tsv")
[ -n "$SHA" ] || { echo "backends/turso/fetch.sh: $TAG is not in backends/turso/tags.tsv" >&2; exit 1; }
if [ "$TAG" = "$DEF" ]; then D=$R/third_party/turso; else D=$R/third_party/turso-$TAG; fi
[ -d "$D/.git" ] || git clone -q --depth 1 --branch "$TAG" https://github.com/tursodatabase/turso "$D"
[ "$(git -C "$D" rev-parse HEAD)" = "$SHA" ] || { echo "backends/turso/fetch.sh: $D is not at $SHA" >&2; exit 1; }
[ "${1:-}" = build ] || exit 0
(cd "$D" && cargo build -q --release -p turso_sdk_kit)
ls "$D"/target/release/libturso_sdk_kit.* >&2
