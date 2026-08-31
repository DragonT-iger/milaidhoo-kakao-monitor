CREATE TABLE IF NOT EXISTS kakao_queue_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_consumed_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    agent_id TEXT NOT NULL
);

INSERT OR IGNORE INTO kakao_queue_state (
    id, last_consumed_id, updated_at, agent_id
) VALUES (1, 0, CURRENT_TIMESTAMP, 'initial');
