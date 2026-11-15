@echo off
rem Start MutiCom without a console window.
rem Keep this file ASCII-only with CRLF line endings: cmd.exe
rem mis-parses UTF-8 or LF-only batch files.
setlocal
cd /d "%~dp0"

set "PY=C:\Python314\pythonw.exe"
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PY=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PY%" goto nopython
start "" "%PY%" -m muticom
exit /b 0

:nopython
echo [ERROR] Python not found: %PY%
echo Edit run.bat and point PY to a Python that has PySide6 installed.
pause
exit /b 1
