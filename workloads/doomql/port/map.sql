-- DOOMQL (c) 2025 Lukas Vogel, MIT licence (src/cedardb-doomql/LICENSE): data/map.sql ported to SQLite
-- (generate_series -> recursive CTEs, LATERAL series -> a join on a counter, ::char -> text).
DELETE FROM map;

WITH RECURSIVE params AS (
  SELECT 64 AS w, 32 AS h
),
nums(n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM nums WHERE n < 63),
grid AS (
  SELECT gx.n AS x, gy.n AS y
  FROM params p, nums gx, nums gy
  WHERE gx.n <= p.w - 1 AND gy.n <= p.h - 1
),
areas(x1, y1, x2, y2) AS (
  VALUES
    (  2,  2, 14, 10),
    ( 20,  2, 34,  9),
    ( 40,  3, 60, 12),
    (  4, 14, 20, 28),
    ( 26, 16, 38, 28),
    ( 44, 16, 60, 28),
    ( 15,  6, 20,  7),
    ( 35,  6, 40,  7),
    ( 10, 11, 11, 14),
    ( 20, 21, 26, 22),
    ( 38, 22, 44, 23),
    ( 28,  9, 29, 16),
    ( 52, 12, 53, 16)
),
normalized AS (
  SELECT min(x1,x2) AS x1, min(y1,y2) AS y1,
         max(x1,x2) AS x2, max(y1,y2) AS y2
  FROM areas
),
room_boxes AS (
  SELECT *
  FROM normalized
  WHERE (x2 - x1 + 1) >= 6 AND (y2 - y1 + 1) >= 6
),
respawns AS (
  SELECT CAST(floor((x1 + x2) / 2.0) AS INTEGER) AS x,
         CAST(floor((y1 + y2) / 2.0) AS INTEGER) AS y
  FROM room_boxes
),
floors AS (
  SELECT fx.n AS x, fy.n AS y
  FROM normalized a
  JOIN nums fx ON fx.n BETWEEN a.x1 AND a.x2
  JOIN nums fy ON fy.n BETWEEN a.y1 AND a.y2
),
map_rows AS (
  SELECT g.x, g.y,
         CASE
          WHEN EXISTS (SELECT 1 FROM respawns r WHERE r.x = g.x AND r.y = g.y)
             THEN 'R'
          WHEN EXISTS (SELECT 1 FROM floors f WHERE f.x = g.x AND f.y = g.y)
              THEN '.'
          ELSE '#' END AS tile
  FROM grid g
)
INSERT INTO map(x, y, tile)
SELECT x, y, tile FROM map_rows ORDER BY x, y;
