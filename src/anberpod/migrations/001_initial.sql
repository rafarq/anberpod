CREATE TABLE podcast (
    id TEXT PRIMARY KEY,
    feed_url TEXT NOT NULL UNIQUE,
    catalog_id INTEGER,
    title TEXT NOT NULL,
    author TEXT,
    description TEXT,
    image_url TEXT,
    language TEXT,
    etag TEXT,
    last_modified TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE subscription (
    podcast_id TEXT PRIMARY KEY REFERENCES podcast(id) ON DELETE CASCADE,
    subscribed_at TEXT NOT NULL
);
CREATE TABLE episode (
    id TEXT PRIMARY KEY,
    podcast_id TEXT NOT NULL REFERENCES podcast(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    guid TEXT,
    media_url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    published_at TEXT,
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
    media_length_bytes INTEGER CHECK(media_length_bytes IS NULL OR media_length_bytes >= 0),
    media_type TEXT,
    image_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(podcast_id, source_key)
);
CREATE TABLE playback (
    episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE,
    position_ms INTEGER NOT NULL DEFAULT 0 CHECK(position_ms >= 0),
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE download (
    episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('queued', 'downloading', 'complete', 'failed')),
    relative_path TEXT,
    temp_relative_path TEXT,
    bytes_received INTEGER NOT NULL DEFAULT 0 CHECK(bytes_received >= 0),
    bytes_total INTEGER CHECK(bytes_total IS NULL OR bytes_total >= 0),
    etag TEXT,
    last_modified TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK(state != 'complete' OR (relative_path IS NOT NULL AND temp_relative_path IS NULL))
);
CREATE TABLE catalog_cache (
    cache_key TEXT PRIMARY KEY,
    payload_relative_path TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT
);
CREATE TABLE setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
