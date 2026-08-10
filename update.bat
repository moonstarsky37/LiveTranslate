@echo off
cd /d "%~dp0"
set PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%PATH%

echo ========================================
echo   Sublume Updater
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
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Virtual environment not found, running install.bat...
    call install.bat
    exit /b %errorlevel%
)

:: Update dependencies — uv when available (same tool install.bat uses), pip otherwise.
:: The torch profile is detected from the venv itself: torch present means the
:: funasr stack (requirements-torch.txt) must be kept up to date too.
echo.
echo Updating dependencies...
del ".venv\.sublume-ready" >nul 2>&1
set "TORCH_PROFILE="
.venv\Scripts\python.exe -c "import torch" >nul 2>&1
if not errorlevel 1 set "TORCH_PROFILE=1"

where uv >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    if errorlevel 1 goto depsfail
    if defined TORCH_PROFILE (
        .venv\Scripts\python.exe -m pip install -r requirements-torch.txt --quiet
        if errorlevel 1 goto depsfail
    )
) else (
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt --quiet
    if errorlevel 1 goto depsfail
    if defined TORCH_PROFILE (
        uv pip install --python .venv\Scripts\python.exe -r requirements-torch.txt --quiet
        if errorlevel 1 goto depsfail
    )
)

:: Stamp the environment as complete so start.bat's interrupted-install guard
:: passes (also heals installs from before the marker existed).
echo ok> ".venv\.sublume-ready"
goto depsdone

:depsfail
echo [ERROR] Failed to update dependencies.
pause
exit /b 1

:depsdone

echo.
echo ========================================
echo   Update complete!
echo ========================================
echo.
echo Double-click start.bat to launch.
echo.
pause
