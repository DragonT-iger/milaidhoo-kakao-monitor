@echo off
chcp 65001 >nul
cd /d "%~dp0cloudflare_worker"
if not exist "node_modules" call npm.cmd ci
call npx.cmd wrangler login
pause
