@echo off
chcp 936 >nul
title 手打模拟器
cd /d "%~dp0"

if exist "手打模拟器.exe" (
    start "" "手打模拟器.exe"
    exit /b
)

where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "shouda.py"
    exit /b
)

echo.
echo   未找到「手打模拟器.exe」，也没有检测到 Python 环境。
echo   请安装 Python 3 并执行：pip install -r requirements.txt
echo.
pause
