#!/usr/bin/env python3
"""workloads/speedtest1/run.py [--rounds N] [--out DIR] <arm>... [-- speedtest1 options]: SQLite's own benchmark
(test/speedtest1.c from SQLite's pinned source tree, fetch-src.sh), unchanged, for SQLite-API backends: each arm is a
backend built by backends/sqlite (make sqlite, make sqlite-api), and speedtest1 is linked with that backend's own
sqlite3.c (its <name>.src) and compile line (<name>.so.cflags), and runs with its environment (<name>.json). Arms run
in alternation, the order rotated by round, one process each; every run uses --verify, and the verification hashes
(of every row speedtest1 reads) must be equal across arms. Default options: --size 100 (testset main) on a file
database. Output: <out>/<arm>.<round>.txt, then summarise.py's table (the first arm the reference)."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
BK = R / 'build' / 'backends'
V = '3540100'


def speedtest1_c():
    subprocess.run(['sh', str(H / 'fetch-src.sh')], check=True)
    c = R / 'build' / 'workloads' / 'speedtest1' / 'speedtest1.c'
    if not c.exists():
        c.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(R / 'build' / f'sqlite-src-{V}.zip') as z:
            c.write_bytes(z.read(f'sqlite-src-{V}/test/speedtest1.c'))
    return c


def build(arm, src_c):
    src, cf = (BK / f'{arm}.src').read_text().strip(), shlex.split((BK / f'{arm}.so.cflags').read_text())
    exe = R / 'build' / 'workloads' / 'speedtest1' / arm / 'speedtest1'
    if not exe.exists() or exe.stat().st_mtime < (BK / f'{arm}.so').stat().st_mtime:
        exe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cf + ['-w', f'-I{src}', str(src_c), f'{src}/sqlite3.c', '-o', str(exe), '-lm', '-lpthread'], check=True)
    return exe


def main():
    argv = sys.argv[1:]
    opts = argv[argv.index('--') + 1 :] if '--' in argv else ['--size', '100']
    argv = argv[: argv.index('--')] if '--' in argv else argv
    a = argparse.ArgumentParser(prog='speedtest1', description=__doc__.split('\n')[0])
    a.add_argument('arms', nargs='+')
    a.add_argument('--rounds', type=int, default=3)
    a.add_argument('--out', default=str(R / 'build' / 'run-speedtest1'))
    o = a.parse_args(argv)
    for arm in o.arms:
        if not (BK / f'{arm}.src').exists():
            sys.exit(f'speedtest1: {arm} is not a SQLite-API backend (no build/backends/{arm}.src: make {arm})')
    out = Path(o.out)
    out.mkdir(parents=True, exist_ok=True)
    src_c = speedtest1_c()
    exes = {arm: build(arm, src_c) for arm in o.arms}
    envs = {arm: json.loads((BK / f'{arm}.json').read_text()).get('env', {}) for arm in o.arms}
    db = out / 'st.db'
    for r in range(1, o.rounds + 1):
        for k in range(len(o.arms)):
            arm = o.arms[(k + r) % len(o.arms)]
            for f in out.glob('st.db*'):
                f.unlink()
            env = {x: y for x, y in os.environ.items() if not any(x in e for e in envs.values())}
            env.update(envs[arm])
            with open(out / f'{arm}.{r}.txt', 'w') as f:
                subprocess.run([str(exes[arm]), '--verify', *opts, str(db)], stdout=f, stderr=subprocess.STDOUT, env=env, check=False)
        print(f'speedtest1: round {r} done', file=sys.stderr)
    subprocess.run([sys.executable, str(H / 'summarise.py'), str(out), *o.arms], check=False)


if __name__ == '__main__':
    main()
