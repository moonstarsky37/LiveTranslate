"""Translation tab: model list management, system prompt, network timeout."""

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sublume.ui.dialogs import ModelEditDialog
from sublume.i18n import t
from sublume.ui.control_panel.settings_io import _save_settings

log = logging.getLogger("Sublume.Panel")


class TranslationTabMixin:
    """Translation tab methods, mixed into ControlPanel."""

    def _create_translation_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        s = self._current_settings

        models_group = QGroupBox(t("group_model_configs"))
        models_layout = QVBoxLayout(models_group)

        self._model_list = QListWidget()
        self._model_list.setFont(QFont("Consolas", 9))
        self._model_list.itemDoubleClicked.connect(self._on_model_double_clicked)
        self._refresh_model_list()
        models_layout.addWidget(self._model_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("btn_add"))
        add_btn.clicked.connect(self._add_model)
        btn_row.addWidget(add_btn)
        edit_btn = QPushButton(t("btn_edit"))
        edit_btn.clicked.connect(self._edit_model)
        btn_row.addWidget(edit_btn)
        dup_btn = QPushButton(t("btn_duplicate"))
        dup_btn.clicked.connect(self._dup_model)
        btn_row.addWidget(dup_btn)
        del_btn = QPushButton(t("btn_remove"))
        del_btn.clicked.connect(self._remove_model)
        btn_row.addWidget(del_btn)
        models_layout.addLayout(btn_row)
        layout.addWidget(models_group)

        prompt_group = QGroupBox(t("group_system_prompt"))
        prompt_layout = QVBoxLayout(prompt_group)

        from sublume.translation.translator import DEFAULT_PROMPT, PROMPT_PRESETS

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(t("label_prompt_preset")))
        self._prompt_preset = QComboBox()
        self._prompt_preset.addItem(t("prompt_daily"), "daily")
        self._prompt_preset.addItem(t("prompt_esports"), "esports")
        self._prompt_preset.addItem(t("prompt_anime"), "anime")
        self._prompt_preset.addItem(t("prompt_webid"), "webid")
        self._prompt_preset.addItem(t("prompt_custom"), "custom")

        current_prompt = s.get("system_prompt", DEFAULT_PROMPT)
        preset_idx = 4  # default to custom
        for i, key in enumerate(["daily", "esports", "anime", "webid"]):
            if current_prompt.strip() == PROMPT_PRESETS[key].strip():
                preset_idx = i
                break
        if current_prompt.strip() == DEFAULT_PROMPT.strip():
            preset_idx = 0
        self._prompt_preset.setCurrentIndex(preset_idx)
        self._prompt_preset.currentIndexChanged.connect(self._on_prompt_preset_changed)
        preset_row.addWidget(self._prompt_preset, 1)
        prompt_layout.addLayout(preset_row)

        # Prompt text editor
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setFont(QFont("Consolas", 9))
        self._prompt_edit.setMaximumHeight(100)
        self._prompt_edit.setPlainText(current_prompt)
        self._prompt_debounce = QTimer()
        self._prompt_debounce.setSingleShot(True)
        self._prompt_debounce.setInterval(600)
        self._prompt_debounce.timeout.connect(self._apply_prompt)
        self._prompt_edit.textChanged.connect(self._prompt_debounce.start)
        prompt_layout.addWidget(self._prompt_edit)
        layout.addWidget(prompt_group)

        net_group = QGroupBox(t("group_network"))
        net_layout = QGridLayout(net_group)
        net_layout.setColumnStretch(0, 1)
        net_layout.setColumnMinimumWidth(1, 180)
        net_layout.addWidget(QLabel(t("label_timeout")), 0, 0)
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 60)
        self._timeout_spin.setValue(s.get("timeout", 5))
        self._timeout_spin.setSuffix(" s")
        self._timeout_spin.valueChanged.connect(
            lambda v: self._current_settings.update({"timeout": v})
        )
        self._timeout_spin.valueChanged.connect(self._auto_save)
        net_layout.addWidget(self._timeout_spin, 0, 1)
        layout.addWidget(net_group)

        layout.addStretch()
        return widget

    def _refresh_model_list(self):
        self._model_list.clear()
        active = self._current_settings.get("active_model", 0)
        for i, m in enumerate(self._current_settings.get("models", [])):
            prefix = ">>> " if i == active else "    "
            proxy = m.get("proxy", "none")
            proxy_tag = f"  [proxy: {proxy}]" if proxy != "none" else ""
            text = (
                f"{prefix}{m['name']}{proxy_tag}\n     {m['api_base']}  |  {m['model']}"
            )
            item = QListWidgetItem(text)
            if i == active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._model_list.addItem(item)

    def _emit_models_list_changed(self):
        models = self._current_settings.get("models", [])
        active_idx = self._current_settings.get("active_model", 0)
        self.models_list_changed.emit(models, active_idx)

    def _add_model(self):
        dlg = ModelEditDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if data["name"] and data["model"]:
                self._current_settings.setdefault("models", []).append(data)
                self._refresh_model_list()
                _save_settings(self._current_settings)
                self._emit_models_list_changed()

    def _edit_model(self):
        row = self._model_list.currentRow()
        models = self._current_settings.get("models", [])
        if row < 0 or row >= len(models):
            return
        dlg = ModelEditDialog(self, models[row])
        if dlg.exec():
            data = dlg.get_data()
            if data["name"] and data["model"]:
                models[row] = data
                self._refresh_model_list()
                _save_settings(self._current_settings)
                self._emit_models_list_changed()
                # Re-apply if editing the active model
                active = self._current_settings.get("active_model", 0)
                if row == active:
                    self.model_changed.emit(data)

    def _dup_model(self):
        row = self._model_list.currentRow()
        models = self._current_settings.get("models", [])
        if row < 0 or row >= len(models):
            return
        dup = dict(models[row])
        dup["name"] = dup["name"] + " (copy)"
        models.append(dup)
        self._refresh_model_list()
        _save_settings(self._current_settings)
        self._emit_models_list_changed()

    def _remove_model(self):
        row = self._model_list.currentRow()
        models = self._current_settings.get("models", [])
        if row < 0 or row >= len(models) or len(models) <= 1:
            return
        models.pop(row)
        active = self._current_settings.get("active_model", 0)
        if active >= len(models):
            self._current_settings["active_model"] = len(models) - 1
        self._refresh_model_list()
        self._model_list.setCurrentRow(min(row, len(models) - 1))
        _save_settings(self._current_settings)
        self._emit_models_list_changed()

    def _on_model_double_clicked(self, item):
        row = self._model_list.row(item)
        models = self._current_settings.get("models", [])
        if 0 <= row < len(models):
            self._model_list.setCurrentRow(row)
            self._edit_model()

    def _on_prompt_preset_changed(self, index):
        from sublume.translation.translator import DEFAULT_PROMPT, PROMPT_PRESETS
        key = self._prompt_preset.itemData(index)
        if key == "custom":
            return
        prompt = PROMPT_PRESETS.get(key, DEFAULT_PROMPT)
        self._prompt_edit.setPlainText(prompt)
        self._apply_prompt()

    def _apply_prompt(self):
        text = self._prompt_edit.toPlainText().strip()
        if text:
            self._current_settings["system_prompt"] = text
            active = self.get_active_model()
            if active:
                self.model_changed.emit(active)
            _save_settings(self._current_settings)
            log.info("System prompt updated")
            # Update preset combo to reflect current state
            from sublume.translation.translator import PROMPT_PRESETS
            self._prompt_preset.blockSignals(True)
            matched = 4  # custom
            for i, key in enumerate(["daily", "esports", "anime", "webid"]):
                if text.strip() == PROMPT_PRESETS[key].strip():
                    matched = i
                    break
            self._prompt_preset.setCurrentIndex(matched)
            self._prompt_preset.blockSignals(False)
