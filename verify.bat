@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo   TokenPanel - integrity check
echo   ============================
echo.

if not exist "TokenPanel.exe" goto :noexe
if not exist "SHA256SUMS.txt" goto :nohash

powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $h=(Get-FileHash -LiteralPath '.\TokenPanel.exe' -Algorithm SHA256).Hash.ToLower(); $line=(Get-Content -LiteralPath '.\SHA256SUMS.txt' -TotalCount 1); $e=($line -split '\s+')[0].ToLower(); Write-Host ('  file     : ' + $h); Write-Host ('  expected : ' + $e); if ($h -eq $e) { Write-Host '  RESULT   : OK - matches the published checksum' -ForegroundColor Green; exit 0 } else { Write-Host '  RESULT   : MISMATCH - do not run this file' -ForegroundColor Red; exit 1 }"

set "CODE=%ERRORLEVEL%"
echo.

if not "%CODE%"=="0" (
  echo   The file does not match the published checksum.
  echo   Delete it and download again from the official page.
  echo.
  pause
  exit /b 1
)

echo   Next step, if Windows shows "Windows protected your PC":
echo     1. Right-click TokenPanel.exe and choose Properties
echo     2. Tick "Unblock" at the bottom of the General tab
echo     3. Click OK, then run it
echo.
echo   That only removes the "downloaded from the Internet" mark.
echo   It does not bypass any security check, and the checksum above
echo   is what actually proves the file is intact.
echo.
pause
exit /b 0

:noexe
echo   [ERROR] TokenPanel.exe not found next to this script.
echo   Put this script in the same folder as TokenPanel.exe.
echo.
pause
exit /b 1

:nohash
echo   [ERROR] SHA256SUMS.txt not found next to this script.
echo   Download it from the same release page as TokenPanel.exe.
echo.
pause
exit /b 1
