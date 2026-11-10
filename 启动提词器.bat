@echo off
cd /d "%~dp0"
title Patent Sales Prompter

rem Prefer the project's virtual environment, then Python on PATH.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] Python not found. Please install Python first.
    pause
    exit /b 1
)

echo Using interpreter: %PY%

rem Self-check dependencies, auto-install if missing
"%PY%" -c "import webview, jieba, requests, pypinyin, win32api, PIL" >nul 2>nul
if errorlevel 1 (
    echo Missing core packages, installing ...
    "%PY%" -m pip install pywebview jieba requests pywin32 pypinyin Pillow
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

"%PY%" app\main.py
if errorlevel 1 (
    echo.
    echo [ERROR] App exited abnormally. See the message above.
    pause
)
