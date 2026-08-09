"""Non-first-launch download dialog for whichever models are missing."""

import logging
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from sublume.model_manager import download_asr, download_silero
from sublume.i18n import t
from sublume.ui.dialogs._common import (
    _LogCapture,
    _short_error,
    _StderrCapture,
    log,
)


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
