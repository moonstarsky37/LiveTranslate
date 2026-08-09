"""Add/edit form for a translation model entry in user_settings.json."""

import json

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QMessageBox,
    QSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from livetranslate.i18n import t


class ModelEditDialog(QDialog):
    """Dialog for adding/editing a model configuration."""

    def __init__(self, parent=None, model_data=None):
        super().__init__(parent)
        self.setWindowTitle(
            t("dialog_edit_model") if model_data else t("dialog_add_model")
        )
        self.setMinimumWidth(500)

        root = QVBoxLayout(self)

        # --- Basic section ---
        basic_group = QGroupBox()
        basic_group.setFlat(True)
        layout = QFormLayout(basic_group)

        self._name = QLineEdit()
        self._api_base = QLineEdit()
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._model = QLineEdit()

        self._proxy_mode = QComboBox()
        self._proxy_mode.addItems(
            [t("proxy_none"), t("proxy_system"), t("proxy_custom")]
        )
        self._proxy_mode.currentIndexChanged.connect(self._on_proxy_mode_changed)
        self._proxy_url = QLineEdit()
        self._proxy_url.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_url.setEnabled(False)

        self._no_system_role = QCheckBox(t("no_system_role"))
        self._no_system_role.setToolTip(t("no_system_role_hint"))
        self._no_think = QCheckBox(t("no_think"))
        self._no_think.setToolTip(t("no_think_hint"))
        self._no_think.setChecked(True)
        self._streaming = QCheckBox(t("streaming"))
        self._streaming.setToolTip(t("streaming_hint"))
        self._streaming.setChecked(True)
        self._json_response = QCheckBox(t("json_response"))
        self._json_response.setToolTip(t("json_response_hint"))
        self._context_turns = QSpinBox()
        self._context_turns.setRange(0, 20)
        self._context_turns.setValue(0)
        self._context_turns.setToolTip(t("context_turns_hint"))

        price_suffix = t("price_suffix")
        self._input_price = QDoubleSpinBox()
        self._input_price.setRange(0, 999)
        self._input_price.setDecimals(2)
        self._input_price.setSuffix(price_suffix)
        self._input_price.setSpecialValueText("—")
        self._output_price = QDoubleSpinBox()
        self._output_price.setRange(0, 999)
        self._output_price.setDecimals(2)
        self._output_price.setSuffix(price_suffix)
        self._output_price.setSpecialValueText("—")

        price_row = QHBoxLayout()
        price_row.addWidget(QLabel(t("label_input_price")))
        price_row.addWidget(self._input_price)
        price_row.addWidget(QLabel(t("label_output_price")))
        price_row.addWidget(self._output_price)

        layout.addRow(t("label_display_name"), self._name)
        layout.addRow(t("label_api_base"), self._api_base)
        layout.addRow(t("label_api_key"), self._api_key)
        layout.addRow(t("label_model"), self._model)
        layout.addRow(t("label_proxy"), self._proxy_mode)
        layout.addRow(t("label_proxy_url"), self._proxy_url)
        layout.addRow(t("label_pricing"), price_row)
        layout.addRow(t("label_context_turns"), self._context_turns)
        layout.addRow("", self._streaming)
        layout.addRow("", self._json_response)
        layout.addRow("", self._no_system_role)
        layout.addRow("", self._no_think)

        root.addWidget(basic_group)

        # --- Advanced section ---
        adv_group = QGroupBox(t("label_advanced_params"))
        adv_layout = QFormLayout(adv_group)
        adv_group.setToolTip(t("override_hint"))

        self._adv_temperature = QDoubleSpinBox()
        self._adv_temperature.setRange(0.0, 2.0)
        self._adv_temperature.setDecimals(2)
        self._adv_temperature.setSingleStep(0.1)
        self._adv_temperature.setValue(0.3)

        self._adv_top_p = QDoubleSpinBox()
        self._adv_top_p.setRange(0.0, 1.0)
        self._adv_top_p.setDecimals(2)
        self._adv_top_p.setSingleStep(0.05)
        self._adv_top_p.setValue(1.0)

        self._adv_max_tokens = QSpinBox()
        self._adv_max_tokens.setRange(1, 32768)
        self._adv_max_tokens.setValue(256)

        self._adv_freq_penalty = QDoubleSpinBox()
        self._adv_freq_penalty.setRange(-2.0, 2.0)
        self._adv_freq_penalty.setDecimals(2)
        self._adv_freq_penalty.setSingleStep(0.1)

        self._adv_presence_penalty = QDoubleSpinBox()
        self._adv_presence_penalty.setRange(-2.0, 2.0)
        self._adv_presence_penalty.setDecimals(2)
        self._adv_presence_penalty.setSingleStep(0.1)

        self._adv_seed = QSpinBox()
        self._adv_seed.setRange(0, 2_000_000_000)

        self._adv_rows = {
            "temperature": self._make_override_row(self._adv_temperature),
            "top_p": self._make_override_row(self._adv_top_p),
            "max_tokens": self._make_override_row(self._adv_max_tokens),
            "frequency_penalty": self._make_override_row(self._adv_freq_penalty),
            "presence_penalty": self._make_override_row(self._adv_presence_penalty),
            "seed": self._make_override_row(self._adv_seed),
        }
        adv_layout.addRow(t("label_temperature"), self._adv_rows["temperature"][1])
        adv_layout.addRow(t("label_top_p"), self._adv_rows["top_p"][1])
        adv_layout.addRow(t("label_max_tokens"), self._adv_rows["max_tokens"][1])
        adv_layout.addRow(
            t("label_frequency_penalty"), self._adv_rows["frequency_penalty"][1]
        )
        adv_layout.addRow(
            t("label_presence_penalty"), self._adv_rows["presence_penalty"][1]
        )
        adv_layout.addRow(t("label_seed"), self._adv_rows["seed"][1])

        self._adv_extra_body = QTextEdit()
        self._adv_extra_body.setPlaceholderText(
            '{"thinking_budget": 1024}'
        )
        self._adv_extra_body.setToolTip(t("extra_body_hint"))
        self._adv_extra_body.setFixedHeight(70)
        adv_layout.addRow(t("label_extra_body"), self._adv_extra_body)

        root.addWidget(adv_group)

        # --- Populate from model_data ---
        if model_data:
            self._name.setText(model_data.get("name", ""))
            self._api_base.setText(model_data.get("api_base", ""))
            self._api_key.setText(model_data.get("api_key", ""))
            self._model.setText(model_data.get("model", ""))
            proxy = model_data.get("proxy", "none")
            if proxy == "system":
                self._proxy_mode.setCurrentIndex(1)
            elif proxy not in ("none", "system") and proxy:
                self._proxy_mode.setCurrentIndex(2)
                self._proxy_url.setText(proxy)
            else:
                self._proxy_mode.setCurrentIndex(0)
            self._no_system_role.setChecked(model_data.get("no_system_role", False))
            self._no_think.setChecked(model_data.get("no_think", True))
            self._streaming.setChecked(model_data.get("streaming", True))
            self._json_response.setChecked(model_data.get("json_response", False))
            self._context_turns.setValue(model_data.get("context_turns", 0))
            self._input_price.setValue(model_data.get("input_price", 0))
            self._output_price.setValue(model_data.get("output_price", 0))

            overrides = model_data.get("overrides") or {}
            for key, (cb, _row, widget) in self._adv_rows.items():
                if key in overrides and overrides[key] is not None:
                    cb.setChecked(True)
                    if isinstance(widget, QSpinBox):
                        widget.setValue(int(overrides[key]))
                    else:
                        widget.setValue(float(overrides[key]))
            extra_body = model_data.get("extra_body")
            if extra_body:
                try:
                    self._adv_extra_body.setPlainText(
                        json.dumps(extra_body, ensure_ascii=False, indent=2)
                    )
                except (TypeError, ValueError):
                    pass

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _make_override_row(self, widget):
        """Build a [checkbox + widget] row that disables the widget when unchecked."""
        cb = QCheckBox(t("override_enable"))
        widget.setEnabled(False)
        cb.toggled.connect(widget.setEnabled)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(cb)
        h.addWidget(widget, 1)
        return cb, row, widget

    def _on_proxy_mode_changed(self, index):
        self._proxy_url.setEnabled(index == 2)

    def _parse_extra_body(self):
        """Return (ok, data_or_error_msg). Empty text → (True, None)."""
        text = self._adv_extra_body.toPlainText().strip()
        if not text:
            return True, None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return False, f"{e}"
        if not isinstance(data, dict):
            return False, "extra_body must be a JSON object"
        return True, data

    def _on_accept(self):
        ok, _ = self._parse_extra_body()
        if not ok:
            QMessageBox.warning(
                self, t("error_title"), t("extra_body_invalid")
            )
            return
        self.accept()

    def get_data(self) -> dict:
        proxy_idx = self._proxy_mode.currentIndex()
        if proxy_idx == 1:
            proxy = "system"
        elif proxy_idx == 2:
            proxy = self._proxy_url.text().strip() or "none"
        else:
            proxy = "none"
        result = {
            "name": self._name.text().strip(),
            "api_base": self._api_base.text().strip(),
            "api_key": self._api_key.text().strip(),
            "model": self._model.text().strip(),
            "proxy": proxy,
        }
        if self._no_system_role.isChecked():
            result["no_system_role"] = True
        if not self._no_think.isChecked():
            result["no_think"] = False
        if not self._streaming.isChecked():
            result["streaming"] = False
        if self._json_response.isChecked():
            result["json_response"] = True
        if self._context_turns.value() > 0:
            result["context_turns"] = self._context_turns.value()
        if self._input_price.value() > 0:
            result["input_price"] = self._input_price.value()
        if self._output_price.value() > 0:
            result["output_price"] = self._output_price.value()

        overrides = {}
        for key, (cb, _row, widget) in self._adv_rows.items():
            if cb.isChecked():
                val = widget.value()
                if isinstance(widget, QDoubleSpinBox):
                    val = round(val, 2)
                overrides[key] = val
        if overrides:
            result["overrides"] = overrides

        ok, data = self._parse_extra_body()
        if ok and data:
            result["extra_body"] = data
        return result
