#!/usr/bin/env python3
"""mkport.py: the runnable SQL for one DOOMQL game, in two dialects, into work/.

  work/sqlite/  the SQLite port: port/*.sql with the macros expanded, plus the sprite data converted from upstream
  work/pg/      the Postgres reference: upstream's files with the determinism patches (each patch must apply exactly
                once, so a changed upstream is caught here), plus port/players.sql
  work/inputs.txt  the fixed input script (gen_inputs below), one line of SQL per frame, valid in both dialects

Macros (PORT.md says why each is needed):
  RINT(e)      Postgres's round(float8) and float-to-int rounding: half to even
  IRINT(e)     CAST(RINT(e) AS INTEGER), i.e. Postgres's CAST(float AS INT) / ::int
  RPAD(s, n)   rpad(s, n, ' '): pads with spaces and truncates to n characters
  REPEAT(s, n) repeat(s, n)
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.environ.get('DOOMQL_BUILD', os.path.join(HERE, '..', '..', 'build', 'workloads', 'doomql'))
UP = os.path.join(B, 'src', 'cedardb-doomql')
W = os.path.join(B, 'work')


def split_args(s):
    """top-level comma split of a macro's argument text (quotes and parentheses respected)"""
    out, depth, cur, q = [], 0, '', False
    for ch in s:
        if q:
            cur += ch
            if ch == "'": q = False
            continue
        if ch == "'": q = True
        elif ch == '(': depth += 1
        elif ch == ')': depth -= 1
        elif ch == ',' and depth == 0:
            out.append(cur.strip()); cur = ''; continue
        cur += ch
    out.append(cur.strip())
    return out


def rint(e):
    return (f"(CASE WHEN ({e}) - floor({e}) = 0.5 THEN floor({e}) + abs(CAST(floor({e}) AS INTEGER)) % 2 "
            f"ELSE round({e}) END)")


MACROS = {
    'RINT': lambda a: rint(a[0]),
    'IRINT': lambda a: f"CAST({rint(a[0])} AS INTEGER)",
    'RPAD': lambda a: f"substr(({a[0]}) || replace(hex(zeroblob({a[1]})), '00', ' '), 1, {a[1]})",
    'REPEAT': lambda a: f"replace(hex(zeroblob({a[1]})), '00', {a[0]})",
}
MACRO_RE = re.compile(r'\b(RINT|IRINT|RPAD|REPEAT)\(')


def expand(sql):
    while True:
        m = MACRO_RE.search(sql)
        if not m: return sql
        i, depth, q = m.end(), 1, False
        while depth:
            ch = sql[i]
            if q:
                if ch == "'": q = False
            elif ch == "'": q = True
            elif ch == '(': depth += 1
            elif ch == ')': depth -= 1
            i += 1
        args = [expand(a) for a in split_args(sql[m.end():i - 1])]
        sql = sql[:m.start()] + MACROS[m.group(1)](args) + sql[i:]


def sprites_sqlite(text):
    """data/player.sql, data/slug.sql: unnest(ARRAY[...]) WITH ORDINALITY -> json_each, generate_series -> a recursive
    CTE, SUBSTRING(s FROM a FOR b) -> substr(s, a, b) (loading only: not part of any timed query)"""
    n0 = len(text)
    text = re.sub(r'FROM unnest\(ARRAY\[', 'FROM (SELECT value AS line, key + 1 AS sy FROM json_each(json_array(', text)
    text = re.sub(r'\]\) WITH ORDINALITY AS t\(line, sy\)', '))) AS t', text)
    text = text.replace('t.sy::int - 1', 't.sy - 1')
    text = re.sub(r'generate_series\(0, (\d+ \* \d+)\) x',
                  r'(WITH RECURSIVE g(x) AS (SELECT 0 UNION ALL SELECT x + 1 FROM g WHERE x < \1) SELECT x FROM g) x', text)
    text = re.sub(r'SUBSTRING\(a\.line FROM (.+?) FOR 1\)', r'substr(a.line, \1, 1)', text)
    for bad in ('unnest', 'ORDINALITY', 'generate_series', 'SUBSTRING', '::'):
        if bad in text: sys.exit(f'mkport: sprite conversion left {bad!r} (upstream changed?)')
    assert len(text) > n0 / 2
    return text


# the Postgres reference: upstream with the same determinism changes the port makes (file, old, new)
PG_PATCHES = [
    ('gamestate.sql', '-- config params concerning the gameplay\n',
     'DROP TABLE IF EXISTS clock;\nCREATE TABLE clock(tick INT NOT NULL);\nINSERT INTO clock VALUES (0);\n\n'
     '-- config params concerning the gameplay\n'),
    ('gamestate.sql', 'last_ammo_refill int default EXTRACT(EPOCH FROM (now()))::int',
     'last_ammo_refill int default 0'),
    ('renderer.sql', 'ORDER BY player_id, px, py, depth ASC\n', 'ORDER BY player_id, px, py, depth ASC, mob_id, ch\n'),
    # two mobs on one minimap tile give that cell two rows, in no particular order: same tie-break as the port
    ('renderer.sql', "string_agg(ch, '' ORDER BY x) as row\n  FROM combined", "string_agg(ch, '' ORDER BY x, ch) as row\n  FROM combined"),
    # Postgres's char(1) drops trailing blanks when cast to text, so the minimap's ' ' tiles became '' and its rows
    # shifted left (not what upstream draws on CedarDB, PORT.md); text keeps them
    ('gamestate.sql', 'CREATE TABLE map(x INT, y INT, tile CHAR);', 'CREATE TABLE map(x INT, y INT, tile TEXT);'),
    ('gameloop.sql', '-- Process all player inputs\n', 'UPDATE clock SET tick = tick + 1;\n\n-- Process all player inputs\n'),
    ('gameloop.sql', "  AND p.ammo > 0\n  AND i.action = 'x';", "  AND p.ammo > 0\n  AND i.action = 'x'\n  ORDER BY p.id;"),
    ('gameloop.sql', 'ORDER BY random()', 'ORDER BY (x * 31 + y * 17 + (SELECT tick FROM clock)) % 101, x, y'),
    ('gameloop.sql', 'EXTRACT(EPOCH FROM (now()))', '((SELECT tick FROM clock) / 30.0)', 2),
]


def lcg(seed):
    while True:
        seed = (seed * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        yield seed >> 33


def gen_inputs(frames, seed=7):
    """a scripted duel in room A for the first 64 frames (shots both ways, a kill, a respawn), then a seeded random
    walk for both players (moves into walls, turns, shots until the ammo runs out, refills)"""
    script = {}
    for f in range(0, 64):
        a1 = a2 = ''
        if f in (8, 24, 40): a1 = 'x'
        if f in (12, 44): a2 = 'x'
        if 48 <= f < 56: a1 = 'd'
        if 56 <= f < 62: a1 = 'w'
        if 30 <= f < 36: a2 = 'a'
        script[f] = (a1, a2)
    r = lcg(seed)
    keys = 'wwwwwwwwaaaddddssxx' + '  '
    lines = []
    for f in range(frames):
        if f in script: a1, a2 = script[f]
        else: a1, a2 = keys[next(r) % len(keys)].strip(), keys[next(r) % len(keys)].strip()
        lines.append(' '.join(
            f"INSERT INTO inputs(player_id, action) VALUES ({p}, '{a}') ON CONFLICT(player_id) DO UPDATE SET action = excluded.action;"
            for p, a in ((1, a1), (2, a2)) if a))
    return '\n'.join(lines) + '\n'


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    os.makedirs(os.path.join(W, 'sqlite'), exist_ok=True)
    os.makedirs(os.path.join(W, 'pg'), exist_ok=True)
    for f in ('schema.sql', 'renderer.sql', 'gameloop.sql', 'map.sql', 'players.sql', 'index.sql', 'frame-p1.sql', 'frame-p2.sql'):
        sql = expand(open(os.path.join(HERE, 'port', f), encoding='utf-8').read())
        open(os.path.join(W, 'sqlite', f), 'w', encoding='utf-8').write(sql)
    sprites = ''.join(sprites_sqlite(open(os.path.join(UP, 'data', f), encoding='utf-8').read()) + '\n'
                      for f in ('player.sql', 'slug.sql'))
    open(os.path.join(W, 'sqlite', 'sprites.sql'), 'w', encoding='utf-8').write(sprites)

    files = {f: open(os.path.join(UP, f), encoding='utf-8').read() for f in ('gamestate.sql', 'renderer.sql', 'gameloop.sql')}
    for p in PG_PATCHES:
        f, old, new = p[:3]; want = p[3] if len(p) > 3 else 1
        n = files[f].count(old)
        if n != want: sys.exit(f'mkport: pg patch for {f} found {n} times, want {want}: {old!r}')
        files[f] = files[f].replace(old, new)
    for f, s in files.items():
        open(os.path.join(W, 'pg', f), 'w', encoding='utf-8').write(s)
    for f in ('map.sql', 'player.sql', 'slug.sql'):
        open(os.path.join(W, 'pg', 'data-' + f), 'w', encoding='utf-8').write(open(os.path.join(UP, 'data', f), encoding='utf-8').read())
    for f in ('players.sql', 'index.sql', 'frame-p1.sql', 'frame-p2.sql'):
        open(os.path.join(W, 'pg', f), 'w', encoding='utf-8').write(open(os.path.join(HERE, 'port', f), encoding='utf-8').read())
    open(os.path.join(W, 'inputs.txt'), 'w').write(gen_inputs(frames))
    print(f'mkport: work/sqlite, work/pg, work/inputs.txt ({frames} frames)')


if __name__ == '__main__':
    main()
