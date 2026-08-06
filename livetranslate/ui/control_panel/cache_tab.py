"""Cache tab: transcript auto-save, model cache listing and deletion."""

import logging
import os
import threading

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from livetranslate.model_manager import (
    MODELS_DIR,
    dir_size,
    format_size,
    get_cache_entries,
)
from livetranslate.i18n import t
from livetranslate.paths import ROOT

log = logging.getLogger("LiveTranslate.Panel")


class CacheTabMixin:
    """Cache tab methods, mixed into ControlPanel."""

    def _create_cache_tab(self):
        from PyQt6.QtWidgets import QCheckBox

        widget = QWidget()
        layout = QVBoxLayout(widget)
        s = self._current_settings

        # Transcript auto-save group
        ts_group = QGroupBox(t("group_transcript"))
        ts_layout = QHBoxLayout(ts_group)
        self._auto_save_transcript_cb = QCheckBox(t("label_auto_save_transcript"))
        self._auto_save_transcript_cb.setToolTip(t("auto_save_transcript_tooltip"))
        self._auto_save_transcript_cb.setChecked(s.get("auto_save_transcript", True))
        self._auto_save_transcript_cb.toggled.connect(self._auto_save)
        ts_layout.addWidget(self._auto_save_transcript_cb, 1)
        ts_open_btn = QPushButton(t("btn_open_transcripts"))
        ts_open_btn.clicked.connect(self._open_transcripts_folder)
        ts_layout.addWidget(ts_open_btn)
        layout.addWidget(ts_group)

        top_row = QHBoxLayout()
        self._cache_total = QLabel("")
        self._cache_total.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        top_row.addWidget(self._cache_total, 1)
        open_btn = QPushButton(t("btn_open_folder"))
        open_btn.clicked.connect(
            lambda: (
                MODELS_DIR.mkdir(parents=True, exist_ok=True),
                os.startfile(str(MODELS_DIR)),
            )
        )
        top_row.addWidget(open_btn)
        delete_all_btn = QPushButton(t("btn_delete_all_exit"))
        delete_all_btn.clicked.connect(self._delete_all_and_exit)
        top_row.addWidget(delete_all_btn)
        layout.addLayout(top_row)

        self._cache_list = QListWidget()
        self._cache_list.setFont(QFont("Consolas", 9))
        self._cache_list.setAlternatingRowColors(True)
        layout.addWidget(self._cache_list, 1)

        self._cache_entries = []
        self._refresh_cache()

        return widget

    def _open_transcripts_folder(self):
        ts_dir = ROOT / "transcripts"
        ts_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(ts_dir))

    def _refresh_cache(self):
        self._cache_list.clear()
        self._cache_total.setText(t("scanning"))

        def _scan():
            entries = get_cache_entries()
            results = []
            for name, path in entries:
                size = dir_size(path)
                results.append((name, str(path), size))
            self._cache_result.emit(results)

        threading.Thread(target=_scan, daemon=True).start()

    def _on_cache_result(self, results):
        self._cache_list.clear()
        self._cache_entries = results
        total = 0
        for name, path, size in results:
            total += size
            self._cache_list.addItem(f"{name}  —  {format_size(size)}")
        if not results:
            self._cache_list.addItem(t("no_cached_models"))
        self._cache_total.setText(
            t("cache_total").format(size=format_size(total), count=len(results))
        )

    def _delete_all_and_exit(self):
        if not self._cache_entries:
            return
        import shutil

        total_size = sum(s for _, _, s in self._cache_entries)
        ret = QMessageBox.warning(
            self,
            t("dialog_delete_title"),
            t("dialog_delete_msg").format(
                count=len(self._cache_entries), size=format_size(total_size)
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        for name, path, _ in self._cache_entries:
            try:
                shutil.rmtree(path)
                log.info(f"Deleted: {path}")
            except Exception as e:
                log.error(f"Failed to delete {path}: {e}")
        QApplication.instance().quit()
