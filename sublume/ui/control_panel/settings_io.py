"""Single settings entry point shims (Phase 2), shared by the tab modules."""

from sublume.config.store import SettingsStore

# Single settings entry point (Phase 2). The wrappers below keep the legacy
# call sites working; new code should take a SettingsStore directly.
_STORE = SettingsStore()
SETTINGS_FILE = _STORE.settings_path


def _load_saved_settings() -> dict | None:
    return _STORE.load()


def _save_settings(settings: dict):
    _STORE.save(settings)
