# DOOMQL on SQLite: what the port changes and why

Upstream is [cedardb/DOOMQL](https://github.com/cedardb/DOOMQL) at `8d6c2fc` (MIT, © 2025 Lukas Vogel), cloned by
`fetch.sh` into `src/cedardb-doomql/`. It is written for CedarDB, and its last commit makes the SQL Postgres-compatible.
The SQLite version is in `port/`. `mkport.py` expands its macros into `work/sqlite/` and converts the sprite data from
upstream.

## Upstream's SQL, inventoried

| File | What it is | Postgres / CedarDB features it relies on |
|---|---|---|
| `gamestate.sql` (84 lines) | tables: `config`, `map`, `inputs`, `settings`, `sprites`, `sprite_pixels`, `mobs`, `players`; view `collisions` | `NUMERIC`, `CHAR`, `SERIAL`, `GENERATED ALWAYS AS IDENTITY`, `TIMESTAMP DEFAULT NOW()`, `EXTRACT(EPOCH FROM now())::int`, `PI()`, `CAST(float AS INT)` |
| `renderer.sql` (510 lines) | views `rays`, `visible_tiles`, `render_3d_frame`, `game_view`, `minimap`, `screen` | `WITH RECURSIVE` (the ray march), `generate_series` (plain and `JOIN LATERAL`), `DISTINCT ON`, `string_agg(... ORDER BY ...)`, `row_number() OVER`, `GREATEST`/`LEAST`, `rpad`, `repeat`, `ROUND(float8)`, `FLOOR`, `::int` casts, `cos`/`sin`/`tan`, `m.*` in CTEs |
| `gameloop.sql` (156 lines) | one game tick: movement, turning, shots, bullets, hits, kills, respawns, ammo | `UPDATE t alias SET ... FROM`, `WITH ... UPDATE`, `DELETE ... USING`, `INSERT ... SELECT`, `ORDER BY random() LIMIT 1`, `now()`, `BEGIN`/`COMMIT` |
| `data/map.sql`, `data/player.sql`, `data/slug.sql` | the 64×32 map; 5 sprites (marine at 3 LODs, a slug at 2) | `generate_series`, `JOIN LATERAL`, `VALUES ... AS t(cols)`, `unnest(ARRAY[...]) WITH ORDINALITY`, `SUBSTRING(s FROM a FOR b)`, `::char` |
| `pyclient.py` (156 lines) | psycopg2 terminal client: keys into `inputs` (`INSERT ... ON CONFLICT DO UPDATE`), `SELECT full_row FROM screen WHERE player_id = %s ORDER BY y` | upsert |
| `server.sh` | loads the SQL, runs `gameloop.sql` through psql about 30 times a second | |

The frame is one query, `SELECT full_row FROM screen WHERE player_id = ? ORDER BY y`. SQLite compiles it to
**94,856 VDBE opcodes, with 581 cursors and 12,913 registers**. The view nest re-evaluates `visible_tiles`, the
recursive ray march, four times per frame:
- for the wall heights;
- for the sprite occlusion;
- twice for the minimap, once for the minimap rows and once again for their width.

Each time, it computes it for every player before `WHERE player_id = ?` filters.

## Rewrites in the SQLite port

Each one keeps upstream's arithmetic in the same order: float results depend on it, and the frames are compared
byte for byte.

| Postgres | SQLite | Why |
|---|---|---|
| `CAST(x AS INT)`, `x::int` on a float | `IRINT(x)`: `CAST(CASE WHEN x - floor(x) = 0.5 THEN floor(x) + abs(CAST(floor(x) AS INTEGER)) % 2 ELSE round(x) END AS INTEGER)` | Postgres rounds float-to-int casts half to even (`rint`); SQLite truncates. The map lookups `m.x = CAST(rt.fx AS INT)` depend on it |
| `ROUND(float8)` | `RINT(x)`, as above without the cast | Postgres's `round(double precision)` is `rint` too. (`ROUND(numeric)` in the HUD rounds half away from zero, as SQLite's `round` does, so it stays `round`) |
| `numeric::int` (the ammo clock) | `CAST(round(x) AS INTEGER)` | numeric casts round half away from zero |
| `generate_series(a, b)` | a recursive CTE counting `a..b` | no table-valued function, so the JIT sees ordinary bytecode, not a virtual table |
| `JOIN LATERAL generate_series(c.x0, c.x1)` (sprite texel spans, the screen's row index) | a recursive CTE emitting one row per value for each outer row | the same rows, in the same per-row cost |
| `GREATEST` / `LEAST` | scalar `max()` / `min()` | no NULL argument reaches them (Postgres ignores NULLs, SQLite returns NULL) |
| `rpad(s, n, ' ')` | `RPAD(s, n)`: `substr(s \|\| replace(hex(zeroblob(n)), '00', ' '), 1, n)` | pads and truncates by characters, as rpad does. This matters: the 129-column 3D rows are cut to 128 |
| `repeat(s, n)` | `REPEAT(s, n)`: `replace(hex(zeroblob(n)), '00', s)` | |
| `DISTINCT ON (player_id, px, py) ... ORDER BY ..., depth` | `row_number() OVER (PARTITION BY player_id, px, py ORDER BY depth, mob_id, ch) = 1` | the tie-break after `depth` is new (see below) |
| `string_agg(ch, '' ORDER BY x)` | unchanged | SQLite has had ordered aggregates since 3.44 |
| `UPDATE t alias SET`, `DELETE FROM t alias` | `UPDATE t AS alias`, `DELETE FROM t AS alias` | grammar |
| `DELETE FROM mobs m USING collisions c WHERE m.id = c.bullet_id` | `DELETE FROM mobs WHERE id IN (SELECT bullet_id FROM collisions)` | no `USING` |
| `NUMERIC`, `CHAR`, `TIMESTAMP` | `REAL`, `TEXT`, `INT` | |
| `SERIAL`, `GENERATED ALWAYS AS IDENTITY` | `INTEGER PRIMARY KEY AUTOINCREMENT` | identity values must never be reused (bullets are deleted), as in Postgres |
| `unnest(ARRAY[...]) WITH ORDINALITY`, `SUBSTRING(... FROM ... FOR ...)` (sprite loading) | `json_each(json_array(...))` with `key + 1`, `substr` | loading only, never timed |

### Determinism (the Postgres reference gets the same changes)

| Upstream | Both dialects | Why |
|---|---|---|
| `now()`, `EXTRACT(EPOCH FROM now())` | a `clock(tick)` table advanced once per game tick; the epoch is `tick / 30.0` | ammo refills depend on wall time |
| `ORDER BY random() LIMIT 1` (respawn point) | `ORDER BY (x * 31 + y * 17 + tick) % 101, x, y LIMIT 1` | |
| `DISTINCT ON ... ORDER BY ..., depth` | adds `mob_id, ch` after `depth` | at a sprite's smaller LODs several texels land on one pixel at equal depth, so which character wins is unspecified |
| the minimap's `string_agg(ch, '' ORDER BY x)` | `ORDER BY x, ch` | a bullet on its shooter's tile (or two players on one) joins two `mobs_overlay` rows to that cell, so the row gets both characters, in an order the query leaves open: Postgres drew `A*`, SQLite `*A` (frame 8). A rendering quirk of upstream, kept, but with a fixed order |
| `INSERT INTO mobs ... SELECT` (bullets) | adds `ORDER BY p.id` | when both players fire in the same tick, the bullets' identity values are fixed |
| `map.tile CHAR` (reference only) | `TEXT` | Postgres's `char(1)` drops trailing blanks when cast to text. The minimap's `' '` (out-of-sight floor) became `''` inside `coalesce`, so its rows lost characters and shifted left. That is a Postgres artefact, not the game's intent (CedarDB draws it aligned), and the port keeps the spaces |

## Not upstream: one index

`port/index.sql` adds `CREATE INDEX map_xy ON map(x, y)`. The ray march's recursive step has a correlated
`NOT EXISTS (SELECT 1 FROM map WHERE x = ... AND y = ... AND tile = '#')`. Postgres turns that into a hash anti-join;
SQLite builds no automatic index for it, so every ray step scans all 2,048 map tiles. Measured on M5, the scan costs
about 8 s per frame, against 0.07–0.25 s with the index. Results are identical either way. `run-modes.sh ... noindex`
runs upstream's schema as it is.

## Peter Gostev's DOOMQL (the second workload)

[petergpt/doomql](https://github.com/petergpt/doomql) at `a2b40b6` (MIT, © 2026 Peter Gostev) is written for SQLite
and needs no port. `mkpeter.py` drives it the way its `host/doomql.py` does:
- per frame, the clock sample and a held key's raw bytes go into `game_clock` / `input_events`;
- then `sql/tick.sql` runs;
- then `SELECT scanline FROM render_frame ORDER BY y` (and `SELECT x, y, r, g, b FROM frame_pixels`).

Its frame query is a 7,491-opcode RGB raycaster (DDA, textures, sprites), with 360 cursors.

## SQLDoom (the BSP successor) is not ported

[cedardb/sqldoom](https://github.com/cedardb/sqldoom) is GPL-2.0. Its 1,634-line `renderer.sql` is one prepared
query, with 28 `LATERAL`s, `bytea`/`get_byte`, `atan2` and parameters. It reads the tables that `wad_loader.py`
(1,337 lines) builds from a WAD. The game logic is CedarScript, stored functions in `sql/runtime/functions/`.
A render-only port would need the 1,365-line schema, the loader output moved into SQLite, and the renderer rewritten.
Since it is GPL, it would have to be kept as a pinned fetch plus a patch. It is listed as a follow-up in RESULTS.md.
