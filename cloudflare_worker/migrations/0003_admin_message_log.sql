ALTER TABLE api_request_log ADD COLUMN user_key TEXT;
ALTER TABLE api_request_log ADD COLUMN response_text TEXT;

CREATE INDEX IF NOT EXISTS idx_api_request_user_received
    ON api_request_log(user_key, received_at, id);
