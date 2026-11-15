-- G-reality schema v2: cache of CC0 photo texture sets (Poly Haven), filled on first use, shared by all worlds.
-- Worlds store only the asset identifier and parameters (MaterialDefinition.texture); bytes live here, so a world
-- reopens with exactly the same surfaces without contacting the source again.

BEGIN;

CREATE TABLE texture_assets (
    asset_id    text PRIMARY KEY,                    -- e.g. polyhaven:rock_wall_08
    source      text NOT NULL CHECK (source = 'polyhaven'),
    license     text NOT NULL CHECK (license = 'CC0-1.0'),
    name        text NOT NULL,
    page_url    text NOT NULL,
    authors     jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE texture_maps (
    asset_id    text NOT NULL REFERENCES texture_assets ON DELETE CASCADE,
    map         text NOT NULL CHECK (map IN ('albedo', 'normal', 'arm')),
    resolution  text NOT NULL,                       -- '1k'
    mime        text NOT NULL,
    sha256      text NOT NULL,
    source_url  text NOT NULL,
    data        bytea NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_id, map)
);

INSERT INTO schema_migrations (version) VALUES ('0002_texture_cache');

COMMIT;
