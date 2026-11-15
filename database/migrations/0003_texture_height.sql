-- G-reality schema v3: texture sets may also cache a height (displacement) map, used for cavity shading.

BEGIN;

ALTER TABLE texture_maps DROP CONSTRAINT texture_maps_map_check;
ALTER TABLE texture_maps ADD CONSTRAINT texture_maps_map_check CHECK (map IN ('albedo', 'normal', 'arm', 'height'));

INSERT INTO schema_migrations (version) VALUES ('0003_texture_height');

COMMIT;
