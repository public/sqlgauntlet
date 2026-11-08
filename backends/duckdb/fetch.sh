#!/bin/sh
# backends/duckdb/fetch.sh: DuckDB's prebuilt shared library and C headers (MIT) at a pinned release, into
# third_party/duckdb/ (gitignored), checked against a recorded SHA-256. Linux x86-64 and arm64, macOS.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); D=$R/third_party/duckdb
V=v2.0.0
case $(uname -s)-$(uname -m) in
  Linux-x86_64) P=linux-amd64; SHA=993ad89e7dfca66cd6da994c2cad090c2fc160ddf9f550498a098ab4bb13cfb1 ;;
  Linux-aarch64) P=linux-arm64; SHA= ;;
  Darwin-*) P=osx-universal; SHA= ;;
  *) echo "backends/duckdb/fetch.sh: no prebuilt DuckDB for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
[ -f "$D/duckdb.h" ] && [ "$(cat "$D/VERSION" 2>/dev/null)" = "$V $P" ] && exit 0
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
mkdir -p "$D" "$R/build"
Z=$R/build/duckdb-$V-$P.tgz
curl -fsSL "https://github.com/duckdb/duckdb/releases/download/$V/duckdb-shared-libs-$P.tar.gz" -o "$Z"
H=$(sha "$Z")
if [ -n "$SHA" ] && [ "$H" != "$SHA" ]; then echo "backends/duckdb/fetch.sh: checksum mismatch ($H)" >&2; exit 1; fi
[ -n "$SHA" ] || echo "backends/duckdb/fetch.sh: $P is unpinned, sha256 $H: record it here" >&2
tar -xzf "$Z" -C "$D"
echo "$V $P" > "$D/VERSION"
echo "backends/duckdb/fetch.sh: DuckDB $V ($P) in $D"
