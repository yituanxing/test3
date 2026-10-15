@echo off
setlocal EnableExtensions
chcp 65001 >nul
pushd "%~dp0.."
echo ================================================
echo ZD Demo - Windows localhost startup
echo Folder: %CD%
echo ================================================
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: Windows PowerShell is missing.
  set "RET=1"
  goto END
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local_demo_windows.ps1"
set "RET=%ERRORLEVEL%"
:END
echo.
if "%RET%"=="0" (
  echo ZD Demo server exited normally.
) else (
  echo ZD Demo startup failed with exit code %RET%.
  echo Check: Python 3.11+, Internet for first-run pip, port 8001, password prompt.
)
echo.
echo This window is kept open so you can read any errors.
pause
popd
exit /b %RET%
