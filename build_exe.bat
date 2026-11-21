@echo off
cd /d "%~dp0"
echo Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo Building TokenPanel.exe ...
pyinstaller --noconfirm --onefile --console ^
  --name TokenPanel ^
  --add-data "tokenpanel\web;tokenpanel\web" ^
  --add-data "pricing.json;." ^
  launch.py
if errorlevel 1 goto fail

echo.
echo Done: dist\TokenPanel.exe
pause
exit /b 0

:fail
echo.
echo Build failed.
pause
exit /b 1
