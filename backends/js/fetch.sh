#!/bin/sh
# backends/js/fetch.sh [what...]: the JavaScript runtimes and SQLite bindings the js arms run on, pinned, into
# third_party/js/<what>/ (gitignored), checked against backends/js/sha256.tsv (an entry missing from it is recorded on
# first fetch: commit the new line). what: node (Node.js, for node:sqlite), bun (bun:sqlite), sqljs (sql.js), wasm
# (@sqlite.org/sqlite-wasm); default all. Nothing here is committed: each is fetched as published.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd); S=$R/backends/js/sha256.tsv; J=$R/third_party/js
NODE=v26.13.0; BUN=bun-v1.4.4; SQLJS=1.15.2; WASM=3.54.1-build1
sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
get() { # get <name> <url> <file>: the download, checked or recorded
  curl -fsSL "$2" -o "$3"; h=$(sha "$3"); k=$(awk -F'\t' -v n="$1" '$1 == n { print $2 }' "$S" 2>/dev/null)
  if [ -z "$k" ]; then printf '%s\t%s\t%s\n' "$1" "$h" "$2" >> "$S"; echo "fetch.sh: recorded $1 $h" >&2
  elif [ "$k" != "$h" ]; then echo "fetch.sh: checksum mismatch for $2" >&2; exit 1; fi
}
case $(uname -s)-$(uname -m) in
  Linux-x86_64) NP=linux-x64; BP=linux-x64 ;; Linux-aarch64) NP=linux-arm64; BP=linux-aarch64 ;;
  Darwin-arm64) NP=darwin-arm64; BP=darwin-aarch64 ;; Darwin-x86_64) NP=darwin-x64; BP=darwin-x64 ;;
  *) echo "fetch.sh: no prebuilt runtimes for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
for w in ${*:-node bun sqljs wasm}; do
  D=$J/$w; [ -f "$D/.done" ] && continue
  rm -rf "$D"; mkdir -p "$D"
  case $w in
    node) get node-$NODE-$NP "https://nodejs.org/dist/$NODE/node-$NODE-$NP.tar.xz" "$D/a.tar.xz"
          tar -xJf "$D/a.tar.xz" -C "$D" --strip-components 1 && rm "$D/a.tar.xz" ;;
    bun)  get $BUN-$BP "https://github.com/oven-sh/bun/releases/download/$BUN/bun-$BP.zip" "$D/a.zip"
          (cd "$D" && unzip -q -j a.zip && rm a.zip) ;;
    sqljs) get sql.js-$SQLJS "https://registry.npmjs.org/sql.js/-/sql.js-$SQLJS.tgz" "$D/a.tgz"
          tar -xzf "$D/a.tgz" -C "$D" --strip-components 1 && rm "$D/a.tgz" ;;
    wasm) get sqlite-wasm-$WASM "https://registry.npmjs.org/@sqlite.org/sqlite-wasm/-/sqlite-wasm-$WASM.tgz" "$D/a.tgz"
          tar -xzf "$D/a.tgz" -C "$D" --strip-components 1 && rm "$D/a.tgz" ;;
    *) echo "fetch.sh: what is $w? (node bun sqljs wasm)" >&2; exit 2 ;;
  esac
  touch "$D/.done"; echo "fetch.sh: $w in $D" >&2
done
