"""First-launch setup wizard: proxy mode, ASR engine choice, and the
explicit-click model download."""

import logging
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)

from livetranslate.model_manager import download_asr, download_silero
from livetranslate.i18n import t
from livetranslate.ui.dialogs._common import (
    _LogCapture,
    _short_error,
    _StderrCapture,
    log,
)


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
