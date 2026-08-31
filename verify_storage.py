from __future__ import annotations

import json
import sqlite3


connection = sqlite3.connect("hilton_monitor_stats.sqlite3")
connection.row_factory = sqlite3.Row
data = {
    "monitored_dates": connection.execute("SELECT COUNT(*) FROM monitored_dates").fetchone()[0],
    "current": connection.execute("SELECT COUNT(*) FROM availability_current").fetchone()[0],
    "events": connection.execute("SELECT COUNT(*) FROM availability_events").fetchone()[0],
    "baseline": connection.execute(
        "SELECT COUNT(*) FROM availability_events WHERE event_type = 'baseline'"
    ).fetchone()[0],
    "changes": connection.execute(
        "SELECT COUNT(*) FROM availability_events WHERE event_type = 'change'"
    ).fetchone()[0],
    "scans": connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0],
    "last_scan": dict(
        connection.execute(
            """
            SELECT id, started_at, completed_at, duration_ms, total_nights,
                   success_count, error_count, baseline_count, change_count
            FROM scan_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    ),
    "available_dates": [
        row[0]
        for row in connection.execute(
            """
            SELECT arrival_date
            FROM availability_current
            WHERE status = 'available'
            ORDER BY arrival_date
            """
        )
    ],
}
print(json.dumps(data, ensure_ascii=False, indent=2))
