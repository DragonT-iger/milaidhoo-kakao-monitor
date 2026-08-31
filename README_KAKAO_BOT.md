# 밀라이두 조회 카카오톡 봇

## 구조

```text
카카오 챗봇 스킬 → Cloudflare Worker → D1 중앙 DB
                                      ↑
                    Windows Hilton 감시기(20분)
```

Hilton 실조회는 카카오 스킬의 5초 제한보다 오래 걸릴 수 있으므로, 챗봇은 감시기가 저장한 최신 결과를 즉시 반환합니다. 응답에는 마지막 조회 시각이 표시되며, 기본 20분 조회 주기에 여유를 둬 마지막 정상 결과가 30분을 넘으면 오래된 데이터 경고를 표시합니다.

현재 운영 스킬 주소는 다음과 같습니다.

```text
https://milaidhoo-kakao-bot.yongbeom.workers.dev/kakao/skill
```

로컬 FastAPI 서버는 비상용·개발용으로만 남겨 두었습니다. 실제 감시 날짜와 최신 상태는 Cloudflare D1을 기준으로 공유됩니다.

## 로컬 FastAPI 실행(선택 사항)

먼저 감시기를 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py
```

다른 창에서 API를 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn kakao_skill_server:app --host 127.0.0.1 --port 8000
```

또는 `start_monitor.bat`, `start_api.bat`을 각각 실행합니다.

## API

- `GET /healthz`: 프로세스 및 DB 상태
- `GET /api/v1/status`: 현재 감시 중인 날짜와 최신 객실 상태
- `GET /api/v1/changes?date=2026-08-08`: 일별 초기값·변경 이벤트와 합계
- `GET /api/v1/scans?date=2026-08-08`: 일별 스캔 실행 증빙
- `GET /api/v1/statistics/hourly`: 이전 데이터 호환용 시간대별 통계
- `POST /kakao/skill`: 카카오 챗봇 SkillPayload 처리
- `GET /docs`: 로컬 API 문서

## 보안 키

외부 공개 전 반드시 API 키와 사용자 해시 솔트를 환경변수로 설정합니다.

```powershell
$env:KAKAO_SKILL_API_KEY = '충분히-긴-임의의-문자열'
$env:KAKAO_USER_HASH_SALT = '별도의-긴-임의의-문자열'
```

챗봇 관리자센터의 스킬 설정에도 동일한 헤더를 등록합니다.

```text
x-api-key: KAKAO_SKILL_API_KEY의 값
```

API 키가 설정된 상태에서는 키가 없거나 다른 요청에 HTTP 403을 반환합니다.

## 카카오 챗봇 관리자센터 연결 순서

1. 카카오비즈니스 계정과 카카오톡 채널을 준비합니다.
2. 챗봇 관리자센터에서 카카오톡 챗봇을 생성합니다.
3. 스킬을 생성하고 URL에 `https://milaidhoo-kakao-bot.yongbeom.workers.dev/kakao/skill`을 등록합니다.
5. 운영·테스트 헤더에 `x-api-key`를 등록합니다.
6. `객실 감시`, `객실 감시 해제`, `객실 감시 목록`, `객실 조회` 발화를 받을 블록을 만들고 스킬을 연결합니다.
7. 출력에서 스킬데이터를 사용하도록 설정합니다.
8. 봇테스트에서 Request/Response JSON과 응답시간을 확인합니다.
9. 개발 채널에서 검증한 뒤 운영 채널에 배포합니다.

공식 문서:

- https://kakaobusiness.gitbook.io/main/tool/chatbot/tutorial/make_chatbot/tutorial_1
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/make_skill
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format

## 현재 지원하는 발화

- `객실 감시`: 빠른 버튼으로 날짜 입력 안내 표시
- `객실 감시 2027-03-17`: 해당 도착일의 1박 객실 감시 등록
- `객실 감시 해제 2027-03-17`: 해당 날짜 감시 해제
- `객실 감시 목록`: 현재 등록된 날짜 확인
- `객실 조회 2027-03-17`: 저장된 해당 날짜의 최신 상태 확인
- `일일 변경 보고`: 오늘의 스캔·변경 보고 확인
- `일일 변경 보고 2026-08-07`: 지정한 날짜의 보고 확인
- `3월 자세히 보기`: 2027년 3월 전체 날짜의 최신 상태 확인
- `클라우드플레어 사용량`: Worker·D1 무료 사용량 확인
- `단톡방 공유 켜기` / `단톡방 공유 끄기`: 내 1:1 챗봇 명령 결과의 단체방 공유 설정

날짜는 항상 `YYYY-MM-DD` 형식으로 입력합니다. 감시기가 실행 중이면 등록과 해제는 다음 감시 주기부터 반영됩니다. 등록된 날짜가 없으면 Hilton 조회 브라우저를 열지 않고 대기합니다.

모든 봇 응답 아래에는 `객실 감시`, `감시 해제`, `감시 목록`, `일일 변경 보고` 빠른 버튼이 표시됩니다. 등록이나 해제 버튼을 누른 뒤 안내에 따라 날짜가 포함된 명령을 입력합니다.

## 알림 방식

- 등록 날짜가 예약 가능이면 마지막 확인 결과를 기준으로 2분마다 반복 알림
- 매진 상태에서는 알림을 보내지 않고, 다시 예약 가능해지면 즉시 알림
- 감시 해제하면 다음 감시 주기부터 반복 알림 중단
- 해제 후 같은 날짜를 다시 등록하면 새 감시로 처리
- 등록 날짜는 20분마다 조회하고, 2027년 3월 전체 날짜는 전체 배치 기준 1시간마다 3일씩 순환하여 변경 보고용으로 조회
- `객실 감시`에 등록되지 않은 날짜에는 개별 예약 가능 알림을 보내지 않음
- `단톡방 공유 끄기`는 내 명령 결과 공유만 끄며 객실 감시 자동 알림에는 영향을 주지 않음
- 카카오톡 채팅방 자체의 알림 끄기는 기기의 소리·푸시만 끄며 메시지 전송·수신은 계속됨

## 현재 운영 흐름

1. `Space` 카카오톡 채널 1:1 채팅에서 감시·조회 명령을 입력합니다.
2. Cloudflare Worker가 1:1 챗봇 답변을 즉시 표시하고, 사용자별 공유 설정이 켜져 있으면 답변을 단체방 전송 대기열에 넣습니다.
3. 중앙 임대를 가진 Windows 감시기가 대기열의 명령 결과와 예약 가능 자동 알림을 `밀라이두 알림 봇🤖` 단체방에 전송합니다.

자동 전송할 때는 정확한 대화방과 입력 컨트롤을 확인하기 위해 카카오톡 창이 잠시 전면으로 전환됩니다.

## 감사 로그

`hilton_monitor_stats.sqlite3`의 `api_request_log` 테이블에 요청 시각, 엔드포인트, 해시 처리된 사용자 ID, 발화, 응답 상태, 처리시간이 기록됩니다. 원본 카카오 사용자 ID는 저장하지 않습니다.

## 배포·다른 PC 연결

Cloudflare 재배포와 다른 PC 연결 절차는 `README_CLOUDFLARE.md`를 확인합니다.
