@echo off
rem Keep this file pure ASCII: cmd.exe reads .bat as the OEM code page,
rem and non-ASCII text here would break line parsing on Chinese Windows.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo.
echo   TokenPanel - Environment Setup
echo   ----------------------------------------
echo.

rem ---------- 1. locate Python ----------
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
  echo   [X] Python not found.
  echo.
  echo   Install Python 3.10 or newer from:
  echo       https://www.python.org/downloads/
  echo.
  echo   Remember to check "Add Python to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

echo   [1/3] Python
%PY% -c "import sys; print('        ' + sys.version.split()[0] + '   ' + sys.executable)"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo   [X] Python is older than 3.10. Please upgrade, then retry.
  echo.
  pause
  exit /b 1
)

rem ---------- 2. install dependency ----------
echo.
echo   [2/3] Installing zstandard
%PY% -m pip install --disable-pip-version-check -q --timeout 20 --retries 1 -r requirements.txt
if errorlevel 1 (
  echo        Default index failed, trying mirror...
  %PY% -m pip install --disable-pip-version-check -q --timeout 20 --retries 1 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)
if errorlevel 1 (
  echo   [X] Install failed. Check your network, or run manually:
  echo       %PY% -m pip install zstandard
  echo.
  pause
  exit /b 1
)

rem ---------- 3. health check ----------
echo.
echo   [3/3] Environment check
echo.
%PY% -m tokenpanel doctor

echo.
echo   ----------------------------------------
echo   Done.
echo     Double-click TokenPanel.vbs  -^> dashboard window
echo     Double-click TokenPanel.bat  -^> run with console
echo.
pause
exit /b 0
