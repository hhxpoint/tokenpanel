@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   TokenPanel 环境准备
echo   ----------------------------------------
echo.

rem ---------- 1. 查找 Python ----------
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
  echo   [X] 未检测到 Python。
  echo.
  echo   请先安装 Python 3.10 或更高版本：
  echo       https://www.python.org/downloads/
  echo.
  echo   安装时务必勾选 "Add Python to PATH"，装完重新运行本文件。
  echo.
  pause
  exit /b 1
)

echo   [1/3] 检测 Python
%PY% -c "import sys; print('        ' + sys.version.split()[0] + '   ' + sys.executable)"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo   [X] 版本低于 3.10，请升级后重试。
  echo.
  pause
  exit /b 1
)

rem ---------- 2. 安装依赖 ----------
echo.
echo   [2/3] 安装依赖 zstandard
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo        默认源失败，改用国内镜像...
  %PY% -m pip install --disable-pip-version-check -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)
if errorlevel 1 (
  echo   [X] 依赖安装失败。请检查网络，或手动执行：
  echo       %PY% -m pip install zstandard
  echo.
  pause
  exit /b 1
)

rem ---------- 3. 体检 ----------
echo.
echo   [3/3] 环境体检
echo.
%PY% -m tokenpanel doctor

echo.
echo   ----------------------------------------
echo   准备完成：双击 TokenPanel.vbs 即可打开面板。
echo   想先看数据：双击 TokenPanel.bat
echo.
pause
exit /b 0
