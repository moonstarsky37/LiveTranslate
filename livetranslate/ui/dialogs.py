import json
import logging
import re
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
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
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from livetranslate.model_manager import download_asr, download_silero
from livetranslate.i18n import t

log = logging.getLogger("LiveTranslate.Dialogs")


def _short_error(exc: BaseException) -> str:
    """First line of an exception message, capped — the download dialogs show
    this to the user, so the multi-line CDN/CAS error dumps stay in the log."""
    text = str(exc).strip() or type(exc).__name__
    return text.splitlines()[0][:200]


class _LogCapture(logging.Handler):
    """Captures log output and emits via callback."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self._callback(self.format(record))
        except Exception:
            pass


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class _StderrCapture:
    """Captures stderr (tqdm) and forwards cleaned lines via callback."""

    def __init__(self, callback, original):
        self._cb = callback
        self._orig = original

    def write(self, text):
        if self._orig:
            self._orig.write(text)
        if not text:
            return
        cleaned = _ANSI_RE.sub("", text)
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                self._cb(line)

    def flush(self):
        if self._orig:
            self._orig.flush()

    def isatty(self):
        return False


class _ModelLoadDialog(QDialog):
    """Modal dialog shown during model download/loading with live log."""

    _log_signal = pyqtSignal(str)

    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LiveTranslate")
        self.setMinimumWidth(500)
        self.setMinimumHeight(300)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        layout = QVBoxLayout(self)
        self._label = QLabel(message)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 8))
        self._log_view.setStyleSheet(
            "background: #1e1e2e; color: #cdd6f4; border: 1px solid #444;"
        )
        layout.addWidget(self._log_view)

        self._log_signal.connect(self._append_log)
        self._log_handler = _LogCapture(self._log_signal.emit)
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)

    def _append_log(self, text):
        self._log_view.append(text)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def done(self, result):
        logging.getLogger().removeHandler(self._log_handler)
        super().done(result)


class SetupWizardDialog(QDialog):
    """First-launch wizard: download required models (HuggingFace only;
    download starts only when the user explicitly clicks the button)."""

    _log_signal = pyqtSignal(str)

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        # Constructor-injected settings store (Phase 2); defaults to the
        # standard repo-root store when the caller does not pass one.
        from livetranslate.config.store import SettingsStore

        self._store = store or SettingsStore()
        self.setWindowTitle(t("window_setup"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(400)
        # WindowCloseButtonHint: closing is always safe — settings are persisted
        # the moment the download button is clicked, so a later launch resumes
        # via the missing-model dialog instead of re-running the wizard.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        layout = QVBoxLayout(self)

        proxy_group = QGroupBox(t("group_download_proxy"))
        proxy_form = QFormLayout(proxy_group)
        self._proxy_mode = QComboBox()
        self._proxy_mode.addItems(
            [t("proxy_none"), t("proxy_system"), t("proxy_custom")]
        )
        # Default to following the system proxy for model downloads
        self._proxy_mode.setCurrentIndex(1)
        self._proxy_mode.currentIndexChanged.connect(self._on_proxy_mode_changed)
        self._proxy_url = QLineEdit()
        self._proxy_url.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_url.setEnabled(False)
        proxy_form.addRow(t("label_proxy"), self._proxy_mode)
        proxy_form.addRow(t("label_proxy_url"), self._proxy_url)
        layout.addWidget(proxy_group)

        # Engine choice belongs here and nowhere else: this is the one moment
        # where the difference is a cost the user is about to pay (what gets
        # downloaded, and whether a graphics card is needed). The labels state
        # size and hardware; the timings sit in the tooltips so the screen stays
        # one glance rather than a quiz.
        engine_group = QGroupBox(t("group_setup_engine"))
        engine_layout = QVBoxLayout(engine_group)
        self._engine_onnx = QRadioButton(t("setup_engine_onnx"))
        self._engine_onnx.setToolTip(t("setup_engine_onnx_tip"))
        self._engine_onnx.setChecked(True)  # works on every machine
        self._engine_torch = QRadioButton(t("setup_engine_torch"))
        self._engine_torch.setToolTip(t("setup_engine_torch_tip"))
        engine_layout.addWidget(self._engine_onnx)
        engine_layout.addWidget(self._engine_torch)
        engine_hint = QLabel(t("setup_engine_hint"))
        engine_hint.setStyleSheet("color: #888; font-size: 11px;")
        engine_hint.setWordWrap(True)
        engine_layout.addWidget(engine_hint)
        layout.addWidget(engine_group)

        # Collect the leftover height here instead of letting the layout inflate
        # the group boxes: without it each box stretches to fill the dialog's
        # minimum height and shows a large void under two rows of content.
        layout.addStretch()

        self._download_btn = QPushButton(t("btn_start_download"))
        self._download_btn.clicked.connect(self._start_download)
        layout.addWidget(self._download_btn)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 8))
        self._log_view.setStyleSheet(
            "background: #1e1e2e; color: #cdd6f4; border: 1px solid #444;"
        )
        self._log_view.hide()
        # Stretch 3 against the spacer's 1: while the log is hidden the spacer
        # holds the leftover height, and once the download starts the log takes
        # most of it back instead of staying at its size hint.
        layout.addWidget(self._log_view, 3)

        self._error = None
        self._log_signal.connect(self._append_log)
        self._log_handler = _LogCapture(self._log_signal.emit)

    def _on_proxy_mode_changed(self, index):
        self._proxy_url.setEnabled(index == 2)

    def _download_proxy(self) -> str:
        index = self._proxy_mode.currentIndex()
        if index == 1:
            return "system"
        if index == 2:
            return self._proxy_url.text().strip() or "system"
        return "none"

    def _append_log(self, text):
        self._log_view.append(text)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def selected_engine(self) -> str:
        return "sensevoice-onnx" if self._engine_onnx.isChecked() else "funasr"

    def _start_download(self):
        self._download_btn.setEnabled(False)
        self._proxy_mode.setEnabled(False)
        self._proxy_url.setEnabled(False)
        self._engine_onnx.setEnabled(False)
        self._engine_torch.setEnabled(False)
        self._log_view.show()

        self._proxy = self._download_proxy()
        self._engine = self.selected_engine()

        # Persist settings the moment the user clicks Download (spec D4):
        # if the download is interrupted or the app is closed, the next launch
        # goes through the missing-model dialog instead of looping back here.
        # Default values come from the schema — single source of truth.
        from livetranslate.config.schema import Settings

        settings = Settings().wizard_defaults()
        settings["download_proxy"] = self._proxy
        settings["asr_engine"] = self._engine
        self._store.save(settings)

        logging.getLogger().addHandler(self._log_handler)
        self._orig_stderr = sys.stderr
        sys.stderr = _StderrCapture(self._log_signal.emit, self._orig_stderr)

        self._error = None
        self._download_thread = threading.Thread(
            target=self._download_worker, args=(self._proxy, self._engine), daemon=True
        )
        self._download_thread.start()

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._check_done)
        self._poll_timer.start()

    def _download_worker(self, proxy, engine):
        try:
            download_silero(proxy=proxy)
            if engine == "sensevoice-onnx":
                download_asr("sensevoice-onnx", hub="hf", proxy=proxy)
            else:
                download_asr(
                    "funasr", model_size="sensevoice-small", hub="hf", proxy=proxy
                )
        except Exception as e:
            self._error = _short_error(e)
            # One friendly line for the visible dialog log; the full traceback
            # goes to the DEBUG file log only.
            log.error(f"Download failed: {self._error}")
            log.debug("Download failure detail", exc_info=True)

    def _check_done(self):
        if self._download_thread.is_alive():
            return
        self._poll_timer.stop()
        sys.stderr = self._orig_stderr
        logging.getLogger().removeHandler(self._log_handler)

        if self._error:
            self._append_log(f"\n{t('download_failed').format(error=self._error)}")
            self._append_log(t("download_failed_hint"))
            self._download_btn.setEnabled(True)
            self._download_btn.setText(t("btn_retry"))
            self._proxy_mode.setEnabled(True)
            self._proxy_url.setEnabled(self._proxy_mode.currentIndex() == 2)
            return

        self._append_log(f"\n{t('download_complete')}")
        QTimer.singleShot(500, self.accept)


class ModelDownloadDialog(QDialog):
    """Download missing models (non-first-launch) with live log."""

    _log_signal = pyqtSignal(str)

    def __init__(self, missing_models, hub="hf", proxy="system", parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("window_download"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(300)
        # WindowCloseButtonHint: closing rejects the dialog — startup then exits
        # cleanly and a runtime engine switch keeps the current worker running.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        layout = QVBoxLayout(self)

        names = ", ".join(m["name"] for m in missing_models)
        info = QLabel(t("downloading_models").format(names=names))
        info.setWordWrap(True)
        layout.addWidget(info)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 8))
        self._log_view.setStyleSheet(
            "background: #1e1e2e; color: #cdd6f4; border: 1px solid #444;"
        )
        layout.addWidget(self._log_view)

        self._close_btn = QPushButton(t("btn_close"))
        self._close_btn.clicked.connect(self.reject)
        self._close_btn.hide()
        layout.addWidget(self._close_btn)

        self._missing = missing_models
        self._hub = hub
        self._proxy = proxy
        self._error = None

        self._log_signal.connect(self._append_log)
        self._log_handler = _LogCapture(self._log_signal.emit)

        QTimer.singleShot(100, self._start_download)

    def _append_log(self, text):
        self._log_view.append(text)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _start_download(self):
        logging.getLogger().addHandler(self._log_handler)
        self._orig_stderr = sys.stderr
        sys.stderr = _StderrCapture(self._log_signal.emit, self._orig_stderr)

        self._download_thread = threading.Thread(
            target=self._download_worker, daemon=True
        )
        self._download_thread.start()

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._check_done)
        self._poll_timer.start()

    def _download_worker(self):
        try:
            for m in self._missing:
                if m["type"] == "silero-vad":
                    download_silero(proxy=self._proxy)
                elif m["type"] in (
                    "sensevoice",
                    "funasr-nano",
                    "funasr-mlt-nano",
                    "anime-whisper",
                ):
                    download_asr(m["type"], hub=self._hub, proxy=self._proxy)
                elif m["type"].startswith("funasr:"):
                    model_key = m["type"].split(":", 1)[1]
                    download_asr(
                        "funasr",
                        model_size=model_key,
                        hub=self._hub,
                        proxy=self._proxy,
                    )
                elif m["type"].startswith("whisper-"):
                    size = m["type"].replace("whisper-", "")
                    download_asr(
                        "whisper", model_size=size, hub=self._hub, proxy=self._proxy
                    )
        except Exception as e:
            self._error = _short_error(e)
            # One friendly line for the visible dialog log; the full traceback
            # goes to the DEBUG file log only.
            log.error(f"Download failed: {self._error}")
            log.debug("Download failure detail", exc_info=True)

    def _check_done(self):
        if self._download_thread.is_alive():
            return
        self._poll_timer.stop()
        sys.stderr = self._orig_stderr
        logging.getLogger().removeHandler(self._log_handler)

        if self._error:
            self._append_log(f"\n{t('download_failed').format(error=self._error)}")
            self._append_log(t("download_failed_hint"))
            self._close_btn.show()
            return

        self._append_log(f"\n{t('download_complete')}")
        QTimer.singleShot(500, self.accept)


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


from livetranslate.i18n import I18N_DIR as _I18N_DIR


def _changelog_to_html(text: str) -> str:
    """Convert CHANGELOG.md subset to HTML (headings, bold, lists)."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            continue  # skip file title
        elif stripped.startswith("- "):
            item = stripped[2:]
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"`(.+?)`", r"<code>\1</code>", item)
            lines.append(f"<li>{item}</li>")
        elif stripped:
            lines.append(f"<p>{stripped}</p>")
    return "\n".join(lines)


def _load_latest_changelog() -> tuple[str, str]:
    """Return (first_h2_title, html) for the latest changelog. Uses i18n lang."""
    from livetranslate.i18n import get_lang
    lang = get_lang()
    path = _I18N_DIR / f"CHANGELOG_{lang}.md"
    if not path.exists():
        path = _I18N_DIR / "CHANGELOG_en.md"
    if not path.exists():
        return "", ""
    text = path.read_text("utf-8")
    # First H2 (## date) is the latest entry and serves as the tracking key
    m = re.search(r"^## (.+)$", text, re.MULTILINE)
    if not m:
        return "", ""
    title = m.group(1).strip()
    # Drop the top-level file heading (# Title) — keep everything from first H2 onwards
    body = text[m.start():]
    return title, _changelog_to_html(body)


