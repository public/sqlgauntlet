#!/usr/bin/env python3
"""workloads/sqldoom/make.py [--poses N] [--db E1M1.db] [--out DIR]: SQLDoom's renderer as a corpus directory (default
build/workloads/sqldoom): one 1,600-line query that renders real Doom (BSP order, textured walls, visplanes, sprites,
the weapon and the HUD) from Freedoom's E1M1, returning the 320x200 RGB frame as one blob. Licensing: COPYING.md
(SQLDoom is GPL-2.0, and so are this directory's port and loader; nothing of SQLDoom or the WAD is committed).
  1. fetch.sh: SQLDoom at its pinned commit and Freedoom 0.13.0 (checksummed)
  2. the map database: pg/load.sh runs SQLDoom's WAD loader on Postgres 16.3 in Docker and exports the renderer's
     tables to SQLite (--db: an existing export instead)
  3. mkpath.py: the camera path; mkport.py: the renderer in SQLite's dialect
  4. the project sqldoom-e1m1: the map as its data (every table dumped as SQL), and N poses spread over the path
     (default 8), each the renderer with the camera (x, y, z, angle) and the clock as parameters ?1..?5, so every
     pose is one statement and every engine renders the same frames. Tagged doom and frame."""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

H = Path(__file__).resolve().parent
R = H.parents[1]
sys.path.insert(0, str(H.parent))
from common import write_project  # noqa: E402

a = argparse.ArgumentParser()
a.add_argument('--poses', type=int, default=8)
a.add_argument('--db')
a.add_argument('--out', default=str(R / 'build' / 'workloads' / 'sqldoom'))
o = a.parse_args()
B = R / 'build' / 'workloads' / 'sqldoom'
W = B / 'work'
subprocess.run(['sh', str(H / 'fetch.sh')], check=True)
W.mkdir(parents=True, exist_ok=True)
db = W / 'E1M1.db'
if o.db:
    shutil.copyfile(o.db, db)
elif not db.exists():
    subprocess.run(['sh', str(H / 'pg' / 'load.sh')], check=True)
env = dict(os.environ, SQLDOOM_BUILD=str(B))
subprocess.run([sys.executable, str(H / 'mkpath.py'), str(db)], cwd=B, check=True)
subprocess.run([sys.executable, str(H / 'mkport.py')], cwd=B, env=env, check=True)


def once(s, old, new):
    if s.count(old) != 1:
        sys.exit(f'sqldoom: expected one {old!r} in the renderer, found {s.count(old)}')
    return s.replace(old, new)


r = (W / 'sqlite' / 'renderer.sql').read_text()
cam = 'FROM (SELECT 1 AS map_id, 157 AS player_thing_id, 2 AS skill, ?1 AS x, ?2 AS y, ?3 AS z, ?4 AS angle) rp'
r = r.replace('FROM render_params rp', cam)  # (render_context and pos: the camera as parameters)
r = once(r, 'MAX(ps.level_tics)', 'MAX(?5)')  # (the clock: light effects, scrolling walls)
poses = [line.split() for line in (W / 'poses.txt').read_text().splitlines() if line.strip()]
pick = [poses[i * (len(poses) - 1) // max(1, o.poses - 1)] for i in range(min(o.poses, len(poses)))]
con = sqlite3.connect(db)
data = '\n'.join(con.iterdump()) + '\n' + (W / 'sqlite' / 'setup.sql').read_text() + (W / 'state.sql').read_text()
con.close()
queries = [(f'sqldoom.e1m1.pose{k:02d}', r.strip().rstrip(';') + ';', ['doom', 'frame']) for k in range(len(pick))]
meta = {
    'name': 'SQLDoom renderer, Freedoom E1M1',
    'repo': 'https://github.com/cedardb/sqldoom',
    'sha': '95753a2',
    'license': 'GPL-2.0-only',
    'workload': 'sqldoom',
    'analyze': False,
}
d = write_project(o.out, 'sqldoom-e1m1', meta, '', queries, data=data)
# the poses as each statement's parameters (write_project writes no @param lines)
q = (d / 'queries.sql').read_text().split('-- @id ')
out = [q[0]]
for blk, (tic, x, y, z, ang) in zip(q[1:], pick):
    head, _, rest = blk.partition('\n')
    params = ''.join(f'-- @param ?{n} {v}\n' for n, v in enumerate((x, y, z, ang, tic), 1))
    out.append(head + '\n' + params + rest)
(d / 'queries.sql').write_text('-- @id '.join(out))
print(f'sqldoom: {len(pick)} poses of {len(poses)} in {o.out}', file=sys.stderr)
