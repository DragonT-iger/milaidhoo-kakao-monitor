from __future__ import annotations

"""Hilton 감시기 실행 래퍼.

기존 Python 3.12 바이트코드에 보존된 조회/알림 기능을 로드하고,
Cloudflare Workers + D1 중앙 제어 기능을 덧붙입니다.
"""

from dataclasses import asdict
from datetime import date, datetime, timedelta
import calendar
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid


APP_DIR = Path(__file__).resolve().parent
LEGACY_PYC_PATH = APP_DIR / "hilton_kakao_monitor.cpython-312.recovery-backup.pyc"
KAKAO_DRAFT_CURSOR_PATH = APP_DIR / "kakao_draft_cursor.json"
_COMMAND_DRAFT_BLOCKED_TEXT = ""
_COMMAND_DRAFT_BLOCKED_REPLY_ID = 0
_COMMAND_DRAFT_BLOCKED_SINCE = 0.0
_LEASE_HEARTBEAT_SECONDS = 300.0


def _load_legacy_module() -> Any:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(
            "이 복구 래퍼는 Python 3.12가 필요합니다. "
            f"현재 버전: {sys.version_info.major}.{sys.version_info.minor}"
        )
    if not LEGACY_PYC_PATH.exists():
        raise RuntimeError(f"기존 감시기 바이트코드가 없습니다: {LEGACY_PYC_PATH}")
    module_name = "_hilton_monitor_legacy"
    loader = importlib.machinery.SourcelessFileLoader(
        module_name,
        str(LEGACY_PYC_PATH),
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise RuntimeError("기존 감시기 모듈 사양을 만들지 못했습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


_legacy = _load_legacy_module()

for _name in dir(_legacy):
    if not _name.startswith("_"):
        globals().setdefault(_name, getattr(_legacy, _name))

RoomResult = _legacy.RoomResult
StatisticsStore = _legacy.StatisticsStore
LOGGER = _legacy.LOGGER
Browser = _legacy.Browser


class RemoteAgentClient:
    def __init__(self, config: dict[str, Any]) -> None:
        remote = config.get("remote_agent", {})
        self.base_url = str(remote.get("base_url", "")).strip().rstrip("/")
        if not self.base_url:
            raise ValueError("remote_agent.base_url이 비어 있습니다.")
        if not (
            self.base_url.startswith("https://")
            or self.base_url.startswith("http://127.0.0.1")
            or self.base_url.startswith("http://localhost")
        ):
            raise ValueError("원격 API 주소는 HTTPS여야 합니다.")

        key_env = str(remote.get("api_key_env", "AGENT_API_KEY")).strip()
        self.api_key = os.environ.get(key_env, "").strip()
        if not self.api_key:
            raise ValueError(f"환경변수 {key_env}에 에이전트 API 키를 설정해 주세요.")

        requested_id = str(remote.get("agent_id") or socket.gethostname()).strip()
        self.agent_id = re.sub(r"[^A-Za-z0-9._-]", "-", requested_id)[:80]
        if not self.agent_id:
            raise ValueError("remote_agent.agent_id가 올바르지 않습니다.")
        self.timeout_seconds = max(3, int(remote.get("timeout_seconds", 15)))

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {
            "accept": "application/json",
            "x-agent-key": self.api_key,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json; charset=utf-8"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"중앙 API 오류 HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"중앙 API 연결 실패: {exc.reason}") from exc

        parsed = json.loads(body or "{}")
        if not isinstance(parsed, dict):
            raise RuntimeError("중앙 API가 올바르지 않은 JSON을 반환했습니다.")
        return parsed

    def acquire_lease(self) -> dict[str, Any]:
        return self._request(
            "/api/v1/agent/lease",
            method="POST",
            payload={"agent_id": self.agent_id},
        )

    def watch_dates(self) -> list[dict[str, str]]:
        payload = self._request("/api/v1/agent/watch-dates")
        return self._clean_watch_dates(payload.get("watch_dates", []))

    def kakao_queue_cursor(self) -> int:
        payload = self._request("/api/v1/agent/kakao-queue/cursor")
        return max(0, int(payload.get("last_consumed_id", 0)))

    def kakao_replies(self, after_id: int) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "after_id": max(0, int(after_id)),
                "agent_id": self.agent_id,
            }
        )
        payload = self._request(
            f"/api/v1/agent/kakao-replies?{query}"
        )
        raw_replies = payload.get("replies", [])
        if not isinstance(raw_replies, list):
            raise RuntimeError("중앙 API 카카오 답변 형식이 올바르지 않습니다.")
        return [
            {
                "id": int(reply["id"]),
                "response_text": str(reply["response_text"]),
            }
            for reply in raw_replies
            if isinstance(reply, dict)
            and reply.get("id") is not None
            and reply.get("response_text")
        ]

    def update_kakao_queue_cursor(self, last_consumed_id: int) -> int:
        payload = self._request(
            "/api/v1/agent/kakao-queue/cursor",
            method="POST",
            payload={
                "agent_id": self.agent_id,
                "last_consumed_id": max(0, int(last_consumed_id)),
            },
        )
        return int(payload.get("last_consumed_id", 0))

    @staticmethod
    def _clean_watch_dates(raw_rows: Any) -> list[dict[str, str]]:
        if not isinstance(raw_rows, list):
            raise RuntimeError("중앙 API 감시 날짜 형식이 올바르지 않습니다.")
        return [
            {
                "arrival_date": str(row["arrival_date"]),
                "added_at": str(row["added_at"]),
            }
            for row in raw_rows
            if isinstance(row, dict)
            and row.get("arrival_date")
            and row.get("added_at")
        ]

    def submit_results(
        self,
        results: list[Any],
        *,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
    ) -> dict[str, Any]:
        scan_id = f"{self.agent_id}:{uuid.uuid4()}"
        return self._request(
            "/api/v1/agent/results",
            method="POST",
            payload={
                "agent_id": self.agent_id,
                "scan_id": scan_id,
                "started_at": started_at.isoformat(timespec="seconds"),
                "completed_at": completed_at.isoformat(timespec="seconds"),
                "duration_ms": duration_ms,
                "results": [asdict(result) for result in results],
            },
        )


def create_remote_agent_client(
    config: dict[str, Any],
) -> RemoteAgentClient | None:
    if not bool(config.get("remote_agent", {}).get("enabled", False)):
        return None
    return RemoteAgentClient(config)


def _close_browser(
    browser: Any | None,
    browser_process: Any | None,
) -> tuple[None, None]:
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    if browser_process is not None and browser_process.poll() is None:
        browser_process.terminate()
    return None, None


def report_dates_from_config(config: dict[str, Any]) -> list[str]:
    months = config.get("report_monitoring", {}).get("months", [])
    if not isinstance(months, list):
        raise ValueError("report_monitoring.months는 YYYY-MM 문자열 목록이어야 합니다.")
    report_dates: set[str] = set()
    for value in months:
        month_text = str(value).strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month_text):
            raise ValueError(f"보고 감시 월 형식이 올바르지 않습니다: {month_text}")
        year, month = map(int, month_text.split("-"))
        try:
            last_day = calendar.monthrange(year, month)[1]
        except (ValueError, calendar.IllegalMonthError) as exc:
            raise ValueError(f"보고 감시 월이 올바르지 않습니다: {month_text}") from exc
        report_dates.update(
            date(year, month, day).isoformat()
            for day in range(1, last_day + 1)
        )
    return sorted(report_dates)


def build_room_url(
    hotel: dict[str, Any],
    arrival: str,
    departure: str,
) -> str:
    params = {
        "arrivalDate": arrival,
        "departureDate": departure,
        "flexibleDates": "false",
        "numRooms": 1,
        "numAdults": int(hotel.get("adults", 2)),
        "numChildren": int(hotel.get("children", 0)),
        "room1ChildAges": "",
        "room1AdultAges": "",
        "displayCurrency": hotel.get("currency", "USD"),
    }
    return (
        "https://www.hilton.com/en/hotels/"
        f"{hotel['hotel_code']}-{hotel['hotel_slug']}/rooms/?"
        + urlencode(params)
    )


def _warm_up_hilton_browser(browser: Any, hotel: dict[str, Any]) -> None:
    if not browser.contexts:
        return
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    base_url = (
        "https://www.hilton.com/en/hotels/"
        f"{hotel['hotel_code']}-{hotel['hotel_slug']}/rooms/"
    )
    response = page.goto(base_url, wait_until="commit", timeout=25_000)
    if response is not None:
        LOGGER.info("Hilton 준비 페이지 HTTP %s", response.status)
    page.wait_for_timeout(2_000)
    _accept_hilton_cookie_consent_if_visible(page)


def report_batch_from_dates(
    report_dates: list[str],
    cursor: int,
    batch_size: int,
    last_attempts: dict[str, str] | None = None,
    interval_seconds: int = 0,
    now: datetime | None = None,
    excluded_dates: set[str] | None = None,
) -> tuple[list[str], int]:
    if not report_dates:
        return [], 0
    start = cursor % len(report_dates)
    size = min(len(report_dates), max(1, batch_size))
    attempts = last_attempts or {}
    excluded = excluded_dates or set()
    current = now or datetime.now().astimezone()
    batch: list[str] = []
    next_cursor = start
    for offset in range(len(report_dates)):
        index = (start + offset) % len(report_dates)
        arrival_date = report_dates[index]
        next_cursor = (index + 1) % len(report_dates)
        if arrival_date in excluded:
            continue
        last_attempted_at = attempts.get(arrival_date)
        if last_attempted_at and interval_seconds > 0:
            try:
                last_attempted = datetime.fromisoformat(last_attempted_at)
                if last_attempted.tzinfo is None:
                    last_attempted = last_attempted.replace(tzinfo=current.tzinfo)
                if (current - last_attempted).total_seconds() < interval_seconds:
                    continue
            except (TypeError, ValueError):
                pass
        batch.append(arrival_date)
        if len(batch) >= size:
            break
    return batch, next_cursor if batch else start


def report_batch_is_due(
    last_attempted_at: Any,
    interval_seconds: int,
    now: datetime | None = None,
) -> bool:
    if not last_attempted_at or interval_seconds <= 0:
        return True
    current = now or datetime.now().astimezone()
    try:
        last_attempted = datetime.fromisoformat(str(last_attempted_at))
        if last_attempted.tzinfo is None:
            last_attempted = last_attempted.replace(tzinfo=current.tzinfo)
    except (TypeError, ValueError):
        return True
    return (current - last_attempted).total_seconds() >= interval_seconds


_HILTON_BLOCK_MARKERS = (
    "hilton page reference code",
    "reference no.",
    "http 403",
    "http 429",
    "access denied",
    "something went wrong",
)

_BROWSER_CLOSED_MARKERS = (
    "targetclosederror",
    "target page, context or browser has been closed",
    "target page has been closed",
    "browser has been closed",
    "browser context has been closed",
    "page has been closed",
)


def _is_hilton_block_text(value: Any) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in _HILTON_BLOCK_MARKERS)


def _is_hilton_block_result(result: Any) -> bool:
    return str(getattr(result, "status", "")) == "error" and _is_hilton_block_text(
        getattr(result, "detail", "")
    )


def _is_browser_closed_text(value: Any) -> bool:
    text = f"{type(value).__name__}: {value}".casefold()
    return any(marker in text for marker in _BROWSER_CLOSED_MARKERS)


def _is_browser_closed_result(result: Any) -> bool:
    return str(getattr(result, "status", "")) == "error" and (
        _is_browser_closed_text(getattr(result, "detail", ""))
    )


def _run_cycle_with_browser_recovery(
    playwright: Any,
    browser: Any | None,
    browser_process: Any | None,
    config: dict[str, Any],
    hotel: dict[str, Any],
    arrival_dates: list[str],
) -> tuple[Any, Any | None, list[Any]]:
    """Edge가 예기치 않게 닫히면 새 감시 창에서 해당 묶음을 한 번 재시도합니다."""
    for attempt in range(2):
        try:
            if browser is None or not browser.is_connected():
                browser, browser_process = _close_browser(
                    browser,
                    browser_process,
                )
                browser, browser_process = _legacy.launch_browser(
                    playwright,
                    config.get("browser", {}),
                )
                try:
                    _warm_up_hilton_browser(browser, hotel)
                except Exception as exc:
                    if _is_browser_closed_text(exc):
                        raise
                    LOGGER.warning("Hilton 준비 페이지 확인 실패: %s", exc)

            results = _legacy.run_cycle(browser, config, arrival_dates)
            closed_results = [
                result
                for result in results
                if _is_browser_closed_result(result)
            ]
            if closed_results:
                closed_dates = ", ".join(
                    str(getattr(result, "arrival_date", "?"))
                    for result in closed_results
                )
                raise RuntimeError(
                    "TargetClosedError: Edge 조회 페이지가 닫혔습니다: "
                    + closed_dates
                )
            return browser, browser_process, results
        except Exception as exc:
            if not _is_browser_closed_text(exc) or attempt >= 1:
                raise
            LOGGER.warning(
                "Edge 감시 창이 닫혀 새 창으로 복구합니다. "
                "해당 날짜를 한 번 재시도합니다: %s",
                ", ".join(arrival_dates),
            )
            browser, browser_process = _close_browser(
                browser,
                browser_process,
            )
            time.sleep(1)

    raise RuntimeError("Edge 감시 창 자동 복구에 실패했습니다.")


def _block_backoff_settings(config: dict[str, Any]) -> tuple[int, int, float]:
    settings = config.get("hilton_block_backoff", {})
    initial_seconds = max(60, int(settings.get("initial_seconds", 900)))
    maximum_seconds = max(
        initial_seconds,
        int(settings.get("maximum_seconds", 14_400)),
    )
    multiplier = max(1.0, float(settings.get("multiplier", 2.0)))
    return initial_seconds, maximum_seconds, multiplier


def _register_hilton_block(
    state: dict[str, Any],
    config: dict[str, Any],
    detected_at: datetime,
    reason: str,
) -> tuple[int, int, datetime]:
    initial_seconds, maximum_seconds, multiplier = _block_backoff_settings(config)
    block_state = state.setdefault("hilton_block_backoff", {})
    if not isinstance(block_state, dict):
        block_state = {}
        state["hilton_block_backoff"] = block_state
    try:
        previous_level = max(0, int(block_state.get("level", 0)))
    except (TypeError, ValueError):
        previous_level = 0
    level = min(previous_level + 1, 30)
    delay_seconds = min(
        maximum_seconds,
        max(initial_seconds, int(initial_seconds * (multiplier ** (level - 1)))),
    )
    retry_at = detected_at + timedelta(seconds=delay_seconds)
    block_state.update(
        {
            "level": level,
            "last_detected_at": detected_at.isoformat(timespec="seconds"),
            "next_retry_at": retry_at.isoformat(timespec="seconds"),
            "reason": reason[:500],
        }
    )
    return level, delay_seconds, retry_at


def _relax_hilton_block(
    state: dict[str, Any],
    config: dict[str, Any],
    succeeded_at: datetime,
) -> tuple[bool, int, int, datetime | None]:
    block_state = state.get("hilton_block_backoff")
    if not isinstance(block_state, dict) or not block_state:
        return False, 0, 0, None
    try:
        previous_level = max(0, int(block_state.get("level", 0)))
    except (TypeError, ValueError):
        previous_level = 0
    next_level = max(0, previous_level - 1)
    if next_level == 0:
        state["hilton_block_backoff"] = {}
        return True, 0, 0, None

    initial_seconds, maximum_seconds, multiplier = _block_backoff_settings(config)
    delay_seconds = min(
        maximum_seconds,
        max(
            initial_seconds,
            int(initial_seconds * (multiplier ** (next_level - 1))),
        ),
    )
    retry_at = succeeded_at + timedelta(seconds=delay_seconds)
    block_state.update(
        {
            "level": next_level,
            "last_success_at": succeeded_at.isoformat(timespec="seconds"),
            "next_retry_at": retry_at.isoformat(timespec="seconds"),
            "reason": "정상 응답 확인 후 차단 단계를 한 단계 낮춤",
        }
    )
    return True, next_level, delay_seconds, retry_at


def _hilton_block_remaining_seconds(
    state: dict[str, Any],
    now: datetime,
) -> float:
    block_state = state.get("hilton_block_backoff", {})
    if not isinstance(block_state, dict):
        return 0.0
    retry_text = str(block_state.get("next_retry_at", "")).strip()
    if not retry_text:
        return 0.0
    try:
        retry_at = datetime.fromisoformat(retry_text)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (retry_at - now).total_seconds())


def _report_cursor_after_attempts(
    report_dates: list[str],
    current_cursor: int,
    attempted_dates: list[str],
    planned_next_cursor: int,
) -> int:
    if not report_dates or not attempted_dates:
        return current_cursor
    last_date = attempted_dates[-1]
    try:
        return (report_dates.index(last_date) + 1) % len(report_dates)
    except ValueError:
        pass
    return planned_next_cursor


def _send_pending_date_alerts(
    hotel: dict[str, Any],
    alert_results: list[Any],
    registrations: dict[str, str],
    state: dict[str, Any],
    notification: dict[str, Any],
    statistics_store: Any,
) -> None:
    dry_run = bool(notification.get("dry_run", True))
    pending, state_changed = _legacy.pending_date_alerts(
        alert_results,
        registrations,
        state,
        dry_run=dry_run,
    )
    if state_changed:
        _legacy.save_json_atomic(_legacy.STATE_PATH, state)
    if not pending:
        return
    message = _legacy.build_date_alert_message(hotel, pending)
    delivered = _legacy.send_kakao(message, notification)
    signature = ",".join(result.arrival_date for result in pending)
    statistics_store.record_notification(
        kind="date_available",
        message=message,
        dry_run=dry_run,
        delivered=delivered,
        condition_key=signature,
    )
    if delivered or dry_run:
        _legacy.mark_date_alerts_sent(
            pending,
            registrations,
            state,
            dry_run=dry_run,
        )


def run_monitor(config: dict[str, Any], once: bool = False) -> int:
    hotel = config["hotel"]
    notification = config.get("notification", {})
    interval = int(config.get("poll_interval_seconds", 30))
    jitter = max(0, int(config.get("jitter_seconds", 3)))
    max_backoff = max(
        interval,
        int(config.get("max_error_backoff_seconds", 600)),
    )
    state = _legacy.load_state()
    statistics_store = _legacy.create_statistics_store(config)
    remote_client = create_remote_agent_client(config)
    if remote_client is not None:
        try:
            _sync_kakao_draft_cursor(remote_client)
        except Exception:
            LOGGER.exception("중앙 답변 큐 커서 초기 동기화 실패")
    report_dates = report_dates_from_config(config)
    report_batch_size = int(config.get("report_monitoring", {}).get("batch_size", 5))
    report_interval_seconds = max(
        interval,
        int(config.get("report_monitoring", {}).get("interval_seconds", 3600)),
    )
    report_cursor = int(state.get("report_batch_cursor", 0) or 0)
    priority_cursor = int(state.get("priority_scan_cursor", 0) or 0)
    consecutive_error_cycles = 0
    repeat_alert_results: list[Any] = []
    repeat_alert_registrations: dict[str, str] = {}

    if once:
        if remote_client is None:
            initial_watch_rows = statistics_store.monitored_dates(hotel)
            initial_lease_granted = True
        else:
            initial_lease = remote_client.acquire_lease()
            initial_lease_granted = bool(initial_lease.get("granted"))
            initial_watch_rows = remote_client._clean_watch_dates(
                initial_lease.get("watch_dates", [])
            )
        if not initial_lease_granted:
            LOGGER.info("다른 감시 PC가 실행 중이어서 조회 없이 종료합니다.")
            statistics_store.close()
            return 0
        if not initial_watch_rows and not report_dates:
            LOGGER.info("등록된 알림·보고 날짜가 없어 조회 없이 종료합니다.")
            statistics_store.close()
            return 0

    source_name = (
        remote_client.base_url
        if remote_client is not None
        else "로컬 SQLite"
    )
    LOGGER.info(
        "날짜 선택 감시 시작: %s / %s / %d초 / 날짜 원본=%s / 보고 날짜=%d개",
        hotel.get("display_name", hotel["hotel_slug"]),
        hotel["room_name"],
        interval,
        source_name,
        len(report_dates),
    )

    with _legacy.sync_playwright() as playwright:
        browser = None
        browser_process = None
        try:
            while True:
                cycle_started = time.monotonic()
                cycle_started_wall = datetime.now().astimezone()
                lease_granted = remote_client is None
                lease: dict[str, Any] = {}
                block_wait_seconds = _hilton_block_remaining_seconds(
                    state,
                    cycle_started_wall,
                )
                try:
                    if remote_client is not None:
                        lease = remote_client.acquire_lease()
                        lease_granted = bool(lease.get("granted"))
                        if lease_granted:
                            watch_rows = remote_client._clean_watch_dates(
                                lease.get("watch_dates", [])
                            )
                        else:
                            watch_rows = []
                    else:
                        watch_rows = statistics_store.monitored_dates(hotel)
                    if lease_granted:
                        try:
                            _poll_command_drafts(remote_client, notification)
                        except Exception:
                            LOGGER.exception("챗봇 답변 초안 확인 실패")

                    registrations = {
                        row["arrival_date"]: row["added_at"]
                        for row in watch_rows
                    }
                    if report_batch_is_due(
                        state.get("report_batch_last_attempted_at"),
                        report_interval_seconds,
                        cycle_started_wall,
                    ):
                        report_batch, next_report_cursor = report_batch_from_dates(
                            report_dates,
                            report_cursor,
                            report_batch_size,
                            excluded_dates=set(registrations),
                        )
                    else:
                        report_batch = []
                        next_report_cursor = report_cursor
                    all_priority_dates = sorted(registrations)
                    if all_priority_dates:
                        priority_index = priority_cursor % len(all_priority_dates)
                        priority_dates = [all_priority_dates[priority_index]]
                    else:
                        priority_dates = []
                    scan_dates = priority_dates + [
                        arrival_date
                        for arrival_date in report_batch
                        if arrival_date not in registrations
                    ]

                    if not lease_granted:
                        consecutive_error_cycles = 0
                        repeat_alert_results = []
                        repeat_alert_registrations = {}
                        holder = (lease.get("lease") or {}).get("agent_id", "다른 PC")
                        LOGGER.info(
                            "다른 감시 PC가 실행 중이어서 이번 주기는 대기합니다: %s",
                            holder,
                        )
                        browser, browser_process = _close_browser(
                            browser,
                            browser_process,
                        )
                    elif not scan_dates:
                        consecutive_error_cycles = 0
                        repeat_alert_results = []
                        repeat_alert_registrations = {}
                        LOGGER.info("등록된 알림·보고 날짜가 없어 조회를 기다립니다.")
                        if state.get("date_alerts"):
                            state["date_alerts"] = {}
                            _legacy.save_json_atomic(_legacy.STATE_PATH, state)
                        browser, browser_process = _close_browser(
                            browser,
                            browser_process,
                        )
                    elif block_wait_seconds > 0:
                        consecutive_error_cycles = 0
                        repeat_alert_results = []
                        repeat_alert_registrations = {}
                        block_state = state.get("hilton_block_backoff", {})
                        LOGGER.warning(
                            "Hilton 차단 대기 중: 단계=%s / 남은 시간=%.0f초 / "
                            "등록 날짜와 3월 전체 조회를 모두 건너뜁니다.",
                            block_state.get("level", "?"),
                            block_wait_seconds,
                        )
                        browser, browser_process = _close_browser(
                            browser,
                            browser_process,
                        )
                    else:
                        LOGGER.info(
                            "이번 주기 알림 날짜: %s / 보고 배치: %s / 전체 조회: %d개",
                            ", ".join(priority_dates) if priority_dates else "없음",
                            ", ".join(report_batch) if report_batch else "없음",
                            len(scan_dates),
                        )
                        if browser is None or not browser.is_connected():
                            browser, browser_process = _legacy.launch_browser(
                                playwright,
                                config.get("browser", {}),
                            )
                            try:
                                _warm_up_hilton_browser(browser, hotel)
                            except Exception as exc:
                                LOGGER.warning("Hilton 준비 페이지 확인 실패: %s", exc)
                        results: list[Any] = []
                        attempted_report_dates: list[str] = []
                        priority_results: list[Any] = []
                        priority_cursor_advanced = False

                        if priority_dates:
                            (
                                browser,
                                browser_process,
                                priority_results,
                            ) = _run_cycle_with_browser_recovery(
                                playwright,
                                browser,
                                browser_process,
                                config,
                                hotel,
                                priority_dates,
                            )
                            results.extend(priority_results)
                            if priority_results and all_priority_dates:
                                priority_cursor = (priority_cursor + 1) % len(
                                    all_priority_dates
                                )
                                state["priority_scan_cursor"] = priority_cursor
                                priority_cursor_advanced = True

                        priority_all_failed = bool(priority_results) and all(
                            result.status == "error"
                            for result in priority_results
                        )
                        priority_blocked = priority_all_failed or any(
                            _is_hilton_block_result(result)
                            for result in priority_results
                        )
                        if priority_blocked and report_batch:
                            LOGGER.warning(
                                "등록 날짜가 모두 실패하거나 Hilton 차단 화면이 확인되어 "
                                "이번 3월 보고 배치 %d개를 건너뜁니다.",
                                len(report_batch),
                            )
                        else:
                            for arrival_date in report_batch:
                                (
                                    browser,
                                    browser_process,
                                    date_results,
                                ) = _run_cycle_with_browser_recovery(
                                    playwright,
                                    browser,
                                    browser_process,
                                    config,
                                    hotel,
                                    [arrival_date],
                                )
                                results.extend(date_results)
                                attempted_report_dates.append(arrival_date)
                                if any(
                                    _is_hilton_block_result(result)
                                    for result in date_results
                                ):
                                    remaining_count = (
                                        len(report_batch)
                                        - len(attempted_report_dates)
                                    )
                                    LOGGER.warning(
                                        "3월 전체 조회 중 Hilton 차단을 확인하여 "
                                        "남은 보고 날짜 %d개를 건너뜁니다.",
                                        remaining_count,
                                    )
                                    break

                        completed_at = datetime.now().astimezone()
                        duration_ms = int(
                            (time.monotonic() - cycle_started) * 1000
                        )
                        scan_stats = statistics_store.record_cycle(
                            hotel,
                            results,
                            started_at=cycle_started_wall,
                            duration_ms=duration_ms,
                        )
                        LOGGER.info(
                            "로컬 스캔 저장: 성공=%d 오류=%d 초기값=%d 변경=%d",
                            scan_stats["success_count"],
                            scan_stats["error_count"],
                            scan_stats["baseline_count"],
                            scan_stats["change_count"],
                        )

                        if remote_client is not None:
                            remote_stats = remote_client.submit_results(
                                results,
                                started_at=cycle_started_wall,
                                completed_at=completed_at,
                                duration_ms=duration_ms,
                            )
                            LOGGER.info(
                                "중앙 D1 업로드: 성공=%s 오류=%s 초기값=%s 변경=%s",
                                remote_stats.get("success_count"),
                                remote_stats.get("error_count"),
                                remote_stats.get("baseline_count"),
                                remote_stats.get("change_count"),
                            )

                        blocked_results = [
                            result
                            for result in results
                            if _is_hilton_block_result(result)
                        ]
                        if not blocked_results and priority_all_failed:
                            blocked_results = list(priority_results)
                        if (
                            not blocked_results
                            and results
                            and all(result.status == "error" for result in results)
                        ):
                            blocked_results = list(results)

                        state_dirty = priority_cursor_advanced
                        if blocked_results:
                            blocked_dates = ", ".join(
                                result.arrival_date for result in blocked_results
                            )
                            level, delay_seconds, retry_at = _register_hilton_block(
                                state,
                                config,
                                completed_at,
                                f"Hilton 차단 화면: {blocked_dates}",
                            )
                            state_dirty = True
                            LOGGER.warning(
                                "Hilton 차단 감지: 단계=%d / %d초 후 재시도 / "
                                "재시도 시각=%s / 등록 날짜와 3월 전체 조회 공통 적용",
                                level,
                                delay_seconds,
                                retry_at.isoformat(timespec="seconds"),
                            )
                            browser, browser_process = _close_browser(
                                browser,
                                browser_process,
                            )
                        elif any(result.status != "error" for result in results):
                            (
                                relaxed,
                                next_level,
                                delay_seconds,
                                retry_at,
                            ) = _relax_hilton_block(state, config, completed_at)
                            if relaxed:
                                state_dirty = True
                                if next_level > 0 and retry_at is not None:
                                    LOGGER.info(
                                        "Hilton 정상 응답 확인: 차단 단계를 %d단계로 낮추고 "
                                        "%d초 후 다시 조회합니다. 다음 조회=%s",
                                        next_level,
                                        delay_seconds,
                                        retry_at.isoformat(timespec="seconds"),
                                    )
                                else:
                                    LOGGER.info(
                                        "Hilton 정상 응답 확인: 차단 단계를 해제하고 "
                                        "기본 조회 주기로 복귀합니다."
                                    )

                        if report_dates and attempted_report_dates:
                            report_cursor = _report_cursor_after_attempts(
                                report_dates,
                                report_cursor,
                                attempted_report_dates,
                                next_report_cursor,
                            )
                            state["report_batch_cursor"] = report_cursor
                            attempted_at = completed_at.isoformat(timespec="seconds")
                            state["report_batch_last_attempted_at"] = attempted_at
                            state_dirty = True

                        if state_dirty:
                            _legacy.save_json_atomic(_legacy.STATE_PATH, state)

                        if all(result.status == "error" for result in results):
                            raise RuntimeError("모든 날짜 조회에 실패했습니다.")

                        consecutive_error_cycles = 0
                        repeat_alert_results = [
                            result
                            for result in results
                            if result.arrival_date in registrations
                        ]
                        repeat_alert_registrations = dict(registrations)
                        _send_pending_date_alerts(
                            hotel,
                            repeat_alert_results,
                            repeat_alert_registrations,
                            state,
                            notification,
                            statistics_store,
                        )

                    _legacy.maybe_send_daily_report(
                        config,
                        statistics_store,
                        notification,
                    )
                except KeyboardInterrupt:
                    LOGGER.info("사용자 요청으로 종료합니다.")
                    return 0
                except Exception as exc:
                    repeat_alert_results = []
                    repeat_alert_registrations = {}
                    if _is_hilton_block_text(exc):
                        detected_at = datetime.now().astimezone()
                        level, delay_seconds, retry_at = _register_hilton_block(
                            state,
                            config,
                            detected_at,
                            str(exc),
                        )
                        _legacy.save_json_atomic(_legacy.STATE_PATH, state)
                        LOGGER.warning(
                            "예외에서 Hilton 차단 감지: 단계=%d / %d초 후 재시도 / %s",
                            level,
                            delay_seconds,
                            retry_at.isoformat(timespec="seconds"),
                        )
                    consecutive_error_cycles += 1
                    LOGGER.exception(
                        "감시 주기 실패 (%d회 연속)",
                        consecutive_error_cycles,
                    )
                    browser, browser_process = _close_browser(
                        browser,
                        browser_process,
                    )

                if once:
                    return 0 if consecutive_error_cycles == 0 else 1

                elapsed = time.monotonic() - cycle_started
                block_wait_seconds = _hilton_block_remaining_seconds(
                    state,
                    datetime.now().astimezone(),
                )
                if block_wait_seconds > 0:
                    sleep_for = max(1.0, block_wait_seconds)
                elif consecutive_error_cycles:
                    backoff = min(
                        max_backoff,
                        interval * (2 ** min(consecutive_error_cycles, 5)),
                    )
                    sleep_for = max(1.0, backoff - elapsed)
                else:
                    sleep_for = max(
                        1.0,
                        interval + _legacy.random.uniform(0, jitter) - elapsed,
                    )
                LOGGER.info("다음 조회까지 %.1f초", sleep_for)
                repeat_alert_callback = None
                if lease_granted and repeat_alert_results:
                    repeat_alert_callback = lambda: _send_pending_date_alerts(
                        hotel,
                        repeat_alert_results,
                        repeat_alert_registrations,
                        state,
                        notification,
                        statistics_store,
                    )
                _wait_with_command_draft_poll(
                    sleep_for,
                    remote_client if lease_granted else None,
                    notification,
                    repeat_alert_callback,
                )
        finally:
            _close_browser(browser, browser_process)
            statistics_store.close()


def _control_ancestor_names(control: Any) -> list[str]:
    names: list[str] = []
    current = control
    while current is not None:
        name = str(getattr(current, "Name", "") or "").strip()
        if name:
            names.append(name)
        current = current.GetParentControl()
    return names


def _focus_kakao_message_input(
    window: Any,
    title: str,
    *,
    exact: bool,
) -> Any:
    message_input = window.DocumentControl(
        Name="RichEdit Control",
        searchDepth=3,
    )
    if not message_input.Exists(1):
        raise RuntimeError("카카오톡 메시지 입력창을 찾지 못했습니다.")

    window.ShowWindow(9)
    window.SwitchToThisWindow()
    window.SetActive()
    time.sleep(0.3)
    message_input.SetFocus()
    time.sleep(0.3)
    focused = _legacy.auto.GetFocusedControl()
    ancestor_names = _control_ancestor_names(focused)
    title_matches = (
        title in ancestor_names
        if exact
        else any(title.casefold() in name.casefold() for name in ancestor_names)
    )
    if not title_matches or "RichEdit Control" not in ancestor_names:
        raise RuntimeError(
            "카카오톡 입력창 포커스 확인에 실패했습니다. "
            f"포커스 경로={ancestor_names!r}"
        )
    return message_input


def send_kakao(message: str, notification: dict[str, Any]) -> bool:
    """정확한 대화방의 입력 컨트롤을 검증하고 메시지를 전송합니다."""
    if notification.get("dry_run", True):
        if notification.get("draft_only", False):
            draft_notification = dict(notification)
            draft_notification["dry_run"] = False
            paste_kakao_draft(message, draft_notification)
            LOGGER.info("[초안 모드] 카카오톡 입력 완료, Enter는 누르지 않음")
            return False
        LOGGER.info("[DRY RUN] 카카오톡 전송 예정 메시지:\n%s", message)
        return False

    title = str(notification["chat_window_title"]).strip()
    exact = str(notification.get("title_match", "exact")).lower() == "exact"
    window = _legacy.check_kakao_window(notification)
    rect = window.BoundingRectangle
    if rect.width() < 250 or rect.height() < 250:
        raise RuntimeError(f"대화방 창 크기가 비정상입니다: {rect}")

    message_input = _focus_kakao_message_input(window, title, exact=exact)
    previous_clipboard = ""
    try:
        previous_clipboard = _legacy.pyperclip.paste()
    except Exception:
        pass

    try:
        message_input.SetFocus()
        _legacy.pyautogui.hotkey("ctrl", "a")
        _legacy.pyperclip.copy(message)
        _legacy.pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        _legacy.pyautogui.press("enter")
        time.sleep(0.5)
    finally:
        try:
            _legacy.pyperclip.copy(previous_clipboard)
        except Exception:
            pass

    LOGGER.info("카카오톡 전송 완료: %s", title)
    return True


def paste_kakao_draft(message: str, notification: dict[str, Any]) -> None:
    if notification.get("dry_run", True):
        LOGGER.info("[DRY RUN] 카카오톡 초안 입력 예정 메시지:\n%s", message)
        return

    title = str(notification["chat_window_title"]).strip()
    exact = str(notification.get("title_match", "exact")).lower() == "exact"
    window = _legacy.check_kakao_window(notification)
    message_input = _focus_kakao_message_input(window, title, exact=exact)
    previous_clipboard = ""
    try:
        previous_clipboard = _legacy.pyperclip.paste()
    except Exception:
        pass
    try:
        message_input.SetFocus()
        _legacy.pyautogui.hotkey("ctrl", "a")
        _legacy.pyperclip.copy(message)
        _legacy.pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
    finally:
        try:
            _legacy.pyperclip.copy(previous_clipboard)
        except Exception:
            pass
    LOGGER.info("카카오톡 테스트 문구 입력 완료(미전송): %s", title)


def _load_kakao_draft_cursor() -> int:
    try:
        payload = json.loads(KAKAO_DRAFT_CURSOR_PATH.read_text(encoding="utf-8"))
        return max(0, int(payload.get("last_id", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _sync_kakao_draft_cursor(remote_client: RemoteAgentClient) -> int:
    central_cursor = remote_client.kakao_queue_cursor()
    local_cursor = _load_kakao_draft_cursor()
    if local_cursor != central_cursor:
        _legacy.save_json_atomic(
            KAKAO_DRAFT_CURSOR_PATH,
            {"last_id": central_cursor},
        )
        LOGGER.info(
            "중앙 답변 큐 커서로 로컬 상태 동기화: %d -> %d",
            local_cursor,
            central_cursor,
        )
    return central_cursor


def _read_kakao_draft_text(notification: dict[str, Any]) -> str:
    window = _legacy.check_kakao_window(notification)
    message_input = window.DocumentControl(
        Name="RichEdit Control",
        searchDepth=3,
    )
    if not message_input.Exists(1):
        raise RuntimeError("카카오톡 메시지 입력창을 찾지 못했습니다.")
    try:
        value_pattern = message_input.GetValuePattern()
        if value_pattern is not None:
            return str(value_pattern.Value or "")
    except Exception:
        pass
    text_pattern = message_input.GetTextPattern()
    if text_pattern is None:
        raise RuntimeError("카카오톡 입력창 내용을 읽지 못했습니다.")
    return str(text_pattern.DocumentRange.GetText(-1) or "")


def _poll_command_drafts(
    remote_client: RemoteAgentClient | None,
    notification: dict[str, Any],
) -> int:
    global _COMMAND_DRAFT_BLOCKED_TEXT
    global _COMMAND_DRAFT_BLOCKED_REPLY_ID
    global _COMMAND_DRAFT_BLOCKED_SINCE

    if remote_client is None or not notification.get("draft_only", False):
        return 0
    last_id = _load_kakao_draft_cursor()
    replies = remote_client.kakao_replies(last_id)
    if not replies:
        return 0
    replies.sort(key=lambda reply: reply["id"])
    next_reply = replies[0]
    command_auto_send = bool(notification.get("command_auto_send", False))
    current_draft = _read_kakao_draft_text(notification).strip()
    if current_draft and current_draft != "메시지 입력":
        force_after_seconds = max(
            1,
            int(notification.get("command_force_send_after_seconds", 60)),
        )
        now_monotonic = time.monotonic()
        draft_unchanged = (
            _COMMAND_DRAFT_BLOCKED_REPLY_ID == next_reply["id"]
            and _COMMAND_DRAFT_BLOCKED_TEXT == current_draft
        )
        if not draft_unchanged:
            _COMMAND_DRAFT_BLOCKED_REPLY_ID = next_reply["id"]
            _COMMAND_DRAFT_BLOCKED_TEXT = current_draft
            _COMMAND_DRAFT_BLOCKED_SINCE = now_monotonic
            LOGGER.info(
                "카카오톡 입력창 사용 중, 최대 %d초 대기: id=%d",
                force_after_seconds,
                next_reply["id"],
            )
            return 0
        blocked_seconds = now_monotonic - _COMMAND_DRAFT_BLOCKED_SINCE
        if not command_auto_send or blocked_seconds < force_after_seconds:
            return 0
        LOGGER.warning(
            "카카오톡 입력창 내용이 %d초 동안 그대로여서 대기 답변을 강제 전송합니다: id=%d",
            int(blocked_seconds),
            next_reply["id"],
        )
    else:
        _COMMAND_DRAFT_BLOCKED_TEXT = ""
        _COMMAND_DRAFT_BLOCKED_REPLY_ID = 0
        _COMMAND_DRAFT_BLOCKED_SINCE = 0.0
    command_notification = dict(notification)
    command_notification["dry_run"] = False
    command_notification["draft_only"] = False
    if command_auto_send:
        send_kakao(next_reply["response_text"], command_notification)
    else:
        paste_kakao_draft(next_reply["response_text"], command_notification)
    confirmed_cursor = remote_client.update_kakao_queue_cursor(next_reply["id"])
    _legacy.save_json_atomic(
        KAKAO_DRAFT_CURSOR_PATH,
        {"last_id": confirmed_cursor},
    )
    action = "자동 전송" if command_auto_send else "초안 입력"
    LOGGER.info("챗봇 답변을 단체방에 순차 %s: id=%d", action, next_reply["id"])
    _COMMAND_DRAFT_BLOCKED_TEXT = ""
    _COMMAND_DRAFT_BLOCKED_REPLY_ID = 0
    _COMMAND_DRAFT_BLOCKED_SINCE = 0.0
    return 1


def _renew_monitor_lease(remote_client: RemoteAgentClient) -> bool | None:
    try:
        lease_state = remote_client.acquire_lease()
    except Exception:
        LOGGER.exception("대기 중 감시 임대 갱신 실패")
        return None
    if lease_state.get("granted"):
        LOGGER.info("대기 중 감시 임대 갱신 완료")
        return True
    holder = (lease_state.get("lease") or {}).get("agent_id", "다른 PC")
    LOGGER.warning(
        "감시 임대가 다른 PC로 넘어가 대기 중 답변 큐와 반복 알림을 중단합니다: %s",
        holder,
    )
    return False


def _wait_with_command_draft_poll(
    seconds: float,
    remote_client: RemoteAgentClient | None,
    notification: dict[str, Any],
    repeat_alert_callback: Any = None,
) -> None:
    drafts_enabled = remote_client is not None and notification.get(
        "draft_only",
        False,
    )
    if not drafts_enabled and repeat_alert_callback is None:
        time.sleep(seconds)
        return
    poll_seconds = min(
        30,
        max(2, int(notification.get("command_draft_poll_seconds", 3))),
    )
    deadline = time.monotonic() + max(0, seconds)
    next_lease_heartbeat = 0.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll_seconds, remaining))
        now_monotonic = time.monotonic()
        if remote_client is not None and now_monotonic >= next_lease_heartbeat:
            lease_renewed = _renew_monitor_lease(remote_client)
            if lease_renewed is False:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                return
            if lease_renewed is None:
                next_lease_heartbeat = now_monotonic + min(
                    60.0,
                    _LEASE_HEARTBEAT_SECONDS,
                )
                continue
            next_lease_heartbeat = now_monotonic + _LEASE_HEARTBEAT_SECONDS
        if drafts_enabled:
            try:
                _poll_command_drafts(remote_client, notification)
            except Exception:
                LOGGER.exception("챗봇 답변 초안 확인 실패")
        if repeat_alert_callback is not None:
            try:
                repeat_alert_callback()
            except Exception:
                LOGGER.exception("예약 가능 반복 알림 실패")


_legacy_build_daily_report_message = _legacy.build_daily_report_message
_legacy_build_date_alert_message = _legacy.build_date_alert_message
_legacy_extract_card_text = _legacy.extract_card_text
_legacy_check_room = _legacy.check_room
_card_text_evaluator = next(
    value
    for value in _legacy_extract_card_text.__code__.co_consts
    if isinstance(value, str) and "(heading) =>" in value
)


def build_daily_report_message(*args: Any, **kwargs: Any) -> str:
    message = _legacy_build_daily_report_message(*args, **kwargs)
    return "\n".join(
        line
        for line in message.splitlines()
        if not ("SQLite" in line and "CSV" in line and "보관" in line)
    )


def build_date_alert_message(*args: Any, **kwargs: Any) -> str:
    message = _legacy_build_date_alert_message(*args, **kwargs)
    repeat_seconds = _available_repeat_seconds()
    if repeat_seconds % 3600 == 0:
        repeat_interval = f"{repeat_seconds // 3600}시간"
    elif repeat_seconds % 60 == 0:
        repeat_interval = f"{repeat_seconds // 60}분"
    else:
        repeat_interval = f"{repeat_seconds}초"
    return (
        message
        + f"\n\n이 알림은 감시 해제 전까지 {repeat_interval}마다 반복됩니다."
    )


def _available_repeat_seconds() -> int:
    try:
        config = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
        seconds = int(config.get("notification", {}).get("repeat_available_seconds", 600))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        seconds = 600
    return max(60, seconds)


def _report_monitoring_number(name: str, default: int) -> int:
    try:
        config = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
        value = int(config.get("report_monitoring", {}).get(name, default))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        value = default
    return value


def extract_card_text(page: Any, room_name: str) -> str:
    timeout_ms = min(
        20_000,
        max(3_000, _report_monitoring_number("card_timeout_seconds", 7_000)),
    )
    heading = page.get_by_role("heading", name=room_name, exact=True).last
    heading.wait_for(state="visible", timeout=timeout_ms)
    return str(heading.evaluate(_card_text_evaluator) or "")


def _accept_hilton_cookie_consent_if_visible(page: Any) -> bool:
    button = page.get_by_role(
        "button",
        name=re.compile(r"Accept All Cookies|allow cookies", re.IGNORECASE),
    ).last
    try:
        button.wait_for(state="visible", timeout=1_000)
    except _legacy.PlaywrightTimeoutError:
        return False
    button.click(timeout=3_000)
    page.wait_for_timeout(500)
    LOGGER.info("Hilton 쿠키 동의 처리 완료")
    return True


def check_room(
    page: Any,
    hotel: dict[str, Any],
    arrival: str,
    departure: str,
) -> Any:
    try:
        try:
            return _legacy_check_room(page, hotel, arrival, departure)
        except Exception:
            if not _accept_hilton_cookie_consent_if_visible(page):
                raise
            LOGGER.info("쿠키 동의 후 %s 날짜를 한 번 다시 조회합니다.", arrival)
            return _legacy_check_room(page, hotel, arrival, departure)
    finally:
        delay = min(
            30,
            max(0, _report_monitoring_number("request_delay_seconds", 3)),
        )
        if delay:
            time.sleep(delay)


def pending_date_alerts(
    results: list[Any],
    registrations: dict[str, str],
    state: dict[str, Any],
    dry_run: bool,
) -> tuple[list[Any], bool]:
    mode_key = "dry_run" if dry_run else "live"
    date_state = state.setdefault("date_alerts", {}).setdefault(mode_key, {})
    state_changed = False

    for arrival_date in list(date_state):
        if arrival_date not in registrations:
            del date_state[arrival_date]
            state_changed = True

    now = datetime.now().astimezone()
    repeat_seconds = _available_repeat_seconds()
    pending: list[Any] = []
    for result in results:
        registration = registrations[result.arrival_date]
        previous = date_state.get(result.arrival_date, {})
        if result.status == "available":
            due = (
                previous.get("registration") != registration
                or previous.get("last_status") != "available"
            )
            if not due:
                last_sent_at = previous.get("last_sent_at")
                try:
                    last_sent = datetime.fromisoformat(str(last_sent_at))
                    if last_sent.tzinfo is None:
                        last_sent = last_sent.replace(tzinfo=now.tzinfo)
                    due = (now - last_sent).total_seconds() >= repeat_seconds
                except (TypeError, ValueError):
                    due = True
            if due:
                pending.append(result)
        elif result.status == "sold_out":
            current = {
                "registration": registration,
                "last_status": "sold_out",
            }
            if previous != current:
                date_state[result.arrival_date] = current
                state_changed = True

    return pending, state_changed


_legacy.send_kakao = send_kakao
_legacy.paste_kakao_draft = paste_kakao_draft
_legacy.build_daily_report_message = build_daily_report_message
_legacy.build_date_alert_message = build_date_alert_message
_legacy.build_room_url = build_room_url
_legacy.extract_card_text = extract_card_text
_legacy.check_room = check_room
_legacy.pending_date_alerts = pending_date_alerts
_legacy.run_monitor = run_monitor

if __name__ == "__main__":
    raise SystemExit(_legacy.main())
