"""Subtitle tab: embeds SubtitleSettingsWidget for the OBS subtitle window."""

from sublume.ui.overlay.subtitle_settings import SubtitleSettingsWidget


class SubtitleTabMixin:
    """Subtitle tab methods, mixed into ControlPanel."""

    def _create_subtitle_tab(self):
        subtitle_settings = self._current_settings.get("subtitle_mode") or {}
        self._subtitle_widget = SubtitleSettingsWidget(subtitle_settings)
        self._subtitle_widget.settings_changed.connect(self._on_subtitle_settings_changed)
        return self._subtitle_widget

    def _on_subtitle_settings_changed(self, s):
        self._current_settings["subtitle_mode"] = s
        self._auto_save()
        self.subtitle_settings_changed.emit(s)

    def update_subtitle_settings(self, s):
        self._current_settings["subtitle_mode"] = s
        self._subtitle_widget.update_settings(s)
