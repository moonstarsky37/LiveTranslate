"""ControlPanel container: tab assembly, auto-save plumbing, settings apply."""

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from livetranslate.model_manager import (
    DEFAULT_FUNASR_MODEL,
    migrate_funasr_settings,
    normalize_funasr_model_key,
)
from livetranslate.i18n import t
from livetranslate.ui.control_panel.settings_io import (
    SETTINGS_FILE,
    _load_saved_settings,
    _save_settings,
)
from livetranslate.ui.control_panel.vad_tab import VadTabMixin
from livetranslate.ui.control_panel.translation_tab import TranslationTabMixin
from livetranslate.ui.control_panel.style_tab import StyleTabMixin
from livetranslate.ui.control_panel.subtitle_tab import SubtitleTabMixin
from livetranslate.ui.control_panel.benchmark_tab import BenchmarkTabMixin
from livetranslate.ui.control_panel.cache_tab import CacheTabMixin
from livetranslate.ui.control_panel.changelog_tab import ChangelogTabMixin

log = logging.getLogger("LiveTranslate.Panel")


class ControlPanel(
    VadTabMixin,
    TranslationTabMixin,
    StyleTabMixin,
    SubtitleTabMixin,
    BenchmarkTabMixin,
    CacheTabMixin,
    ChangelogTabMixin,
    QWidget,
):
    """Settings and monitoring panel."""

    settings_changed = pyqtSignal(dict)
    model_changed = pyqtSignal(dict)
    models_list_changed = pyqtSignal(list, int)
    subtitle_settings_changed = pyqtSignal(dict)
    _bench_result = pyqtSignal(str)
    _cache_result = pyqtSignal(list)
    reset_positions = pyqtSignal()

    def __init__(self, config, saved_settings=None):
        super().__init__()
        self._config = config
        self.setWindowTitle(t("window_control_panel"))
        # The overlay and the subtitle window are both WindowStaysOnTopHint, and
        # raise_() cannot cross that z-band — without the same hint the panel
        # opens *underneath* the very widgets it configures.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(480, 560)
        self.resize(520, 650)

        saved = migrate_funasr_settings(saved_settings) or _load_saved_settings()
        if saved:
            self._current_settings = saved
        else:
            tc = config["translation"]
            self._current_settings = {
                "vad_mode": "silero",
                "vad_threshold": config["asr"]["vad_threshold"],
                "energy_threshold": 0.02,
                "min_speech_duration": config["asr"]["min_speech_duration"],
                "max_speech_duration": config["asr"]["max_speech_duration"],
                "silence_mode": "auto",
                "silence_duration": 0.8,
                "asr_language": config["asr"].get("language", "auto"),
                "asr_engine": "funasr",
                "funasr_model": config["asr"].get(
                    "funasr_model", DEFAULT_FUNASR_MODEL
                ),
                "asr_device": "cuda",
                "sensevoice_pad_seconds": config["asr"].get(
                    "sensevoice_pad_seconds", 0.5
                ),
                "whisper_pad_seconds": config["asr"].get(
                    "whisper_pad_seconds", 0.5
                ),
                "models": [
                    {
                        "name": f"{tc['model']}",
                        "api_base": tc["api_base"],
                        "api_key": tc["api_key"],
                        "model": tc["model"],
                    }
                ],
                "active_model": 0,
                "hub": "hf",
            }

        if "models" not in self._current_settings:
            tc = config["translation"]
            self._current_settings["models"] = [
                {
                    "name": f"{tc['model']}",
                    "api_base": tc["api_base"],
                    "api_key": tc["api_key"],
                    "model": tc["model"],
                }
            ]
            self._current_settings["active_model"] = 0

        self._current_settings.setdefault(
            "funasr_model",
            config["asr"].get("funasr_model", DEFAULT_FUNASR_MODEL),
        )
        self._current_settings["funasr_model"] = normalize_funasr_model_key(
            self._current_settings.get("funasr_model")
        )
        self._current_settings.setdefault(
            "sensevoice_pad_seconds",
            config["asr"].get("sensevoice_pad_seconds", 0.5),
        )
        self._current_settings.setdefault(
            "whisper_pad_seconds",
            config["asr"].get("whisper_pad_seconds", 0.5),
        )

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tabs.addTab(self._create_vad_tab(), t("tab_vad_asr"))
        tabs.addTab(self._create_translation_tab(), t("tab_translation"))
        tabs.addTab(self._create_style_tab(), t("tab_style"))
        tabs.addTab(self._create_subtitle_tab(), t("tab_subtitle"))
        tabs.addTab(self._create_benchmark_tab(), t("tab_benchmark"))
        self._cache_tab_index = tabs.addTab(self._create_cache_tab(), t("tab_cache"))
        tabs.addTab(self._create_changelog_tab(), t("tab_changelog"))
        tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(tabs)

        self._bench_result.connect(self._on_bench_result)
        self._cache_result.connect(self._on_cache_result)

        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._do_auto_save)

        # Fit initial height based on whisper group visibility
        QTimer.singleShot(0, lambda: self.resize(self.width(), self.sizeHint().height() + 20))

    def _on_tab_changed(self, index):
        if index == self._cache_tab_index:
            self._refresh_cache()

    def _auto_save(self):
        self._save_timer.start()

    def _do_auto_save(self):
        self._apply_settings()
        _save_settings(self._current_settings)

    def _apply_settings(self):
        self._current_settings["asr_language"] = self._get_asr_lang_code()
        engine_map = {
            0: "whisper",
            1: "funasr",
            2: "anime-whisper",
            3: "remote-whisper",
            4: "sensevoice-onnx",
        }
        self._current_settings["asr_engine"] = engine_map.get(
            self._asr_engine.currentIndex(), "whisper"
        )
        self._current_settings["funasr_model"] = self._selected_funasr_model()
        if hasattr(self, "_remote_url_edit"):
            url = self._remote_url_edit.text().strip()
            if url:
                self._current_settings["remote_asr_url"] = url
        self._current_settings["whisper_model_size"] = (
            self._selected_whisper_model()
        )
        dev_text = self._asr_device.currentText()
        self._current_settings["asr_device"] = dev_text.split(" (")[0]
        audio_idx = self._audio_device.currentIndex()
        if audio_idx == 0:
            self._current_settings["audio_device"] = "__disabled__"
        elif audio_idx == 1:
            self._current_settings["audio_device"] = None
        else:
            self._current_settings["audio_device"] = self._audio_device.currentText()
        mic_idx = self._mic_device.currentIndex()
        if mic_idx == 0:
            self._current_settings["mic_device"] = None
        elif mic_idx == 1:
            self._current_settings["mic_device"] = "__default__"
        else:
            self._current_settings["mic_device"] = self._mic_device.currentText()
        self._current_settings["hub"] = "hf"
        self._current_settings["sensevoice_pad_seconds"] = round(
            self._sensevoice_pad_seconds.value(), 2
        )
        self._current_settings["whisper_pad_seconds"] = round(
            self._whisper_pad_seconds.value(), 2
        )
        prompt_text = self._prompt_edit.toPlainText().strip()
        if prompt_text:
            self._current_settings["system_prompt"] = prompt_text
        self._current_settings["timeout"] = self._timeout_spin.value()
        if hasattr(self, "_incremental_asr_cb"):
            self._on_timing_changed()
        if hasattr(self, "_auto_save_transcript_cb"):
            self._current_settings["auto_save_transcript"] = (
                self._auto_save_transcript_cb.isChecked()
            )
        if hasattr(self, "_overlay_template_combo"):
            self._current_settings["overlay_template"] = (
                self._overlay_template_combo.currentData()
            )
        if hasattr(self, "_hf_token_edit"):
            self._current_settings["hf_token"] = self._hf_token_edit.text().strip()
        if hasattr(self, "_style_preset"):
            self._current_settings["style"] = self._collect_style()
        safe = {
            k: v
            for k, v in self._current_settings.items()
            if k not in ("models", "system_prompt", "hf_token")
        }
        log.info(f"Settings applied: {safe}")
        self.settings_changed.emit(dict(self._current_settings))

    def get_settings(self):
        return dict(self._current_settings)

    def get_active_model(self) -> dict | None:
        models = self._current_settings.get("models", [])
        idx = self._current_settings.get("active_model", 0)
        if 0 <= idx < len(models):
            return models[idx]
        return None

    def has_saved_settings(self) -> bool:
        return SETTINGS_FILE.exists()
