#!/bin/sh
# workloads/doom-vdbe/build-export.sh [FRAMES] [TICKS] [OUT]: build vdbecc + our exporter (vdbe2sqlite.rs) in Docker
# (rust:1.88; DOCKER_CPUS=<list> pins its CPUs), then export Doom's program, RAM image and Turso's own per-frame reference hashes (the first FRAMES rows,
# default 300) into OUT (default out/). TICKS is vdbe_run's argument (game loop iterations, default effectively
# endless); a finite TICKS makes the program end, which qvm needs (it hands out rows when the program finishes).
set -e
H=$(cd "$(dirname "$0")" && pwd)
sh "$H/fetch.sh"
cd "$H/../../build/workloads/doom-vdbe"
FRAMES=${1:-300} TICKS=${2:-100000000} OUT=${3:-out}
case $OUT in /*) ;; *) OUT=$PWD/$OUT ;; esac
cp "$H/vdbe2sqlite.rs" turso-doom/vdbecc/src/bin/vdbe2sqlite.rs
mkdir -p "$OUT"
docker run --rm ${DOCKER_CPUS:+--cpuset-cpus "$DOCKER_CPUS"} -v "$PWD/turso-doom":/src -v "$OUT":/out -v doomjit-cargo:/usr/local/cargo/registry \
  -w /src rust:1.88 sh -c "
    cargo build --release -p vdbecc --bin vdbe2sqlite &&
    ./target/release/vdbe2sqlite /out --ref $FRAMES --arg $TICKS --patch vdbe_wad=doom/DOOM1.WAD doom/ll/*.ll"
