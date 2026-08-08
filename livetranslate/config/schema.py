"""Typed schema for user settings.

Field names mirror the JSON keys in user_settings.json exactly, so existing
files load without any conversion. Unknown keys are preserved verbatim in
`extras` (forward compatibility: an older build must never eat keys written by
a newer one). `to_dict()` only emits fields that were present in the source —
loading a sparse legacy file and saving it back must not inject new keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

# The subset the setup wizard persists on first launch (download_proxy is
# overridden with the user's wizard choice before saving).
_WIZARD_KEYS = (
    "hub",
    "download_proxy",
    "asr_engine",
    "funasr_model",
    "vad_mode",
    "vad_threshold",
    "energy_threshold",
    "min_speech_duration",
    "max_speech_duration",
    "silence_mode",
    "silence_duration",
    "asr_language",
    "target_language",
)

_INTERNAL_FIELDS = ("extras", "present")


@dataclass
class Settings:
    """Canonical settings fields and their fresh-install defaults."""

    hub: str = "hf"
    download_proxy: str = "system"
    asr_engine: str = "funasr"
    funasr_model: str = "sensevoice-small"
    vad_mode: str = "silero"
    vad_threshold: float = 0.3
    energy_threshold: float = 0.02
    min_speech_duration: float = 1.0
    max_speech_duration: float = 8.0
    silence_mode: str = "auto"
    silence_duration: float = 0.8
    vad_min_density: float = 0.25
    asr_language: str = "auto"
    target_language: str = "zh-TW"
    models: list[dict[str, Any]] = field(default_factory=list)
    active_model: int = 0
    sensevoice_pad_seconds: float = 0.5
    whisper_pad_seconds: float = 0.5
    remote_asr_url: str = "http://127.0.0.1:8765"
    whisper_model_size: str = "medium"
    asr_device: str = "cuda"
    audio_device: str | None = None
    mic_device: str | None = None
    system_prompt: str = ""
    timeout: int = 10
    incremental_asr: bool = False
    interim_interval: float = 2.0
    auto_save_transcript: bool = True
    ui_lang: str = "zh-TW"
    hf_token: str = ""
    cache_path: str | None = None
    style: dict[str, Any] = field(default_factory=dict)
    subtitle_mode: dict[str, Any] = field(default_factory=dict)
    overlay_x: int = 0
    overlay_y: int = 0
    overlay_w: int = 0
    overlay_h: int = 0

    # Bookkeeping (not settings themselves)
    extras: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    present: set[str] = field(default_factory=set, init=False, repr=False)

    @classmethod
    def known_keys(cls) -> set[str]:
        return {f.name for f in fields(cls)} - set(_INTERNAL_FIELDS)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = cls.known_keys()
        kwargs: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        present: set[str] = set()
        for key, value in data.items():
            if key in known:
                kwargs[key] = value
                present.add(key)
            else:
                extras[key] = value
        settings = cls(**kwargs)
        settings.extras = extras
        settings.present = present
        return settings

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name in _INTERNAL_FIELDS:
                continue
            if f.name in self.present:
                out[f.name] = getattr(self, f.name)
        out.update(self.extras)
        return out

    def wizard_defaults(self) -> dict[str, Any]:
        """The dict the setup wizard persists — single source of those values."""
        return {key: getattr(self, key) for key in _WIZARD_KEYS}
