const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const CLOUDFLARE_FREE_LIMITS = {
  workerRequests: 100_000,
  d1RowsRead: 5_000_000,
  d1RowsWritten: 100_000,
  d1StorageBytes: 5_000_000_000,
};
let cloudflareUsageCache = null;

function nowIso() {
  return new Date().toISOString();
}

function koreaToday() {
  return new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function validIsoDate(value) {
  if (!DATE_PATTERN.test(value)) return false;
  const parsed = new Date(value + "T00:00:00Z");
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function commandHelp() {
  return [
    "[객실 명령 안내]",
    "객실 감시 YYYY-MM-DD",
    "객실 감시 해제 YYYY-MM-DD",
    "객실 감시 목록",
    "객실 조회 YYYY-MM-DD",
    "일일 변경 보고 [YYYY-MM-DD]",
    "3월 자세히 보기",
    "클라우드플레어 사용량",
    "단톡방 공유 [켜기|끄기] (내 명령 결과만)",
  ].join("\n");
}

function statusSymbol(status) {
  if (status === "available") return "예약 가능";
  if (status === "sold_out") return "예약 불가(매진)";
  if (status === "error") return "조회 오류";
  return "미확인";
}

function kakaoResponse(text) {
  return {
    version: "2.0",
    template: {
      outputs: [{ simpleText: { text: text.slice(0, 1000) } }],
      quickReplies: [
        { label: "객실 감시", action: "message", messageText: "객실 감시" },
        { label: "감시 해제", action: "message", messageText: "객실 감시 해제" },
        { label: "감시 목록", action: "message", messageText: "객실 감시 목록" },
        { label: "일일 변경 보고", action: "message", messageText: "일일 변경 보고" },
        { label: "3월 자세히 보기", action: "message", messageText: "3월 자세히 보기" },
        { label: "CF 사용량", action: "message", messageText: "클라우드플레어 사용량" },
        { label: "공유 설정", action: "message", messageText: "단톡방 공유" },
      ],
    },
  };
}

function json(body, status = 200) {
  return Response.json(body, {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function authorized(request, expected, headerName) {
  return Boolean(expected) && request.headers.get(headerName) === expected;
}

function rows(result) {
  return Array.isArray(result && result.results) ? result.results : [];
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function anonymousUserKey(value) {
  const source = String(value || "").trim();
  if (!source) return "알 수 없음";
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(source)
  );
  return "사용자-" + Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 12);
}

function koreaDateTime(isoValue) {
  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.valueOf())) return String(isoValue || "-");
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function koreaShortDateTime(isoValue) {
  const parsed = Date.parse(isoValue || "");
  if (!Number.isFinite(parsed)) return "-";
  const koreaIso = new Date(parsed + 9 * 60 * 60 * 1000).toISOString();
  return koreaIso.slice(0, 16).replace("T", " ");
}

function adminAuthorized(request, env) {
  const expectedPassword = String(env.ADMIN_PASSWORD || "").trim();
  if (!expectedPassword) return false;
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Basic ")) return false;
  try {
    const decoded = atob(authorization.slice(6));
    const separator = decoded.indexOf(":");
    return separator > 0 &&
      decoded.slice(0, separator) === "admin" &&
      decoded.slice(separator + 1) === expectedPassword;
  } catch (error) {
    return false;
  }
}

function adminUnauthorized() {
  return new Response("관리자 로그인이 필요합니다.", {
    status: 401,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      "www-authenticate": 'Basic realm="Milaidhoo Admin", charset="UTF-8"',
    },
  });
}

function elapsedLabel(seconds) {
  const safeSeconds = Math.max(0, Math.floor(Number(seconds || 0)));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  if (hours) return hours + "시간 " + minutes + "분";
  return minutes + "분";
}

function usagePercent(value, limit) {
  if (!Number.isFinite(value) || !Number.isFinite(limit) || limit <= 0) return "-";
  return Math.min(999, value / limit * 100).toFixed(value / limit < 0.001 ? 3 : 1) + "%";
}

function usageCount(value) {
  return Math.max(0, Number(value || 0)).toLocaleString("ko-KR");
}

function usageBytes(value) {
  const bytes = Math.max(0, Number(value || 0));
  if (bytes >= 1_000_000_000) return (bytes / 1_000_000_000).toFixed(2) + " GB";
  if (bytes >= 1_000_000) return (bytes / 1_000_000).toFixed(2) + " MB";
  if (bytes >= 1_000) return (bytes / 1_000).toFixed(0) + " kB";
  return usageCount(bytes) + " B";
}

async function cloudflareUsageText(env, fetcher = fetch, nowMs = Date.now()) {
  if (cloudflareUsageCache && cloudflareUsageCache.expiresAt > nowMs) {
    return cloudflareUsageCache.text;
  }
  const required = [
    env.CLOUDFLARE_ANALYTICS_TOKEN,
    env.CLOUDFLARE_ACCOUNT_ID,
    env.CLOUDFLARE_D1_DATABASE_ID,
    env.CLOUDFLARE_WORKER_SCRIPT,
  ];
  if (required.some((value) => !String(value || "").trim())) {
    return [
      "[Cloudflare 사용량]",
      "Analytics 읽기 설정이 아직 완료되지 않았습니다.",
    ].join("\n");
  }

  const now = new Date(nowMs);
  const startDate = now.toISOString().slice(0, 10);
  const datetimeStart = startDate + "T00:00:00.000Z";
  const datetimeEnd = now.toISOString();
  const query = `query Usage(
    $accountTag: string!, $start: Date!, $end: Date!,
    $datetimeStart: string!, $datetimeEnd: string!,
    $databaseId: string!, $scriptName: string!
  ) {
    viewer {
      accounts(filter: { accountTag: $accountTag }) {
        d1AnalyticsAdaptiveGroups(
          limit: 1000
          filter: { date_geq: $start, date_leq: $end, databaseId: $databaseId }
        ) {
          sum { readQueries writeQueries rowsRead rowsWritten }
        }
        d1StorageAdaptiveGroups(
          limit: 10
          filter: { date_geq: $start, date_leq: $end, databaseId: $databaseId }
        ) {
          max { databaseSizeBytes }
        }
        workersInvocationsAdaptive(
          limit: 1000
          filter: {
            scriptName: $scriptName
            datetime_geq: $datetimeStart
            datetime_leq: $datetimeEnd
          }
        ) {
          sum { requests errors }
        }
      }
    }
  }`;
  try {
    const response = await fetcher("https://api.cloudflare.com/client/v4/graphql", {
      method: "POST",
      headers: {
        authorization: "Bearer " + env.CLOUDFLARE_ANALYTICS_TOKEN,
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        query,
        variables: {
          accountTag: env.CLOUDFLARE_ACCOUNT_ID,
          start: startDate,
          end: startDate,
          datetimeStart,
          datetimeEnd,
          databaseId: env.CLOUDFLARE_D1_DATABASE_ID,
          scriptName: env.CLOUDFLARE_WORKER_SCRIPT,
        },
      }),
    });
    const payload = await response.json();
    if (!response.ok || (Array.isArray(payload.errors) && payload.errors.length)) {
      const message = payload && payload.errors && payload.errors[0] && payload.errors[0].message;
      throw new Error(message || "Cloudflare Analytics API 오류 " + response.status);
    }
    const account = payload && payload.data && payload.data.viewer &&
      payload.data.viewer.accounts && payload.data.viewer.accounts[0];
    if (!account) throw new Error("Cloudflare 계정 지표가 없습니다.");

    const d1 = (account.d1AnalyticsAdaptiveGroups || []).reduce(
      (total, item) => ({
        rowsRead: total.rowsRead + Number(item && item.sum && item.sum.rowsRead || 0),
        rowsWritten: total.rowsWritten + Number(item && item.sum && item.sum.rowsWritten || 0),
      }),
      { rowsRead: 0, rowsWritten: 0 }
    );
    const workers = (account.workersInvocationsAdaptive || []).reduce(
      (total, item) => ({
        requests: total.requests + Number(item && item.sum && item.sum.requests || 0),
        errors: total.errors + Number(item && item.sum && item.sum.errors || 0),
      }),
      { requests: 0, errors: 0 }
    );
    const storageBytes = Math.max(
      0,
      ...(account.d1StorageAdaptiveGroups || []).map(
        (item) => Number(item && item.max && item.max.databaseSizeBytes || 0)
      )
    );
    const text = [
      "[Cloudflare 사용량 · 오늘]",
      "Workers 요청 " + usageCount(workers.requests) + "/100,000 (" +
        usagePercent(workers.requests, CLOUDFLARE_FREE_LIMITS.workerRequests) + ")",
      "Workers 오류 " + usageCount(workers.errors) + "건",
      "D1 읽기 " + usageCount(d1.rowsRead) + "/5,000,000행 (" +
        usagePercent(d1.rowsRead, CLOUDFLARE_FREE_LIMITS.d1RowsRead) + ")",
      "D1 쓰기 " + usageCount(d1.rowsWritten) + "/100,000행 (" +
        usagePercent(d1.rowsWritten, CLOUDFLARE_FREE_LIMITS.d1RowsWritten) + ")",
      "D1 저장 " + usageBytes(storageBytes) + "/5 GB (" +
        usagePercent(storageBytes, CLOUDFLARE_FREE_LIMITS.d1StorageBytes) + ")",
      "기준 UTC 00:00~현재 · 집계가 몇 분 늦을 수 있습니다.",
    ].join("\n");
    cloudflareUsageCache = { text, expiresAt: nowMs + 5 * 60 * 1000 };
    return text;
  } catch (error) {
    return [
      "[Cloudflare 사용량]",
      "조회 실패: " + String(error && error.message || error).slice(0, 120),
    ].join("\n");
  }
}

async function registeredMonitoringStatus(env, nowMs = Date.now()) {
  const result = await env.DB.prepare(
    "SELECT m.arrival_date, " +
    "c.status, c.price, c.last_checked_at, a.status AS attempt_status, " +
    "a.detail AS attempt_detail, a.checked_at AS attempt_checked_at " +
    "FROM monitored_dates AS m " +
    "LEFT JOIN availability_current AS c " +
      "ON c.arrival_date = m.arrival_date " +
    "LEFT JOIN availability_latest_attempt AS a " +
      "ON a.arrival_date = m.arrival_date " +
    "ORDER BY m.arrival_date"
  ).all();
  const thresholdSeconds = Math.max(
    60,
    Number(env.WATCH_STALE_SECONDS || 3600)
  );
  return rows(result).map((item) => {
    const checkedMs = Date.parse(item.last_checked_at || "");
    const ageSeconds = Number.isFinite(checkedMs)
      ? Math.max(0, Math.floor((nowMs - checkedMs) / 1000))
      : null;
    const stale = ageSeconds == null || ageSeconds >= thresholdSeconds;
    return {
      ...item,
      age_seconds: ageSeconds,
      age_label: ageSeconds == null ? "정상 확인 기록 없음" : elapsedLabel(ageSeconds),
      stale,
      latest_error: item.attempt_status === "error"
        ? compactAttemptError(item.attempt_detail)
        : null,
    };
  });
}

async function updateKakaoQueueCursor(env, agentId, lastConsumedId) {
  const safeId = Math.max(0, Number(lastConsumedId || 0));
  await env.DB.prepare(
    "INSERT INTO kakao_queue_state (id, last_consumed_id, updated_at, agent_id) " +
    "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET " +
    "last_consumed_id = MAX(kakao_queue_state.last_consumed_id, excluded.last_consumed_id), " +
    "updated_at = CASE WHEN excluded.last_consumed_id >= kakao_queue_state.last_consumed_id " +
    "THEN excluded.updated_at ELSE kakao_queue_state.updated_at END, " +
    "agent_id = CASE WHEN excluded.last_consumed_id >= kakao_queue_state.last_consumed_id " +
    "THEN excluded.agent_id ELSE kakao_queue_state.agent_id END"
  ).bind(safeId, nowIso(), agentId).run();
  return kakaoQueueCursor(env);
}

async function kakaoQueueCursor(env) {
  const result = await env.DB.prepare(
    "SELECT last_consumed_id FROM kakao_queue_state WHERE id = 1"
  ).all();
  return Math.max(0, Number((rows(result)[0] || {}).last_consumed_id || 0));
}

async function adminResetQueue(request, env) {
  const requestUrl = new URL(request.url);
  const origin = request.headers.get("origin");
  if (origin && origin !== requestUrl.origin) {
    return new Response("잘못된 요청 출처입니다.", { status: 403 });
  }
  const form = await request.formData();
  if (form.get("confirmed") !== "yes") {
    return new Response("초기화 확인이 필요합니다.", { status: 400 });
  }
  const stateResult = await env.DB.prepare(
    "SELECT last_consumed_id FROM kakao_queue_state WHERE id = 1"
  ).all();
  const lastConsumedId = Number(
    (rows(stateResult)[0] || {}).last_consumed_id || 0
  );
  const maxResult = await env.DB.prepare(
    "SELECT COALESCE(MAX(id), ?) AS max_id FROM kakao_reply_outbox"
  ).bind(lastConsumedId).all();
  const maxId = Math.max(
    lastConsumedId,
    Number((rows(maxResult)[0] || {}).max_id || 0)
  );
  await env.DB.prepare(
    "DELETE FROM kakao_reply_outbox WHERE id > ?"
  ).bind(lastConsumedId).run();
  await updateKakaoQueueCursor(env, "admin-reset", maxId);
  return Response.redirect(requestUrl.origin + "/admin?queue_reset=1", 303);
}

async function adminLogPage(request, env) {
  const url = new URL(request.url);
  const query = String(url.searchParams.get("q") || "").trim().slice(0, 100);
  const beforeId = Math.max(0, Number(url.searchParams.get("before_id") || 0));
  const afterId = Math.max(0, Number(url.searchParams.get("after_id") || 0));
  const limit = Math.min(
    50,
    Math.max(10, Number(url.searchParams.get("limit") || 30))
  );
  const columns =
    "SELECT id, received_at, user_key, utterance, response_text, " +
    "response_status, response_ms, shared_to_group FROM api_request_log ";
  const filters = [];
  const bindings = [];
  if (afterId) {
    filters.push("id > ?");
    bindings.push(afterId);
  } else if (beforeId) {
    filters.push("id < ?");
    bindings.push(beforeId);
  }
  if (query) {
    filters.push(
      "(instr(COALESCE(utterance, ''), ?) > 0 OR " +
      "instr(COALESCE(response_text, ''), ?) > 0 OR " +
      "instr(COALESCE(user_key, ''), ?) > 0)"
    );
    bindings.push(query, query, query);
  }
  const order = afterId ? "ASC" : "DESC";
  const statement = env.DB.prepare(
    columns + (filters.length ? "WHERE " + filters.join(" AND ") + " " : "") +
    "ORDER BY id " + order + " LIMIT ?"
  );
  const result = await statement.bind(...bindings, limit).all();
  const items = rows(result).map((item) => ({
    ...item,
    display_time: koreaDateTime(item.received_at),
  }));
  return json({
    items,
    has_more: !afterId && items.length === limit,
  });
}

async function adminPage(request, env) {
  const url = new URL(request.url);
  const query = String(url.searchParams.get("q") || "").trim().slice(0, 100);
  const registeredStatus = await registeredMonitoringStatus(env);
  const registeredIncidents = registeredStatus.filter((item) => item.stale);
  const registeredStatusHtml = registeredIncidents.length
    ? `<section class="incident"><strong>감시 등록 날짜 조회 장애</strong><ul>${registeredIncidents.map((item) => {
      const lastNormal = item.last_checked_at
        ? koreaDateTime(item.last_checked_at) + " · " + item.age_label + " 전"
        : item.age_label;
      const reason = item.latest_error
        ? ` · 최근 실패: ${escapeHtml(item.latest_error)}`
        : "";
      return `<li><b>${escapeHtml(item.arrival_date)}</b> — 마지막 정상 확인: ${escapeHtml(lastNormal)}${reason}</li>`;
    }).join("")}</ul></section>`
    : registeredStatus.length
      ? `<div class="health-ok">감시 등록 날짜 정상 · ${registeredStatus.length}개 모두 최근 1시간 이내 확인</div>`
      : `<div class="health-ok">현재 감시 등록된 날짜가 없습니다.</div>`;
  const latestLogResult = await env.DB.prepare(
    "SELECT id, received_at FROM api_request_log ORDER BY id DESC LIMIT 1"
  ).all();
  const latestLog = rows(latestLogResult)[0] || {};
  const queueStateResult = await env.DB.prepare(
    "SELECT last_consumed_id, updated_at, agent_id " +
    "FROM kakao_queue_state WHERE id = 1"
  ).all();
  const queueState = rows(queueStateResult)[0] || {
    last_consumed_id: 0,
    updated_at: null,
    agent_id: "미연결",
  };
  const queueResult = await env.DB.prepare(
    "SELECT id, created_at, utterance, response_text " +
    "FROM kakao_reply_outbox WHERE id > ? ORDER BY id LIMIT 30"
  ).bind(Number(queueState.last_consumed_id || 0)).all();
  const queueRows = rows(queueResult);
  const queueTableRows = queueRows.map((item) => `<tr>
    <td class="queue-id">${escapeHtml(item.id)}</td>
    <td class="time">${escapeHtml(koreaDateTime(item.created_at))}</td>
    <td><pre>${escapeHtml(item.utterance || "(빈 입력)")}</pre></td>
    <td><pre>${escapeHtml(item.response_text)}</pre></td>
    <td><span class="status waiting">전송 대기</span></td>
  </tr>`).join("");
  const html = `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>밀라이두 챗봇 관리자</title>
  <style>
    :root { color-scheme: light; --ink:#172033; --muted:#657089; --line:#dfe4ec; --blue:#3159d8; --red:#b42318; --bg:#f5f7fb; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Malgun Gothic",sans-serif; }
    main { width:min(1500px,96vw); margin:24px auto 48px; }
    header { display:flex; gap:20px; justify-content:space-between; align-items:end; margin-bottom:18px; }
    h1 { margin:0 0 4px; font-size:25px; }
    h2 { margin:24px 0 10px; font-size:19px; }
    .sub { color:var(--muted); }
    .cards { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-bottom:16px; }
    .card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
    .card b { display:block; font-size:22px; margin-top:3px; }
    form { display:flex; gap:8px; margin-bottom:14px; }
    input { flex:1; min-width:0; border:1px solid #bcc5d5; border-radius:9px; padding:10px 12px; font:inherit; }
    button,a.button { border:0; border-radius:9px; padding:10px 14px; color:#fff; background:var(--blue); text-decoration:none; cursor:pointer; font:inherit; }
    button.danger { background:var(--red); }
    form.reset { align-items:center; justify-content:flex-end; background:#fff4f2; border:1px solid #f3c6c0; border-radius:10px; padding:10px 12px; }
    form.reset label { display:flex; align-items:center; gap:7px; margin-right:auto; color:#7a271a; }
    form.reset input { flex:none; width:18px; height:18px; }
    .table-wrap { overflow:auto; background:#fff; border:1px solid var(--line); border-radius:12px; }
    table { width:100%; border-collapse:collapse; min-width:1000px; }
    th,td { padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { position:sticky; top:0; background:#eef2f8; z-index:1; }
    pre { margin:0; max-width:480px; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }
    .time { width:155px; white-space:nowrap; }
    .user { width:145px; white-space:nowrap; }
    .status { display:inline-block; border-radius:999px; padding:2px 8px; font-weight:700; }
    .status.ok { color:#146c43; background:#dff5e9; }
    .status.error { color:#a52b2b; background:#fde5e5; }
    .status.waiting { color:#854a0e; background:#fff0c2; }
    .status.muted { color:#596579; background:#edf0f4; }
    .queue-id { width:70px; font-weight:700; }
    .notice { border-radius:10px; padding:10px 12px; color:#146c43; background:#dff5e9; margin-bottom:12px; }
    .incident { border:2px solid #d92d20; border-radius:12px; padding:13px 16px; color:#912018; background:#fff1f0; margin-bottom:16px; }
    .incident strong { display:block; font-size:18px; margin-bottom:5px; }
    .incident ul { margin:0; padding-left:21px; }
    .health-ok { border:1px solid #9ad5b4; border-radius:10px; padding:10px 12px; color:#146c43; background:#eaf8f0; margin-bottom:16px; }
    .load-state { padding:14px; text-align:center; color:var(--muted); }
    small,.empty { color:var(--muted); }
    @media (max-width:700px) { header { display:block; } .cards { grid-template-columns:1fr; } main { margin-top:14px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div><h1>밀라이두 챗봇 관리자</h1><div class="sub">최근 기록만 부분 조회 · 아래로 내리면 30개씩 추가 · 사용자 번호는 익명 처리</div></div>
    <a class="button" href="/admin">새로고침</a>
  </header>
  ${registeredStatusHtml}
  <section class="cards">
    <div class="card">최근 입력 ID<b>${latestLog.id ? "#" + Number(latestLog.id) : "없음"}</b></div>
    <div class="card">현재 표시<b id="loaded-count">0건</b></div>
    <div class="card">최근 입력<b>${escapeHtml(latestLog.received_at ? koreaDateTime(latestLog.received_at) : "없음")}</b></div>
  </section>
  ${url.searchParams.get("queue_reset") === "1" ? '<div class="notice">대기 중이던 답변 큐를 초기화했습니다.</div>' : ""}
  <h2>단체방 답변 큐</h2>
  <section class="cards">
    <div class="card">마지막 처리 ID<b>${Number(queueState.last_consumed_id || 0)}</b></div>
    <div class="card">전송 대기<b>${queueRows.length.toLocaleString("ko-KR")}${queueRows.length === 30 ? "+" : ""}건</b></div>
    <div class="card">PC 상태 갱신<b>${escapeHtml(queueState.updated_at ? koreaDateTime(queueState.updated_at) : "미연결")}</b><small>${escapeHtml(queueState.agent_id || "")}</small></div>
  </section>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>등록 시각</th><th>사용자 입력</th><th>단체방 전송 내용</th><th>상태</th></tr></thead>
      <tbody>${queueTableRows || '<tr><td colspan="5" class="empty">대기 중인 답변이 없습니다.</td></tr>'}</tbody>
    </table>
  </div>
  <form class="reset" method="post" action="/admin/queue/reset">
    <label><input type="checkbox" name="confirmed" value="yes" required>아직 전송하지 않은 답변을 모두 삭제합니다.</label>
    <button class="danger" type="submit">대기 큐 초기화</button>
  </form>
  <h2>사용자 입력 기록</h2>
  <form id="log-search-form" method="get" action="/admin">
    <input id="log-search" name="q" value="${escapeHtml(query)}" placeholder="사용자 번호, 명령, 답변 검색">
    <button type="submit">검색</button>
  </form>
  <div class="table-wrap">
    <table>
      <thead><tr><th>입력 시각</th><th>사용자</th><th>사용자 입력</th><th>봇 답변</th><th>단체방</th><th>처리</th></tr></thead>
      <tbody id="log-rows"></tbody>
    </table>
    <div id="log-load-state" class="load-state">최근 기록을 불러오는 중입니다.</div>
  </div>
</main>
<script>
(() => {
  const tbody = document.getElementById("log-rows");
  const loadState = document.getElementById("log-load-state");
  const loadedCount = document.getElementById("loaded-count");
  const searchInput = document.getElementById("log-search");
  const searchForm = document.getElementById("log-search-form");
  const initialLatestId = ${Number(latestLog.id || 0)};
  let beforeId = 0;
  let latestId = initialLatestId;
  let loading = false;
  let hasMore = true;

  function addTextCell(row, value, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value == null ? "" : String(value);
    row.appendChild(cell);
    return cell;
  }

  function logRow(item) {
    const row = document.createElement("tr");
    row.dataset.logId = String(item.id);
    addTextCell(row, item.display_time || item.received_at || "-", "time");
    addTextCell(row, item.user_key || "이전 기록", "user");
    const utterance = addTextCell(row, "");
    const utterancePre = document.createElement("pre");
    utterancePre.textContent = item.utterance || "(빈 입력)";
    utterance.appendChild(utterancePre);
    const answer = addTextCell(row, "");
    const answerPre = document.createElement("pre");
    answerPre.textContent = item.response_text || "이전 기록: 답변 내용 미저장";
    answer.appendChild(answerPre);
    const share = addTextCell(row, "");
    const shareBadge = document.createElement("span");
    const shared = Number(item.shared_to_group) === 1;
    shareBadge.className = "status " + (shared ? "ok" : "muted");
    shareBadge.textContent = item.shared_to_group == null
      ? "이전 기록" : shared ? "공유" : "미공유";
    share.appendChild(shareBadge);
    const processing = addTextCell(row, "");
    const statusBadge = document.createElement("span");
    statusBadge.className = "status " +
      (Number(item.response_status) === 200 ? "ok" : "error");
    statusBadge.textContent = String(item.response_status ?? "-");
    processing.appendChild(statusBadge);
    processing.appendChild(document.createElement("br"));
    const duration = document.createElement("small");
    duration.textContent = String(item.response_ms ?? "-") + "ms";
    processing.appendChild(duration);
    return row;
  }

  function updateCount() {
    loadedCount.textContent = tbody.querySelectorAll("tr[data-log-id]").length + "건";
  }

  async function requestLogs(parameters) {
    const url = new URL("/admin/api/logs", location.origin);
    url.searchParams.set("limit", "30");
    const query = searchInput.value.trim();
    if (query) url.searchParams.set("q", query);
    for (const [key, value] of Object.entries(parameters)) {
      if (value) url.searchParams.set(key, String(value));
    }
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }

  async function loadOlder(reset = false) {
    if (loading || (!hasMore && !reset)) return;
    if (reset) {
      tbody.textContent = "";
      beforeId = 0;
      latestId = initialLatestId;
      hasMore = true;
      updateCount();
    }
    loading = true;
    loadState.textContent = "기록을 불러오는 중입니다.";
    try {
      const data = await requestLogs(beforeId ? { before_id: beforeId } : {});
      for (const item of data.items) {
        tbody.appendChild(logRow(item));
        beforeId = beforeId ? Math.min(beforeId, Number(item.id)) : Number(item.id);
        latestId = Math.max(latestId, Number(item.id));
      }
      hasMore = Boolean(data.has_more);
      loadState.textContent = data.items.length
        ? hasMore ? "아래로 내리면 이전 기록을 더 불러옵니다." : "마지막 기록입니다."
        : "표시할 기록이 없습니다.";
      updateCount();
    } catch (error) {
      loadState.textContent = "기록 조회 실패 · 새로고침 후 다시 시도해 주세요.";
    } finally {
      loading = false;
    }
  }

  async function loadNew() {
    if (loading || !latestId || searchInput.value.trim()) return;
    try {
      const data = await requestLogs({ after_id: latestId });
      for (const item of data.items) {
        tbody.insertBefore(logRow(item), tbody.firstChild);
        latestId = Math.max(latestId, Number(item.id));
      }
      if (data.items.length) updateCount();
    } catch (error) {
      // 다음 60초 주기에 다시 시도합니다.
    }
  }

  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const nextUrl = new URL(location.href);
    const query = searchInput.value.trim();
    if (query) nextUrl.searchParams.set("q", query);
    else nextUrl.searchParams.delete("q");
    history.replaceState(null, "", nextUrl);
    loadOlder(true);
  });
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadOlder();
  }, { rootMargin: "300px" });
  observer.observe(loadState);
  loadOlder();
  setInterval(loadNew, 60000);
})();
</script>
</body>
</html>`;
  return new Response(html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    },
  });
}

async function monitoredDates(env) {
  const result = await env.DB.prepare(
    "SELECT arrival_date, added_at FROM monitored_dates ORDER BY arrival_date"
  ).all();
  return rows(result);
}

async function watchListText(env) {
  const items = await registeredMonitoringStatus(env);
  if (!items.length) return "[객실 감시 목록]\n등록된 날짜가 없습니다.";
  const lines = ["[객실 감시 목록 · " + items.length + "개]"];
  for (const item of items) {
    const price = item.price ? " · " + item.price : "";
    lines.push("- " + item.arrival_date + " " + statusSymbol(item.status) + price);

    if (item.stale) {
      if (item.last_checked_at) {
        lines.push(
          "  ⚠️ 조회 장애 · 마지막 정상 " +
          koreaShortDateTime(item.last_checked_at) + " (" + item.age_label + " 전)"
        );
      } else {
        lines.push("  ⚠️ 조회 장애 · 정상 확인 기록 없음");
      }
      if (item.attempt_status === "error") {
        lines.push(
          "  최근 실패 " + koreaShortDateTime(item.attempt_checked_at) +
          " · " + item.latest_error
        );
      } else if (item.attempt_checked_at) {
        lines.push(
          "  최근 시도 " + koreaShortDateTime(item.attempt_checked_at) +
          " · " + statusSymbol(item.attempt_status)
        );
      }
      continue;
    }

    if (item.attempt_status === "error") {
      lines.push(
        "  최근 시도 " + koreaShortDateTime(item.attempt_checked_at) +
        " · 실패(" + item.latest_error + ")"
      );
      lines.push(
        "  마지막 정상 " + koreaShortDateTime(item.last_checked_at) +
        " · " + item.age_label + " 전"
      );
    } else {
      lines.push(
        "  최근 조회 " +
        koreaShortDateTime(item.attempt_checked_at || item.last_checked_at) +
        " · " + item.age_label + " 전 · 정상"
      );
    }
  }
  return lines.join("\n").slice(0, 1000);
}

async function dateStatusText(env, arrivalDate) {
  const result = await env.DB.prepare(
    "SELECT m.arrival_date AS watched, c.status, c.price, c.total, c.url, " +
    "c.detail, c.last_checked_at, c.last_changed_at " +
    "FROM (SELECT ? AS requested_date) AS requested " +
    "LEFT JOIN monitored_dates AS m ON m.arrival_date = requested.requested_date " +
    "LEFT JOIN availability_current AS c ON c.arrival_date = requested.requested_date"
  ).bind(arrivalDate).all();
  const item = rows(result)[0] || {};
  const lines = [
    "[객실 조회 · " + arrivalDate + "]",
    "감시 상태: " + (item.watched ? "등록됨" : "미등록"),
  ];
  if (!item.last_checked_at) {
    lines.push("저장된 조회 결과가 없습니다.");
    if (item.watched) lines.push("다음 감시 주기부터 확인합니다.");
    return lines.join("\n");
  }
  lines.push("최근 결과: " + statusSymbol(item.status));
  if (item.price) lines.push("1박 요금: " + item.price);
  if (item.total && item.total !== item.price) lines.push("총액: " + item.total);
  lines.push("조회 시각: " + item.last_checked_at);
  const ageSeconds = Math.max(
    0,
    Math.floor((Date.now() - Date.parse(item.last_checked_at)) / 1000)
  );
  if (ageSeconds > Number(env.STALE_AFTER_SECONDS || 600)) {
    lines.push("⚠️ 최신 조회가 " + ageSeconds + "초 전입니다.");
  }
  if (!["available", "sold_out"].includes(item.status) && item.detail) {
    lines.push("상세: " + item.detail);
  }
  return lines.join("\n");
}

function reportStatus(status) {
  if (status === "available") return "가능";
  if (status === "sold_out") return "매진";
  if (status === "error") return "오류";
  return status || "-";
}

function statusChangeAlertText(month, changes) {
  const [year, monthNumber] = month.split("-").map(Number);
  const lines = ["[" + year + "년 " + monthNumber + "월 객실 상태 변경]"];
  for (const change of changes) {
    const price = change.newStatus === "available" && change.price
      ? " · " + change.price
      : "";
    lines.push(
      "- " + change.arrivalDate + " " +
      statusSymbol(change.oldStatus) + " → " +
      statusSymbol(change.newStatus) + price
    );
  }
  lines.push("상태가 바뀐 시점에 한 번만 알립니다.");
  return lines.join("\n").slice(0, 1000);
}

function koreaTime(isoValue) {
  const parsed = Date.parse(isoValue);
  if (Number.isNaN(parsed)) return "--:--";
  return new Date(parsed + 9 * 60 * 60 * 1000).toISOString().slice(11, 16);
}

async function monthlyOverviewText(env, month) {
  const [year, monthNumber] = month.split("-").map(Number);
  const nextMonth = new Date(Date.UTC(year, monthNumber, 1))
    .toISOString().slice(0, 7) + "-01";
  const daysInMonth = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  const result = await env.DB.prepare(
    "SELECT status, COUNT(*) AS count FROM availability_current " +
    "WHERE arrival_date >= ? AND arrival_date < ? GROUP BY status"
  ).bind(month + "-01", nextMonth).all();
  const counts = Object.fromEntries(
    rows(result).map((item) => [item.status, Number(item.count || 0)])
  );
  const available = counts.available || 0;
  const soldOut = counts.sold_out || 0;
  const unknown = Math.max(0, daysInMonth - available - soldOut);
  return [
    "[" + year + "년 " + monthNumber + "월 전체 현황]",
    "예약 가능 " + available + "일 · 매진 " + soldOut +
      "일 · 미확인 " + unknown + "일",
  ].join("\n");
}

function compactAttemptError(detail) {
  const text = String(detail || "").replace(/\s+/g, " ").trim();
  const http = text.match(/HTTP\s+\d{3}/i);
  if (http) return http[0].toUpperCase();
  if (/Hilton Page Reference Code|SOMETHING WENT WRONG|Reference No\./i.test(text)) {
    return "Hilton 차단 화면";
  }
  if (/502 Bad Gateway/i.test(text)) return "Hilton 오류(502)";
  if (/TargetClosed|Target page, context or browser has been closed/i.test(text)) {
    return "브라우저 종료";
  }
  if (/Page\.goto.*Timeout|navigation.*timeout/i.test(text)) {
    return "페이지 이동 시간 초과";
  }
  if (/timeout|time.?out|시간.?초과/i.test(text)) return "시간 초과";
  if (/찾지 못|not found/i.test(text)) return "객실 카드 미표시";
  return text ? text.slice(0, 10) : "원인 미상";
}

async function monthlyDetailText(env, month) {
  const [year, monthNumber] = month.split("-").map(Number);
  const nextMonth = new Date(Date.UTC(year, monthNumber, 1))
    .toISOString().slice(0, 7) + "-01";
  const daysInMonth = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  const result = await env.DB.prepare(
    "SELECT dates.arrival_date, c.status, c.price, " +
    "a.status AS attempt_status, a.detail AS attempt_detail " +
    "FROM (" +
      "SELECT arrival_date FROM availability_current " +
      "WHERE arrival_date >= ? AND arrival_date < ? " +
      "UNION SELECT arrival_date FROM availability_latest_attempt " +
      "WHERE arrival_date >= ? AND arrival_date < ?" +
    ") AS dates " +
    "LEFT JOIN availability_current AS c " +
      "ON c.arrival_date = dates.arrival_date " +
    "LEFT JOIN availability_latest_attempt AS a " +
      "ON a.arrival_date = dates.arrival_date " +
    "ORDER BY dates.arrival_date"
  ).bind(month + "-01", nextMonth, month + "-01", nextMonth).all();
  const byDate = new Map(rows(result).map((item) => [item.arrival_date, item]));
  const lines = ["[" + year + "년 " + monthNumber + "월 자세히 보기]"];
  for (let day = 1; day <= daysInMonth; day += 1) {
    const arrivalDate = month + "-" + String(day).padStart(2, "0");
    const item = byDate.get(arrivalDate) || {};
    let status;
    let price = "";
    if (item.attempt_status === "error") {
      const previous = item.status ? "(이전 " + reportStatus(item.status) + ")" : "";
      status = "조회 실패" + previous + " · " +
        compactAttemptError(item.attempt_detail);
    } else {
      const effectiveStatus = item.status || item.attempt_status;
      status = effectiveStatus ? statusSymbol(effectiveStatus) : "미확인";
      price = effectiveStatus === "available" && item.price
        ? " · " + item.price
        : "";
    }
    lines.push(
      String(monthNumber).padStart(2, "0") + "/" +
      String(day).padStart(2, "0") + " " + status + price
    );
  }
  return lines.join("\n").slice(0, 1000);
}

async function dailyReportText(env, reportDate) {
  const summaryResult = await env.DB.prepare(
    "SELECT COUNT(*) AS scan_count, " +
    "COALESCE(SUM(success_count), 0) AS success_count, " +
    "COALESCE(SUM(error_count), 0) AS error_count, " +
    "COALESCE(SUM(baseline_count), 0) AS baseline_count, " +
    "COALESCE(SUM(change_count), 0) AS change_count, " +
    "COALESCE(AVG(duration_ms), 0) AS average_duration_ms " +
    "FROM scan_runs WHERE date(completed_at, '+9 hours') = ?"
  ).bind(reportDate).all();
  const summary = rows(summaryResult)[0] || {};
  const eventResult = await env.DB.prepare(
    "SELECT observed_at, arrival_date, old_status, new_status, " +
    "old_price, new_price FROM availability_events " +
    "WHERE event_type = 'change' AND date(observed_at, '+9 hours') = ? " +
    "ORDER BY id LIMIT 21"
  ).bind(reportDate).all();
  const events = rows(eventResult);
  const totalChanges = Number(summary.change_count || 0);
  const lines = [
    "[밀라이두 일일 변경 보고 · " + reportDate + "]",
    "스캔 " + Number(summary.scan_count || 0).toLocaleString("ko-KR") + "회",
    "성공/오류 " + Number(summary.success_count || 0).toLocaleString("ko-KR") +
      "/" + Number(summary.error_count || 0).toLocaleString("ko-KR") + "건",
    "평균 소요 " + (Number(summary.average_duration_ms || 0) / 1000).toFixed(1) + "초",
    "초기값 " + Number(summary.baseline_count || 0).toLocaleString("ko-KR") +
      "건 / 변경 " + totalChanges.toLocaleString("ko-KR") + "건",
    "",
  ];
  if (!events.length) {
    lines.push("예약 상태 또는 가격 변경 없음");
  } else {
    for (const event of events.slice(0, 20)) {
      const arrival = String(event.arrival_date || "").slice(5).replace("-", "/");
      const price = event.old_price !== event.new_price
        ? " (" + (event.old_price || "-") + "→" + (event.new_price || "-") + ")"
        : "";
      lines.push(
        koreaTime(event.observed_at) + " " + arrival + " " +
        reportStatus(event.old_status) + "→" + reportStatus(event.new_status) + price
      );
    }
    if (totalChanges > 20) lines.push("외 " + (totalChanges - 20) + "건");
  }
  const overview = "\n\n" + await monthlyOverviewText(
    env,
    String(env.REPORT_MONTH || "2027-03")
  );
  const maxBodyLength = Math.max(0, 1000 - overview.length);
  let body = lines.join("\n");
  if (body.length > maxBodyLength) {
    body = body.slice(0, Math.max(0, maxBodyLength - 4)).trimEnd() + "\n...";
  }
  return body + overview;
}

function commandDate(command, prefix) {
  const value = command.slice(prefix.length).trim();
  if (!validIsoDate(value)) {
    return { error: "날짜는 YYYY-MM-DD 형식으로 입력해 주세요." };
  }
  return { value };
}

async function groupSharingEnabled(env, userKey) {
  const result = await env.DB.prepare(
    "SELECT share_enabled FROM user_group_share_settings WHERE user_key = ?"
  ).bind(userKey).all();
  const setting = rows(result)[0];
  return !setting || Number(setting.share_enabled) === 1;
}

async function setGroupSharing(env, userKey, enabled) {
  await env.DB.prepare(
    "INSERT INTO user_group_share_settings (user_key, share_enabled, updated_at) " +
    "VALUES (?, ?, ?) ON CONFLICT(user_key) DO UPDATE SET " +
    "share_enabled = excluded.share_enabled, updated_at = excluded.updated_at"
  ).bind(userKey, enabled ? 1 : 0, nowIso()).run();
}

function isGroupSharingCommand(utterance) {
  const command = String(utterance || "").trim().replace(/\s+/g, " ");
  return [
    "단톡방 공유",
    "단톡방 공유 켜기",
    "단톡방 공유 끄기",
  ].includes(command);
}

async function handleCommand(env, utterance, userKey = "알 수 없음") {
  const command = String(utterance || "").trim().replace(/\s+/g, " ");
  if (command === "단톡방 공유") {
    const enabled = await groupSharingEnabled(env, userKey);
    return [
      "[단톡방 공유 설정]",
      "적용 범위: 내 1:1 챗봇 명령 결과",
      "현재: " + (enabled ? "공유 중" : "공유 안 함"),
      "변경: 단톡방 공유 켜기 / 단톡방 공유 끄기",
      "객실 감시 자동 알림에는 영향이 없습니다.",
    ].join("\n");
  }
  if (command === "단톡방 공유 켜기") {
    await setGroupSharing(env, userKey, true);
    return "단톡방 공유를 켰습니다.\n앞으로 내 1:1 챗봇 명령 결과가 단체방에 공유됩니다.";
  }
  if (command === "단톡방 공유 끄기") {
    await setGroupSharing(env, userKey, false);
    return [
      "단톡방 공유를 껐습니다.",
      "내 명령 결과는 1:1 챗봇에만 표시됩니다.",
      "객실 감시 자동 알림은 계속 전송됩니다.",
      "자동 알림을 끄려면 객실 감시 해제 YYYY-MM-DD를 사용하세요.",
    ].join("\n");
  }
  if (command === "객실 감시") {
    return [
      "[객실 감시 등록]",
      "감시할 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.",
      "예: 객실 감시 2027-03-17",
    ].join("\n");
  }
  if (command === "객실 감시 해제") {
    return [
      "[객실 감시 해제]",
      "해제할 날짜를 YYYY-MM-DD 형식으로 입력해 주세요.",
      "예: 객실 감시 해제 2027-03-17",
    ].join("\n");
  }
  if (command === "객실 감시 목록") return watchListText(env);
  if (command === "일일 변경 보고") return dailyReportText(env, koreaToday());
  if (command === "클라우드플레어 사용량" || command === "CF 사용량") {
    return cloudflareUsageText(env);
  }
  if (command === "3월 자세히 보기") {
    return monthlyDetailText(env, String(env.REPORT_MONTH || "2027-03"));
  }

  if (command.startsWith("일일 변경 보고 ")) {
    const parsed = commandDate(command, "일일 변경 보고 ");
    if (parsed.error) return "입력 오류: " + parsed.error + "\n\n" + commandHelp();
    return dailyReportText(env, parsed.value);
  }

  if (command.startsWith("객실 감시 해제 ")) {
    const parsed = commandDate(command, "객실 감시 해제 ");
    if (parsed.error) return "입력 오류: " + parsed.error + "\n\n" + commandHelp();
    const result = await env.DB.prepare(
      "DELETE FROM monitored_dates WHERE arrival_date = ?"
    ).bind(parsed.value).run();
    const changed = Number(result && result.meta && result.meta.changes || 0) > 0;
    return changed
      ? "객실 감시를 해제했습니다.\n날짜: " + parsed.value
      : "감시 중인 날짜가 아닙니다.\n날짜: " + parsed.value;
  }

  if (command.startsWith("객실 감시 ")) {
    const parsed = commandDate(command, "객실 감시 ");
    if (parsed.error) return "입력 오류: " + parsed.error + "\n\n" + commandHelp();
    if (parsed.value < koreaToday()) {
      return "입력 오류: 지난 날짜는 감시할 수 없습니다.\n\n" + commandHelp();
    }
    const result = await env.DB.prepare(
      "INSERT OR IGNORE INTO monitored_dates (arrival_date, added_at) VALUES (?, ?)"
    ).bind(parsed.value, nowIso()).run();
    const changed = Number(result && result.meta && result.meta.changes || 0) > 0;
    return changed
      ? "객실 감시를 등록했습니다.\n날짜: " + parsed.value +
        "\n다음 감시 주기부터 확인합니다."
      : "이미 감시 중인 날짜입니다.\n날짜: " + parsed.value;
  }

  if (command.startsWith("객실 조회 ")) {
    const parsed = commandDate(command, "객실 조회 ");
    if (parsed.error) return "입력 오류: " + parsed.error + "\n\n" + commandHelp();
    return dateStatusText(env, parsed.value);
  }

  return commandHelp();
}

async function recordKakaoReply(env, utterance, responseText) {
  await env.DB.prepare(
    "INSERT INTO kakao_reply_outbox (created_at, utterance, response_text) " +
    "VALUES (?, ?, ?)"
  ).bind(
    nowIso(),
    String(utterance || "").slice(0, 500),
    String(responseText || "").slice(0, 1000)
  ).run();
}

async function kakaoRepliesAfter(env, afterId) {
  const safeAfterId = Math.max(
    0,
    Number(afterId || 0),
    await kakaoQueueCursor(env)
  );
  const result = await env.DB.prepare(
    "SELECT id, created_at, utterance, response_text " +
    "FROM kakao_reply_outbox WHERE id > ? ORDER BY id LIMIT 20"
  ).bind(safeAfterId).all();
  return rows(result);
}

function cleanAgentId(value) {
  const agentId = String(value || "").trim();
  return /^[A-Za-z0-9._-]{1,80}$/.test(agentId) ? agentId : null;
}

async function acquireLease(env, agentId) {
  const now = nowIso();
  const leaseUntil = new Date(
    Date.now() + Number(env.LEASE_SECONDS || 900) * 1000
  ).toISOString();
  await env.DB.prepare(
    "INSERT INTO monitor_lease (id, agent_id, lease_until, updated_at) " +
    "VALUES (1, ?, ?, ?) " +
    "ON CONFLICT(id) DO UPDATE SET " +
    "agent_id = excluded.agent_id, lease_until = excluded.lease_until, " +
    "updated_at = excluded.updated_at " +
    "WHERE monitor_lease.agent_id = excluded.agent_id OR monitor_lease.lease_until < ?"
  ).bind(agentId, leaseUntil, now, now).run();
  const result = await env.DB.prepare(
    "SELECT agent_id, lease_until FROM monitor_lease WHERE id = 1"
  ).all();
  const lease = rows(result)[0];
  const granted = Boolean(
    lease && lease.agent_id === agentId && lease.lease_until >= now
  );
  return {
    granted,
    lease,
    watch_dates: granted ? await monitoredDates(env) : [],
  };
}

async function verifyLease(env, agentId) {
  const result = await env.DB.prepare(
    "SELECT agent_id, lease_until FROM monitor_lease WHERE id = 1"
  ).all();
  const lease = rows(result)[0];
  return Boolean(
    lease && lease.agent_id === agentId && lease.lease_until >= nowIso()
  );
}

async function submitResults(env, payload) {
  const agentId = cleanAgentId(payload && payload.agent_id);
  const scanId = String(payload && payload.scan_id || "").trim();
  const results = Array.isArray(payload && payload.results) ? payload.results : [];
  if (!agentId || !/^[A-Za-z0-9._:-]{8,160}$/.test(scanId)) {
    return {
      error: "agent_id 또는 scan_id 형식이 잘못되었습니다.",
      statusCode: 400,
    };
  }
  if (!(await verifyLease(env, agentId))) {
    return {
      error: "이 에이전트의 감시 임대가 만료되었거나 다른 PC가 사용 중입니다.",
      statusCode: 409,
    };
  }
  if (results.length > 100) {
    return { error: "한 번에 최대 100개 날짜만 제출할 수 있습니다.", statusCode: 400 };
  }

  const existingScan = await env.DB.prepare(
    "SELECT scan_id FROM scan_runs WHERE scan_id = ?"
  ).bind(scanId).all();
  if (rows(existingScan).length) {
    return { duplicate: true, scan_id: scanId, statusCode: 200 };
  }

  const observedAt = String(payload.completed_at || nowIso());
  let successCount = 0;
  let errorCount = 0;
  let baselineCount = 0;
  let changeCount = 0;
  const monitoredMonth = String(env.REPORT_MONTH || "2027-03");
  const roomStatusChanges = [];

  for (const raw of results) {
    const arrivalDate = String(raw && raw.arrival_date || "");
    const departureDate = String(raw && raw.departure_date || "");
    const status = String(raw && raw.status || "");
    if (
      !validIsoDate(arrivalDate) ||
      !validIsoDate(departureDate) ||
      !["available", "sold_out", "error"].includes(status)
    ) {
      errorCount += 1;
      continue;
    }
    const price = raw.price == null ? null : String(raw.price).slice(0, 200);
    const total = raw.total == null ? null : String(raw.total).slice(0, 200);
    await env.DB.prepare(
      "INSERT INTO availability_latest_attempt (" +
      "arrival_date, departure_date, status, price, total, url, detail, " +
      "checked_at, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(arrival_date) DO UPDATE SET " +
      "departure_date = excluded.departure_date, status = excluded.status, " +
      "price = excluded.price, total = excluded.total, url = excluded.url, " +
      "detail = excluded.detail, checked_at = excluded.checked_at, " +
      "agent_id = excluded.agent_id"
    ).bind(
      arrivalDate,
      departureDate,
      status,
      price,
      total,
      String(raw.url || "").slice(0, 2000),
      String(raw.detail || "").slice(0, 1000),
      observedAt,
      agentId
    ).run();
    if (status === "error") {
      errorCount += 1;
      continue;
    }

    successCount += 1;
    const previousResult = await env.DB.prepare(
      "SELECT status, price, total, first_seen_at, last_changed_at " +
      "FROM availability_current WHERE arrival_date = ?"
    ).bind(arrivalDate).all();
    const previous = rows(previousResult)[0];
    const changed = !previous ||
      previous.status !== status ||
      previous.price !== price ||
      previous.total !== total;
    const eventType = !previous ? "baseline" : changed ? "change" : null;
    if (!previous) baselineCount += 1;
    else if (changed) changeCount += 1;
    if (
      previous &&
      previous.status !== status &&
      arrivalDate.startsWith(monitoredMonth + "-")
    ) {
      roomStatusChanges.push({
        arrivalDate,
        oldStatus: previous.status,
        newStatus: status,
        price,
      });
    }

    if (eventType) {
      await env.DB.prepare(
        "INSERT OR IGNORE INTO availability_events (" +
        "scan_id, observed_at, event_type, arrival_date, departure_date, " +
        "old_status, new_status, old_price, new_price, old_total, new_total, " +
        "url, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
      ).bind(
        scanId,
        observedAt,
        eventType,
        arrivalDate,
        departureDate,
        previous ? previous.status : null,
        status,
        previous ? previous.price : null,
        price,
        previous ? previous.total : null,
        total,
        String(raw.url || "").slice(0, 2000),
        agentId
      ).run();
    }

    await env.DB.prepare(
      "INSERT INTO availability_current (" +
      "arrival_date, departure_date, status, price, total, url, detail, " +
      "first_seen_at, last_checked_at, last_changed_at, agent_id" +
      ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(arrival_date) DO UPDATE SET " +
      "departure_date = excluded.departure_date, status = excluded.status, " +
      "price = excluded.price, total = excluded.total, url = excluded.url, " +
      "detail = excluded.detail, last_checked_at = excluded.last_checked_at, " +
      "last_changed_at = excluded.last_changed_at, agent_id = excluded.agent_id"
    ).bind(
      arrivalDate,
      departureDate,
      status,
      price,
      total,
      String(raw.url || "").slice(0, 2000),
      String(raw.detail || "").slice(0, 1000),
      previous ? previous.first_seen_at : observedAt,
      observedAt,
      changed ? observedAt : previous.last_changed_at,
      agentId
    ).run();
  }

  if (roomStatusChanges.length) {
    roomStatusChanges.sort((left, right) =>
      left.arrivalDate.localeCompare(right.arrivalDate)
    );
    await recordKakaoReply(
      env,
      monitoredMonth + " 자동 감시 상태 변경",
      statusChangeAlertText(monitoredMonth, roomStatusChanges)
    );
  }

  await env.DB.prepare(
    "INSERT INTO scan_runs (" +
    "scan_id, agent_id, started_at, completed_at, duration_ms, total_nights, " +
    "success_count, error_count, baseline_count, change_count" +
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(
    scanId,
    agentId,
    String(payload.started_at || observedAt),
    observedAt,
    Math.max(0, Number(payload.duration_ms || 0)),
    results.length,
    successCount,
    errorCount,
    baselineCount,
    changeCount
  ).run();

  return {
    scan_id: scanId,
    success_count: successCount,
    error_count: errorCount,
    baseline_count: baselineCount,
    change_count: changeCount,
    statusCode: 200,
  };
}

async function handleKakao(request, env) {
  if (!authorized(request, env.KAKAO_SKILL_API_KEY, "x-api-key")) {
    return json({ detail: "잘못된 API 키입니다." }, 403);
  }
  const started = Date.now();
  let utterance = "";
  let userKey = "알 수 없음";
  let responseText = "";
  let responseStatus = 200;
  let shareToGroup = true;
  try {
    const payload = await request.json();
    utterance = String(
      payload && payload.userRequest && payload.userRequest.utterance || ""
    ).trim();
    userKey = await anonymousUserKey(
      payload && payload.userRequest && payload.userRequest.user &&
      payload.userRequest.user.id
    );
    responseText = await handleCommand(env, utterance, userKey);
  } catch (error) {
    responseStatus = 400;
    responseText = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
  try {
    shareToGroup = !isGroupSharingCommand(utterance) &&
      await groupSharingEnabled(env, userKey);
    if (shareToGroup) {
      await recordKakaoReply(env, utterance, responseText);
    }
  } catch (error) {
    console.error("카카오 답변 대기함 저장 실패", error);
  }
  try {
    await env.DB.prepare(
      "INSERT INTO api_request_log (" +
      "received_at, endpoint, utterance, response_status, response_ms, " +
      "user_key, response_text, shared_to_group" +
      ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      nowIso(),
      "/kakao/skill",
      utterance.slice(0, 500),
      responseStatus,
      Date.now() - started,
      userKey,
      responseText.slice(0, 1000),
      shareToGroup ? 1 : 0
    ).run();
  } catch (error) {
    console.error("카카오 요청 로그 저장 실패", error);
  }
  return json(kakaoResponse(responseText), responseStatus);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return Response.redirect(new URL("/admin", url).toString(), 302);
    }

    if (request.method === "GET" && url.pathname === "/healthz") {
      try {
        await env.DB.prepare("SELECT 1 AS ok").all();
        return json({ ok: true, service: "milaidhoo-kakao-bot" });
      } catch (error) {
        return json({ ok: false, detail: String(error) }, 503);
      }
    }

    if (request.method === "GET" && url.pathname === "/admin") {
      if (!adminAuthorized(request, env)) return adminUnauthorized();
      return adminPage(request, env);
    }

    if (request.method === "GET" && url.pathname === "/admin/api/logs") {
      if (!adminAuthorized(request, env)) return adminUnauthorized();
      return adminLogPage(request, env);
    }

    if (request.method === "POST" && url.pathname === "/admin/queue/reset") {
      if (!adminAuthorized(request, env)) return adminUnauthorized();
      return adminResetQueue(request, env);
    }

    if (request.method === "POST" && url.pathname === "/kakao/skill") {
      return handleKakao(request, env);
    }

    if (url.pathname.startsWith("/api/v1/agent/")) {
      if (!authorized(request, env.AGENT_API_KEY, "x-agent-key")) {
        return json({ detail: "잘못된 에이전트 키입니다." }, 403);
      }

      if (request.method === "POST" && url.pathname === "/api/v1/agent/lease") {
        const body = await request.json();
        const agentId = cleanAgentId(body && body.agent_id);
        if (!agentId) {
          return json({ detail: "agent_id 형식이 잘못되었습니다." }, 400);
        }
        return json(await acquireLease(env, agentId));
      }

      if (
        request.method === "GET" &&
        url.pathname === "/api/v1/agent/watch-dates"
      ) {
        return json({ watch_dates: await monitoredDates(env) });
      }

      if (
        request.method === "GET" &&
        url.pathname === "/api/v1/agent/kakao-replies"
      ) {
        const agentId = cleanAgentId(url.searchParams.get("agent_id"));
        if (!agentId) {
          return json({ detail: "agent_id 형식이 잘못되었습니다." }, 400);
        }
        if (!(await verifyLease(env, agentId))) {
          return json(
            { detail: "대화 큐는 현재 객실 감시 임대 소유자만 처리할 수 있습니다." },
            409
          );
        }
        return json({
          replies: await kakaoRepliesAfter(env, url.searchParams.get("after_id")),
        });
      }

      if (
        request.method === "GET" &&
        url.pathname === "/api/v1/agent/kakao-queue/cursor"
      ) {
        return json({ last_consumed_id: await kakaoQueueCursor(env) });
      }

      if (
        request.method === "POST" &&
        url.pathname === "/api/v1/agent/kakao-queue/cursor"
      ) {
        const body = await request.json();
        const agentId = cleanAgentId(body && body.agent_id);
        const lastConsumedId = Number(body && body.last_consumed_id);
        if (!agentId || !Number.isInteger(lastConsumedId) || lastConsumedId < 0) {
          return json({ detail: "큐 커서 형식이 잘못되었습니다." }, 400);
        }
        return json({
          last_consumed_id: await updateKakaoQueueCursor(
            env,
            agentId,
            lastConsumedId
          ),
        });
      }

      if (
        request.method === "POST" &&
        url.pathname === "/api/v1/agent/results"
      ) {
        const outcome = await submitResults(env, await request.json());
        const status = outcome.statusCode;
        delete outcome.statusCode;
        return json(outcome.error ? { detail: outcome.error } : outcome, status);
      }
    }

    return json({ detail: "Not Found" }, 404);
  },
};

export {
  commandHelp,
  cloudflareUsageText,
  dailyReportText,
  handleCommand,
  isGroupSharingCommand,
  kakaoResponse,
  monthlyDetailText,
  registeredMonitoringStatus,
  statusChangeAlertText,
  submitResults,
  validIsoDate,
};
