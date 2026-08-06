"""Settings dataclass schema — field names mirror user_settings.json keys.

Contract: an existing settings file loads without conversion, unknown keys are
preserved verbatim through a load/save round trip, and Settings() defaults are
the canonical fresh-install state (what the setup wizard persists).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from livetranslate.config.schema import Settings  # noqa: E402


# Shape mirrors a real user_settings.json from a working install.
REAL_FILE = {
    "hub": "hf",
    "download_proxy": "system",
    "asr_engine": "funasr",
    "funasr_model": "sensevoice-small",
    "vad_mode": "silero",
    "vad_threshold": 0.3,
    "energy_threshold": 0.02,
    "min_speech_duration": 1.0,
    "max_speech_duration": 8.0,
    "silence_mode": "auto",
    "silence_duration": 0.8,
    "asr_language": "ja",
    "target_language": "zh-TW",
    "models": [
        {
            "name": "translategemma-local",
            "api_base": "http://localhost:8080/v1",
            "api_key": "sk-local",
            "model": "translategemma",
            "proxy": "none",
            "context_turns": 10,
        }
    ],
    "active_model": 0,
    "sensevoice_pad_seconds": 0.5,
    "whisper_pad_seconds": 0.5,
    "remote_asr_url": "http://127.0.0.1:8765",
    "whisper_model_size": "medium",
    "asr_device": "cuda:0",
    "audio_device": None,
    "mic_device": None,
    "system_prompt": "You are a real-time subtitle translator.",
    "timeout": 5,
    "incremental_asr": False,
    "interim_interval": 2.0,
    "auto_save_transcript": True,
    "style": {"preset": "default", "bg_color": "#000000"},
    "subtitle_mode": {"enabled": False, "window_x": 100, "window_y": 625},
    "overlay_x": 2571,
    "overlay_y": 94,
    "overlay_w": 1085,
    "overlay_h": 875,
}


def test_real_file_round_trips_exactly():
    settings = Settings.from_dict(REAL_FILE)
    assert settings.to_dict() == REAL_FILE


def test_unknown_future_keys_are_preserved():
    data = dict(REAL_FILE)
    data["some_future_key"] = {"nested": [1, 2, 3]}
    settings = Settings.from_dict(data)
    assert settings.to_dict()["some_future_key"] == {"nested": [1, 2, 3]}


def test_typed_fields_are_accessible_as_attributes():
    settings = Settings.from_dict(REAL_FILE)
    assert settings.hub == "hf"
    assert settings.target_language == "zh-TW"
    assert settings.active_model == 0
    assert settings.models[0]["model"] == "translategemma"


def test_defaults_match_fresh_install_state():
    # These are the values the setup wizard persists on first launch —
    # Settings() is their single source of truth.
    s = Settings()
    assert s.hub == "hf"
    assert s.download_proxy == "system"
    assert s.asr_engine == "funasr"
    assert s.funasr_model == "sensevoice-small"
    assert s.vad_mode == "silero"
    assert s.vad_threshold == 0.3
    assert s.energy_threshold == 0.02
    assert s.min_speech_duration == 1.0
    assert s.max_speech_duration == 8.0
    assert s.silence_mode == "auto"
    assert s.silence_duration == 0.8
    assert s.asr_language == "auto"
    assert s.target_language == "zh-TW"


def test_wizard_defaults_dict_shape():
    # The wizard persists exactly this subset; keep it stable.
    d = Settings().wizard_defaults()
    assert d == {
        "hub": "hf",
        "download_proxy": "system",
        "asr_engine": "funasr",
        "funasr_model": "sensevoice-small",
        "vad_mode": "silero",
        "vad_threshold": 0.3,
        "energy_threshold": 0.02,
        "min_speech_duration": 1.0,
        "max_speech_duration": 8.0,
        "silence_mode": "auto",
        "silence_duration": 0.8,
        "asr_language": "auto",
        "target_language": "zh-TW",
    }
