#!/bin/sh
# fetch.sh: Turso's Doom example (vdbecc, the 82 Doom .ll modules, the shareware DOOM1.WAD), pinned, into turso-doom/.
# Doom (doomgeneric) is GPL-2.0 and the WAD is id Software's shareware: both stay local (gitignored), never committed.
set -e
cd "$(dirname "$0")/../.."; mkdir -p build/workloads/doom-vdbe; cd build/workloads/doom-vdbe
REV=52be17f94914a90f2e6af8e2ab296f478534e679
WAD_SHA=1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771   # doom1.wad 1.9 shareware (md5 f0cefca4...)
if [ ! -d turso-doom/.git ]; then
  git init -q turso-doom
  git -C turso-doom remote add origin https://github.com/tursodatabase/turso-vdbe-doom-example.git
fi
[ "$(git -C turso-doom rev-parse -q --verify HEAD 2>/dev/null)" = "$REV" ] || {
  GIT_TERMINAL_PROMPT=0 git -C turso-doom -c credential.helper= fetch -q --depth 1 origin "$REV"
  git -C turso-doom checkout -q --detach FETCH_HEAD
}
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
[ "$(sha turso-doom/doom/DOOM1.WAD)" = "$WAD_SHA" ] || { echo "fetch.sh: DOOM1.WAD checksum mismatch" >&2; exit 1; }
echo "fetch.sh: turso-doom @ $REV, DOOM1.WAD ok"
