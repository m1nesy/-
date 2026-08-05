@echo off
cd /d %~dp0
title Patent Sales Prompter

rem Prefer D:\anaconda (all dependencies installed there)
set "PY="
if exist "D:\anaconda\python.exe" set "PY=D:\anaconda\python.exe"
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
"%PY%" -c "import webview, jieba, requests" >nul 2>nul
if errorlevel 1 (
    echo Missing packages, installing pywebview jieba requests ...
    "%PY%" -m pip install pywebview jieba requests pywin32
)

"%PY%" app\main.py
if errorlevel 1 (
    echo.
    echo [ERROR] App exited abnormally. See the message above.
    pause
)
