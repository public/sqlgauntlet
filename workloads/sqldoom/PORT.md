# SQLDoom's renderer on SQLite: what the port changes and why

Upstream is [cedardb/sqldoom](https://github.com/cedardb/sqldoom) at `95753a2` (GPL-2.0, © CedarDB). `fetch.sh`
clones it into `src/` and downloads Freedoom 0.13.0 next to it. Neither is committed. `mkport.py` (GPL-2.0, see
COPYING.md) generates both renderers into `work/` from upstream's `sql/renderer.sql`. It refuses to run if any rewrite
no longer applies exactly once.

## The pieces

| Step | What runs | Where |
|---|---|---|
| load | upstream's `wad_loader.py` on Postgres 16.3, Freedoom's `freedoom1.wad`, map E1M1, skill 2; then upstream's `doom_reset_stage` (light thinkers, monster and player state at level start) | `pg/load.sh`, `pg/pgload.py` (Docker, CPUs 8–13) |
| copy | the 34 tables and materialized views the renderer reads, Postgres → SQLite, with their primary keys and indexes | `pg/export.py` → `work/E1M1.db` (189k rows, 33 MB) |
| camera | 134 poses: a 16-step spin at the player start, then a walk through the start room, the long hall and the side room with the pit | `mkpath.py` → `work/poses.txt` |
| reference | upstream's query, prepared, one `EXECUTE render(1, 157, 2, x, y, z, angle)` per pose | `pg/render-pg.sh` |
| port | the SQLite query, a one-row `render_params` table for `$1..$7`, and doomrun's per-frame input line | `mkport.py` → `work/sqlite/`, `work/inputs.txt` |

Render only: no game tics run. Per pose the clock advances 2 tics (`player_state.level_tics`, which drives the light
effects and the scrolling walls), and the pistol is raised (`work/state.sql`), in both dialects. Each frame is one
`bytea`/BLOB: 320×200×3 bytes, the 168-row view and the status bar.

## What the loader needed on Postgres

`wad_loader.py` targets CedarDB. Everything it does on Postgres is upstream's own code, except:
- **The CedarScript loader functions** (`doom_materialize_render_segs`, `_render_things`, `_all_render_things`)
  and `doom_reset_stage` are installed as `LANGUAGE sql` functions returning `void`. Their bodies are plain SQL
  already. One `UPDATE ... FROM` joins on its own target table, which CedarDB allows and Postgres does not; the
  condition moves to the `WHERE` clause.
- **The CedarScript runtime is not installed.** Only `43_render_light.sql` is: the renderer's three light functions,
  plain SQL.
- **`ARG_MAX`** is a CedarDB built-in. Postgres gets an `arg_max(bigint, bigint)` aggregate that keeps the first
  maximum.

Music is not rendered (no fluidsynth in the image), and the renderer does not read it.

## The renderer on Postgres: one change

CedarDB lets a `SELECT` refer to its own output aliases: `render_settings` defines `screen_w`, then uses it in `cx`,
`focal`, and so on. Postgres (and SQLite) do not. The three base settings move into a derived table `b0`, and the
rest of the query is upstream's text unchanged (`settings()` in mkport.py). The render runs as a prepared statement
with upstream's parameter types.

## The SQLite port

The same `render_settings` change, then these rewrites, each kept in upstream's arithmetic order, because the frames
are compared byte for byte.

| Upstream (CedarDB/Postgres) | SQLite | Why |
|---|---|---|
| `$1..$7` | columns of `render_params` (one row, updated per pose by doomrun's input line) | doomrun prepares the frame query once and steps it per pose |
| `CROSS JOIN LATERAL generate_series(a, b) AS x` (18 of them) | `JOIN (SELECT v AS x FROM series) AS x ON x.x BETWEEN a AND b`; `series` is a table of 0..1023 | no LATERAL. The alias keeps its column name, so unqualified references stay as written. `series` is an integer-primary-key range seek, not a virtual table, so the JIT sees ordinary bytecode. Every range is clamped to the screen or is a short string's length |
| `CROSS JOIN LATERAL (SELECT e AS c, ...) a`, with no FROM (7) | dropped; `a.c` replaced by `(e)` within its own UNION arm | the expressions are computed where they are used (once to three times instead of once) |
| `LEFT JOIN LATERAL (... ORDER BY q.seq DESC LIMIT 1)` (panel_clips: the carried ceiling and floor) | a correlated scalar subquery for that `seq`, then a join on the unique `(col_x, seq)` | same row; no LATERAL |
| `CROSS JOIN LATERAL (... ORDER BY rotation preference LIMIT 1)` (sprite rotation) | the same: the chosen rotation, then a join on the key `(sprite, frame, rotation)` | |
| `float::int`, `::bigint`, `::smallint` that round (7 places: the BSP side test, the seg facing test, the depth key, fuzz, projectile angle) | `IRINT(e)`: half to even, as Postgres's `rint` | SQLite's `CAST` truncates. Every other integer cast takes an integer or a `FLOOR`/`CEIL` and becomes `CAST(... AS INTEGER)`. `mkport.py -v` lists them |
| `::float8`, `::real`, `::text`, `::bytea`, `::char(1)`, `::varchar(4)` | `CAST`, or the literal, or nothing | |
| `GREATEST` / `LEAST` | scalar `max()` / `min()` | Postgres ignores NULL arguments and SQLite returns NULL. Where a NULL can reach one (a missing carried ceiling or floor), the row is dropped later either way |
| `get_byte(b, n)` | `((instr('00,01,...,FF', hex(substr(b, n + 1, 1))) - 1) / 3)` | plain SQL: the byte's two hex digits found in a 767-character table |
| `octet_length`, `ascii`, `lpad(n, 3, '0')`, `substring(s FROM a FOR n)`, `IS DISTINCT FROM`, `bool_and` | `length`, `unicode`, `substr('00' \|\| n, -3)`, `substr(s, a, n)`, `IS NOT`, `min` | |
| `degrees(x)` | `x / 0.017453292519943295` | Postgres divides by `RADIANS_PER_DEGREE`, while SQLite multiplies by `180/π`, which can differ in the last bit. `radians` is the same in both |
| `doom_light_index`, `_scale`, `_zdepth` (SQL functions) | inlined, as Postgres inlines them | |
| `ARG_MAX(palette_index, layer)` | SQLite's bare column next to `max(layer)`; `x`, `y` cast to INTEGER | see the plan notes below |
| `string_agg(rgb, ''::bytea ORDER BY pix)` | `CAST(string_agg(rgb, x'' ORDER BY pix) AS BLOB)` | |
| `UNION ALL ... FROM generate_series(0, 255999) WHERE g + 0 < 0` | dropped | an always-empty arm that steers CedarDB's optimizer |

### Plan changes (results identical; without them a frame takes 30 s or more)

Postgres runs these with hash joins. SQLite falls back to nested scans unless it can build an automatic index, so
**an unmodified port took 30 s per frame on the M5**, almost all of it in one anti-join. With the changes below it
takes 0.4 s. Each is the same rows by construction, and the frames say so (RESULTS.md §3).

1. **Multi-use CTEs are `MATERIALIZED` (24 of them).** Postgres evaluates a CTE referenced more than once only once.
   SQLite flattened `heights` into all seven `wall_parts` arms, re-running the BSP walk and the seg joins seven times.
   `MATERIALIZED` is Postgres's default here.
2. **`seg_id + 0` → `seg_id` (3 places).** Upstream's "psst, optimizer hack" hides `seg_id` from CedarDB's optimizer.
   For SQLite it hides `render_segs`'s primary key, so each visible seg (and each wall column span) scanned all 2,057
   segs.
3. **`FULL JOIN` → `LEFT JOIN` plus the unmatched right side, by `NOT IN`** (`final_pixels`). SQLite builds no
   automatic index on the right side of a RIGHT or FULL JOIN, so every resolved pixel scanned every weapon-sprite
   pixel: about 100M rows a frame.
4. **Integer affinity on two expression columns.** An expression column has no affinity, and SQLite does not use an
   index on it to compare against an INTEGER column. `view_holes`'s `NOT EXISTS (SELECT 1 FROM final_pixels WHERE
   f.x = sx.x ...)` therefore scanned `final_pixels` once per screen pixel: 1.4 billion rows, 90% of a frame. On the
   `LEFT JOIN` side of (3), `COALESCE(p.x, r.x)` equals `r.x`, and using `r.x` keeps its INTEGER affinity, so upstream's
   `NOT EXISTS` stays as written and gets an automatic index. `ui_resolved` (the ARG_MAX) casts its `x`, `y` for the
   same reason (`ui_colored` scanned it per status-bar pixel).

### Build: SQLITE_MAX_EXPR_DEPTH

SQLite 3.54 refuses a WITH whose CTE expansion has opened 1,000 cursors or more ("VIEWs and/or subqueries nested
too deep"; a guard added 2026-10-04 against exponential expansion). SQLDoom's query, after expansion, is numbered
up to cursor 36,123, of which 391 are opened. `build.sh` and the Xeon payload compile with
`-DSQLITE_MAX_EXPR_DEPTH=100000`. Stock SQLite 3.54.1 cannot run this query without it.
