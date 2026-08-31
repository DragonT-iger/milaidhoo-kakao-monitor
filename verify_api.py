from __future__ import annotations

import json
import sqlite3

from kakao_skill_server import (
    DATABASE_PATH,
    daily_changes,
    daily_scans,
    ensure_api_schema,
    kakao_response,
    kakao_watch_list_text,
    latest_status,
)


connection = sqlite3.connect(DATABASE_PATH)
connection.row_factory = sqlite3.Row
ensure_api_schema(connection)
status_payload = latest_status(connection)
changes_payload = daily_changes(connection, "2026-08-08")
scans_payload = daily_scans(connection, "2026-08-08")
kakao_payload = kakao_response(kakao_watch_list_text(connection))
kakao_text = kakao_payload["template"]["outputs"][0]["simpleText"]["text"]

assert status_payload["monitored_count"] == len(status_payload["nights"])
assert len(scans_payload["scans"]) >= 1
assert len(kakao_text) <= 1000

print(
    json.dumps(
        {
            "database_exists": DATABASE_PATH.exists(),
            "monitored_dates": status_payload["monitored_count"],
            "available_monitored_dates": sum(night["available"] for night in status_payload["nights"]),
            "event_count": len(changes_payload["events"]),
            "scan_count": len(scans_payload["scans"]),
            "kakao_response_chars": len(kakao_text),
            "kakao_text": kakao_text,
        },
        ensure_ascii=False,
        indent=2,
    )
)
