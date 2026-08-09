"""Single-entry settings store.

Owns: file locations, atomic writes, load-time migrations, and read-only
factory defaults from config.yaml. Nothing else in the codebase may read or
write user_settings.json directly.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from sublume.model_manager import migrate_funasr_settings
from sublume.paths import ROOT

log = logging.getLogger("Sublume.Config")

# Bare "zh" in these keys migrates to zh-TW (fork default; see CLAUDE.md).
_LEGACY_LANG_KEYS = ("target_language", "ui_lang")


class SettingsStore:
    def __init__(
        self,
        settings_path: Path | None = None,
        factory_path: Path | None = None,
    ) -> None:
        self.settings_path = settings_path or (ROOT / "user_settings.json")
        self.factory_path = factory_path or (ROOT / "config.yaml")
        self._factory_cache: dict[str, Any] | None = None

    def exists(self) -> bool:
        return self.settings_path.exists()

    def load(self) -> dict[str, Any] | None:
        """Load user settings with migrations applied, or None when absent or
        unreadable. Fork migrations (hub/zh) are persisted immediately so the
        next launch starts clean; funasr normalization stays in-memory only
        (matches long-standing behavior — it re-applies cheaply every load)."""
        try:
            if not self.settings_path.exists():
                return None
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception as e:  # corrupted file must not kill startup
            log.warning(f"Failed to load settings: {e}")
            return None
        if not isinstance(data, dict):
            log.warning("Settings file is not a JSON object; ignoring it")
            return None

        fork_migrated = self._apply_fork_migrations(data)
        migrate_funasr_settings(data)
        log.info(f"Loaded saved settings from {self.settings_path}")
        if fork_migrated:
            self.save(data)
            log.info("Fork settings migration applied (hub->hf / zh->zh-TW)")
        return data

    def save(self, settings: dict[str, Any]) -> None:
        """Atomic write: full temp file then os.replace, so a crash mid-write
        can never leave a truncated settings file behind."""
        try:
            tmp = self.settings_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(self.settings_path)
            log.info(f"Settings saved to {self.settings_path}")
        except Exception as e:
            log.warning(f"Failed to save settings: {e}")

    def factory_defaults(self) -> dict[str, Any]:
        """config.yaml as read-only factory defaults (deep copy per call)."""
        if self._factory_cache is None:
            with open(self.factory_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            self._factory_cache = loaded if isinstance(loaded, dict) else {}
        return deepcopy(self._factory_cache)

    @staticmethod
    def _apply_fork_migrations(data: dict[str, Any]) -> bool:
        changed = False
        if data.get("hub") == "ms":
            data["hub"] = "hf"
            changed = True
        for key in _LEGACY_LANG_KEYS:
            if data.get(key) == "zh":
                data[key] = "zh-TW"
                changed = True
        return changed
