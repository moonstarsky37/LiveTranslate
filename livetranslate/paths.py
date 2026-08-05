"""Repo-root anchored paths.

Runtime data (config.yaml, user_settings.json, models/, logs/, transcripts/,
the vendored funasr_nano/ tree) deliberately lives beside start.bat at the repo
root — NOT inside the package — so a portable unzip keeps everything together
and `models/` survives package-layout changes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
