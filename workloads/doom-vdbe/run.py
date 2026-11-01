#!/usr/bin/env python3
"""workloads/doom-vdbe/run.py [--export DIR] [--frames N] [--rounds R] <arm>...: Doom as one VDBE program (Turso's Doom
example, compiled from the game's LLVM IR to Turso's bytecode, exported by vdbe2sqlite.rs and rebuilt instruction by
instruction as a real SQLite Vdbe by doomsql.c: DESIGN.md), run through sqlite3_step on SQLite-API backends built on
SQLite 3.54 (the Vdbe is built from its internal opcode numbers; other versions are skipped): each arm's
own sqlite3.c (build/backends/<arm>.src) and compile line, its environment applied. The program renders the attract
loop; every frame's hash must equal Turso's own (ref.txt, from the export) and the other arms'.
  --export DIR  an existing export (prog.txt, ram.bin, meta.txt, ref.txt); default build/workloads/doom-vdbe/out, made
                by build-export.sh (fetch.sh: Turso's example repository and the shareware DOOM1.WAD, GPL-2.0 and id
                Software's licence, never committed; then Turso's vdbecc in Docker, rust:1.88)
Prints, per arm and round, the frames matching Turso's and the seconds, then whether the arms agree."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
BK = R / 'build' / 'backends'
a = argparse.ArgumentParser()
a.add_argument('arms', nargs='+')
a.add_argument('--export')
a.add_argument('--frames', type=int, default=60)
a.add_argument('--rounds', type=int, default=1)
o = a.parse_args()
out = Path(o.export) if o.export else R / 'build' / 'workloads' / 'doom-vdbe' / 'out'
if not (out / 'prog.txt').exists():
    subprocess.run(['sh', str(H / 'build-export.sh'), str(max(300, o.frames)), '100000000', str(out)], check=True)
ref = dict(line.split() for line in (out / 'ref.txt').read_text().splitlines() if line.strip())
exes, envs = {}, {}
for arm in o.arms:
    if not (BK / f'{arm}.src').exists():
        sys.exit(f'doom-vdbe: {arm} is not a SQLite-API backend (no build/backends/{arm}.src)')
    src, cf = (BK / f'{arm}.src').read_text().strip(), shlex.split((BK / f'{arm}.so.cflags').read_text())
    ver = next((ln.split('"')[1] for ln in open(Path(src) / 'sqlite3.h', errors='replace') if ln.startswith('#define SQLITE_VERSION ')), '?')
    if not ver.startswith('3.54.'):  # (doomsql.c builds the Vdbe from SQLite's internal opcode numbers, which change between versions)
        print(f'doom-vdbe: {arm} skipped: SQLite {ver} internals, the program is built for 3.54', file=sys.stderr)
        continue
    exe = R / 'build' / 'workloads' / 'doom-vdbe' / arm / 'doomsql'
    if not exe.exists() or exe.stat().st_mtime < (BK / f'{arm}.so').stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cf + ['-w', f'-I{src}', str(H / 'doomsql.c'), '-o', str(exe), '-lm', '-lpthread'], check=True)
    exes[arm] = exe
    envs[arm] = json.loads((BK / f'{arm}.json').read_text()).get('env', {})
frames = {}
arms = [x for x in o.arms if x in exes]
for r in range(1, o.rounds + 1):
    for k in range(len(arms)):
        arm = arms[(k + r) % len(arms)]
        env = {x: y for x, y in os.environ.items() if not any(x in e for e in envs.values())}
        env.update(envs[arm])
        t0 = time.time()
        p = subprocess.run([str(exes[arm]), str(out), 'stock', '--frames', str(o.frames)], capture_output=True, text=True, env=env)
        dt = time.time() - t0
        got = {f[1]: f[2] for f in (line.split() for line in p.stdout.splitlines()) if len(f) >= 3 and f[0] == 'frame'}
        frames.setdefault(arm, got)
        same = sum(1 for n, h in got.items() if ref.get(n) == h)
        print(f'doom-vdbe: {arm} round {r}: {len(got)} frames, {same} equal to Turso\'s, {dt:.1f} s' + (f' (exit {p.returncode})' if p.returncode else ''))
base = frames[arms[0]]
for arm in arms[1:]:
    d = [n for n in base if frames[arm].get(n) != base[n]]
    print(f'doom-vdbe: {arm} vs {arms[0]}: ' + ('every frame equal' if not d else f'{len(d)} frames differ, first {d[0]}'))
