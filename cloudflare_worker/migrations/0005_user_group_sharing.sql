CREATE TABLE IF NOT EXISTS user_group_share_settings (
    user_key TEXT PRIMARY KEY,
    share_enabled INTEGER NOT NULL CHECK (share_enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);

ALTER TABLE api_request_log ADD COLUMN shared_to_group INTEGER;
