-- G-reality schema v1 (DRAFT from Chunk 0: reviewed, NOT yet applied to any Neon branch).
-- Applied in Chunk 1 by the migration runner to a development branch first.
-- Conventions: one row per authoritative fact; JSONB only for contract-validated documents;
-- revisions are assigned inside the mutation transaction while holding the worlds row lock.

BEGIN;

CREATE TABLE schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE worlds (
    world_id          text PRIMARY KEY,
    name              text NOT NULL,
    seed              bigint NOT NULL CHECK (seed >= 0),
    revision          bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    generator_version integer NOT NULL DEFAULT 1,
    region_size_m     double precision NOT NULL DEFAULT 64,
    contract_version  text NOT NULL,
    status            text NOT NULL,
    spec              jsonb NOT NULL,          -- WorldSpec (terrain, features, prompt)
    environment       jsonb NOT NULL,          -- EnvironmentState
    gameplay          jsonb NOT NULL,          -- GameplayState (tick, counters)
    sim_clock_at      timestamptz NOT NULL DEFAULT now(),  -- wall time corresponding to gameplay.tick
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE geometry_definitions (
    world_id     text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    geometry_id  text NOT NULL,
    version      integer NOT NULL,
    definition   jsonb NOT NULL,               -- GeometryDefinition (validated SDF AST)
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, geometry_id, version)
);

CREATE TABLE world_regions (
    world_id          text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    rx                integer NOT NULL,
    rz                integer NOT NULL,
    revision          bigint NOT NULL DEFAULT 0,
    materialized      boolean NOT NULL DEFAULT false,
    generator_version integer NOT NULL DEFAULT 1,
    smoke_density     real NOT NULL DEFAULT 0,
    hazard_level      real NOT NULL DEFAULT 0,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, rx, rz)
);

CREATE TABLE entities (
    world_id         text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    entity_id        text NOT NULL,
    kind             text NOT NULL,
    geometry_id      text NOT NULL,
    geometry_params  jsonb NOT NULL DEFAULT '{}',
    px double precision NOT NULL, py double precision NOT NULL, pz double precision NOT NULL,
    qx double precision NOT NULL DEFAULT 0, qy double precision NOT NULL DEFAULT 0,
    qz double precision NOT NULL DEFAULT 0, qw double precision NOT NULL DEFAULT 1,
    scale            double precision NOT NULL DEFAULT 1,
    rx               integer NOT NULL,
    rz               integer NOT NULL,
    bound_radius     double precision NOT NULL,
    capabilities     text[] NOT NULL DEFAULT '{}',
    condition        text NOT NULL DEFAULT 'intact',   -- denormalized from state for indexed sim queries
    state            jsonb NOT NULL,                   -- EntityState
    origin           text NOT NULL DEFAULT 'generated',
    revision         bigint NOT NULL,
    created_revision bigint NOT NULL,
    PRIMARY KEY (world_id, entity_id),
    FOREIGN KEY (world_id, rx, rz) REFERENCES world_regions
);
CREATE INDEX entities_region_idx  ON entities (world_id, rx, rz);
CREATE INDEX entities_burning_idx ON entities (world_id) WHERE condition = 'burning';

CREATE TABLE players (
    world_id   text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    player_id  text NOT NULL,
    state      jsonb NOT NULL,                 -- PlayerState
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, player_id)
);

-- Idempotency + audit for every player command, including rejected ones.
CREATE TABLE commands (
    world_id       text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    command_id     text NOT NULL,
    player_id      text NOT NULL,
    text           text NOT NULL,
    intent         jsonb,                      -- ParsedIntent
    status         text NOT NULL,              -- committed | rejected | conflict | error
    result         jsonb NOT NULL,             -- MutationResult (returned verbatim on retry)
    world_revision bigint NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, command_id)
);

-- Append-only event log: exactly one event per world revision.
CREATE TABLE world_events (
    world_id        text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    revision        bigint NOT NULL CHECK (revision >= 1),
    event_id        text NOT NULL UNIQUE,
    tick            bigint NOT NULL,
    type            text NOT NULL,
    cause           text NOT NULL,             -- player | simulation | system
    command_id      text,
    parent_event_id text,
    entity_ids      text[] NOT NULL DEFAULT '{}',
    regions         jsonb NOT NULL DEFAULT '[]',
    bounds          jsonb,
    payload         jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, revision)
);
CREATE INDEX world_events_time_idx   ON world_events (world_id, created_at DESC);
CREATE INDEX world_events_entity_idx ON world_events USING gin (entity_ids);

-- History of every region revision bump (which event caused it).
CREATE TABLE region_revisions (
    world_id       text NOT NULL,
    rx             integer NOT NULL,
    rz             integer NOT NULL,
    revision       bigint NOT NULL,
    world_revision bigint NOT NULL,
    event_id       text,
    changed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, rx, rz, revision),
    FOREIGN KEY (world_id, rx, rz) REFERENCES world_regions ON DELETE CASCADE
);

-- Periodic full snapshots (replay accelerator + debugging); state tables remain the read path.
CREATE TABLE world_snapshots (
    world_id   text NOT NULL REFERENCES worlds ON DELETE CASCADE,
    revision   bigint NOT NULL,
    tick       bigint NOT NULL,
    snapshot   jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, revision)
);

CREATE TABLE neural_jobs (
    job_id                 text PRIMARY KEY,
    world_id               text NOT NULL,
    rx                     integer NOT NULL,
    rz                     integer NOT NULL,
    source_region_revision bigint NOT NULL,
    reason                 text NOT NULL,
    priority               double precision NOT NULL DEFAULT 0,
    state                  text NOT NULL,
    quality                text NOT NULL,
    attempt                integer NOT NULL DEFAULT 1,
    error                  text,
    metrics                jsonb,
    asset_id               text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    started_at             timestamptz,
    finished_at            timestamptz,
    FOREIGN KEY (world_id, rx, rz) REFERENCES world_regions ON DELETE CASCADE
);
-- At most one live job per region; a newer revision replaces it (old one -> superseded).
CREATE UNIQUE INDEX neural_jobs_live_idx ON neural_jobs (world_id, rx, rz)
    WHERE state IN ('queued', 'awaiting_views', 'training', 'validating');
CREATE INDEX neural_jobs_queue_idx ON neural_jobs (state, priority, created_at);

CREATE TABLE neural_assets (
    asset_id        text PRIMARY KEY,
    world_id        text NOT NULL,
    rx              integer NOT NULL,
    rz              integer NOT NULL,
    region_revision bigint NOT NULL,
    state           text NOT NULL,             -- active | superseded
    method          text NOT NULL,
    format          text NOT NULL,
    uri             text NOT NULL,             -- storage-adapter key; bytes live outside Postgres
    sha256          text NOT NULL,
    byte_size       bigint NOT NULL,
    gaussian_count  integer NOT NULL,
    manifest        jsonb NOT NULL,            -- full NeuralAssetManifest
    job_id          text NOT NULL REFERENCES neural_jobs,
    created_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (world_id, rx, rz) REFERENCES world_regions ON DELETE CASCADE,
    UNIQUE (world_id, rx, rz, region_revision)
);
CREATE UNIQUE INDEX neural_assets_active_idx ON neural_assets (world_id, rx, rz) WHERE state = 'active';

INSERT INTO schema_migrations (version) VALUES ('0001_init');

COMMIT;
