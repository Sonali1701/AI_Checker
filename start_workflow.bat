@echo off
setlocal
cd /d "%~dp0"
set "AICHECKER_START_PAGE=/workflow.html"
set "AICHECKER_PORT=8767"
python run_client.py
if errorlevel 1 (
  echo.
  echo Could not start. See aichecker.log or install requirements with: python -m pip install -r requirements.txt
  pause
)
endlocal
