@echo off
rem Build a single-file, dependency-free TokenPanel.exe
rem Requires: pip install pyinstaller
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   Building TokenPanel.exe ...
echo.

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name TokenPanel ^
  --add-data "tokenpanel\web;tokenpanel\web" ^
  --add-data "pricing.json;." ^
  launch.py

if errorlevel 1 goto fail

rem Generate the checksum file that ships with every release.
python -c "import hashlib,pathlib;p=pathlib.Path('dist/TokenPanel.exe');h=hashlib.sha256(p.read_bytes()).hexdigest();pathlib.Path('SHA256SUMS.txt').write_text(f'{h}  TokenPanel.exe\n',encoding='utf-8');print('  SHA256:',h)"

if errorlevel 1 goto fail

echo.
echo   Done.  dist\TokenPanel.exe
echo   The exe keeps its data next to itself (data\tokenpanel.db).
echo   Checksum written to SHA256SUMS.txt - upload it with the exe.
echo.
pause
exit /b 0

:fail
echo.
echo   Build failed.
pause
exit /b 1
