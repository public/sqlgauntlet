-- DOOMQL (c) 2025 Lukas Vogel, MIT licence (src/cedardb-doomql/LICENSE): renderer.sql ported to SQLite.
-- The views and their expressions are upstream's, in the same order (float arithmetic is order-sensitive). Rewrites
-- (PORT.md has the list and why):
--   generate_series(a, b)          -> a recursive CTE counting a..b (LATERAL ones: one recursive row per outer row)
--   CAST(float AS INT), ::int      -> IRINT(e): Postgres rounds half to even (rint), SQLite truncates
--   ROUND(float8)                  -> RINT(e) (rint too; ROUND(numeric) rounds half away from zero, as SQLite's round)
--   GREATEST / LEAST               -> max() / min() (no NULL arguments reach them here)
--   rpad(s, n, ' '), repeat(s, n)  -> RPAD(s, n), REPEAT(s, n): substr / replace(hex(zeroblob(n)), '00', s)
--   DISTINCT ON (...) ORDER BY ... -> row_number() OVER (PARTITION BY ... ORDER BY depth, mob_id, ch) = 1; the tie
--                                     break after depth is added (pg/ adds the same), see PORT.md
--   string_agg(ch, '' ORDER BY x)  -> as is (SQLite 3.44+); the minimap's gets a tie-break (pg/ too), see PORT.md
-- The macros (upper case) are expanded by mkport.py.

DROP VIEW IF EXISTS screen;
DROP VIEW IF EXISTS minimap;
DROP VIEW IF EXISTS game_view;
DROP VIEW IF EXISTS render_3d_frame;
DROP VIEW IF EXISTS visible_tiles;
DROP VIEW IF EXISTS rays;

CREATE VIEW rays AS
WITH RECURSIVE cols(col) AS (
    SELECT 0
    UNION ALL
    SELECT col + 1 FROM cols WHERE col < (SELECT view_w FROM settings)
  )
SELECT p.id as player_id,
  m.x AS player_x,
  m.y AS player_y,
  c.col,
  (m.dir - s.fov/2.0 + s.fov * (c.col*1.0 / (s.view_w - 1))) AS angle
FROM cols c
CROSS JOIN settings s
CROSS JOIN players p
JOIN mobs m ON p.id = m.id;

CREATE VIEW visible_tiles AS
WITH RECURSIVE raytrace AS (
    SELECT
      r.player_id,
      r.col,
      1 as step_count,
      r.player_x + COS(r.angle)*s.step as fx,
      r.player_y + SIN(r.angle)*s.step as fy,
      r.angle,
      0.0 as dist
    FROM rays r
    CROSS JOIN settings s

    UNION ALL

    SELECT
      rt.player_id as player_id,
      rt.col as col,
      rt.step_count + 1 as step_count,
      rt.fx + COS(rt.angle)*s.step as fx,
      rt.fy + SIN(rt.angle)*s.step as fy,
      rt.angle,
      step_count * s.step * COS(rt.angle - m.dir) as dist
    FROM settings s
    CROSS JOIN raytrace rt
    JOIN players p ON p.id = rt.player_id
    JOIN mobs m ON m.id = p.id
    WHERE rt.step_count < s.max_steps
      AND NOT EXISTS (
        SELECT 1
        FROM map m
        WHERE m.x = IRINT(rt.fx)
          AND m.y = IRINT(rt.fy)
          AND m.tile = '#')
    )
  SELECT DISTINCT
    rt.player_id,
    m.tile,
    IRINT(rt.fx) AS tile_x,
    IRINT(rt.fy) AS tile_y,
    col,
    min(dist) as dist
  FROM raytrace rt
  JOIN map m ON m.x = IRINT(rt.fx) AND m.y = IRINT(rt.fy)
  GROUP BY tile_x, tile_y, m.tile, col, player_id;

CREATE VIEW render_3d_frame AS
WITH RECURSIVE
  cols(col) AS (
    SELECT 0
    UNION ALL
    SELECT col + 1 FROM cols WHERE col < (SELECT view_w FROM settings)
  ),
  rows_gen(row) AS (
    SELECT 0
    UNION ALL
    SELECT row + 1 FROM rows_gen WHERE row < (SELECT view_h FROM settings)
  ),
  visible_walls AS (
    SELECT
      player_id,
      vt.col,
      min(dist) as dist
    FROM map m
    JOIN visible_tiles vt ON vt.tile_x = m.x AND vt.tile_y = m.y
    WHERE m.tile = '#'
    GROUP BY player_id, vt.col
  ),
  distances AS (
    select r.player_id,
      r.col,
      r.angle,
      coalesce(v.dist, s.max_steps * s.step) as dist
    FROM settings s
    CROSS JOIN rays r
    LEFT JOIN visible_walls v ON r.col = v.col AND r.player_id = v.player_id
  ),
  heights AS (
    SELECT
      p.id as player_id,
      d.col,
      CASE WHEN d.dist <= 0
        THEN s.view_h
        ELSE max(0, min(s.view_h, IRINT(s.view_h / (d.dist * COS(d.angle - m.dir)))))
      END AS height
    FROM settings s
    CROSS JOIN distances d
    JOIN players p ON p.id = d.player_id
    JOIN mobs m ON m.id = p.id
  ),
  pixels AS (
    SELECT
      h.player_id,
      c.col AS x,
      rg.row AS y,
      CASE WHEN rg.row < (s.view_h - h.height) / 2 THEN ' '
        WHEN rg.row >= (s.view_h + h.height) / 2 THEN '.'
        WHEN d.dist < s.max_steps * s.step / 4 THEN '█'
        WHEN d.dist < s.max_steps * s.step * 2 / 4 THEN '▓'
        WHEN d.dist < s.max_steps * s.step * 3 / 4 THEN '▒'
        ELSE '░' END AS ch
      FROM cols c
      JOIN heights h ON c.col = h.col
      JOIN distances d ON h.player_id = d.player_id AND h.col = d.col
      CROSS JOIN rows_gen rg
      CROSS JOIN settings s)
SELECT player_id, x, y, ch FROM pixels ORDER BY y, x;

CREATE VIEW game_view as
WITH RECURSIVE
config as (
  SELECT
    p.id as player_id,
    m.x as player_x,
    m.y as player_y,
    m.dir as player_dir,
    s.view_w / (2 * tan(s.fov / 2)) as projection_factor
  FROM settings s
  CROSS JOIN players p
  JOIN mobs m ON p.id = m.id
),
projected_mobs as (
  SELECT
    c.player_id,
    m.*,
    (m.x - c.player_x) * cos(-c.player_dir) - (m.y - c.player_y) * sin(-c.player_dir) as depth,
    (m.x - c.player_x) * sin(-c.player_dir) + (m.y - c.player_y) * cos(-c.player_dir) as horiz
  FROM mobs m
  CROSS JOIN config c
),
screen_mobs as (
  SELECT
    pm.*,
    RINT(s.view_w / 2 + (pm.horiz / pm.depth) * c.projection_factor) AS screen_x_center,
    RINT(s.view_h / 2) AS screen_y_center
  FROM projected_mobs pm
  CROSS JOIN settings s
  CROSS JOIN config c
  WHERE pm.depth > 0.1
),
column_distances AS (
  SELECT player_id, col, MAX(dist) AS dist
  FROM visible_tiles
  GROUP BY player_id, col
),
bullet_lods AS (
  SELECT
    (SELECT id FROM sprites WHERE name = 'shot_slug_away_12x12') AS near_id,
    (SELECT id FROM sprites WHERE name = 'shot_slug_away_6x6')  AS far_id
),
marine_lods AS (
  SELECT
    (SELECT id FROM sprites WHERE name = 'marine_outline_16x20') AS near_id,
    (SELECT id FROM sprites WHERE name = 'marine_outline_12x15') AS mid_id,
    (SELECT id FROM sprites WHERE name = 'marine_outline_8x10')  AS far_id
),
screen_mobs_lod AS (
  SELECT
    sm.*,
    CASE
      WHEN sm.kind = 'bullet' AND sm.depth > 4 THEN blod.far_id
      WHEN sm.kind = 'player' AND sm.depth > 6 THEN ml.far_id
      WHEN sm.kind = 'player' AND sm.depth > 3 THEN ml.mid_id
      ELSE sm.sprite_id
    END AS effective_sprite_id
  FROM screen_mobs sm
  CROSS JOIN bullet_lods blod
  CROSS JOIN marine_lods ml
),
expanded_sprite_pixels AS (
  SELECT
    sm.player_id,
    sm.id AS mob_id,
    sm.depth,
    sp.sx, sp.sy, sp.ch,
    spr.w, spr.h,
    sm.screen_x_center,
    sm.screen_y_center,
    (c.projection_factor * sm.world_w / sm.depth) / spr.w AS scale_x,
    (c.projection_factor * sm.world_h / sm.depth) / spr.h AS scale_y
  FROM config c, screen_mobs_lod sm
  JOIN sprites spr ON spr.id = sm.effective_sprite_id
  JOIN sprite_pixels sp ON sp.sprite_id = spr.id
  WHERE sp.ch IS NOT NULL AND sp.ch <> ' '
),
base AS (
  SELECT
    esp.player_id,
    esp.mob_id,
    esp.depth,
    esp.ch,
    esp.sx, esp.sy,
    esp.scale_x,
    esp.scale_y,
    esp.w, esp.h,
    IRINT(esp.screen_x_center - RINT((esp.w/2.0) * esp.scale_x)) AS ax,
    IRINT(esp.screen_y_center - RINT((esp.h/2.0) * esp.scale_y)) AS ay
  FROM expanded_sprite_pixels esp
),
spans AS (
  SELECT
    b.*,
    IRINT(b.ax + FLOOR(b.sx * b.scale_x)) AS x0_raw,
    IRINT(b.ax + FLOOR((b.sx + 1) * b.scale_x) - 1) AS x1_raw,
    IRINT(b.ay + FLOOR(b.sy * b.scale_y)) AS y0_raw,
    IRINT(b.ay + FLOOR((b.sy + 1) * b.scale_y) - 1) AS y1_raw
  FROM base b
  WHERE b.ch IS NOT NULL
),
clamped AS (
  SELECT
    s.player_id, s.mob_id, s.depth, s.ch, se.view_w, se.view_h,
    max(s.x0_raw, 0)                          AS x0,
    min(max(s.x1_raw, s.x0_raw), se.view_w-1) AS x1,
    max(s.y0_raw, 0)                          AS y0,
    min(max(s.y1_raw, s.y0_raw), se.view_h-1) AS y1
  FROM spans s, settings se
),
-- JOIN LATERAL generate_series(c.x0, c.x1) AS px: one row per px of each texel span
span_x(player_id, mob_id, depth, ch, px, x1, y0, y1) AS (
  SELECT c.player_id, c.mob_id, c.depth, c.ch, c.x0, c.x1, c.y0, c.y1 FROM clamped c WHERE c.x0 <= c.x1
  UNION ALL
  SELECT player_id, mob_id, depth, ch, px + 1, x1, y0, y1 FROM span_x WHERE px < x1
),
-- JOIN LATERAL generate_series(c.y0, c.y1) AS py
sprite_screen_pixels(player_id, mob_id, depth, ch, px, py, y1) AS (
  SELECT player_id, mob_id, depth, ch, px, y0, y1 FROM span_x WHERE y0 <= y1
  UNION ALL
  SELECT player_id, mob_id, depth, ch, px, py + 1, y1 FROM sprite_screen_pixels WHERE py < y1
),
visible_sprite_pixels AS (
  SELECT
    ssp.player_id, ssp.mob_id, ssp.depth, ssp.ch, ssp.px, ssp.py,
    cd.dist AS wall_depth
  FROM sprite_screen_pixels ssp
  LEFT JOIN column_distances cd
    ON cd.player_id = ssp.player_id
   AND cd.col = ssp.px
   WHERE depth < COALESCE(cd.dist, 1e9)
),
closest_sprite_pixel AS (
  SELECT player_id, px, py, ch, depth FROM (
    SELECT player_id, px, py, ch, depth,
      row_number() OVER (PARTITION BY player_id, px, py ORDER BY depth ASC, mob_id, ch) AS rn
    FROM visible_sprite_pixels)
  WHERE rn = 1
),
patched_framebuffer AS (
  SELECT
    bf.player_id,
    bf.y,
    bf.x,
    COALESCE(csp.ch, bf.ch) AS ch
  FROM render_3d_frame bf
  LEFT JOIN closest_sprite_pixel csp
    ON csp.player_id = bf.player_id AND csp.px = bf.x AND csp.py = bf.y
),
final_frame AS (
  SELECT
    player_id,
    y,
    string_agg(ch, '' ORDER BY x) as row
  FROM patched_framebuffer
  GROUP BY player_id, y
)
select * from final_frame ORDER BY y;

CREATE VIEW minimap AS
WITH RECURSIVE dimensions AS (
  SELECT max(x) as max_x, max(y) as max_y FROM map
),
gx(x) AS (SELECT 0 UNION ALL SELECT x + 1 FROM gx WHERE x < (SELECT max_x FROM dimensions)),
gy(y) AS (SELECT 0 UNION ALL SELECT y + 1 FROM gy WHERE y < (SELECT max_y FROM dimensions)),
grid AS (
  SELECT x, y
  FROM dimensions d
  CROSS JOIN gx
  CROSS JOIN gy
),
tiles_to_display AS (
  SELECT player_id, tile_x, tile_y, min(tile) as tile
  FROM visible_tiles
  GROUP BY player_id, tile_x, tile_y
),
mobs_overlay AS (
  SELECT
    t.player_id,
    CAST(FLOOR(m.x) AS INTEGER) AS x,
    CAST(FLOOR(m.y) AS INTEGER) AS y,
    COALESCE(m.minimap_icon, '?') AS ch
  FROM mobs m
  JOIN tiles_to_display t
    ON CAST(FLOOR(m.x) AS INTEGER) = t.tile_x AND CAST(FLOOR(m.y) AS INTEGER) = t.tile_y
),
combined AS (
  SELECT pl.id AS player_id, g.x, g.y,
    coalesce(
      mo.ch,
      CASE WHEN t.tile = 'R' THEN '.' ELSE t.tile END,
      CASE WHEN base.tile = '.' OR base.tile = 'R' THEN ' '
           ELSE base.tile END,
      ' '
    ) as ch
  FROM players pl
  CROSS JOIN grid g
  LEFT JOIN map base ON g.x = base.x AND g.y = base.y
  LEFT JOIN tiles_to_display t ON g.x = t.tile_x AND g.y = t.tile_y AND pl.id = t.player_id
  LEFT JOIN mobs_overlay mo ON g.x = mo.x AND g.y = mo.y AND pl.id = mo.player_id
),
lines AS (
  SELECT
    player_id,
    y,
    string_agg(ch, '' ORDER BY x, ch) as row   -- (tie-break added, PORT.md: two mobs on one tile give two rows)
  FROM combined
  GROUP BY player_id, y
)
SELECT *
FROM lines;

CREATE VIEW screen AS
WITH RECURSIVE
mm AS (
  SELECT player_id, y, row
  FROM minimap
),
mm_dims AS (
  SELECT
         MAX(LENGTH(row)) AS mm_w,
         MAX(y)           AS y_max
  FROM mm
),
gameview_lines AS (
  SELECT g.player_id,
         g.y,
         '║' || RPAD(g.row, view_w) || '║' AS view_col
  FROM game_view g
  CROSS JOIN settings s
),
gameview_frame AS (
  SELECT p.id as player_id, -1 AS y, '╔' || REPEAT('═', view_w) || '╗' AS view_col
  FROM players p
  CROSS JOIN settings s

  UNION ALL

  SELECT *
  FROM gameview_lines

  UNION ALL

  SELECT p.id as player_id, s.view_w + 1 AS y, '╚' || REPEAT('═', view_w) || '╝' AS view_col
  FROM players p
  CROSS JOIN settings s
),
minimap_lines AS (
  SELECT m.player_id,
         m.y,
         '║' || RPAD(m.row, md.mm_w) || '║' AS mm_col
  FROM mm m
  CROSS JOIN mm_dims md
),
minimap_frame AS (
  SELECT p.id as player_id, -1 AS y, '╔' || REPEAT('═', md.mm_w) || '╗' AS mm_col
  FROM mm_dims md
  CROSS JOIN players p

  UNION ALL

  SELECT *
  FROM minimap_lines

  UNION ALL

  SELECT p.id as player_id, md.y_max + 1 AS y, '╚' || REPEAT('═', md.mm_w) || '╝' AS mm_col
  FROM mm_dims md
  CROSS JOIN players p
),
player_lines AS (
  SELECT
    row_number() OVER (ORDER BY p.id) - 1 AS y,
    (
      p.id || ': ' || RPAD(m.name, 10) || ' (' || m.minimap_icon || ') '
      || 'score: ' || p.score || '   '
      || 'HP: [' ||
         REPEAT('█', max(0, min(20, CAST(round(20 * max(0, min(p.hp,100)) / 100.0) AS INTEGER)))) ||
         REPEAT(' ', max(0, 20 - CAST(round(20 * max(0, min(p.hp,100)) / 100.0) AS INTEGER))) ||
         '] ' || max(0, p.hp) || '   '
      || 'AMMO: ' ||
        REPEAT('•', COALESCE(p.ammo,0))
    ) AS player_row
  FROM players p
  JOIN mobs m ON p.id = m.id
),
hud_dims AS (
  SELECT MAX(y) AS hud_max_y
  FROM player_lines
),
bounds AS (
  SELECT
    p.id as player_id,
    max(
      s.view_h + 1,
      md.y_max + 1,
      hd.hud_max_y
    ) AS y_top
  FROM players p
  CROSS JOIN settings s
  CROSS JOIN mm_dims md
  CROSS JOIN hud_dims hd
),
-- JOIN LATERAL generate_series(-1, b.y_top) AS gs(y)
row_index(player_id, y, y_top) AS (
  SELECT b.player_id, -1, b.y_top FROM bounds b WHERE -1 <= b.y_top
  UNION ALL
  SELECT player_id, y + 1, y_top FROM row_index WHERE y < y_top
),
frames_by_row AS (
  SELECT
    ri.player_id,
    ri.y,
    gv.view_col,
    mf.mm_col
  FROM row_index ri
  LEFT JOIN gameview_frame gv
    ON gv.player_id = ri.player_id AND gv.y = ri.y
  LEFT JOIN minimap_frame mf
    ON mf.player_id = ri.player_id AND mf.y = ri.y
)
SELECT
  f.player_id,
  f.y,
  COALESCE(f.view_col, REPEAT(' ', s.view_w + 2)) || '   ' ||
  COALESCE(f.mm_col,  REPEAT(' ', COALESCE(md.mm_w, 0) + 2)) || '   ' ||
  COALESCE(pl.player_row, '') AS full_row
FROM frames_by_row f
LEFT JOIN player_lines pl ON pl.y = f.y, mm_dims md, settings s
ORDER BY f.player_id, f.y;
