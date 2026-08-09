"""Unified settings layer (Phase 2): schema + single-entry store."""

from sublume.config.schema import Settings
from sublume.config.store import SettingsStore

__all__ = ["Settings", "SettingsStore"]
