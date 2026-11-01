-- Not upstream: the index SQLite needs for the plan Postgres gets without one (PORT.md). SQLite builds no automatic
-- index for the correlated NOT EXISTS (SELECT 1 FROM map ...) inside the raytrace's recursive step, so without it every
-- ray step scans all 2048 map tiles. Results are the same either way (the 'noindex' variant leaves this file out).
CREATE INDEX IF NOT EXISTS map_xy ON map(x, y);
