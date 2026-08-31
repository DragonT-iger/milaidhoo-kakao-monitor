# Milaidhoo 객실 감시 · 카카오 챗봇

Milaidhoo Maldives의 `Water Pool Villa` 예약 가능 여부를 확인하고, Cloudflare Worker·D1과 카카오 챗봇을 통해 감시 날짜와 알림을 관리하는 Windows용 프로젝트입니다.

## 다른 Windows PC에서 실행

PowerShell 또는 터미널에서 저장소를 내려받습니다.

```powershell
git clone https://github.com/DragonT-iger/milaidhoo-kakao-monitor.git
cd milaidhoo-kakao-monitor
```

그다음 `새PC_설치.bat`를 더블클릭합니다.

설치 과정에서 다음 작업이 자동으로 진행됩니다.

- Python 3.12 및 필요한 Python 패키지 확인·설치
- Microsoft Edge 확인
- Node.js와 Cloudflare 개발 패키지 확인·설치
- 중앙 Worker 연결용 `AGENT_API_KEY` 입력
- 바탕화면 실행 아이콘 생성
- Windows 로그인 시 객실 감시 자동 실행 등록

설치가 끝나면 카카오톡 PC에서 `밀라이두 알림 봇🤖` 대화방을 열고, 바탕화면의 `밀라이두 객실감시` 아이콘을 실행합니다.

> `hilton_kakao_monitor.cpython-312.recovery-backup.pyc`는 현재 감시기의 필수 실행 파일입니다. 반드시 Python 3.12를 사용해야 합니다.

## 비밀정보

실제 키와 관리자 인증정보는 GitHub에 올라가지 않습니다.

- `.env.agent`: 객실 감시 PC가 중앙 Worker에 연결할 때 사용하는 키
- `.env.admin`: 관리자 페이지 로컬 접속 정보
- Cloudflare Worker Secret: Cloudflare 계정에만 저장

형식은 `.env.agent.example`과 `.env.admin.example`을 참고하세요. 실제 값을 채운 파일은 `.gitignore`로 제외됩니다.

## 운영 주소

- 관리자 페이지: <https://milaidhoo-kakao-bot.yongbeom.workers.dev/>
- 카카오 스킬: `https://milaidhoo-kakao-bot.yongbeom.workers.dev/kakao/skill`
- 상태 확인: <https://milaidhoo-kakao-bot.yongbeom.workers.dev/healthz>

## Cloudflare 개발·배포

일반 감시 PC는 Cloudflare 로그인이 필요하지 않습니다. Worker 코드를 수정하거나 배포할 PC에서만 다음 파일을 사용합니다.

1. `Cloudflare_로그인.bat`
2. `Cloudflare_배포.bat`

Wrangler 명령은 `cloudflare_worker` 폴더에서 실행합니다.

## 자세한 문서

- [새 PC 이전 안내](README_새PC.md)
- [객실 감시기 안내](README_HILTON_MONITOR.md)
- [카카오 챗봇 안내](README_KAKAO_BOT.md)
- [Cloudflare 운영 안내](README_CLOUDFLARE.md)
