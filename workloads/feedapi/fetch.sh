#!/bin/sh
# workloads/feedapi/fetch.sh: the twelve-dollar server challenge (arjaythedev/twelve-dollar-server-challenge, MIT) at a
# pinned commit, into build/workloads/feedapi/src: its fixed schema (schema.sql) and its deterministic seed generator
# (seed/generate.mjs: 50,000 users, 500,000 posts, ~2,000,000 likes, 20,000 JWTs). Nothing of it is committed here.
set -eu
B=$(cd "$(dirname "$0")/../.." && pwd)/build/workloads/feedapi; mkdir -p "$B"
C=c1cdb38ae3d515ac026bfd54cd5d88c16aa76d41
[ -d "$B/src/.git" ] || git clone -q https://github.com/arjaythedev/twelve-dollar-server-challenge "$B/src"
git -C "$B/src" cat-file -e "$C^{commit}" 2>/dev/null || git -C "$B/src" fetch -q origin
git -C "$B/src" checkout -q "$C"
