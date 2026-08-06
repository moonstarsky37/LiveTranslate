"""Control panel package (Phase 3 split): 7 tab modules + container.

Public surface preserved: `from livetranslate.ui.control_panel import
ControlPanel, SETTINGS_FILE, _load_saved_settings, _save_settings` works
exactly as when control_panel was a single module.
"""

from livetranslate.ui.control_panel.settings_io import (
    SETTINGS_FILE,
    _load_saved_settings,
    _save_settings,
)
from livetranslate.ui.control_panel.panel import ControlPanel

__all__ = ["ControlPanel", "SETTINGS_FILE", "_load_saved_settings", "_save_settings"]
