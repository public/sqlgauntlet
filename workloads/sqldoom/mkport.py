#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Rewrites SQLDoom's renderer (cedardb/sqldoom, GPL-2.0, (c) CedarDB): see COPYING.md.
"""mkport.py: SQLDoom's renderer (src/cedardb-sqldoom/sql/renderer.sql) in two dialects, plus the per-frame input.

  work/pg/renderer.sql      the Postgres reference: upstream's query with the one change Postgres needs (settings())
  work/pg/frames.sql        psql script: per pose, set the clock and EXECUTE the prepared renderer, dumping the frame (bytea, as hex)
  work/sqlite/setup.sql     render_params (the 7 parameters, a table) and series (0..1023, for generate_series)
  work/sqlite/renderer.sql  the SQLite port (port(): PORT.md lists every rewrite)
  work/inputs.txt           doomrun's per-frame input: one line of SQL per pose (clock and render_params)
Both dialects get work/state.sql (the render-only stage: the pistol raised)."""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.environ.get('SQLDOOM_BUILD', os.path.join(HERE, '..', '..', 'build', 'workloads', 'sqldoom'))
UP = os.path.join(B, 'src', 'cedardb-sqldoom', 'sql', 'renderer.sql')
W = os.path.join(B, 'work')
MAP_ID, PLAYER, SKILL = 1, 157, 2


def once(text, old, new, n=1):
    c = text.count(old)
    if c != n: sys.exit(f'mkport: expected {n} of {old[:70]!r}, found {c} (upstream changed?)')
    return text.replace(old, new)


# Postgres (and SQLite) have no lateral column aliases: render_settings's derived settings refer to screen_w, screen_h
# and fov_rad in the same SELECT, which CedarDB allows. The three base settings move to a derived table.
def settings(text):
    text = once(text, '''    SELECT
      320::int  AS screen_w,
      168::int  AS screen_h,  -- 200 minus the real 32px status bar
      radians(90.0)::float8 AS fov_rad,
''', '''    SELECT screen_w, screen_h, fov_rad,
''')
    return once(text, '''      64::int AS flat_size
), cam AS (''', '''      64::int AS flat_size
    FROM (SELECT 320::int AS screen_w,
                 168::int AS screen_h,  -- 200 minus the real 32px status bar
                 radians(90.0)::float8 AS fov_rad) b0
), cam AS (''')


# ---- the SQLite port --------------------------------------------------------------------------------------------
# Structural rewrites (exact upstream text, each must apply exactly once): the constructs SQLite has no form for.
STRUCT = [
    # the seven parameters come from a one-row table (doomrun prepares the frame query once and runs it per pose)
    ("""           CASE WHEN $3::int<=1 THEN 1 WHEN $3::int=2 THEN 2 ELSE 4 END
             AS skill_bit
), pos AS (""", """           CASE WHEN $3::int<=1 THEN 1 WHEN $3::int=2 THEN 2 ELSE 4 END
             AS skill_bit
    FROM render_params rp
), pos AS ("""),
    ("""    SELECT $4::float8 AS x, $5::float8 AS y, $6::float8 AS z, $7::float8 AS angle
), frame_clock AS (""", """    SELECT $4::float8 AS x, $5::float8 AS y, $6::float8 AS z, $7::float8 AS angle
    FROM render_params rp
), frame_clock AS ("""),
    # LEFT JOIN LATERAL (... ORDER BY seq DESC LIMIT 1): the nearest earlier panel's seq as a correlated scalar
    # subquery, then that panel's columns by (col_x, seq), which is unique (row_number)
    ("""  FROM panel_seq p
  CROSS JOIN render_settings rs
  CROSS JOIN cam c
  LEFT JOIN LATERAL (
    SELECT q.fsec, q.f_ceil, q.f_light, q.f_ceil_is_sky AS is_sky
    FROM panel_seq q
    WHERE q.col_x = p.col_x AND q.seq < p.seq
      AND q.f_ceil > c.pz
    ORDER BY q.seq DESC
    LIMIT 1
  ) pc ON TRUE
  LEFT JOIN LATERAL (
    SELECT q.fsec, q.f_floor, q.f_light
    FROM panel_seq q
    WHERE q.col_x = p.col_x AND q.seq < p.seq
      AND q.f_floor < c.pz
    ORDER BY q.seq DESC
    LIMIT 1
  ) pf ON TRUE
""", """  FROM (SELECT p0.*,
               (SELECT q.seq
                FROM panel_seq q
                WHERE q.col_x = p0.col_x AND q.seq < p0.seq
                  AND q.f_ceil > c0.pz
                ORDER BY q.seq DESC
                LIMIT 1) AS pc_seq,
               (SELECT q.seq
                FROM panel_seq q
                WHERE q.col_x = p0.col_x AND q.seq < p0.seq
                  AND q.f_floor < c0.pz
                ORDER BY q.seq DESC
                LIMIT 1) AS pf_seq
        FROM panel_seq p0 CROSS JOIN cam c0) p
  CROSS JOIN render_settings rs
  CROSS JOIN cam c
  LEFT JOIN (SELECT q.col_x, q.seq, q.fsec, q.f_ceil, q.f_light, q.f_ceil_is_sky AS is_sky FROM panel_seq q) pc
    ON pc.col_x = p.col_x AND pc.seq = p.pc_seq
  LEFT JOIN (SELECT q.col_x, q.seq, q.fsec, q.f_floor, q.f_light FROM panel_seq q) pf
    ON pf.col_x = p.col_x AND pf.seq = p.pf_seq
"""),
    # CROSS JOIN LATERAL (... LIMIT 1) for the sprite rotation: the same, the key is (sprite, frame, rotation)
    ("""  FROM thing_view tv
  CROSS JOIN render_settings rs
  CROSS JOIN LATERAL (
    SELECT f.*
    FROM sprite_frames f
    WHERE f.sprite = tv.sprite AND f.frame = tv.frame
      AND f.rotation IN (0, tv.wanted_rotation)
    ORDER BY CASE WHEN f.rotation = tv.wanted_rotation THEN 0 ELSE 1 END
    LIMIT 1
  ) sf
""", """  FROM (SELECT tv0.*,
               (SELECT f.rotation
                FROM sprite_frames f
                WHERE f.sprite = tv0.sprite AND f.frame = tv0.frame
                  AND f.rotation IN (0, tv0.wanted_rotation)
                ORDER BY CASE WHEN f.rotation = tv0.wanted_rotation THEN 0 ELSE 1 END
                LIMIT 1) AS sf_rotation
        FROM thing_view tv0) tv
  CROSS JOIN render_settings rs
  JOIN sprite_frames sf ON sf.sprite = tv.sprite AND sf.frame = tv.frame AND sf.rotation = tv.sf_rotation
"""),
    # the union's column names come from its first arm, where two are bare casts
    ("t.id::bigint AS thing_id, t.x::real, t.y::real, t.angle,", "t.id::bigint AS thing_id, t.x::real AS x, t.y::real AS y, t.angle AS angle,"),
    # ARG_MAX (CedarDB): SQLite's bare column next to max() comes from the row holding the max. x and y are cast to
    # INTEGER: an expression column has no affinity, and SQLite will not index it for ui_colored's join with the
    # INTEGER columns of ui_static_pixels (10,240 pixels each scanned all ~1,900 HUD pixels)
    ("""  SELECT x,y,ARG_MAX(palette_index,layer) AS palette_index
  FROM ui_pixels GROUP BY x,y""", """  SELECT x, y, palette_index
  FROM (SELECT CAST(x AS INTEGER) AS x, CAST(y AS INTEGER) AS y, palette_index, max(layer)
        FROM ui_pixels GROUP BY 1, 2)"""),
    ("  FROM generate_series(2,7) slot(weapon_id)", "  FROM (SELECT v AS weapon_id FROM series WHERE v BETWEEN 2 AND 7) slot"),
    # an always-empty arm that steers CedarDB's optimizer
    ("""  UNION ALL -- hack to trick the optimizer
  SELECT g AS x, 0 AS y, g AS pix, '\\x000000'::bytea AS rgb
  FROM generate_series(0, 255999) AS g WHERE g + 0 < 0
""", ""),
    ("SELECT string_agg(rgb, ''::bytea ORDER BY pix) AS frame_rgb", "SELECT CAST(string_agg(rgb, x'' ORDER BY pix) AS BLOB) AS frame_rgb"),
    ("LPAD(ASCII(g.ch)::text, 3, '0')", "substr('00' || unicode(g.ch), -3)", 2),
    # 'seg_id + 0' hides seg_id from CedarDB's optimizer ("psst, optimizer hack"). For SQLite it hides the primary key:
    # each on_screen row (heights) and each wall column span (seg_light_bias) scanned all of render_segs
    ("JOIN render_segs s ON s.map_id = p.map_id AND s.seg_id + 0 = p.seg_id", "JOIN render_segs s ON s.map_id = p.map_id AND s.seg_id = p.seg_id"),
    ("SELECT s.seg_id + 0 AS seg_id, s.light_bias", "SELECT s.seg_id AS seg_id, s.light_bias"),
    ("SELECT s.seg_id + 0 AS seg_id, bo.bsp_seq", "SELECT s.seg_id AS seg_id, bo.bsp_seq"),
    # FULL JOIN: SQLite builds no automatic index on the right side of a RIGHT or FULL JOIN, so each resolved pixel
    # scanned every psprite pixel (~100M rows a frame). The same rows: a LEFT JOIN, plus the unmatched psprite pixels
    # by NOT IN ((x, y) is unique on both sides and never NULL). On the LEFT JOIN side COALESCE(p.x, r.x) is r.x; using
    # r.x keeps its INTEGER affinity, without which view_holes's NOT EXISTS cannot index final_pixels (below)
    ("""  SELECT COALESCE(p.x,r.x) AS x, COALESCE(p.y,r.y) AS y,
         COALESCE(p.light_index,r.light_index) AS light_index,
         COALESCE(p.palette_index,r.palette_index) AS palette_index
  FROM resolved r FULL JOIN psprite_resolved p USING (x,y)
""", """  SELECT r.x AS x, r.y AS y,
         COALESCE(p.light_index,r.light_index) AS light_index,
         COALESCE(p.palette_index,r.palette_index) AS palette_index
  FROM resolved r LEFT JOIN psprite_resolved p USING (x,y)
  UNION ALL
  SELECT p.x, p.y, p.light_index, p.palette_index
  FROM psprite_resolved p
  WHERE (p.x, p.y) NOT IN (SELECT x, y FROM resolved)
"""),
]
PARAMS = {'$1::int': 'rp.map_id', '$2::int': 'rp.player_thing_id', '$3::int': 'rp.skill', '$4::float8': 'rp.x',
          '$5::float8': 'rp.y', '$6::float8': 'rp.z', '$7::float8': 'rp.angle'}
# float -> integer casts: Postgres rounds half to even, so these become IRINT (the others cast integers, or FLOOR/CEIL)
IRINT_OPS = {'(p.x - n.x)', '(p.y - n.y)', '(pos.y - s.y1)', '(pos.x - s.x1)', 'sp.depth',
             'DEGREES(ATAN2(mp.vy,mp.vx))', '(GREATEST(0.0::float8, LEAST(u.depth, 131071.0))*4096)'}
# the three light functions (sql/runtime/functions/43_render_light.sql), inlined as macros, as Postgres inlines them
LIGHT = {
    'doom_light_index': lambda a: (f"max(0, min(31, (15 - min(15, max(0, CAST(floor(({a[0]}) / 16.0) AS INTEGER) "
                                   f"+ ({a[1]})))) * 4 - ({a[2]})))"),
    'doom_light_scale': lambda a: f"CAST(floor(min(47.0, max(0.0, 2560.0 / NULLIF(({a[0]}), 0.0))) / 2.0) AS INTEGER)",
    'doom_light_zdepth': lambda a: f"CAST(floor(80.0 / (min(127, CAST(floor(({a[0]}) / 16.0) AS INTEGER)) + 1)) AS INTEGER)",
}
BYTES = ','.join(f'{i:02X}' for i in range(256))
MACROS = {
    'IRINT': lambda a: f"CAST({rint(a[0])} AS INTEGER)",
    # Postgres's degrees() divides by RADIANS_PER_DEGREE; SQLite's multiplies by 180/pi, which can differ in the last bit
    'DEGREES': lambda a: f"(({a[0]}) / 0.017453292519943295)",
    # get_byte(bytea, n): byte n's hex digits located in '00,01,...,FF'
    'GET_BYTE': lambda a: f"((instr('{BYTES}', hex(substr({a[0]}, ({a[1]}) + 1, 1))) - 1) / 3)",
    **LIGHT,
}
NOT_FN = {'AND', 'OR', 'NOT', 'THEN', 'ELSE', 'WHEN', 'SELECT', 'IN', 'BETWEEN', 'AS', 'ON', 'BY', 'CASE', 'END', 'WHERE'}


def rint(e):
    return (f"(CASE WHEN ({e}) - floor({e}) = 0.5 THEN floor({e}) + abs(CAST(floor({e}) AS INTEGER)) % 2 "
            f"ELSE round({e}) END)")


def strip_comments(t):
    out, i, q = [], 0, False
    while i < len(t):
        ch = t[i]
        if q:
            out.append(ch); q = ch != "'"
        elif ch == "'":
            out.append(ch); q = True
        elif t.startswith('--', i):
            j = t.find('\n', i); i = len(t) if j < 0 else j; continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r'[ \t]+\n', '\n', ''.join(out))


def close(t, i):
    """index just past the parenthesis matching the '(' at t[i]"""
    depth, q = 0, False
    while True:
        ch = t[i]
        if q: q = ch != "'"
        elif ch == "'": q = True
        elif ch == '(': depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0: return i + 1
        i += 1


def open_(t, j):
    """index of the '(' matching the ')' at t[j]"""
    depth = 0
    while True:
        ch = t[j]
        if ch == ')': depth += 1
        elif ch == '(':
            depth -= 1
            if depth == 0: return j
        elif ch == "'":
            j = t.rindex("'", 0, j)
        j -= 1


def split_top(s, sep=','):
    out, depth, cur, q, i = [], 0, '', False, 0
    while i < len(s):
        ch = s[i]
        if q:
            q = ch != "'"
        elif ch == "'": q = True
        elif ch == '(': depth += 1
        elif ch == ')': depth -= 1
        elif depth == 0 and s.startswith(sep, i):
            out.append(cur.strip()); cur = ''; i += len(sep); continue
        cur += ch; i += 1
    out.append(cur.strip())
    return out


def series(t):
    """CROSS JOIN LATERAL generate_series(a, b) AS n[(c)] -> JOIN (SELECT v AS c FROM series) AS n ON n.c BETWEEN a AND b
    (series is 0..1023; every range here is clamped to the screen or is a short string's length)"""
    n = 0
    while True:
        m = re.search(r'CROSS JOIN LATERAL\s+generate_series\(', t)
        if not m: return t, n
        e = close(t, m.end() - 1)
        a, b = split_top(t[m.end():e - 1])
        am = re.match(r'\s*(?:AS\s+)?(\w+)(?:\((\w+)\))?', t[e:])
        name, col = am.group(1), am.group(2) or am.group(1)
        t = t[:m.start()] + f'JOIN (SELECT v AS {col} FROM series) AS {name} ON {name}.{col} BETWEEN {a} AND {b}' + t[e + am.end():]
        n += 1


def arm_bounds(t, i):
    starts = [t.rfind('UNION ALL', 0, i)] + [m.end() for m in re.finditer(r'\n\)[^\n]*\n', t[:i])]
    ends = [k for k in (t.find('UNION ALL', i), t.find('\n)', i)) if k >= 0]
    return max(starts), min(ends)


def scalar_laterals(t):
    """CROSS JOIN LATERAL (SELECT e AS c, ...) a, with no FROM: dropped, and a.c replaced by (e) in its SELECT"""
    n = 0
    while True:
        m = re.search(r'CROSS JOIN LATERAL \(\s*SELECT\b', t)
        if not m: return t, n
        o = t.index('(', m.start()); e = close(t, o)
        body = t[o + 1:e - 1].strip()[len('SELECT'):]
        if re.search(r'\bFROM\b', body): sys.exit('mkport: a LATERAL with FROM left')
        am = re.match(r'\s*(\w+)', t[e:]); alias = am.group(1)
        cols = {}
        for item in split_top(body):
            em = re.match(r'(.*)\bAS\s+(\w+)$', item, re.S)
            cols[em.group(2)] = ' '.join(em.group(1).split())
        s0, s1 = arm_bounds(t, m.start())
        t = t[:m.start()] + t[e + am.end():]
        s1 -= (e + am.end()) - m.start()
        arm = t[s0:s1]
        for c, ex in cols.items():
            # a bare a.c in the select list keeps its name
            arm = re.sub(rf'(^|,)(\s*){alias}\.{c}(?=\s*(,|\n\s*FROM\b))', lambda m: f'{m.group(1)}{m.group(2)}({ex}) AS {c}',
                         arm, flags=re.M)
            arm, k = re.subn(rf'\b{alias}\.{c}\b', lambda _: f'({ex})', arm)
            if not k and f') AS {c}' not in arm: sys.exit(f'mkport: {alias}.{c} unused')
        if re.search(rf'\b{alias}\.\w', arm): sys.exit(f'mkport: {alias}.* left')
        t = t[:s0] + arm + t[s1:]
        n += 1


def substrings(t):
    """substring(s FROM a FOR n) -> substr(s, a, n)"""
    while True:
        m = re.search(r'(?i)\bsubstring\(', t)
        if not m: return t
        e = close(t, m.end() - 1)
        inner = t[m.end():e - 1]
        s, rest = split_top(inner, ' FROM ') if ' FROM ' in inner else split_top(inner, '\n FROM ')
        a, n_ = split_top(rest, ' FOR ')
        t = t[:m.start()] + f'substr({s}, {a}, {n_})' + t[e:]


def operand(t, i):
    """start of the operand of the postfix cast at t[i] ('::')"""
    j = i - 1
    if t[j] == ')':
        k = open_(t, j); fm = re.search(r'(\w+)\s*$', t[:k])
        return fm.start() if fm and fm.group(1).upper() not in NOT_FN else k
    if t[j] == "'": return t.rindex("'", 0, j)
    m = re.search(r'[\w.]+$', t[:i])
    if m.group(0) == 'END':
        depth, k = 0, i
        for w in reversed(list(re.finditer(r'\b(CASE|END)\b', t[:i]))):
            depth += 1 if w.group(1) == 'END' else -1
            if depth == 0: return w.start()
    return m.start()


CAST_RE = re.compile(r'::(double precision|bigint|smallint|int|float8|real|text|bytea|char\(1\)|varchar\(4\))')


def casts(t, audit):
    while True:
        ms = list(CAST_RE.finditer(t))
        if not ms: return t
        m = ms[-1]; s = operand(t, m.start()); op = t[s:m.start()]; ty = m.group(1); norm = ' '.join(op.split())
        if ty in ('int', 'bigint', 'smallint'):
            if re.fullmatch(r'-?\d+', op): new = op
            elif norm in IRINT_OPS: new = f'IRINT({op})'; audit['irint'] += 1
            else:
                new = f'CAST({op} AS INTEGER)'
                if not re.match(r'(?i)(floor|ceil)\b', op): audit['int'].append(norm)
        elif ty in ('float8', 'real', 'double precision'):
            new = op if re.fullmatch(r'-?[\d.]+(e-?\d+)?', op) or op.startswith('radians(') else f'CAST({op} AS REAL)'
        elif ty == 'text':
            new = op if op == 'NULL' or op.startswith("'") else f'CAST({op} AS TEXT)'
        elif ty == 'bytea':
            new = "x'" + op[3:-1] + "'" if op.startswith("'\\x") else "x''"
        else:
            new = op
        t = t[:s] + new + t[m.end():]


def expand(t, names):
    rx = re.compile(r'\b(' + '|'.join(names) + r')\(')
    while True:
        m = rx.search(t)
        if not m: return t
        e = close(t, m.end() - 1)
        args = split_top(t[m.end():e - 1])
        t = t[:m.start()] + MACROS[m.group(1)](args) + t[e:]


def materialize(t):
    """Postgres evaluates a CTE referenced more than once once (it folds only single-use ones into the query); SQLite
    may flatten a multi-use CTE into every reference (it did: heights, under all seven wall_parts arms). Such CTEs get
    SQLite's MATERIALIZED, which is Postgres's default."""
    names = re.findall(r'(?m)(?:^|\), |WITH RECURSIVE )(\w+) AS \($', t)
    n = 0
    for name in names:
        if len(re.findall(rf'\b(?:FROM|JOIN)\s+{name}\b', t)) > 1:
            t = re.sub(rf'(?m)((?:^|\), |WITH RECURSIVE ){name}) AS \($', r'\1 AS MATERIALIZED (', t); n += 1
    return t, n


def port(up):
    t = settings(up)
    for r in STRUCT:
        t = once(t, r[0], r[1], r[2] if len(r) > 2 else 1)
    for k, v in PARAMS.items():
        t = t.replace(k, v)
    t = strip_comments(t)
    t, nm = materialize(t)
    t, ns = series(t)
    t, nl = scalar_laterals(t)
    t = substrings(t)
    audit = {'irint': 0, 'int': []}
    t = casts(t, audit)
    if audit['irint'] != len(IRINT_OPS): sys.exit(f"mkport: {audit['irint']} IRINTs, want {len(IRINT_OPS)}")
    t = re.sub(r'\bGREATEST\s*\(', 'max(', t)
    t = re.sub(r'\bLEAST\s*\(', 'min(', t)
    t = once(t, 'BOOL_AND(', 'min(')
    t = t.replace('IS DISTINCT FROM', 'IS NOT').replace('OCTET_LENGTH(', 'length(')
    t = expand(t, list(MACROS))
    for bad in ('LATERAL', 'generate_series', '::', '$', 'GREATEST', 'LEAST', 'ARG_MAX', 'DISTINCT FROM', 'OCTET',
                'GET_BYTE', 'BOOL_AND', 'substring', 'LPAD', 'ASCII', 'doom_light', 'DEGREES'):
        if bad in t: sys.exit(f'mkport: {bad!r} left in the SQLite port')
    return t, nm, ns, nl, audit


def main():
    up = open(UP, encoding='utf-8').read()
    for d in ('pg', 'sqlite'): os.makedirs(os.path.join(W, d), exist_ok=True)
    pgr = settings(up)
    open(os.path.join(W, 'pg', 'renderer.sql'), 'w').write(pgr)
    poses = [l.split() for l in open(os.path.join(W, 'poses.txt'))]
    state = "UPDATE player_weapons SET state = 'ready', sy = 32;\n"
    open(os.path.join(W, 'state.sql'), 'w').write(state)
    with open(os.path.join(W, 'pg', 'frames.sql'), 'w') as f:
        f.write('\\set ON_ERROR_STOP 1\n\\pset format unaligned\n\\pset tuples_only on\n\\i /sd/work/state.sql\n')
        f.write('PREPARE render(int, int, int, float8, float8, float8, float8) AS\n' + pgr.rstrip() + '\n')
        for i, (tic, x, y, z, a) in enumerate(poses):
            f.write(f'UPDATE player_state SET level_tics = {tic};\n\\o /out/pg/f{i:05d}.hex\n'
                    f'EXECUTE render({MAP_ID}, {PLAYER}, {SKILL}, {x}, {y}, {z}, {a});\n\\o\n')
    lt, nm, ns, nl, audit = port(up)
    open(os.path.join(W, 'sqlite', 'renderer.sql'), 'w').write(lt)
    open(os.path.join(W, 'sqlite', 'setup.sql'), 'w').write(
        'CREATE TABLE IF NOT EXISTS series(v INTEGER PRIMARY KEY);\n'
        'INSERT OR IGNORE INTO series WITH RECURSIVE s(v) AS (SELECT 0 UNION ALL SELECT v + 1 FROM s WHERE v < 1023) SELECT v FROM s;\n'
        'DROP TABLE IF EXISTS render_params;\n'
        'CREATE TABLE render_params(map_id INTEGER, player_thing_id INTEGER, skill INTEGER, x REAL, y REAL, z REAL, angle REAL);\n'
        f'INSERT INTO render_params VALUES ({MAP_ID}, {PLAYER}, {SKILL}, 0, 0, 0, 0);\n')
    with open(os.path.join(W, 'inputs.txt'), 'w') as f:
        for tic, x, y, z, a in poses:
            f.write(f'UPDATE player_state SET level_tics = {tic}; UPDATE render_params SET x = {x}, y = {y}, z = {z}, angle = {a};\n')
    if '-v' in sys.argv:
        print('integer casts kept as CAST (not FLOOR/CEIL):', *sorted(set(audit['int'])), sep='\n  ')
    print(f'mkport: work/pg, work/sqlite ({len(lt.splitlines())} lines; {nm} CTEs MATERIALIZED, {ns} LATERAL generate_series, '
          f'{nl} scalar LATERALs), '
          f'work/inputs.txt ({len(poses)} poses)')


if __name__ == '__main__':
    main()
