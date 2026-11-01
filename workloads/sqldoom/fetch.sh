#!/bin/sh
# workloads/sqldoom/fetch.sh: SQLDoom and the Freedoom IWAD into build/workloads/sqldoom/src, pinned. Nothing fetched
# here is committed.
#   cedardb-sqldoom  SQLDoom, CedarDB, GPL-2.0, at a pinned commit: the renderer, schema and WAD loader this folder runs
#   freedoom         Freedoom 0.13.0 (BSD-3-Clause), SHA-256-checked: freedoom1.wad supplies the map, textures, sprites
set -eu
cd "$(dirname "$0")/../.."; mkdir -p build/workloads/sqldoom && cd build/workloads/sqldoom; mkdir -p src/dl
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
[ -d src/cedardb-sqldoom/.git ] || git clone -q https://github.com/cedardb/sqldoom src/cedardb-sqldoom
git -C src/cedardb-sqldoom fetch -q origin 2>/dev/null || true
git -C src/cedardb-sqldoom checkout -q 95753a2
echo "fetch: src/cedardb-sqldoom at $(git -C src/cedardb-sqldoom rev-parse --short HEAD)"
Z=src/dl/freedoom-0.13.0.zip; ZSHA=3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59
[ -f $Z ] || curl -fsSL -o $Z https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip
[ "$(sha $Z)" = $ZSHA ] || { echo "fetch: $Z checksum mismatch" >&2; exit 1; }
mkdir -p src/freedoom && unzip -q -j -o $Z freedoom-0.13.0/freedoom1.wad freedoom-0.13.0/COPYING.txt -d src/freedoom
echo "fetch: src/freedoom/freedoom1.wad (Freedoom 0.13.0)"
