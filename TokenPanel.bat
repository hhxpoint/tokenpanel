@echo off
cd /d "%~dp0"
python -m tokenpanel desktop %*
if errorlevel 1 (
  echo.
  echo Startup failed. Check that Python 3.10+ is installed and on PATH.
  pause
)
