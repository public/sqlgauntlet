-- DOOMQL (c) 2025 Lukas Vogel, MIT licence (src/cedardb-doomql/LICENSE): gamestate.sql ported to SQLite.
-- Rewrites (PORT.md): NUMERIC -> REAL; CHAR -> TEXT; SERIAL / GENERATED ALWAYS AS IDENTITY -> INTEGER PRIMARY KEY
-- AUTOINCREMENT (identity values are never reused, as in Postgres); now() -> the deterministic clock table (30 ticks a
-- second, advanced by tick.sql); PI() -> pi(); float-to-int casts round half to even as Postgres's do (IRINT).

DROP TABLE IF EXISTS clock;
CREATE TABLE clock(tick INT NOT NULL);
INSERT INTO clock VALUES (0);

DROP TABLE IF EXISTS config;
CREATE TABLE config(
  player_move_speed REAL DEFAULT 0.3,
  player_turn_speed REAL DEFAULT 0.2,
  ammo_max INT DEFAULT 10,
  ammo_refill_interval_seconds INT DEFAULT 2);

insert into config (player_move_speed, player_turn_speed, ammo_max, ammo_refill_interval_seconds) values (0.3, 0.2, 10, 2);

DROP TABLE IF EXISTS map;
CREATE TABLE map(x INT, y INT, tile TEXT);

DROP TABLE IF EXISTS inputs;
CREATE TABLE inputs(
  player_id INT PRIMARY KEY,
  action TEXT,
  timestamp INT DEFAULT 0
);

DROP TABLE IF EXISTS settings;
CREATE TABLE settings(fov DOUBLE PRECISION, step DOUBLE PRECISION, max_steps INT, view_w INT, view_h INT);
INSERT INTO settings VALUES (pi()/3, 0.1, 100, 128, 64);

CREATE TABLE IF NOT EXISTS sprites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  w INT NOT NULL,
  h INT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprite_pixels (
  sprite_id INT REFERENCES sprites(id),
  sx INT NOT NULL,
  sy INT NOT NULL,
  ch TEXT,
  PRIMARY KEY (sprite_id, sx, sy)
);

CREATE TABLE IF NOT EXISTS mobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  owner INT,
  x DOUBLE PRECISION NOT NULL,
  y DOUBLE PRECISION NOT NULL,
  dir DOUBLE PRECISION DEFAULT 0,
  world_w DOUBLE PRECISION default 1,
  world_h DOUBLE PRECISION default 1,
  name TEXT,
  sprite_id INT REFERENCES sprites(id),
  minimap_icon TEXT
);

CREATE TABLE IF NOT EXISTS players (
  id INT REFERENCES mobs(id),
  score INT DEFAULT 0,
  hp INT DEFAULT 100,
  ammo INT DEFAULT 10,
  last_ammo_refill int default 0   -- (Postgres: the epoch at insert; players.sql sets it from the clock)
);

DROP VIEW IF EXISTS collisions;
CREATE VIEW collisions AS
  SELECT m.id AS bullet_id,
    m.owner as bullet_owner,
    p.id AS player_id,
    p_m.x AS player_x,
    p_m.y AS player_y
  FROM mobs m, players p, mobs p_m
  WHERE IRINT(m.x) = IRINT(p_m.x)
  AND IRINT(m.y) = IRINT(p_m.y)
  AND m.kind = 'bullet'
  AND p_m.id = p.id
  AND m.owner != p.id;
