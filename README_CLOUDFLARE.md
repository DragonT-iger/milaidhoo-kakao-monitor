# Cloudflare Workers + D1 배포

이 구성은 도메인 구매, 공유기 포트포워딩, 고정 IP가 필요 없습니다.

- 카카오 스킬 주소: `https://milaidhoo-kakao-bot.yongbeom.workers.dev/kakao/skill`
- 관리자 페이지: `https://milaidhoo-kakao-bot.yongbeom.workers.dev/admin`
- Worker 루트 주소(`/`)로 접속해도 관리자 페이지로 자동 이동합니다.
- 중앙 저장소: Cloudflare D1
- Hilton 실제 조회: Windows PC의 `hilton_kakao_monitor.py`
- 여러 PC가 켜져 있어도 D1 임대를 받은 한 대만 조회하고 대화 큐를 소비

## 1. 최초 1회 배포

PowerShell에서 다음 폴더로 이동합니다.

```powershell
cd C:\Users\dydyq\Desktop\test\cloudflare_worker
```

Cloudflare 로그인:

```powershell
npx.cmd wrangler login
```

D1 데이터베이스 생성:

```powershell
npx.cmd wrangler d1 create milaidhoo-monitor
```

출력된 `database_id`를 `cloudflare_worker/wrangler.jsonc`의
`REPLACE_WITH_D1_DATABASE_ID` 자리에 넣습니다.

D1 테이블 생성:

```powershell
npm.cmd run db:remote
```

서로 다른 긴 키 두 개를 생성합니다.

```powershell
[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()
```

첫 번째 값을 카카오 스킬 키로 등록합니다.

```powershell
npx.cmd wrangler secret put KAKAO_SKILL_API_KEY
```

두 번째 값을 Windows 에이전트 키로 등록합니다.

```powershell
npx.cmd wrangler secret put AGENT_API_KEY
```

Worker 배포:

```powershell
npm.cmd run deploy
```

배포 결과에 표시된 `https://...workers.dev` 주소가 고정 운영 주소입니다.
Worker 이름이나 Cloudflare 계정의 workers.dev 하위 도메인을 바꾸지 않는 한 같은 주소를 계속 사용합니다.

## 2. Windows 감시기 연결

루트의 `config.json`에서 다음 값을 바꿉니다.

```json
"remote_agent": {
  "enabled": true,
    "base_url": "https://milaidhoo-kakao-bot.yongbeom.workers.dev",
  "agent_id": "",
  "api_key_env": "AGENT_API_KEY",
  "timeout_seconds": 15
}
```

`agent_id`가 비어 있으면 Windows 컴퓨터 이름을 자동 사용합니다.

에이전트 키는 파일에 저장하지 않고 사용자 환경변수에 넣습니다.

```powershell
[Environment]::SetEnvironmentVariable(
  "AGENT_API_KEY",
  "두 번째로 만든 키",
  "User"
)
```

새 PowerShell 또는 새 로그인 세션에서 감시기를 실행합니다.

```powershell
& 'C:\Users\dydyq\AppData\Local\Programs\Python\Python312\python.exe' .\hilton_kakao_monitor.py
```

이 복구 실행 래퍼는 기존 기능이 보존된 Python 3.12 바이트코드를 사용하므로
다른 PC에도 Python 3.12와 `hilton_kakao_monitor.cpython-312.recovery-backup.pyc`가 필요합니다.

## 3. 카카오 챗봇 관리자센터

스킬 URL:

```text
https://milaidhoo-kakao-bot.yongbeom.workers.dev/kakao/skill
```

스킬의 운영·테스트 헤더:

```text
x-api-key: KAKAO_SKILL_API_KEY에 넣은 첫 번째 키
```

연결할 발화:

- `객실 감시` (빠른 버튼에서 날짜 입력 안내 표시)
- `객실 감시 2027-03-17`
- `객실 감시 해제 2027-03-17`
- `객실 감시 목록`
- `객실 조회 2027-03-17`

상태 확인 주소는 인증 없이 사용할 수 있습니다.

```text
https://milaidhoo-kakao-bot.yongbeom.workers.dev/healthz
```

## 4. 다른 PC로 이동

다음 항목만 준비하면 됩니다.

1. 이 프로젝트 폴더 전체 복사
2. Python 3.12와 `requirements.txt` 설치
3. Edge 및 카카오톡 로그인
4. 같은 `config.json` 사용
5. 같은 `AGENT_API_KEY` 사용자 환경변수 설정

동시에 두 PC를 실행하면 먼저 D1 임대를 받은 PC만 Hilton을 조회하고,
다른 PC는 임대가 끝날 때까지 자동 대기합니다.
대화 큐도 같은 임대 소유자만 소비합니다. 마지막 처리 ID는 D1을 원본으로
사용하며, 새 PC의 로컬 커서가 없거나 오래되어도 중앙 커서는 뒤로 가지 않습니다.
임대 소유 PC는 조회 사이의 대기 중 5분마다 15분 임대를 갱신합니다. 감시기가
종료되거나 임대 갱신이 멈추면 최대 15분 후 다른 PC가 Hilton 조회와 대화 큐
처리를 함께 인계합니다.

## 비밀값 주의

- `.dev.vars`와 실제 키는 GitHub에 올리지 않습니다.
- 카카오 키와 에이전트 키는 반드시 서로 다르게 만듭니다.
- 키가 노출되면 `wrangler secret put`으로 즉시 새 값으로 교체합니다.
