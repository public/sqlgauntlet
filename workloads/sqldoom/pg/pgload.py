#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Derived from SQLDoom (cedardb/sqldoom, GPL-2.0, (c) CedarDB): it rewords upstream SQL, see COPYING.md.
"""pgload.py <sqldoom checkout> <wad> <map> <skill>: SQLDoom's own wad_loader.py, run against Postgres 16.

wad_loader.py targets CedarDB. Two things it installs do not exist in Postgres, and are replaced here, unchanged in
what they compute:
  - the loader's materialization functions (sql/loader/functions) and doom_reset_stage are LANGUAGE cedarscript, but
    their bodies are plain SQL statements: they are installed as LANGUAGE sql functions returning void;
  - the CedarScript game-tic runtime is not installed (this is render only). The renderer needs three of its plain
    SQL functions (43_render_light.sql, installed as is) and CedarDB's built-in ARG_MAX aggregate (defined below).
Then the stage is reset as the game does at level start (doom_reset_stage: the light thinkers, monster/player state)."""
import sys
from pathlib import Path

UP = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(UP))
import wad_loader  # noqa: E402

CS = 'LANGUAGE cedarscript AS $doom$'


# CedarDB lets an UPDATE's FROM joins refer to the target table; Postgres only allows that in WHERE (path, old, new)
FIXES = [('sql/runtime/functions/04_reset_stage.sql',
          'JOIN thing_combat_defs d ON d.thing_type = t.type AND d.floats\nWHERE t.map_id = p_map_id',
          'JOIN thing_combat_defs d ON d.floats\nWHERE d.thing_type = t.type AND t.map_id = p_map_id')]


def as_sql(path):
    text = (UP / path).read_text(encoding='utf-8')
    if text.count(CS) != 1:
        sys.exit(f'pgload: {path}: expected one cedarscript function (upstream changed?)')
    for f, old, new in FIXES:
        if f == path:
            if text.count(old) != 1: sys.exit(f'pgload: {path}: fix does not apply (upstream changed?)')
            text = text.replace(old, new)
    return text.replace(CS, 'RETURNS void LANGUAGE sql AS $doom$')


ARG_MAX = """
DROP AGGREGATE IF EXISTS arg_max(bigint, bigint);
DROP TYPE IF EXISTS arg_max_state CASCADE;
CREATE TYPE arg_max_state AS (v bigint, k bigint);
CREATE FUNCTION arg_max_step(s arg_max_state, v bigint, k bigint) RETURNS arg_max_state LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN k IS NULL THEN s WHEN s IS NULL OR k > (s).k THEN ROW(v, k)::arg_max_state ELSE s END $$;
CREATE FUNCTION arg_max_final(s arg_max_state) RETURNS bigint LANGUAGE sql IMMUTABLE AS $$ SELECT (s).v $$;
CREATE AGGREGATE arg_max(bigint, bigint) (SFUNC = arg_max_step, STYPE = arg_max_state, FINALFUNC = arg_max_final);
"""


def install_loader_functions(cur):
    for f in ('00_materialize_render_segs.sql', '01_materialize_render_things.sql', '02_materialize_all_render_things.sql'):
        cur.execute(as_sql('sql/loader/functions/' + f))


def install_runtime(cur):
    cur.execute((UP / 'sql/runtime/functions/43_render_light.sql').read_text(encoding='utf-8'))
    cur.execute(as_sql('sql/runtime/functions/04_reset_stage.sql'))
    cur.execute(ARG_MAX)


wad_loader.install_loader_functions = install_loader_functions
wad_loader.install_cedarscript_runtime = install_runtime
wad, name, skill = sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.argv = ['wad_loader.py', wad, name]
wad_loader.main()
conn = wad_loader.psycopg2.connect('')
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute('SELECT map_id FROM maps WHERE name = %s', (name,))
    map_id = cur.fetchone()[0]
    cur.execute('SELECT doom_reset_stage(%s, %s)', (map_id, skill))
    cur.execute('ANALYZE')
print(f'pgload: {name} is map_id {map_id}, stage reset at skill {skill}')
