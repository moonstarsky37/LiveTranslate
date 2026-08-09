# Sublume - One-click installer (git-clone workflow)
# Usage: Double-click install.bat (or run: powershell -ExecutionPolicy Bypass -File scripts\install.ps1)
#
# Environment setup is delegated to uv, which downloads its own CPython 3.12.
# No system Python is needed, and a system Python of the wrong version can no
# longer poison the venv. This mirrors the portable release bootstrap written
# by scripts/build_release.ps1 — keep the two in sync.

$ErrorActionPreference = "Stop"
# This script lives in scripts/; the project root is one level up.
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

# Hardlinks across volumes fail on some setups; copying is slower but reliable.
$env:UV_LINK_MODE = "copy"

function Write-Step { param($msg) Write-Host "`n[$((Get-Date).ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "  OK: $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  WARN: $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "  ERROR: $msg" -ForegroundColor Red }

function Enable-SystemProxy {
    # uv (Python download) and pip honor *_PROXY env vars but not the Windows
    # registry system proxy; bridge it here. An already-set env proxy wins.
    if ($env:HTTPS_PROXY -or $env:HTTP_PROXY) {
        Write-Host "  Using proxy from environment" -ForegroundColor Gray
        return
    }
    try {
        $reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        $s = Get-ItemProperty -Path $reg -ErrorAction Stop
        if ($s.ProxyEnable -ne 1 -or -not $s.ProxyServer) { return }
        $server = [string]$s.ProxyServer
        $http = $null; $https = $null
        if ($server -like "*=*") {
            foreach ($part in ($server -split ';')) {
                $kv = $part -split '=', 2
                if ($kv.Count -eq 2 -and $kv[0] -eq 'http')  { $http  = $kv[1] }
                if ($kv.Count -eq 2 -and $kv[0] -eq 'https') { $https = $kv[1] }
            }
        } else {
            $http = $server; $https = $server
        }
        if (-not $http)  { $http  = $https }
        if (-not $https) { $https = $http }
        if (-not $http) { return }
        if ($http  -notmatch '^\w+://') { $http  = "http://$http" }
        if ($https -notmatch '^\w+://') { $https = "http://$https" }
        $env:HTTP_PROXY  = $http
        $env:HTTPS_PROXY = $https
        $env:ALL_PROXY   = $https
        Write-Host "  Detected Windows system proxy: $https (applied to uv/pip)" -ForegroundColor Green
    } catch {}
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "   Sublume Installer" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta

Enable-SystemProxy

# ── Step 1: Find or install uv ──
Write-Step "Detecting uv..."

function Find-Uv {
    try {
        $null = & uv --version 2>&1
        if ($LASTEXITCODE -eq 0) { return "uv" }
    } catch {}
    # Freshly installed uv is not on this session's PATH yet; check the two
    # locations winget and the official installer use.
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

$Uv = Find-Uv

if (-not $Uv) {
    Write-Warn "uv not found — it manages Python and the dependencies for this project"

    $hasWinget = $false
    try {
        $null = & winget --version 2>&1
        if ($LASTEXITCODE -eq 0) { $hasWinget = $true }
    } catch {}

    if ($hasWinget) {
        Write-Step "Installing uv via winget..."
        & winget install astral-sh.uv --accept-package-agreements --accept-source-agreements
        # Refresh PATH so the new uv is visible without reopening the shell
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        $Uv = Find-Uv
    }

    if (-not $Uv) {
        Write-Warn "Falling back to the official installer: https://astral.sh/uv/install.ps1"
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        } catch {
            Write-Err "uv installation failed: $_"
        }
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        $Uv = Find-Uv
    }

    if (-not $Uv) {
        Write-Err "Could not install uv automatically."
        Write-Host "  Install it manually (https://docs.astral.sh/uv/getting-started/installation/)" -ForegroundColor Yellow
        Write-Host "  then run install.bat again." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
}
Write-Ok ((& $Uv --version) -join " ")

# ── Step 2: Create venv ──
Write-Step "Creating virtual environment (Python 3.12)..."

# Validate an existing venv: it may be half-built, built by a Python we no
# longer support (3.13+ has no ctranslate2 wheels), or plain corrupted (#18).
function Test-VenvHealthy {
    param([string]$VenvPythonExe)
    if (-not (Test-Path $VenvPythonExe)) { return $false }
    try {
        $ver = & $VenvPythonExe --version 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        if ($ver -notmatch "Python (\d+)\.(\d+)") { return $false }
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        # 3.13+ rejected: no ctranslate2 cp313 wheels (#15), strict SSL breaks torch.hub (#20)
        if ($major -ne 3 -or $minor -lt 10 -or $minor -gt 12) {
            Write-Warn "Existing venv is $ver (need 3.10-3.12)"
            return $false
        }
        return $true
    } catch {
        return $false
    }
}

$Python = ".venv\Scripts\python.exe"
$needVenv = $true
if (Test-Path ".venv") {
    if (Test-VenvHealthy $Python) {
        Write-Ok "Existing venv is healthy, reusing"
        $needVenv = $false
    } else {
        Write-Warn "Existing venv is unusable, recreating..."
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
    }
}

if ($needVenv) {
    # --managed-python forces uv's own CPython build instead of whatever the
    # system has; --seed installs pip so update.bat keeps working.
    & $Uv venv --python 3.12 --managed-python --seed .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Retrying without --managed-python (older uv)..."
        & $Uv venv --python 3.12 --seed .venv
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Python)) {
        Write-Err "Failed to create venv"
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Ok "Created .venv"
}

# ── Step 3: Detect GPU ──
Write-Step "Detecting GPU..."

$HasNvidia = $false
$CudaVer = "cu126"
try {
    $gpu = & nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0 -and $gpu) {
        $HasNvidia = $true
        Write-Ok "NVIDIA GPU detected: $(($gpu -split "`n")[0].Trim())"

        # Detect compute capability to choose CUDA version
        # Blackwell (sm_120, compute_cap >= 12.0) requires cu128
        $cc = & nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null
        if ($LASTEXITCODE -eq 0 -and $cc) {
            $ccVal = [double](($cc -split "`n")[0].Trim())
            if ($ccVal -ge 12.0) {
                $CudaVer = "cu128"
                Write-Ok "Blackwell+ architecture (compute $ccVal) detected, using CUDA 12.8"
            } else {
                Write-Ok "Compute capability $ccVal, using CUDA 12.6"
            }
        }
    }
} catch {}

if (-not $HasNvidia) {
    Write-Warn "No NVIDIA GPU detected, will install CPU-only PyTorch"
}

# Let user choose
Write-Host ""
if ($HasNvidia) {
    $cudaLabel = if ($CudaVer -eq "cu128") { "CUDA 12.8" } else { "CUDA 12.6" }
    Write-Host "  [1] $cudaLabel (recommended for your NVIDIA GPU)" -ForegroundColor White
    Write-Host "  [2] CPU only" -ForegroundColor White
    $choice = Read-Host "  Select PyTorch version [1]"
    if ($choice -eq "2") { $HasNvidia = $false }
} else {
    Write-Host "  [1] CPU only" -ForegroundColor White
    Write-Host "  [2] CUDA (if you have NVIDIA GPU)" -ForegroundColor White
    $choice = Read-Host "  Select PyTorch version [1]"
    if ($choice -eq "2") { $HasNvidia = $true }
}

$TorchIndex = if ($HasNvidia) {
    "https://download.pytorch.org/whl/$CudaVer"
} else {
    "https://download.pytorch.org/whl/cpu"
}

# ── Step 4: Install PyTorch ──
Write-Step "Installing PyTorch (this may take a few minutes)..."
Write-Host "  Using index: $TorchIndex" -ForegroundColor Gray

& $Uv pip install --python $Python torch torchaudio --index-url $TorchIndex
if ($LASTEXITCODE -ne 0) {
    Write-Err "PyTorch installation failed"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "PyTorch installed"

# ── Step 5: Install dependencies ──
Write-Step "Installing dependencies from requirements.txt..."

& $Uv pip install --python $Python -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to install dependencies"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Dependencies installed"

# ── Done ──
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Installation complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To start Sublume:" -ForegroundColor White
Write-Host "    Double-click start.bat" -ForegroundColor Yellow
Write-Host "    or run: .venv\Scripts\python.exe main.py" -ForegroundColor Yellow
Write-Host ""
Write-Host "  First launch will download ASR models (~1GB)." -ForegroundColor White
Write-Host ""
Read-Host "Press Enter to exit"
