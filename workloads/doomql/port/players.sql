-- The fixed cast for the scripted runs (the upstream client inserts one player per terminal, at (4, 4) facing east).
-- Player 1 starts there; player 2 across room A on the same row, facing west, so each sees the other's sprite.
INSERT INTO mobs(kind, x, y, dir, name, sprite_id, minimap_icon) VALUES ('player', 4, 4, 0, 'alice', 0, 'A');
INSERT INTO players(id, last_ammo_refill) VALUES (1, 0);
INSERT INTO mobs(kind, x, y, dir, name, sprite_id, minimap_icon) VALUES ('player', 12, 4, pi(), 'bob', 0, 'B');
INSERT INTO players(id, last_ammo_refill) VALUES (2, 0);
INSERT INTO inputs(player_id, action) VALUES (1, ''), (2, '');
