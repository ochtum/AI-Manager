@echo off
setlocal
cd /d %~dp0\..\..
start "AI CLI Watcher" cmd /c python ai_cli_watcher.py
echo Launched AI CLI Watcher
