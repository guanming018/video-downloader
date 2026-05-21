@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ====================================
echo   视频下载服务启动中...
echo ====================================
echo.

:: 将 WinGet 和 ffmpeg 加入 PATH
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"

:: 清理旧进程（端口 16888）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :16888') do (
    taskkill /f /pid %%a >nul 2>&1
)
if %errorlevel% equ 0 echo   已清理旧进程
timeout /t 1 /nobreak >nul

E:\python.exe main.py
pause
