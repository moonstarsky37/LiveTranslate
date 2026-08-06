"""Unified settings layer (Phase 2): schema + single-entry store."""

from livetranslate.config.schema import Settings
from livetranslate.config.store import SettingsStore

__all__ = ["Settings", "SettingsStore"]
