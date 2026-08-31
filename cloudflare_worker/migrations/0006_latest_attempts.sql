CREATE TABLE IF NOT EXISTS availability_latest_attempt (
    arrival_date TEXT PRIMARY KEY,
    departure_date TEXT NOT NULL,
    status TEXT NOT NULL,
    price TEXT,
    total TEXT,
    url TEXT NOT NULL,
    detail TEXT,
    checked_at TEXT NOT NULL,
    agent_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_latest_attempt_checked
    ON availability_latest_attempt(checked_at, arrival_date);
