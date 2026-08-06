"""VAD / ASR tab: engine, language, devices, whisper download, VAD tuning."""

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from livetranslate.model_manager import (
    DEFAULT_FUNASR_MODEL,
    _WHISPER_SIZES,
    funasr_model_options,
    funasr_supports_padding,
    format_size,
    list_local_faster_whisper_models,
    normalize_funasr_model_key,
    resolve_custom_whisper_model,
)
from livetranslate.i18n import t, LANGUAGES
from livetranslate.ui.control_panel.settings_io import _save_settings


class VadTabMixin:
    """VAD / ASR tab methods, mixed into ControlPanel."""

    def _create_vad_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        s = self._current_settings

        asr_group = QGroupBox(t("group_asr_engine"))
        asr_layout = QGridLayout(asr_group)
        asr_layout.setColumnStretch(0, 1)
        asr_layout.setColumnMinimumWidth(1, 180)

        self._asr_engine = QComboBox()
        self._asr_engine.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._asr_engine.addItems(
            [
                f"[{t('asr_accurate')}] Whisper (faster-whisper)",
                f"[{t('asr_fast')}] FunASR",
                "Anime-Whisper (ja, anime/galgame)",
                "Remote Whisper (remote GPU server)",
            ]
        )
        engine_map_idx = {
            "whisper": 0,
            "funasr": 1,
            "anime-whisper": 2,
            "remote-whisper": 3,
        }
        engine_idx = engine_map_idx.get(s.get("asr_engine"), 0)
        self._asr_engine.setCurrentIndex(engine_idx)
        asr_layout.addWidget(QLabel(t("label_engine")), 0, 0)
        asr_layout.addWidget(self._asr_engine, 0, 1)
        self._asr_engine.currentIndexChanged.connect(self._auto_save)

        self._asr_lang = QComboBox()
        for code, native in LANGUAGES:
            label = t("asr_lang_auto") if code == "auto" else native
            self._asr_lang.addItem(f"{code} - {label}", code)
        lang = s.get("asr_language", self._config["asr"].get("language", "auto"))
        idx = self._asr_lang.findData(lang)
        if idx >= 0:
            self._asr_lang.setCurrentIndex(idx)
        asr_layout.addWidget(QLabel(t("label_language_hint")), 1, 0)
        asr_layout.addWidget(self._asr_lang, 1, 1)
        self._asr_lang.currentIndexChanged.connect(self._auto_save)

        self._asr_device = QComboBox()
        devices = ["cuda", "cpu"]
        try:
            import torch

            for i in range(torch.cuda.device_count()):
                name = torch.cuda.get_device_name(i)
                devices.insert(i, f"cuda:{i} ({name})")
            if torch.cuda.device_count() > 0:
                devices = [d for d in devices if d != "cuda"]
        except Exception:
            pass
        self._asr_device.addItems(devices)
        saved_dev = s.get("asr_device", self._config["asr"].get("device", "cuda"))
        for i in range(self._asr_device.count()):
            if self._asr_device.itemText(i).startswith(saved_dev):
                self._asr_device.setCurrentIndex(i)
                break
        asr_layout.addWidget(QLabel(t("label_device")), 2, 0)
        asr_layout.addWidget(self._asr_device, 2, 1)
        self._asr_device.currentIndexChanged.connect(self._auto_save)

        self._funasr_model_label = QLabel(t("label_funasr_model"))
        self._funasr_model_combo = QComboBox()
        for key, display_name in funasr_model_options():
            self._funasr_model_combo.addItem(display_name, key)
        saved_funasr_model = normalize_funasr_model_key(
            s.get("funasr_model", DEFAULT_FUNASR_MODEL)
        )
        funasr_idx = self._funasr_model_combo.findData(saved_funasr_model)
        if funasr_idx >= 0:
            self._funasr_model_combo.setCurrentIndex(funasr_idx)
        self._funasr_model_combo.currentIndexChanged.connect(
            self._on_funasr_model_changed
        )
        asr_layout.addWidget(self._funasr_model_label, 3, 0)
        asr_layout.addWidget(self._funasr_model_combo, 3, 1)

        self._whisper_pad_label = QLabel(t("label_whisper_padding"))
        self._whisper_pad_seconds = QDoubleSpinBox()
        self._whisper_pad_seconds.setRange(0.0, 5.0)
        self._whisper_pad_seconds.setDecimals(2)
        self._whisper_pad_seconds.setSingleStep(0.1)
        try:
            whisper_pad_seconds = float(s.get("whisper_pad_seconds", 0.5))
        except (TypeError, ValueError):
            whisper_pad_seconds = 0.5
        self._whisper_pad_seconds.setValue(whisper_pad_seconds)
        self._whisper_pad_seconds.setSuffix(" s")
        self._whisper_pad_seconds.setSpecialValueText(t("whisper_padding_off"))
        self._whisper_pad_seconds.setToolTip(t("whisper_padding_tooltip"))
        asr_layout.addWidget(self._whisper_pad_label, 4, 0)
        asr_layout.addWidget(self._whisper_pad_seconds, 4, 1)
        self._whisper_pad_seconds.valueChanged.connect(self._auto_save)

        self._sensevoice_pad_label = QLabel(t("label_sensevoice_padding"))
        self._sensevoice_pad_seconds = QDoubleSpinBox()
        self._sensevoice_pad_seconds.setRange(0.0, 5.0)
        self._sensevoice_pad_seconds.setDecimals(2)
        self._sensevoice_pad_seconds.setSingleStep(0.1)
        try:
            sensevoice_pad_seconds = float(s.get("sensevoice_pad_seconds", 0.5))
        except (TypeError, ValueError):
            sensevoice_pad_seconds = 0.5
        self._sensevoice_pad_seconds.setValue(sensevoice_pad_seconds)
        self._sensevoice_pad_seconds.setSuffix(" s")
        self._sensevoice_pad_seconds.setSpecialValueText(t("sensevoice_padding_off"))
        self._sensevoice_pad_seconds.setToolTip(t("sensevoice_padding_tooltip"))
        asr_layout.addWidget(self._sensevoice_pad_label, 5, 0)
        asr_layout.addWidget(self._sensevoice_pad_seconds, 5, 1)
        self._sensevoice_pad_seconds.valueChanged.connect(self._auto_save)

        self._audio_device = QComboBox()
        self._audio_device.addItem(t("audio_disabled"))
        self._audio_device.addItem(t("system_default"))
        try:
            from livetranslate.core.audio_capture import list_output_devices

            for name in list_output_devices():
                self._audio_device.addItem(name)
        except Exception:
            pass
        saved_audio = s.get("audio_device")
        if saved_audio == "__disabled__":
            self._audio_device.setCurrentIndex(0)
        elif saved_audio:
            idx = self._audio_device.findText(saved_audio)
            if idx >= 0:
                self._audio_device.setCurrentIndex(idx)
        else:
            self._audio_device.setCurrentIndex(1)  # system default
        asr_layout.addWidget(QLabel(t("label_audio")), 6, 0)
        asr_layout.addWidget(self._audio_device, 6, 1)
        self._audio_device.currentIndexChanged.connect(self._auto_save)

        self._mic_device = QComboBox()
        self._mic_device.addItem(t("mic_disabled"))
        self._mic_device.addItem(t("system_default"))
        try:
            from livetranslate.core.audio_capture import list_input_devices

            for name in list_input_devices():
                self._mic_device.addItem(name)
        except Exception:
            pass
        saved_mic = s.get("mic_device")
        if saved_mic:
            if saved_mic in ("__default__", "default"):
                self._mic_device.setCurrentIndex(1)
            else:
                idx = self._mic_device.findText(saved_mic)
                if idx >= 0:
                    self._mic_device.setCurrentIndex(idx)
        asr_layout.addWidget(QLabel(t("label_mic")), 7, 0)
        asr_layout.addWidget(self._mic_device, 7, 1)
        self._mic_device.currentIndexChanged.connect(self._auto_save)

        # This fork downloads exclusively from HuggingFace — the source
        # selector was removed (spec D2).

        self._ui_lang_combo = QComboBox()
        self._ui_lang_combo.addItems(["English", "繁體中文", "简体中文"])
        from livetranslate.i18n import get_lang

        saved_lang = s.get("ui_lang", get_lang())
        if saved_lang == "zh":
            saved_lang = "zh-TW"
        _ui_lang_index = {"en": 0, "zh-TW": 1, "zh-CN": 2}
        self._ui_lang_combo.setCurrentIndex(_ui_lang_index.get(saved_lang, 1))
        asr_layout.addWidget(QLabel(t("label_ui_lang")), 9, 0)
        asr_layout.addWidget(self._ui_lang_combo, 9, 1)
        self._ui_lang_combo.currentIndexChanged.connect(self._on_ui_lang_changed)

        layout.addWidget(asr_group)

        # Whisper model download — only visible when engine is Whisper
        self._whisper_group = QGroupBox(t("group_download_whisper"))
        whisper_layout = QHBoxLayout(self._whisper_group)
        self._whisper_size_combo = QComboBox()
        saved_size = s.get(
            "whisper_model_size", self._config["asr"].get("model_size", "medium")
        )
        self._populate_whisper_models(saved_size)
        self._whisper_size_combo.currentIndexChanged.connect(
            self._on_whisper_size_changed
        )
        whisper_layout.addWidget(self._whisper_size_combo)
        self._whisper_status = QLabel("")
        self._whisper_status.setStyleSheet("color: #888; font-size: 11px;")
        whisper_layout.addWidget(self._whisper_status, 1)
        self._whisper_dl_btn = QPushButton(t("btn_download_whisper"))
        self._whisper_dl_btn.clicked.connect(self._download_whisper)
        whisper_layout.addWidget(self._whisper_dl_btn)
        layout.addWidget(self._whisper_group)
        self._whisper_group.setVisible(engine_idx == 0)
        self._asr_engine.currentIndexChanged.connect(
            self._on_engine_changed_whisper_vis
        )
        self._on_engine_changed_whisper_vis(engine_idx)
        self._update_whisper_size_label()

        # Remote ASR server URL — only visible when engine is Remote Whisper
        self._remote_group = QGroupBox("Remote ASR Server")
        remote_layout = QHBoxLayout(self._remote_group)
        remote_layout.addWidget(QLabel("URL"))
        self._remote_url_edit = QLineEdit(
            s.get("remote_asr_url", "http://127.0.0.1:8765")
        )
        self._remote_url_edit.setPlaceholderText("http://127.0.0.1:8765")
        self._remote_url_edit.editingFinished.connect(self._auto_save)
        remote_layout.addWidget(self._remote_url_edit, 1)
        layout.addWidget(self._remote_group)
        self._remote_group.setVisible(engine_idx == 3)

        mode_group = QGroupBox(t("group_vad_mode"))
        mode_layout = QVBoxLayout(mode_group)
        self._vad_mode = QComboBox()
        self._vad_mode.addItems([t("vad_silero"), t("vad_energy"), t("vad_disabled")])
        mode_map = {"silero": 0, "energy": 1, "disabled": 2}
        self._vad_mode.setCurrentIndex(mode_map.get(s.get("vad_mode", "energy"), 1))
        self._vad_mode.currentIndexChanged.connect(self._on_vad_mode_changed)
        self._vad_mode.currentIndexChanged.connect(self._auto_save)
        mode_layout.addWidget(self._vad_mode)
        layout.addWidget(mode_group)

        silero_group = QGroupBox(t("group_silero_threshold"))
        silero_layout = QGridLayout(silero_group)
        self._vad_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._vad_threshold_slider.setRange(0, 100)
        vad_pct = int(s.get("vad_threshold", 0.5) * 100)
        self._vad_threshold_slider.setValue(vad_pct)
        self._vad_threshold_slider.valueChanged.connect(self._on_threshold_changed)
        self._vad_threshold_slider.sliderReleased.connect(self._auto_save)
        self._vad_threshold_label = QLabel(f"{vad_pct}%")
        self._vad_threshold_label.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        silero_layout.addWidget(QLabel(t("label_threshold")), 0, 0)
        silero_layout.addWidget(self._vad_threshold_slider, 0, 1)
        silero_layout.addWidget(self._vad_threshold_label, 0, 2)
        layout.addWidget(silero_group)

        energy_group = QGroupBox(t("group_energy_threshold"))
        energy_layout = QGridLayout(energy_group)
        self._energy_slider = QSlider(Qt.Orientation.Horizontal)
        self._energy_slider.setRange(1, 100)
        energy_pm = int(s.get("energy_threshold", 0.03) * 1000)
        self._energy_slider.setValue(energy_pm)
        self._energy_slider.valueChanged.connect(self._on_energy_changed)
        self._energy_slider.sliderReleased.connect(self._auto_save)
        self._energy_label = QLabel(f"{energy_pm}\u2030")
        self._energy_label.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        energy_layout.addWidget(QLabel(t("label_threshold")), 0, 0)
        energy_layout.addWidget(self._energy_slider, 0, 1)
        energy_layout.addWidget(self._energy_label, 0, 2)
        layout.addWidget(energy_group)

        timing_group = QGroupBox(t("group_timing"))
        timing_layout = QGridLayout(timing_group)
        timing_layout.setColumnStretch(0, 1)
        timing_layout.setColumnMinimumWidth(1, 180)
        self._min_speech = QDoubleSpinBox()
        self._min_speech.setRange(0.1, 5.0)
        self._min_speech.setSingleStep(0.1)
        self._min_speech.setValue(s.get("min_speech_duration", 2.0))
        self._min_speech.setSuffix(" s")
        self._min_speech.valueChanged.connect(self._on_timing_changed)
        self._min_speech.valueChanged.connect(self._auto_save)
        self._max_speech = QDoubleSpinBox()
        self._max_speech.setRange(2.0, 30.0)
        self._max_speech.setSingleStep(1.0)
        self._max_speech.setValue(s.get("max_speech_duration", 6.0))
        self._max_speech.setSuffix(" s")
        self._max_speech.valueChanged.connect(self._on_timing_changed)
        self._max_speech.valueChanged.connect(self._auto_save)
        self._silence_mode = QComboBox()
        self._silence_mode.addItems([t("silence_auto"), t("silence_fixed")])
        saved_smode = s.get("silence_mode", "auto")
        self._silence_mode.setCurrentIndex(0 if saved_smode == "auto" else 1)
        self._silence_mode.currentIndexChanged.connect(self._on_silence_mode_changed)
        self._silence_mode.currentIndexChanged.connect(self._on_timing_changed)
        self._silence_mode.currentIndexChanged.connect(self._auto_save)

        self._silence_duration = QDoubleSpinBox()
        self._silence_duration.setRange(0.1, 3.0)
        self._silence_duration.setSingleStep(0.1)
        self._silence_duration.setValue(s.get("silence_duration", 0.8))
        self._silence_duration.setSuffix(" s")
        self._silence_duration.setEnabled(saved_smode != "auto")
        self._silence_duration.valueChanged.connect(self._on_timing_changed)
        self._silence_duration.valueChanged.connect(self._auto_save)

        timing_layout.addWidget(QLabel(t("label_min_speech")), 0, 0)
        timing_layout.addWidget(self._min_speech, 0, 1)
        timing_layout.addWidget(QLabel(t("label_max_speech")), 1, 0)
        timing_layout.addWidget(self._max_speech, 1, 1)
        timing_layout.addWidget(QLabel(t("label_silence")), 2, 0)
        timing_layout.addWidget(self._silence_mode, 2, 1)
        timing_layout.addWidget(QLabel(t("label_silence_dur")), 3, 0)
        timing_layout.addWidget(self._silence_duration, 3, 1)

        from PyQt6.QtWidgets import QCheckBox

        self._incremental_asr_cb = QCheckBox(t("label_incremental_asr"))
        self._incremental_asr_cb.setToolTip(t("incremental_asr_tooltip"))
        self._incremental_asr_cb.setChecked(s.get("incremental_asr", False))
        self._incremental_asr_cb.toggled.connect(self._on_timing_changed)
        self._incremental_asr_cb.toggled.connect(self._auto_save)
        timing_layout.addWidget(self._incremental_asr_cb, 4, 0)

        self._interim_interval_spin = QDoubleSpinBox()
        self._interim_interval_spin.setRange(1.0, 10.0)
        self._interim_interval_spin.setSingleStep(0.5)
        self._interim_interval_spin.setValue(s.get("interim_interval", 2.0))
        self._interim_interval_spin.setSuffix(" s")
        self._interim_interval_spin.setEnabled(s.get("incremental_asr", False))
        self._interim_interval_spin.valueChanged.connect(self._on_timing_changed)
        self._interim_interval_spin.valueChanged.connect(self._auto_save)
        self._incremental_asr_cb.toggled.connect(self._interim_interval_spin.setEnabled)
        timing_layout.addWidget(QLabel(t("label_interim_interval")), 5, 0)
        timing_layout.addWidget(self._interim_interval_spin, 5, 1)

        layout.addWidget(timing_group)

        layout.addStretch()
        return widget

    def _get_asr_lang_code(self) -> str:
        """Get the language code from the ASR language combo (stored as userData)."""
        return self._asr_lang.currentData() or "auto"

    def _on_engine_changed_whisper_vis(self, index):
        self._whisper_group.setVisible(index == 0)
        is_funasr = index == 1
        if hasattr(self, "_funasr_model_combo"):
            self._funasr_model_label.setVisible(is_funasr)
            self._funasr_model_combo.setVisible(is_funasr)
        if hasattr(self, "_whisper_pad_seconds"):
            is_whisper = index == 0
            self._whisper_pad_label.setVisible(is_whisper)
            self._whisper_pad_seconds.setVisible(is_whisper)
        if hasattr(self, "_sensevoice_pad_seconds"):
            show_funasr_pad = is_funasr and funasr_supports_padding(
                self._selected_funasr_model()
            )
            self._sensevoice_pad_label.setVisible(show_funasr_pad)
            self._sensevoice_pad_seconds.setVisible(show_funasr_pad)
        if hasattr(self, "_remote_group"):
            self._remote_group.setVisible(index == 3)
        # Resize window to fit content after whisper group visibility change
        def _fit():
            self.adjustSize()
            h = self.sizeHint().height() + 20
            self.resize(self.width(), max(h, self.minimumHeight()))
        QTimer.singleShot(0, _fit)

    def _selected_funasr_model(self) -> str:
        value = self._funasr_model_combo.currentData()
        return normalize_funasr_model_key(str(value) if value else None)

    def _on_funasr_model_changed(self):
        self._current_settings["funasr_model"] = self._selected_funasr_model()
        self._on_engine_changed_whisper_vis(self._asr_engine.currentIndex())
        self._auto_save()

    def _selected_whisper_model(self) -> str:
        value = self._whisper_size_combo.currentData()
        return str(value) if value else self._whisper_size_combo.currentText()

    def _populate_whisper_models(self, saved_value: str):
        self._whisper_size_combo.clear()
        for size in _WHISPER_SIZES:
            self._whisper_size_combo.addItem(size, size)

        local_prefix = t("whisper_local_prefix")
        for item in list_local_faster_whisper_models():
            idx = self._whisper_size_combo.count()
            self._whisper_size_combo.addItem(
                f"{local_prefix}: {item['name']}", item["path"]
            )
            self._whisper_size_combo.setItemData(
                idx, item["path"], Qt.ItemDataRole.ToolTipRole
            )

        selected = resolve_custom_whisper_model(saved_value) or saved_value
        idx = self._whisper_size_combo.findData(selected)
        if idx < 0:
            idx = self._whisper_size_combo.findText(saved_value)
        if idx < 0 and selected:
            label = f"{t('whisper_missing_local')}: {Path(str(selected)).name}"
            idx = self._whisper_size_combo.count()
            self._whisper_size_combo.addItem(label, selected)
            self._whisper_size_combo.setItemData(
                idx, str(selected), Qt.ItemDataRole.ToolTipRole
            )
        if idx >= 0:
            self._whisper_size_combo.setCurrentIndex(idx)

    def _update_whisper_size_label(self):
        from livetranslate.model_manager import is_asr_cached, _MODEL_SIZE_BYTES

        size = self._selected_whisper_model()
        cached = is_asr_cached("whisper", size, "hf")
        if size not in _WHISPER_SIZES:
            if cached:
                self._whisper_status.setText(t("whisper_local_ready"))
                self._whisper_status.setStyleSheet("color: #4a4; font-size: 11px;")
            else:
                self._whisper_status.setText(t("whisper_invalid_local"))
                self._whisper_status.setStyleSheet("color: #d66; font-size: 11px;")
            self._whisper_dl_btn.setEnabled(False)
            return
        if cached:
            self._whisper_status.setText(t("whisper_already_cached"))
            self._whisper_status.setStyleSheet("color: #4a4; font-size: 11px;")
            self._whisper_dl_btn.setEnabled(False)
        else:
            est = _MODEL_SIZE_BYTES.get(f"whisper-{size}", 0)
            self._whisper_status.setText(f"~{format_size(est)}")
            self._whisper_status.setStyleSheet("color: #888; font-size: 11px;")
            self._whisper_dl_btn.setEnabled(True)

    def _on_whisper_size_changed(self):
        self._current_settings["whisper_model_size"] = (
            self._selected_whisper_model()
        )
        self._update_whisper_size_label()
        # If already cached, switch engine immediately
        from livetranslate.model_manager import is_asr_cached

        size = self._selected_whisper_model()
        if is_asr_cached("whisper", size, "hf"):
            self._auto_save()

    def _download_whisper(self):
        from livetranslate.model_manager import is_asr_cached, get_missing_models

        size = self._selected_whisper_model()
        if size not in _WHISPER_SIZES:
            return
        hub = "hf"
        if is_asr_cached("whisper", size, hub):
            return
        missing = get_missing_models("whisper", size, hub)
        missing = [m for m in missing if m["type"] != "silero-vad"]
        if not missing:
            return
        from livetranslate.ui.dialogs import ModelDownloadDialog

        dlg = ModelDownloadDialog(missing, hub=hub, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._update_whisper_size_label()
            # Switch to Whisper engine with the downloaded size
            self._auto_save()

    def _on_silence_mode_changed(self, index):
        self._silence_duration.setEnabled(index == 1)

    def _on_vad_mode_changed(self, index):
        modes = ["silero", "energy", "disabled"]
        self._current_settings["vad_mode"] = modes[index]

    def _on_threshold_changed(self, value):
        val = value / 100.0
        self._current_settings["vad_threshold"] = val
        self._vad_threshold_label.setText(f"{value}%")
        if not self._vad_threshold_slider.isSliderDown():
            self._auto_save()

    def _on_energy_changed(self, value):
        val = value / 1000.0
        self._current_settings["energy_threshold"] = val
        self._energy_label.setText(f"{value}\u2030")
        if not self._energy_slider.isSliderDown():
            self._auto_save()

    def _on_timing_changed(self):
        self._current_settings["min_speech_duration"] = round(self._min_speech.value(), 2)
        self._current_settings["max_speech_duration"] = round(self._max_speech.value(), 2)
        self._current_settings["silence_mode"] = (
            "auto" if self._silence_mode.currentIndex() == 0 else "fixed"
        )
        self._current_settings["silence_duration"] = round(self._silence_duration.value(), 2)
        self._current_settings["incremental_asr"] = self._incremental_asr_cb.isChecked()
        self._current_settings["interim_interval"] = round(self._interim_interval_spin.value(), 2)

    def _on_ui_lang_changed(self, index):
        lang = ("en", "zh-TW", "zh-CN")[index] if 0 <= index <= 2 else "zh-TW"
        self._current_settings["ui_lang"] = lang
        _save_settings(self._current_settings)
        from livetranslate.i18n import set_lang

        set_lang(lang)
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            "LiveTranslate",
            "Language changed. Please restart the application.\n"
            "語言已變更，請重新啟動應用程式。\n"
            "语言已更改，请重启应用程序。",
        )
