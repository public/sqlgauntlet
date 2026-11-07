#!/bin/sh
# Fetch CodSpeed's instrument-hooks C library (dist/core.c + includes/) at a pinned commit, sha256-checked,
# into ${1:-build/instrument-hooks}. Used by `make codspeed` (src/driver.c with -DSG_CODSPEED).
set -eu
REV=8fd5b878499f819561b33219cdafaa6f8c28b4e4
DEST=${1:-build/instrument-hooks}
URL=https://raw.githubusercontent.com/CodSpeedHQ/instrument-hooks/$REV
if [ -f "$DEST/.rev" ] && [ "$(cat "$DEST/.rev")" = "$REV" ]; then exit 0; fi
mkdir -p "$DEST/dist" "$DEST/includes"
while read -r sum f; do
  curl -fsSL --retry 3 "$URL/$f" -o "$DEST/$f"
  if command -v sha256sum >/dev/null; then got=$(sha256sum "$DEST/$f"); else got=$(shasum -a 256 "$DEST/$f"); fi
  if [ "${got%% *}" != "$sum" ]; then
    echo "sha256 mismatch for $f" >&2
    rm -f "$DEST/$f"
    exit 1
  fi
done <<'SUMS'
36dbf21b27c663e4b7eea5537f2816c83a0961fce1e84d6b54254108c7195f83 dist/core.c
b31181cd93c415016a81f49a9267751b0dd86ea9863ff856d16bfcc4d83b93f2 includes/core.h
3155854ed2d995f04a70065b6b26ee86de49af27c5a54d68dc32a3eb1d123f6e includes/callgrind.h
8a9d93a3adad7eb9049a0a7bb8f948cda5e800be46ec838e5e9c01219a4806c1 includes/valgrind.h
90e43d92698b7a50f901c09f67e3eacdd757dc45ea40583525d7107a3aaf45c3 includes/compat.h
bc9dcfc7eb9b29b345d79b5726d598a91b1e52b94d109e956edd12cb16d499cd includes/zig.h
SUMS
echo "$REV" >"$DEST/.rev"
