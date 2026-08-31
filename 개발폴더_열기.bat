@echo off
start "" explorer.exe "%~dp0"
start "밀라이두 개발 터미널" powershell.exe -NoExit -Command "Set-Location -LiteralPath '%~dp0'"
