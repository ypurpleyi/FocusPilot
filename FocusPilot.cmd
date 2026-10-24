@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo FocusPilot requires the local Python environment. See README.md.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m focuspilot
