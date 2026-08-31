CREATE TABLE IF NOT EXISTS kakao_reply_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    utterance TEXT,
    response_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kakao_reply_outbox_id
    ON kakao_reply_outbox(id);
