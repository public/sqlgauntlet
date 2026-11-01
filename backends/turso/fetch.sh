#!/bin/sh
# backends/turso/fetch.sh [build]: Turso (formerly Limbo, MIT) at a pinned tag into third_party/turso (gitignored). With
# "build": its C ABI crate (sdk-kit/, turso_sdk_kit, header sdk-kit/turso.h) as a library in third_party/turso/target/release/.
# Rust is only needed for this backend; nothing of Turso is committed here.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); D=$R/third_party/turso
TAG=v0.8.3; SHA=607bff9fd0e547a25a97b3bf06e83bcc5d99d53c
[ -d "$D/.git" ] || git clone -q --depth 1 --branch "$TAG" https://github.com/tursodatabase/turso "$D"
[ "$(git -C "$D" rev-parse HEAD)" = "$SHA" ] || { echo "backends/turso/fetch.sh: $D is not at $SHA" >&2; exit 1; }
[ "${1:-}" = build ] || exit 0
(cd "$D" && cargo build -q --release -p turso_sdk_kit)
ls "$D"/target/release/libturso_sdk_kit.* >&2
