CREATE TABLE IF NOT EXISTS monitored_dates (
    arrival_date TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_current (
    arrival_date TEXT PRIMARY KEY,
    departure_date TEXT NOT NULL,
    status TEXT NOT NULL,
    price TEXT,
    total TEXT,
    url TEXT NOT NULL,
    detail TEXT,
    first_seen_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    agent_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    arrival_date TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    old_price TEXT,
    new_price TEXT,
    old_total TEXT,
    new_total TEXT,
    url TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    UNIQUE(scan_id, arrival_date)
);
CREATE INDEX IF NOT EXISTS idx_availability_events_date
    ON availability_events(observed_at, arrival_date);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    total_nights INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    baseline_count INTEGER NOT NULL,
    change_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_completed
    ON scan_runs(completed_at, id);

CREATE TABLE IF NOT EXISTS monitor_lease (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    agent_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    utterance TEXT,
    response_status INTEGER NOT NULL,
    response_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_request_received
    ON api_request_log(received_at, id);
