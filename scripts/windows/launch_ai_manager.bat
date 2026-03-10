@echo off
setlocal
cd /d %~dp0\..\..
start "AI Manager" cmd /c python ai_manager.py
echo Launched AI Manager
