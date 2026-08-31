# Hilton Water Pool Villa 감시기

Milaidhoo Maldives의 `Water Pool Villa`를 사용자가 등록한 날짜만 1박 단위로 확인하고, 예약 가능해지면 알림을 보냅니다. 최초 상태와 이후 변경만 이력으로 누적하고, 각 스캔의 실행 증빙은 별도로 저장합니다.

현재 기본 설정은 다음과 같습니다.

- 알림 범위: 카카오 명령으로 등록된 날짜만 2분 반복 알림
- 변경 보고 범위: 2027-03-01부터 2027-03-31까지 전체 배치 기준 1시간마다 3일씩 순환 확인
- 조회 단위: 등록한 각 도착일을 1박씩 개별 확인
- 인원: 객실 1개, 성인 2명
- 조회 간격: 20분 + 0~15초 임의 지연
- 브라우저: Hilton의 자동 접속 차단을 줄이기 위해 별도 프로필의 일반 Edge 창 사용
- 알림 조건: 등록 날짜가 예약 가능하면 마지막 확인 결과를 기준으로 감시 해제 전까지 2분마다 반복 알림
- 반복 중단: 카카오에서 해당 날짜를 감시 해제하면 다음 조회부터 알림 중단
- 일일 통계 보고: 비활성화. 필요할 때 보고서를 수동 생성해 확인
- 실제 카카오톡 전송 활성화(`dry_run: false`)
- 감시 날짜 원본: Cloudflare D1 중앙 목록
- 중복 PC 방지: 먼저 중앙 임대를 얻은 PC 한 대만 Hilton 조회

## 1. 카카오톡 대화방 준비

카카오톡에서 알림을 받을 대화방을 별도 창으로 미리 열어 둡니다. 프로그램은 KakaoTalk 프로세스가 소유한 네이티브 창 중 정확히 `밀라이두 알림 봇🤖`인 창만 포커스하며, 그 창의 `RichEdit Control` 입력창을 다시 확인한 뒤에만 전송합니다. 대화방을 검색하거나 변경하지 않습니다.

창 제목을 확인하려면 PowerShell에서 다음을 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --list-windows
```

기본 창 제목은 `밀라이두 알림 봇🤖`으로 설정되어 있습니다.

메시지를 보내지 않고 창 탐색과 활성화만 검사하려면 다음을 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --check-kakao-window
```

## 2. 안전한 드라이런

안전 점검이 필요할 때는 `config.json`의 `dry_run`을 일시적으로 `true`로 바꾼 뒤 한 번만 조회합니다. 현재 실운영 값은 `false`입니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --once
```

실제 카카오톡 메시지는 보내지 않고 `hilton_monitor.log`에 전송 예정 내용만 기록됩니다. 조회 결과는 드라이런에서도 통계 데이터베이스에 저장됩니다.

## 3. 카카오톡 입력 위치 테스트

`config.json`의 `chat_window_title`을 설정하고 대화방 창을 열어 둡니다. `dry_run: true`에서는 아래 명령도 로그만 남깁니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --test-kakao
```

실제 테스트 메시지를 보낼 준비가 되었을 때만 `dry_run`을 `false`로 바꾼 뒤 같은 명령을 실행합니다. 이 명령은 해당 대화방에 테스트 메시지 한 건을 실제 전송합니다.

현재는 좌표 클릭 대신 카카오톡의 실제 입력 컨트롤을 직접 선택하므로 다중 모니터에서도 `input_offset_y`를 조정할 필요가 없습니다. 이 설정은 이전 방식과의 호환을 위해 남겨 두었습니다.

Enter를 누르지 않고 입력창에 문구만 붙여넣으려면 다음을 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --test-kakao-draft
```

이 테스트 문구는 직접 지우면 됩니다.

## 4. 지속 감시 실행

검증 후 `config.json`의 `dry_run`을 `false`로 바꾸고 다음 파일을 더블클릭합니다.

```text
start_monitor.bat
```

또는 PowerShell에서 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py
```

종료하려면 콘솔에서 `Ctrl+C`를 누릅니다.

감시용 Edge는 `.edge_monitor_profile` 폴더의 별도 프로필을 사용합니다. 개인 Edge 프로필이나 로그인 정보에는 접근하지 않습니다. 첫 실행 때 Edge 시작 안내 화면이 나타나면 한 번 닫거나 건너뛴 뒤 프로그램을 다시 실행하세요.

## 알림 조건

감시 날짜는 카카오 챗봇에서 관리합니다.

- `객실 감시 2027-03-17`: 날짜 등록
- `객실 감시 해제 2027-03-17`: 날짜 해제
- `객실 감시 목록`: 등록 목록 확인
- `객실 조회 2027-03-17`: 저장된 최신 상태 확인

카카오에서 등록한 날짜는 반복 알림 대상이며 20분마다 조회합니다. 별도로 `report_monitoring.months`에 설정한 2027년 3월 전체 날짜는 상태·가격 변경 보고용으로 전체 배치 기준 1시간마다 3일씩 순환 조회하며 개별 가능 알림은 보내지 않습니다. 새 감시용 Edge에서는 Hilton 쿠키 동의를 한 번 처리하고, 객실 카드가 20초 안에 나타나지 않으면 오류로 기록합니다. 등록 날짜가 예약 가능하면 다음 Hilton 조회를 기다리는 동안에도 마지막 확인 결과를 기준으로 2분마다 반복 알림을 보내며, 감시 해제하거나 다음 조회에서 매진·오류로 확인되면 반복을 중단합니다. 반복 알림 때문에 Hilton을 2분마다 다시 조회하지는 않습니다.

`단톡방 공유 끄기`는 해당 사용자의 1:1 챗봇 명령 결과만 단체방에 공유하지 않도록 설정합니다. 감시기에 등록된 날짜의 예약 가능 자동 알림은 이 설정과 무관하게 계속 전송되며, 중단하려면 `객실 감시 해제 YYYY-MM-DD`를 사용해야 합니다. 카카오톡 채팅방 자체의 알림 끄기는 기기의 소리·푸시만 끄며 메시지 전송이나 수신을 막지 않습니다.

대화 큐의 마지막 처리 ID는 Cloudflare D1을 원본으로 사용합니다. 새 PC는 시작할 때 중앙 커서를 내려받아 로컬 `kakao_draft_cursor.json`을 초기화하며, 로컬 파일이 없거나 오래되어도 중앙 커서를 뒤로 되돌리지 않습니다. 객실 감시 임대를 가진 PC만 대화 큐를 소비합니다.

감시 임대는 여러 PC의 중복 조회·중복 전송을 막는 15분짜리 중앙 잠금입니다. 임대를 얻은 감시기는 다음 조회를 기다리는 동안 5분마다 임대를 갱신하므로 답변 큐 처리 권한을 유지합니다. 갱신 시 다른 PC가 소유자로 확인되면 남은 대기 시간에는 답변 큐와 반복 알림 처리를 중단하고 다음 감시 주기에 다시 임대를 요청합니다. 감시기나 PC가 종료되면 갱신도 멈추므로 최대 15분 후 다른 PC가 인계할 수 있습니다.

기본 조회 주기는 20분입니다. Hilton의 `HTTP 403`, `HTTP 429`, `Hilton Page Reference Code` 화면 또는 등록 날짜 전체 실패가 확인되면 그 주기의 남은 3월 전체 조회를 중단합니다. 이후 등록 날짜와 3월 전체 조회 모두 15분, 30분, 1시간, 2시간, 4시간 순서로 재시도 간격이 늘어나며 최대 4시간으로 제한됩니다. 대기 단계와 다음 재시도 시각은 상태 파일에 저장되므로 프로그램을 다시 실행해도 즉시 반복 조회하지 않습니다. 정상 객실 응답을 받으면 한 단계씩 4시간, 2시간, 1시간, 30분 순서로 낮아지며 최저 주기는 기본 20분입니다.

현재 중앙 API 주소는 `https://milaidhoo-kakao-bot.yongbeom.workers.dev`입니다. 다른 PC에서는 프로젝트 폴더와 `.env.agent`를 함께 옮기고 Python 3.12 환경을 준비하면 같은 감시 목록을 사용합니다.

## 상태·변경 이력과 증빙 로그

- `monitored_dates`: 현재 감시할 날짜 목록
- `availability_current`: 한 번 이상 조회한 날짜의 최신 상태 유지
- `availability_events`: 최초 기준값(`baseline`)과 실제 상태·가격 변경(`change`)만 누적
- `scan_runs`: 매 조회의 시작·완료 시각, 소요시간, 성공·오류·변경 건수
- 조회 오류는 현재 상태를 덮어쓰거나 변경 이벤트를 만들지 않음
- `notification_history` 테이블: 날짜별 가능 알림과 일일 보고의 시각, 조건, 드라이런 여부, 실제 전송 성공 여부, 메시지 전문
- `hilton_monitor.log`: 프로그램 실행·브라우저·오류 로그
- `reports/YYYY-MM-DD_changes.csv`: 해당 날짜의 초기값·변경 이벤트
- `reports/YYYY-MM-DD_daily_report.txt`: 카카오톡으로 보내는 일일 요약문

일일 보고는 매일 `daily_report_time`에 전날의 스캔 횟수, 성공·오류, 평균 소요시간과 변경 이벤트를 집계합니다. 기본값은 `00:05`입니다.

특정 날짜 보고서를 수동 생성하려면 다음을 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py --report 2026-08-07
```


## 주의사항

- 카카오톡 대화방 창 제목을 정확히 일치시킨 뒤에만 실제 전송을 켜세요.
- 전송 직전에 다른 프로그램이 키보드·마우스 포커스를 가져가면 실패할 수 있습니다.
- PC가 켜져 있고 카카오톡이 로그인된 상태여야 합니다.
- 등록 날짜가 많으면 한 주기 조회에 시간이 걸립니다. 오류가 반복되면 프로그램이 최대 10분까지 자동으로 조회 간격을 늘립니다.
- 사이트 화면 구조가 바뀌면 객실 탐지 부분을 수정해야 할 수 있습니다.
