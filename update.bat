@echo off
cd /d "%~dp0"
set PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%

echo ========================================
echo   LiveTranslate Updater
echo ========================================
echo.

:: Check git
git --version >nul 2>&1
if errorlevel 1 (
    echo Git not found, attempting to install via winget...
    winget --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Git not found and winget is not available.
        echo Please install Git from https://git-scm.com/downloads
        pause
        exit /b 1
    )
    winget install Git.Git --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERROR] Git installation failed.
        pause
        exit /b 1
    )
    :: Refresh PATH
    set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%ProgramFiles%\Git\cmd;%PATH%"
    git --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Git installed but not found in PATH. Please restart and try again.
        pause
        exit /b 1
    )
    echo Git installed successfully.
    echo.
)

:: Pull latest code — pinned to origin/main (this fork), never any other remote
echo Updating from:
git remote get-url origin
echo.
echo Pulling latest changes...
git pull origin main
if errorlevel 1 (
    echo.
    echo [ERROR] git pull failed. Check for local conflicts.
    pause
    exit /b 1
)

:: Check venv
if not exist ".venv\Scripts\pip.exe" (
    echo.
    echo Virtual environment not found, running install.bat...
    call install.bat
    exit /b %errorlevel%
)

:: Update dependencies
echo.
echo Updating dependencies...
.venv\Scripts\pip.exe install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to update dependencies.
    pause
    exit /b 1
)

.venv\Scripts\pip.exe install pysbd --quiet

echo.
echo ========================================
echo   Update complete!
echo ========================================
echo.
echo Double-click start.bat to launch.
echo.
pause
