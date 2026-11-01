-- DOOMQL (c) 2025 Lukas Vogel, MIT licence (src/cedardb-doomql/LICENSE): gameloop.sql ported to SQLite, run once
-- per frame by the driver. Rewrites (PORT.md): UPDATE t alias -> UPDATE t AS alias; DELETE ... USING -> DELETE ...
-- WHERE id IN (...); CAST(float AS INT) -> IRINT; now() -> the clock table; ORDER BY random() (respawn point) -> a
-- deterministic order of the clock tick (pg/ does the same); GREATEST/LEAST -> max/min.

UPDATE clock SET tick = tick + 1;

-- Process all player inputs
BEGIN TRANSACTION;

WITH new_positions AS (
  SELECT
    p.id,
    m.x + cos(m.dir) * (SELECT player_move_speed FROM config) AS new_x,
    m.y + sin(m.dir) * (SELECT player_move_speed FROM config) AS new_y
  FROM players p, mobs m, inputs i
  WHERE p.id = i.player_id
  AND m.id = p.id
  AND i.action = 'w'
  UNION ALL
  SELECT
    p.id,
    m.x - cos(m.dir) * (SELECT player_move_speed FROM config) AS new_x,
    m.y - sin(m.dir) * (SELECT player_move_speed FROM config) AS new_y
  FROM players p, mobs m, inputs i
  WHERE p.id = i.player_id
  AND m.id = p.id
  AND i.action = 's'
),
filtered_positions AS (
  SELECT np.id, np.new_x, np.new_y
  FROM new_positions np, map m
  WHERE m.x = IRINT(np.new_x)
  AND m.y = IRINT(np.new_y)
  AND m.tile != '#'
)
UPDATE mobs AS m SET
x = np.new_x,
y = np.new_y
FROM filtered_positions np
WHERE m.id = np.id;

UPDATE mobs AS m SET
dir = dir - (select player_turn_speed from config)
FROM inputs i, players p
WHERE m.id = p.id
AND m.id = i.player_id
AND i.action = 'a';

UPDATE mobs AS m SET
dir = dir + (select player_turn_speed from config)
FROM inputs i, players p
WHERE m.id = p.id
AND m.id = i.player_id
AND i.action = 'd';

INSERT INTO mobs(kind, owner, x, y, dir, name, sprite_id, minimap_icon, world_w, world_H)
  SELECT
    'bullet',
    p.id,
    source.x,
    source.y,
    source.dir,
    null,
    (SELECT id FROM sprites WHERE name = 'shot_slug_away_12x12'),
    '*',
    .5,
    .5
  FROM players p, mobs source, inputs i
  WHERE p.id = source.id
  AND i.player_id = p.id
  AND p.ammo > 0
  AND i.action = 'x'
  ORDER BY p.id;

UPDATE players AS p SET ammo = ammo - 1
FROM inputs i
WHERE p.id = i.player_id
AND p.ammo > 0
AND i.action = 'x';

COMMIT;

-- Process all bullets
BEGIN TRANSACTION;

UPDATE mobs SET x = x + cos(dir) * 0.5, y = y + sin(dir) * 0.5 WHERE kind = 'bullet';

DELETE FROM mobs WHERE x < 0 OR x >= (select max(x) from map) OR y < 0 OR y >= (select max(y) from map) AND kind = 'bullet';

DELETE FROM mobs AS b WHERE EXISTS (SELECT 1 FROM map m WHERE m.x = IRINT(b.x) AND m.y = IRINT(b.y) AND m.tile = '#') AND kind = 'bullet';

UPDATE players AS p SET hp = hp - 50
FROM collisions c
WHERE p.id = c.player_id;

UPDATE players AS p SET score = score + 1
FROM collisions c
WHERE p.id = c.bullet_owner
AND EXISTS (SELECT 1 FROM players p2 WHERE p2.id = c.player_id AND p2.hp <= 0);

DELETE FROM mobs
WHERE id IN (SELECT c.bullet_id FROM collisions c);

UPDATE mobs AS m
SET x = r.x, y = r.y, dir = 0
FROM players p
CROSS JOIN (
  SELECT x, y
  FROM map
  WHERE tile = 'R'
  ORDER BY (x * 31 + y * 17 + (SELECT tick FROM clock)) % 101, x, y
  LIMIT 1
) AS r
WHERE m.id = p.id
  AND p.hp <= 0;

UPDATE players AS p SET
  hp = 100,
  ammo = 10
FROM mobs m
WHERE p.id = m.id
AND p.hp <= 0;

COMMIT;

-- Periodically refill players' ammo (the epoch is tick / 30.0)
WITH d AS (
  SELECT
    p.id,
    CAST(FLOOR(((SELECT tick FROM clock) / 30.0 - p.last_ammo_refill) / c.ammo_refill_interval_seconds) AS INTEGER) AS steps
  FROM players p, config c
  WHERE p.ammo != c.ammo_max
)
UPDATE players AS p
SET
  ammo = max(0, min(c.ammo_max, p.ammo + d.steps)),
  last_ammo_refill = CAST(round((SELECT tick FROM clock) / 30.0) AS INTEGER)   -- (numeric::int: half away from zero)
FROM d, config c
WHERE p.id = d.id
  AND d.steps > 0;

UPDATE inputs AS i SET action = '';
