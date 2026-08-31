@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-Pensieve.ps1"
set "PENSIEVE_EXIT_CODE=%ERRORLEVEL%"
if "%PENSIEVE_EXIT_CODE%"=="0" (
    echo.
    echo Pensieve stop check completed safely.
    timeout /t 3 /nobreak >nul
    exit /b 0
)
echo.
echo Pensieve stop check encountered an error. Review the message above.
pause
exit /b %PENSIEVE_EXIT_CODE%
