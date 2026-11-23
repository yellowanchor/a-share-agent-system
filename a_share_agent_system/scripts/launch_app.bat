@echo off
rem ============================================================
rem  One-click launcher: open the A-share dashboard in browser
rem  (keeps a console window while starting; use the desktop
rem   shortcut for a completely silent start)
rem ============================================================
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_app.ps1"
if errorlevel 1 pause
endlocal
