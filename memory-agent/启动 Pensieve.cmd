@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-Pensieve.ps1"
set "PENSIEVE_EXIT_CODE=%ERRORLEVEL%"
if "%PENSIEVE_EXIT_CODE%"=="0" (
    echo.
    echo Pensieve started successfully.
    timeout /t 3 /nobreak >nul
    exit /b 0
)
echo.
echo Pensieve failed to start. Review the error above.
echo This window will remain open.
pause
exit /b %PENSIEVE_EXIT_CODE%
