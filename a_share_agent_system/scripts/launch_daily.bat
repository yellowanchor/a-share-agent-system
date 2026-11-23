@echo off
rem ============================================================
rem  One-click: update data / build daily picks / make brief
rem  Runs in a visible console so you can see progress and
rem  choose a task (1 = signals only, 2 = update, 3 = full).
rem ============================================================
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_daily.ps1" -Mode menu
endlocal
