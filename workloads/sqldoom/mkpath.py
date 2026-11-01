#!/usr/bin/env python3
"""mkpath.py <map.db> [frames]: the fixed camera path through E1M1, one pose per frame, into work/poses.txt.

A spin in place at the player start, then a walk through the waypoints below: the start room, the long hall, the
side room with the pit and back along the hall. Each pose is "tic x y z angle": the camera is at eye height (41)
over the floor of the sector it stands in (found by walking the map's BSP, as the loader places things), looks along its direction of
travel, and the level clock (player_state.level_tics, which drives the light effects and scrolling walls) advances
2 tics per frame. The script refuses a path that passes through a solid wall or a closed door. Floats are written
with repr, so both renderers get identical doubles."""
import math, sqlite3, sys

WAYPOINTS = [(-416, 256), (-200, 256), (400, 180), (880, 300), (1180, 250), (1180, 120), (700, 60), (100, 100)]
SPIN = 16          # frames of the opening spin (22.5 degrees each)
STEP = 24.0        # map units per frame along the path
EYE = 41

db = sqlite3.connect(sys.argv[1])
nodes = {i: (x, y, dx, dy) for i, x, y, dx, dy in db.execute('SELECT id, x, y, dx, dy FROM nodes')}
kids = {(n, s): (k, cn, cs) for n, s, k, cn, cs in db.execute(
    'SELECT node_id, side, child_kind, child_node_id, child_ssector_id FROM node_children')}
ssec = dict(db.execute('SELECT ssector_id, fsec FROM render_segs r WHERE seg_id = '
                       '(SELECT min(seg_id) FROM render_segs q WHERE q.ssector_id = r.ssector_id)'))
floor = dict(db.execute('SELECT id, floor_height FROM sectors'))
walls = db.execute('SELECT x1, y1, x2, y2 FROM render_segs WHERE bsec IS NULL OR b_ceil <= b_floor '
                   'OR f_ceil <= f_floor').fetchall()


def sector(x, y):
    n = max(nodes)
    while True:
        nx, ny, dx, dy = nodes[n]
        kind, cn, cs = kids[(n, 'R' if (x - nx) * dy - (y - ny) * dx > 0 else 'L')]
        if kind == 'SSECTOR': return ssec[cs]
        n = cn


def crosses(a, b):
    def orient(p, q, r): return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    for x1, y1, x2, y2 in walls:
        c, d = (x1, y1), (x2, y2)
        if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
            return (x1, y1, x2, y2)


poses = []
x0, y0 = WAYPOINTS[0]
for i in range(SPIN):
    poses.append((float(x0), float(y0), i * 22.5))
for (ax, ay), (bx, by) in zip(WAYPOINTS, WAYPOINTS[1:]):
    w = crosses((ax, ay), (bx, by))
    if w: sys.exit(f'mkpath: {ax},{ay} -> {bx},{by} passes through the wall {w}')
    dist = math.hypot(bx - ax, by - ay); ang = math.degrees(math.atan2(by - ay, bx - ax)) % 360
    n = max(1, round(dist / STEP))
    for k in range(n):
        poses.append((ax + (bx - ax) * k / n, ay + (by - ay) * k / n, ang))
want = int(sys.argv[2]) if len(sys.argv) > 2 else len(poses)
with open('work/poses.txt', 'w') as f:
    for i, (x, y, a) in enumerate(poses[:want]):
        f.write(f'{2 * i} {x!r} {y!r} {float(floor[sector(x, y)] + EYE)!r} {a!r}\n')
print(f'mkpath: {min(want, len(poses))} poses (path has {len(poses)}) -> work/poses.txt')
