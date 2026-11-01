#!/usr/bin/env python3
"""export.py <out.db>: copy the tables (and materialized views) SQLDoom's renderer reads from Postgres into SQLite.

Types: integers -> INTEGER, real/double -> REAL (real is read as ::float8, the exact widened value, not its shortest
float4 text), boolean -> INTEGER 0/1, bytea -> BLOB, text/varchar/char/enum -> TEXT. Each table keeps its primary key
and Postgres's other indexes, so SQLite has the same access paths."""
import sqlite3, sys
import psycopg2

TABLES = """ammo_defs colormap_rgb effect_sprite_defs flat_textures linedefs maps monster_ai monster_projectiles
mp_players node_children node_path_steps nodes picked_up_items player_state player_weapon_owned player_weapons
projectile_defs render_segs render_things sector_light_fx sectors sprite_frames sprite_lumps thing_ai_frames
thing_combat_defs thing_health things ui_hud_pixels ui_patches ui_static_pixels walltex_meta weapon_defs weapon_frames
world_effects""".split()
INT = {'smallint', 'integer', 'bigint', 'boolean'}
REAL = {'real', 'double precision'}

pg = psycopg2.connect('')
cur = pg.cursor()
db = sqlite3.connect(sys.argv[1])
total = 0
for t in TABLES:
    cur.execute("""SELECT a.attname, format_type(a.atttypid, a.atttypmod), t.typtype
                   FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
                   WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""", (t,))
    cols = cur.fetchall()
    cur.execute("""SELECT i.indisprimary, i.indisunique, c.relname,
                          array(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(n, o)
                                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n ORDER BY k.o)
                   FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                   WHERE i.indrelid = %s::regclass AND i.indexprs IS NULL AND i.indpred IS NULL ORDER BY c.relname""", (t,))
    idx = cur.fetchall()
    defs, sel = [], []
    for name, typ, kind in cols:
        base = typ.split('(')[0]
        if base in INT: st = 'INTEGER'
        elif base in REAL: st = 'REAL'
        elif base == 'bytea': st = 'BLOB'
        else: st = 'TEXT'
        defs.append(f'"{name}" {st}')
        sel.append(f'"{name}"::float8' if base == 'real' else f'"{name}"::text' if kind == 'e' else f'"{name}"')
    pk = [cols_ for prim, _, _, cols_ in idx if prim]
    if pk: defs.append('PRIMARY KEY (' + ', '.join(pk[0]) + ')')
    db.execute(f'DROP TABLE IF EXISTS "{t}"')
    db.execute(f'CREATE TABLE "{t}" ({", ".join(defs)})')
    for prim, uniq, iname, icols in idx:
        if not prim:
            db.execute(f'CREATE {"UNIQUE " if uniq else ""}INDEX "{iname}" ON "{t}" ({", ".join(icols)})')
    cur.execute(f'SELECT {", ".join(sel)} FROM "{t}"')
    rows = [tuple(bytes(v) if isinstance(v, memoryview) else v for v in r) for r in cur.fetchall()]
    db.executemany(f'INSERT INTO "{t}" VALUES ({", ".join("?" * len(cols))})', rows)
    total += len(rows)
db.commit()
db.execute('ANALYZE')
db.commit()
db.execute('VACUUM')
print(f'export: {len(TABLES)} tables, {total} rows -> {sys.argv[1]}')
