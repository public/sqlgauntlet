#!/usr/bin/env python3
"""workloads/doomql/make.py [ticks...] [--out DIR]: DOOMQL as a corpus directory (default build/workloads/doomql): two
games whose every frame is one SQL query (NOTICE.md), each at game states <ticks> into a fixed input script (default 0
and 120), one project per game and state:
  doomql-t<K>        Lukas Vogel's game, through this repository's SQLite port (port/, mkport.py): the frame of each
                     of its two players, SELECT full_row FROM screen WHERE player_id = ? ORDER BY y (a nest of views
                     around a recursive ray march: ~95,000 VDBE opcodes)
  doomql-peter-t<K>  Peter Gostev's game, unchanged: the frame as ANSI scanlines, and its raster (x, y, r, g, b)
The state is built, not stepped: the project's data runs the first K lines of the input script and the game's tick
after each, so every engine renders the same frame. The statements are tagged doom and frame."""

import argparse
import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
sys.path.insert(0, str(H.parent))
from common import write_project  # noqa: E402

a = argparse.ArgumentParser()
a.add_argument('ticks', nargs='*', type=int, default=[0, 120])
a.add_argument('--out', default=str(R / 'build' / 'workloads' / 'doomql'))
o = a.parse_args()
B = R / 'build' / 'workloads' / 'doomql'
subprocess.run(['sh', str(H / 'fetch.sh')], check=True)
frames = max(o.ticks + [1])
subprocess.run([sys.executable, str(H / 'mkport.py'), str(frames)], check=True)
subprocess.run([sys.executable, str(H / 'mkpeter.py'), str(frames)], check=True)
W, P, U = B / 'work' / 'sqlite', B / 'work' / 'peter', B / 'src' / 'petergpt-doomql' / 'sql'
read = lambda p: Path(p).read_text()
lines = lambda p: read(p).split('\n')
meta = {'repo': 'https://github.com/cedardb/DOOMQL', 'sha': '8d6c2fc', 'license': 'MIT', 'workload': 'doomql', 'analyze': False}
vogel = read(W / 'schema.sql') + '\n' + read(W / 'renderer.sql') + '\n' + read(W / 'index.sql')
peter = ''.join(read(U / f) + '\n' for f in ('001_schema.sql', '002_level.sql', '003_render.sql', '004_trace.sql', '005_runtime_source.sql'))
for k in o.ticks:
    ins = lines(B / 'work' / 'inputs.txt')
    data = read(W / 'map.sql') + read(W / 'sprites.sql') + read(W / 'players.sql') + ''.join(ins[i] + '\n' + read(W / 'gameloop.sql') for i in range(k))
    q = [(f'doomql.t{k}.frame_p{n}', read(W / f'frame-p{n}.sql').strip(), ['doom', 'frame']) for n in (1, 2)]
    write_project(o.out, f'doomql-t{k}', dict(meta, name=f'DOOMQL (Lukas Vogel), tick {k}'), vogel, q, data=data)
    pins = lines(P / 'inputs.txt')
    data = read(U / 'reset.sql') + ''.join(pins[i] + '\n' + read(U / 'tick.sql') for i in range(k))
    q = [(f'doomql-peter.t{k}.{n}', read(P / f'{f}.sql').strip(), ['doom', 'frame']) for n, f in (('frame', 'frame'), ('pixels', 'pixels'))]
    pm = dict(meta, name=f'DOOMQL (Peter Gostev), tick {k}', repo='https://github.com/petergpt/doomql', sha='a2b40b6')
    write_project(o.out, f'doomql-peter-t{k}', pm, peter, q, data=data)
print(f'doomql: ticks {o.ticks}, {4 * len(o.ticks)} frame statements in {o.out}', file=sys.stderr)
