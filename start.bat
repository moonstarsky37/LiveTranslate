@echo off
cd /d "%~dp0"
set PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo Please run install.bat first to set up the environment.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\.sublume-ready" (
    echo [ERROR] Virtual environment setup is incomplete.
    echo A previous install was interrupted before it finished.
    echo Please run install.bat again to finish installing and verifying dependencies.
    echo.
    pause
    exit /b 1
)

echo Starting Sublume...
.venv\Scripts\python.exe main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Sublume exited with an error.
    pause
)
