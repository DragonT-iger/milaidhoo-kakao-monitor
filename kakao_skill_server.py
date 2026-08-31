from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
API_LOG_PATH = APP_DIR / "kakao_skill_api.log"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_config()
HOTEL = CONFIG["hotel"]
API_CONFIG = CONFIG.get("api", {})
STATISTICS_CONFIG = CONFIG.get("statistics", {})
DATABASE_PATH = APP_DIR / STATISTICS_CONFIG.get("database_path", "hilton_monitor_stats.sqlite3")
STALE_AFTER_SECONDS = int(API_CONFIG.get("stale_after_seconds", 180))


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("kakao-skill-api")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    rotating = RotatingFileHandler(
        API_LOG_PATH,
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    logger.addHandler(rotating)
    return logger


LOGGER = setup_logger()
app = FastAPI(
    title="Milaidhoo Availability API",
    version="1.0.0",
    description="Hilton 감시기가 적재한 객실 상태와 통계를 제공하는 API 및 카카오 챗봇 스킬 서버",
)


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    if not DATABASE_PATH.exists():
        raise HTTPException(status_code=503, detail="통계 데이터베이스가 아직 생성되지 않았습니다.")
    connection = sqlite3.connect(DATABASE_PATH, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        ensure_api_schema(connection)
        yield connection
    finally:
        connection.close()


def ensure_api_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            user_hash TEXT,
            utterance TEXT,
            response_status INTEGER NOT NULL,
            response_ms INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS monitored_dates (
            hotel_code TEXT NOT NULL,
            room_name TEXT NOT NULL,
            arrival_date TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (hotel_code, room_name, arrival_date)
        );
        CREATE INDEX IF NOT EXISTS idx_monitored_dates_arrival
            ON monitored_dates(arrival_date);
        """
    )
    connection.commit()


def authenticate(api_key: str | None) -> None:
    expected = os.environ.get("KAKAO_SKILL_API_KEY", "")
    if expected and not hmac.compare_digest(api_key or "", expected):
        raise HTTPException(status_code=403, detail="잘못된 API 키입니다.")


def hash_user_id(user_id: str | None) -> str | None:
    if not user_id:
        return None
    salt = os.environ.get("KAKAO_USER_HASH_SALT", "milaidhoo-local")
    return hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()


def latest_status(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = list(
        connection.execute(
            """
            SELECT
                monitored.arrival_date,
                monitored.added_at,
                current.last_checked_at AS checked_at,
                current.last_changed_at,
                current.departure_date,
                current.status,
                current.price,
                current.total,
                current.url,
                current.detail
            FROM monitored_dates AS monitored
            LEFT JOIN availability_current AS current
              ON current.hotel_code = monitored.hotel_code
             AND current.room_name = monitored.room_name
             AND current.arrival_date = monitored.arrival_date
            WHERE monitored.hotel_code = ? AND monitored.room_name = ?
            ORDER BY monitored.arrival_date
            """,
            (HOTEL["hotel_code"], HOTEL["room_name"]),
        )
    )

    last_scan = connection.execute(
        """
        SELECT completed_at, duration_ms, total_nights, success_count, error_count,
               baseline_count, change_count
        FROM scan_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    checked_values = [row["checked_at"] for row in rows if row["checked_at"]]
    checked_at = max(checked_values) if checked_values else None
    age_seconds = None
    if checked_at:
        checked_datetime = datetime.fromisoformat(checked_at)
        age_seconds = max(0, int((datetime.now().astimezone() - checked_datetime).total_seconds()))
    nights = [
        {
            "arrival_date": row["arrival_date"],
            "departure_date": row["departure_date"],
            "status": row["status"] or "unknown",
            "available": row["status"] == "available",
            "price": row["price"],
            "total": row["total"],
            "url": row["url"],
            "added_at": row["added_at"],
            "last_checked_at": row["checked_at"],
            "last_changed_at": row["last_changed_at"],
            "detail": row["detail"] if row["status"] not in (None, "available", "sold_out") else None,
        }
        for row in rows
    ]
    return {
        "hotel": HOTEL.get("display_name", HOTEL["hotel_slug"]),
        "room": HOTEL["room_name"],
        "checked_at": checked_at,
        "age_seconds": age_seconds,
        "stale": age_seconds is not None and age_seconds > STALE_AFTER_SECONDS,
        "last_scan": dict(last_scan) if last_scan else None,
        "monitored_count": len(nights),
        "nights": nights,
    }


def parse_report_date(report_date: str) -> str:
    try:
        return date.fromisoformat(report_date).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date는 YYYY-MM-DD 형식이어야 합니다.") from exc


def daily_changes(connection: sqlite3.Connection, report_date: str) -> dict[str, Any]:
    parsed_date = parse_report_date(report_date)
    summary = connection.execute(
        """
        SELECT
            COUNT(*) AS scan_count,
            COALESCE(SUM(total_nights), 0) AS total_nights,
            COALESCE(SUM(success_count), 0) AS success_count,
            COALESCE(SUM(error_count), 0) AS error_count,
            COALESCE(SUM(baseline_count), 0) AS baseline_count,
            COALESCE(SUM(change_count), 0) AS change_count,
            COALESCE(AVG(duration_ms), 0) AS average_duration_ms,
            COALESCE(MAX(duration_ms), 0) AS maximum_duration_ms
        FROM scan_runs
        WHERE local_date = ?
        """,
        (parsed_date,),
    ).fetchone()
    events = list(
        connection.execute(
            """
            SELECT
                id, observed_at, event_type, arrival_date, departure_date,
                old_status, new_status, old_price, new_price,
                old_total, new_total, url
            FROM availability_events
            WHERE local_date = ?
            ORDER BY id
            """,
            (parsed_date,),
        )
    )
    return {
        "date": parsed_date,
        "hotel": HOTEL.get("display_name", HOTEL["hotel_slug"]),
        "room": HOTEL["room_name"],
        "summary": dict(summary),
        "events": [dict(row) for row in events],
    }


def daily_scans(connection: sqlite3.Connection, report_date: str) -> dict[str, Any]:
    parsed_date = parse_report_date(report_date)
    rows = list(
        connection.execute(
            """
            SELECT
                id, started_at, completed_at, duration_ms, total_nights,
                success_count, error_count, baseline_count, change_count
            FROM scan_runs
            WHERE local_date = ?
            ORDER BY id
            """,
            (parsed_date,),
        )
    )
    return {"date": parsed_date, "scans": [dict(row) for row in rows]}


def hourly_statistics(connection: sqlite3.Connection, report_date: str) -> dict[str, Any]:
    parsed_date = date.fromisoformat(parse_report_date(report_date))

    rows = list(
        connection.execute(
            """
            SELECT
                local_hour,
                arrival_date,
                COUNT(*) AS total_checks,
                SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available_checks,
                SUM(CASE WHEN status = 'sold_out' THEN 1 ELSE 0 END) AS sold_out_checks,
                SUM(CASE WHEN status NOT IN ('available', 'sold_out') THEN 1 ELSE 0 END) AS error_checks,
                MIN(price) AS minimum_price,
                MAX(price) AS maximum_price
            FROM room_checks
            WHERE local_date = ?
            GROUP BY local_hour, arrival_date
            ORDER BY local_hour, arrival_date
            """,
            (parsed_date.isoformat(),),
        )
    )
    return {
        "date": parsed_date.isoformat(),
        "hotel": HOTEL.get("display_name", HOTEL["hotel_slug"]),
        "room": HOTEL["room_name"],
        "rows": [dict(row) for row in rows],
    }


def status_symbol(status: str) -> str:
    return {
        "available": "✅ 가능",
        "sold_out": "❌ 매진",
        "error": "⚠️ 오류",
        "unknown": "❔ 확인 불가",
    }.get(status, "❔ 확인 불가")


def parse_arrival_date(value: str, *, future_only: bool = False) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("존재하는 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.") from exc
    if future_only and parsed < datetime.now().astimezone().date():
        raise ValueError("오늘 이전 날짜는 감시 등록할 수 없습니다.")
    return parsed.isoformat()


def monitored_dates(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT arrival_date, added_at
            FROM monitored_dates
            WHERE hotel_code = ? AND room_name = ?
            ORDER BY arrival_date
            """,
            (HOTEL["hotel_code"], HOTEL["room_name"]),
        )
    )


def add_monitored_date(connection: sqlite3.Connection, arrival_date: str) -> bool:
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO monitored_dates (
                hotel_code, room_name, arrival_date, added_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                HOTEL["hotel_code"],
                HOTEL["room_name"],
                arrival_date,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
    return cursor.rowcount == 1


def remove_monitored_date(connection: sqlite3.Connection, arrival_date: str) -> bool:
    with connection:
        cursor = connection.execute(
            """
            DELETE FROM monitored_dates
            WHERE hotel_code = ? AND room_name = ? AND arrival_date = ?
            """,
            (HOTEL["hotel_code"], HOTEL["room_name"], arrival_date),
        )
    return cursor.rowcount == 1


def kakao_watch_list_text(connection: sqlite3.Connection) -> str:
    rows = monitored_dates(connection)
    if not rows:
        return "[객실 감시 목록]\n등록된 날짜가 없습니다."
    lines = [f"[객실 감시 목록 · {len(rows)}개]"]
    lines.extend(f"- {row['arrival_date']}" for row in rows)
    return "\n".join(lines)[:1000]


def kakao_date_status_text(connection: sqlite3.Connection, arrival_date: str) -> str:
    watched = connection.execute(
        """
        SELECT 1
        FROM monitored_dates
        WHERE hotel_code = ? AND room_name = ? AND arrival_date = ?
        """,
        (HOTEL["hotel_code"], HOTEL["room_name"], arrival_date),
    ).fetchone()
    row = connection.execute(
        """
        SELECT status, price, total, url, detail, last_checked_at, last_changed_at
        FROM availability_current
        WHERE hotel_code = ? AND room_name = ? AND arrival_date = ?
        """,
        (HOTEL["hotel_code"], HOTEL["room_name"], arrival_date),
    ).fetchone()
    lines = [
        f"[객실 조회 · {arrival_date}]",
        f"감시 상태: {'등록됨' if watched else '미등록'}",
    ]
    if row is None:
        lines.append("저장된 조회 결과가 없습니다.")
        if watched:
            lines.append("다음 감시 주기부터 확인합니다.")
        return "\n".join(lines)

    lines.append(f"최근 결과: {status_symbol(row['status'])}")
    if row["price"]:
        lines.append(f"1박 요금: {row['price']}")
    if row["total"] and row["total"] != row["price"]:
        lines.append(f"총액: {row['total']}")
    lines.append(f"조회 시각: {row['last_checked_at']}")
    checked_at = datetime.fromisoformat(row["last_checked_at"])
    age_seconds = max(0, int((datetime.now().astimezone() - checked_at).total_seconds()))
    if age_seconds > STALE_AFTER_SECONDS:
        lines.append(f"⚠️ 최신 조회가 {age_seconds}초 전입니다.")
    if row["status"] not in ("available", "sold_out") and row["detail"]:
        lines.append(f"상세: {row['detail']}")
    return "\n".join(lines)[:1000]


def kakao_command_help() -> str:
    return "\n".join(
        [
            "[객실 명령 안내]",
            "객실 감시 YYYY-MM-DD",
            "객실 감시 해제 YYYY-MM-DD",
            "객실 감시 목록",
            "객실 조회 YYYY-MM-DD",
            "일일 변경 보고 [YYYY-MM-DD]",
        ]
    )


def handle_kakao_command(connection: sqlite3.Connection, utterance: str) -> str:
    command = " ".join(utterance.split())
    if command == "객실 감시":
        return "\n".join(
            [
                "[객실 감시 등록]",
                "감시할 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.",
                "예: 객실 감시 2027-03-17",
            ]
        )
    if command == "객실 감시 해제":
        return "\n".join(
            [
                "[객실 감시 해제]",
                "해제할 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.",
                "예: 객실 감시 해제 2027-03-17",
            ]
        )
    if command == "객실 감시 목록":
        return kakao_watch_list_text(connection)
    if command == "일일 변경 보고":
        return kakao_changes_text(daily_changes(connection, date.today().isoformat()))

    match = re.fullmatch(r"일일 변경 보고 (.+)", command)
    if match:
        try:
            report_date = parse_arrival_date(match.group(1))
        except ValueError as exc:
            return f"입력 오류: {exc}\n\n{kakao_command_help()}"
        return kakao_changes_text(daily_changes(connection, report_date))

    match = re.fullmatch(r"객실 감시 해제 (.+)", command)
    if match:
        try:
            arrival_date = parse_arrival_date(match.group(1))
        except ValueError as exc:
            return f"입력 오류: {exc}\n\n{kakao_command_help()}"
        if remove_monitored_date(connection, arrival_date):
            return f"객실 감시를 해제했습니다.\n날짜: {arrival_date}"
        return f"감시 중인 날짜가 아닙니다.\n날짜: {arrival_date}"

    match = re.fullmatch(r"객실 감시 (.+)", command)
    if match:
        try:
            arrival_date = parse_arrival_date(match.group(1), future_only=True)
        except ValueError as exc:
            return f"입력 오류: {exc}\n\n{kakao_command_help()}"
        if add_monitored_date(connection, arrival_date):
            return f"객실 감시를 등록했습니다.\n날짜: {arrival_date}\n다음 감시 주기부터 확인합니다."
        return f"이미 감시 중인 날짜입니다.\n날짜: {arrival_date}"

    match = re.fullmatch(r"객실 조회 (.+)", command)
    if match:
        try:
            arrival_date = parse_arrival_date(match.group(1))
        except ValueError as exc:
            return f"입력 오류: {exc}\n\n{kakao_command_help()}"
        return kakao_date_status_text(connection, arrival_date)

    return kakao_command_help()


def kakao_status_text(payload: dict[str, Any]) -> str:
    if not payload["nights"]:
        return "[객실 감시 목록]\n등록된 날짜가 없습니다."
    lines = [f"[객실 감시 현황 · {payload['monitored_count']}개]"]
    for night in payload["nights"]:
        price = f" · {night['price']}" if night["price"] else ""
        lines.append(f"- {night['arrival_date']} {status_symbol(night['status'])}{price}")
    if payload["stale"]:
        lines.append(f"⚠️ 최신 조회가 {payload['age_seconds']}초 전입니다.")
    return "\n".join(lines)[:1000]


def aggregate_symbol(row: dict[str, Any] | None) -> str:
    if row is None:
        return "?"
    available = int(row["available_checks"] or 0)
    sold_out = int(row["sold_out_checks"] or 0)
    if available and sold_out:
        return "△"
    if available:
        return "O"
    if sold_out:
        return "X"
    return "?"


def kakao_changes_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    changes = [event for event in payload["events"] if event["event_type"] == "change"]
    lines = [
        f"[밀라이두 일일 변경 보고 · {payload['date']}]",
        f"스캔 {int(summary['scan_count']):,}회",
        f"성공/오류 {int(summary['success_count']):,}/{int(summary['error_count']):,}건",
        f"평균 소요 {float(summary['average_duration_ms']) / 1000:.1f}초",
        f"초기값 {int(summary['baseline_count']):,}건 / 변경 {len(changes):,}건",
        "",
    ]
    if not changes:
        lines.append("예약 상태 또는 가격 변경 없음")
    else:
        for event in changes[:20]:
            observed = datetime.fromisoformat(event["observed_at"]).strftime("%H:%M")
            arrival = date.fromisoformat(event["arrival_date"]).strftime("%m/%d")
            price = ""
            if event["old_price"] != event["new_price"]:
                price = f" ({event['old_price'] or '-'}→{event['new_price'] or '-'})"
            lines.append(
                f"{observed} {arrival} {event['old_status'] or '-'}→{event['new_status']}{price}"
            )
        if len(changes) > 20:
            lines.append(f"외 {len(changes) - 20}건")
    return "\n".join(lines)[:1000]


def kakao_response(text: str) -> dict[str, Any]:
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text[:1000]}}],
            "quickReplies": [
                {"label": "객실 감시", "action": "message", "messageText": "객실 감시"},
                {"label": "감시 해제", "action": "message", "messageText": "객실 감시 해제"},
                {"label": "감시 목록", "action": "message", "messageText": "객실 감시 목록"},
                {"label": "일일 변경 보고", "action": "message", "messageText": "일일 변경 보고"},
            ],
        },
    }


def extract_kakao_request(payload: dict[str, Any]) -> tuple[str, str | None]:
    user_request = payload.get("userRequest") or {}
    utterance = str(user_request.get("utterance") or "").strip()
    user = user_request.get("user") or {}
    user_id = user.get("id")
    return utterance, str(user_id) if user_id else None


def record_api_request(
    connection: sqlite3.Connection,
    endpoint: str,
    user_id: str | None,
    utterance: str,
    response_status: int,
    started_at: float,
) -> None:
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    with connection:
        connection.execute(
            """
            INSERT INTO api_request_log (
                received_at, endpoint, user_hash, utterance, response_status, response_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                endpoint,
                hash_user_id(user_id),
                utterance[:500],
                response_status,
                elapsed_ms,
            ),
        )
    LOGGER.info("%s status=%s duration_ms=%s utterance=%r", endpoint, response_status, elapsed_ms, utterance[:100])


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "database_exists": DATABASE_PATH.exists(),
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.get("/api/v1/status")
def api_status(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    authenticate(x_api_key)
    with database() as connection:
        return latest_status(connection)


@app.get("/api/v1/statistics/hourly")
def api_hourly_statistics(
    date_value: str | None = Query(default=None, alias="date"),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    authenticate(x_api_key)
    report_date = date_value or datetime.now().astimezone().date().isoformat()
    with database() as connection:
        return hourly_statistics(connection, report_date)


@app.get("/api/v1/changes")
def api_daily_changes(
    date_value: str | None = Query(default=None, alias="date"),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    authenticate(x_api_key)
    report_date = date_value or datetime.now().astimezone().date().isoformat()
    with database() as connection:
        return daily_changes(connection, report_date)


@app.get("/api/v1/scans")
def api_daily_scans(
    date_value: str | None = Query(default=None, alias="date"),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    authenticate(x_api_key)
    report_date = date_value or datetime.now().astimezone().date().isoformat()
    with database() as connection:
        return daily_scans(connection, report_date)


@app.post("/kakao/skill")
def kakao_skill(
    payload: dict[str, Any],
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    started_at = time.monotonic()
    authenticate(x_api_key)
    utterance, user_id = extract_kakao_request(payload)
    endpoint = "/kakao/skill"
    with database() as connection:
        try:
            response = kakao_response(handle_kakao_command(connection, utterance))
            record_api_request(connection, endpoint, user_id, utterance, 200, started_at)
            return response
        except HTTPException as exc:
            record_api_request(connection, endpoint, user_id, utterance, exc.status_code, started_at)
            raise
