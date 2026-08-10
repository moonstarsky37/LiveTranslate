#!/usr/bin/env bash
# Sublume launcher (macOS). Kept bash-3.2 compatible.

set -eu
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[ERROR] Virtual environment not found."
    echo "Run ./install.sh first to set up the environment."
    exit 1
fi

if [ ! -f ".venv/.sublume-ready" ]; then
    echo "[ERROR] Virtual environment setup is incomplete."
    echo "A previous install was interrupted before it finished."
    echo "Run ./install.sh again to finish installing and verifying dependencies."
    exit 1
fi

echo "Starting Sublume..."
exec .venv/bin/python main.py
