@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 -m uvicorn kakao_skill_server:app --host 127.0.0.1 --port 8000
pause
