@echo off
rem Debug mode: keep the console window so tracebacks are visible.
rem Keep this file ASCII-only with CRLF line endings.
setlocal
cd /d "%~dp0"

set "PY=C:\Python314\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" goto nopython
"%PY%" -m muticom
if errorlevel 1 pause
exit /b 0

:nopython
echo [ERROR] Python not found: %PY%
echo Edit run_debug.bat and point PY to a Python that has PySide6 installed.
pause
exit /b 1
