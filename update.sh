#!/usr/bin/env bash
# Sublume updater (macOS, Lightweight profile). Kept bash-3.2 compatible.

set -eu
cd "$(dirname "$0")"

printf '========================================\n'
printf '   Sublume Updater (macOS)\n'
printf '========================================\n\n'

command -v git >/dev/null 2>&1 || {
    echo "[ERROR] git not found. Install the Xcode Command Line Tools: xcode-select --install"
    exit 1
}

echo "Updating from:"
git remote get-url origin
echo
echo "Pulling latest changes..."
git pull origin main || {
    echo "[ERROR] git pull failed. Check for local conflicts."
    exit 1
}

if [ ! -x ".venv/bin/python" ]; then
    echo "Virtual environment not found, running install.sh..."
    exec ./install.sh
fi

UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
fi
[ -n "$UV" ] || { echo "[ERROR] uv not found - run ./install.sh"; exit 1; }

echo
echo "Updating dependencies..."
rm -f ".venv/.sublume-ready"
"$UV" pip install --python .venv/bin/python -r requirements.txt --quiet || {
    echo "[ERROR] Failed to update dependencies."
    exit 1
}
"$UV" pip check --python .venv/bin/python || {
    echo "[ERROR] Installed dependencies are inconsistent."
    exit 1
}
date > ".venv/.sublume-ready"

printf '\n========================================\n'
printf '   Update complete!\n'
printf '========================================\n\n'
printf 'Start Sublume with: ./start.sh\n'
