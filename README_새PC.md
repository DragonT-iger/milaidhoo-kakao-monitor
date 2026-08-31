# 밀라이두 챗봇 프로젝트 — 새 PC 이전

이 폴더가 객실 감시, 카카오 챗봇, Cloudflare Worker 개발의 기준 폴더입니다.

## 새 PC에서 실행

1. GitHub에서 저장소를 내려받습니다.

   ```powershell
   git clone https://github.com/DragonT-iger/milaidhoo-kakao-monitor.git
   cd milaidhoo-kakao-monitor
   ```

2. `새PC_설치.bat`를 더블클릭합니다.
3. 설치 중 기존 PC와 동일한 `AGENT_API_KEY`를 입력합니다. 또는 `.env.agent`를 안전한 방법으로 직접 복사합니다.
4. 설치가 끝나면 바탕화면에 생성된 `밀라이두 객실감시` 아이콘을 실행합니다.
5. 카카오톡 PC에서 `밀라이두 알림 봇🤖` 단톡방을 열어 둡니다.

설치 스크립트는 현재 사용자 로그인 때 객실 감시가 자동으로 시작되도록 `Milaidhoo Room Monitor` 작업도 등록합니다. Windows가 재부팅되어도 사용자 로그인 후 감시기가 다시 실행됩니다.

감시기 실행에는 Python 3.12가 반드시 필요합니다. 설치 스크립트가 Python 패키지와 Cloudflare 개발 패키지를 자동으로 준비합니다.

## Cloudflare 코드를 수정하거나 배포할 때

- 최초 한 번: `Cloudflare_로그인.bat`
- 배포: `Cloudflare_배포.bat`
- 관리자 페이지: `관리자페이지_열기.bat`
- 작업 폴더와 터미널: `개발폴더_열기.bat`

Cloudflare 로그인 정보는 새 PC마다 다시 로그인합니다. Worker의 운영 Secret은 Cloudflare에 저장되어 있으므로 프로젝트 파일에 들어 있지 않습니다.

## 중요한 파일

- `hilton_kakao_monitor.py`: 객실 조회와 카카오 단톡방 전송
- `hilton_kakao_monitor.cpython-312.recovery-backup.pyc`: 기존 조회 엔진, 반드시 함께 복사
- `config.json`: 감시 주기와 동작 설정
- `.env.agent`: 이 PC와 Cloudflare를 연결하는 에이전트 키
- `cloudflare_worker/`: 카카오 명령, D1 DB, 관리자 페이지 서버 코드

`.env.agent`와 `.env.admin`은 비밀정보입니다. 메신저나 공개 저장소에 올리지 마세요. 다른 PC로 옮길 때는 개인 USB 또는 암호화된 저장소를 사용하세요.

## 복사하지 않아도 되는 파일

다음 항목은 실행 중 자동 생성되므로 새 PC에 없어도 됩니다.

- `node_modules/`
- `.edge_monitor_profile/`
- `__pycache__/`
- `*.log`
- `hilton_monitor_stats.sqlite3*`
- `hilton_monitor_state.json`
- `kakao_draft_cursor.json`
- `reports/`

객실 상태와 감시 날짜의 중앙 원본은 Cloudflare D1에 저장되어 새 PC에서도 이어서 사용할 수 있습니다.
