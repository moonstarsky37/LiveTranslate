#!/usr/bin/env bash
# Sublume - macOS installer (git-clone workflow, Lightweight profile only).
# Mirrors install.bat's philosophy: uv manages its own CPython 3.12, no
# system Python involved; a ready-marker is written only after verification.
# Kept bash-3.2 compatible (macOS ships /bin/bash 3.2).

set -eu

cd "$(dirname "$0")"

step() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$1"; }
ok()   { printf '  OK: %s\n' "$1"; }
warn() { printf '  WARN: %s\n' "$1"; }
fail() { printf '  ERROR: %s\n' "$1" >&2; exit 1; }

printf '\n========================================\n'
printf '   Sublume Installer (macOS)\n'
printf '========================================\n'

# -- Step 1: find or install uv --
step "Detecting uv..."
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
fi
if [ -z "$UV" ]; then
    warn "uv not found - it manages Python and the dependencies for this project"
    if command -v brew >/dev/null 2>&1; then
        step "Installing uv via Homebrew..."
        brew install uv || warn "brew install uv failed, falling back"
        UV="$(command -v uv || true)"
    fi
    if [ -z "$UV" ]; then
        step "Installing uv via the official install script..."
        curl -LsSf https://astral.sh/uv/install.sh | sh || fail "uv installation failed"
        UV="$HOME/.local/bin/uv"
    fi
fi
[ -n "$UV" ] && [ -x "$UV" ] || fail "uv is still not available - install it manually (https://docs.astral.sh/uv/) and rerun"
ok "$("$UV" --version)"

# -- Step 2: create venv (Python 3.12, uv-managed) --
step "Creating virtual environment (Python 3.12)..."
PY=".venv/bin/python"
NEED_VENV=1
if [ -x "$PY" ]; then
    VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    case "$VER" in
        3.10|3.11|3.12) ok "Existing venv is healthy (Python $VER), reusing"; NEED_VENV=0 ;;
        *) warn "Existing venv is unusable (Python ${VER:-broken}), recreating..."; rm -rf .venv ;;
    esac
fi
if [ "$NEED_VENV" = "1" ]; then
    "$UV" venv --python 3.12 --managed-python --seed .venv || fail "Failed to create venv"
    ok "Created .venv"
fi

# -- Step 3: install and verify (Lightweight profile - no torch) --
READY=".venv/.sublume-ready"
rm -f "$READY"

step "Installing dependencies from requirements.txt..."
"$UV" pip install --python "$PY" -r requirements.txt || fail "Failed to install dependencies"
ok "Dependencies installed"

step "Verifying installed packages..."
"$UV" pip check --python "$PY" || fail "Installed dependencies are inconsistent"
date > "$READY"
ok "Environment verified"

printf '\n========================================\n'
printf '   Installation complete!\n'
printf '========================================\n\n'
printf '  Start Sublume with: ./start.sh\n'
printf '  First launch will download the SenseVoice ONNX model (~240MB).\n'
printf '  System audio capture needs the BlackHole virtual device:\n'
printf '    brew install blackhole-2ch\n'
printf '  then create a Multi-Output Device in Audio MIDI Setup.\n\n'
