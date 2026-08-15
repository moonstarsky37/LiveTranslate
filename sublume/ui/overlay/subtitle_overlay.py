"""SubtitleOverlay: chat-style overlay window for live transcription."""

import sys

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)
from sublume.i18n import t
from sublume.ui.overlay.chat import ChatMessage
from sublume.ui.overlay.handle import DragHandle
from sublume.ui.overlay.monitor import MonitorBar
from sublume.ui.overlay.theming import DEFAULT_STYLE, _hex_to_rgba
from sublume.ui.overlay.click_through import set_click_through

# Re-exported for backward compatibility (style tab imports these here).
from sublume.ui.overlay.theming import STYLE_PRESETS  # noqa: F401


class SubtitleOverlay(QWidget):
    """Chat-style overlay window for displaying live transcription."""

    add_message_signal = pyqtSignal(int, str, str, str, float)
    update_translation_signal = pyqtSignal(int, str, float)
    update_streaming_signal = pyqtSignal(int, str)
    clear_signal = pyqtSignal()
    # Monitor signals (thread-safe)
    update_monitor_signal = pyqtSignal(float, float, object)
    update_stats_signal = pyqtSignal(int, int, int, int, float)
    update_asr_device_signal = pyqtSignal(str)
    update_asr_status_signal = pyqtSignal(str)

    settings_requested = pyqtSignal()
    target_language_changed = pyqtSignal(str)
    source_language_changed = pyqtSignal(str)
    model_switch_requested = pyqtSignal(int)
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    subtitle_toggled = pyqtSignal()
    mode_changed = pyqtSignal(str)  # "full" or "compact"
    position_changed = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self._config = config
        self._messages = {}
        self._max_messages = 50
        self._click_through = False
        self._height_before_compact = None
        self._mode_anim = None
        self._pos_save_timer = QTimer(self)
        self._pos_save_timer.setSingleShot(True)
        self._pos_save_timer.setInterval(500)
        self._pos_save_timer.timeout.connect(lambda: self.position_changed.emit())
        self._last_saved_geo = None
        self._setup_ui()

        self.add_message_signal.connect(self._on_add_message)
        self.update_translation_signal.connect(self._on_update_translation)
        self.update_streaming_signal.connect(self._on_update_streaming)
        self.clear_signal.connect(self._on_clear)
        self.update_monitor_signal.connect(self._on_update_monitor)
        self.update_stats_signal.connect(self._on_update_stats)
        self.update_asr_device_signal.connect(self._on_update_asr_device)
        self.update_asr_status_signal.connect(self._on_update_asr_status)

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowTitle("Sublume")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if sys.platform == "darwin":
            # Qt::Tool is an NSPanel on macOS, and AppKit hides those whenever
            # the owning application goes inactive. Click-through activates
            # whatever is underneath on every click, so without this opt-out the
            # overlay vanishes the first time the user clicks through it and
            # only comes back via the Dock icon.
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        width = 620
        height = 500
        x = geo.right() - width - 20
        y = geo.bottom() - height - 60
        self.setGeometry(x, y, width, height)
        self.setMinimumSize(480, 200)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._container = QWidget()
        self._container.setStyleSheet(
            "background-color: rgba(15, 15, 25, 200); border-radius: 8px;"
        )

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(4, 4, 4, 4)
        container_layout.setSpacing(0)

        # Drag handle
        self._handle = DragHandle()
        self._handle.settings_clicked.connect(self.settings_requested.emit)
        self._handle.subtitle_clicked.connect(self.subtitle_toggled.emit)
        self._handle.click_through_toggled.connect(self._set_click_through)
        self._handle.topmost_toggled.connect(self._set_topmost)
        self._handle.taskbar_toggled.connect(self._set_taskbar)
        self._handle.target_language_changed.connect(self.target_language_changed.emit)
        self._handle.source_language_changed.connect(self.source_language_changed.emit)
        self._handle.model_changed.connect(self.model_switch_requested.emit)
        self._handle.start_clicked.connect(self.start_requested.emit)
        self._handle.stop_clicked.connect(self.stop_requested.emit)
        self._handle.hide_clicked.connect(self.hide_requested.emit)
        self._handle.clear_clicked.connect(self._on_clear)
        self._handle.quit_clicked.connect(self.quit_requested.emit)
        self._handle.mode_changed.connect(self._on_mode_changed)
        self._handle.position_changed.connect(self.position_changed)
        container_layout.addWidget(self._handle)

        # Monitor bar (collapsible)
        self._monitor = MonitorBar()
        container_layout.addWidget(self._monitor)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                width: 6px; background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,60); border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet("background: transparent;")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(0, 0, 0, 0)
        self._msg_layout.setSpacing(2)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        container_layout.addWidget(self._scroll)

        grip_row = QHBoxLayout()
        grip_row.addStretch()
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.setStyleSheet("background: transparent;")
        grip_row.addWidget(self._grip)
        container_layout.addLayout(grip_row)

        main_layout.addWidget(self._container)

        self._ct_timer = QTimer(self)
        self._ct_timer.timeout.connect(self._check_click_through)
        self._ct_timer.start(50)

    def _schedule_pos_save(self):
        if not self.isVisible():
            return
        geo = (self.x(), self.y(), self.width(), self.height())
        if geo != self._last_saved_geo:
            self._last_saved_geo = geo
            self._pos_save_timer.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_pos_save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_pos_save()

    def set_running(self, running: bool):
        self._handle.set_running(running)

    def _set_topmost(self, enabled: bool):
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _set_taskbar(self, enabled: bool):
        flags = self.windowFlags()
        if enabled:
            flags &= ~Qt.WindowType.Tool
        else:
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.show()

    def _set_click_through(self, enabled: bool):
        self._click_through = enabled
        if not enabled:
            set_click_through(int(self.winId()), False)

    def _check_click_through(self):
        if not self._click_through:
            return
        cursor = QCursor.pos()
        local = self.mapFromGlobal(cursor)

        scroll_top = self._scroll.mapTo(self, QPoint(0, 0)).y()
        in_header = 0 <= local.x() <= self.width() and 0 <= local.y() < scroll_top

        # Header stays interactive; the rest of the window passes clicks through.
        set_click_through(int(self.winId()), not in_header)

    def _on_mode_changed(self, mode: str):
        compact = mode == "compact"
        self._monitor.setVisible(not compact)
        ChatMessage._compact_mode = compact
        s = ChatMessage._current_style
        for msg in self._messages.values():
            msg.apply_style(s)
        self.mode_changed.emit(mode)

        # Animate window height
        if self._mode_anim and self._mode_anim.state() != QPropertyAnimation.State.Stopped:
            self._mode_anim.stop()

        if compact:
            self._height_before_compact = self.height()
            target_h = self.minimumHeight()
        else:
            target_h = self._height_before_compact or 500

        actual_h = self.frameGeometry().height()
        if abs(actual_h - target_h) < 10:
            self.resize(self.width(), target_h)
        else:
            anim = QPropertyAnimation(self, b"size")
            anim.setDuration(200)
            anim.setStartValue(QSize(self.width(), actual_h))
            anim.setEndValue(QSize(self.width(), target_h))
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._mode_anim = anim
            anim.start()

    def set_mode(self, mode: str):
        self._handle.set_mode(mode)

    def set_template(self, template: str):
        """Switch the header layout (classic / compact / minimal)."""
        self._handle.set_template(template)

    def set_subtitle_checked(self, checked: bool):
        self._handle.set_subtitle_checked(checked)

    @pyqtSlot(float, float, object)
    def _on_update_monitor(self, rms: float, vad_conf: float, mic_rms):
        self._monitor.update_audio(rms, vad_conf, mic_rms)

    @pyqtSlot(int, int, int, int, float)
    def _on_update_stats(self, asr_count, tl_count, prompt_tokens, completion_tokens, cost):
        self._monitor.update_pipeline_stats(
            asr_count, tl_count, prompt_tokens, completion_tokens, cost
        )

    @pyqtSlot(str)
    def _on_update_asr_device(self, device: str):
        self._monitor.update_asr_device(device)

    @pyqtSlot(str)
    def _on_update_asr_status(self, status: str):
        self._monitor.update_asr_status(status)

    @pyqtSlot(int, str, str, str, float)
    def _on_add_message(self, msg_id, timestamp, original, source_lang, asr_ms):
        msg = ChatMessage(msg_id, timestamp, original, source_lang, asr_ms)
        self._messages[msg_id] = msg
        self._msg_layout.addWidget(msg)

        if len(self._messages) > self._max_messages:
            oldest_id = min(self._messages.keys())
            old_msg = self._messages.pop(oldest_id)
            self._msg_layout.removeWidget(old_msg)
            old_msg.deleteLater()

        QTimer.singleShot(50, self._scroll_to_bottom)

    @pyqtSlot(int, str, float)
    def _on_update_translation(self, msg_id, translated, translate_ms):
        msg = self._messages.get(msg_id)
        if msg:
            msg.set_translation(translated, translate_ms)
            QTimer.singleShot(50, self._scroll_to_bottom)

    def _on_update_streaming(self, msg_id, partial_text):
        msg = self._messages.get(msg_id)
        if msg:
            msg.update_streaming(partial_text)

    @pyqtSlot()
    def _on_clear(self):
        for msg in self._messages.values():
            self._msg_layout.removeWidget(msg)
            msg.deleteLater()
        self._messages.clear()

    def _scroll_to_bottom(self):
        if not self._handle.auto_scroll:
            return
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def apply_style(self, style: dict):
        s = {**DEFAULT_STYLE, **style}
        # Migrate old single font_family to split fields
        if "font_family" in s and "original_font_family" not in style:
            s["original_font_family"] = s["font_family"]
            s["translation_font_family"] = s["font_family"]
        # Container background
        bg_rgba = _hex_to_rgba(s["bg_color"], s["bg_opacity"])
        self._container.setStyleSheet(
            f"background-color: {bg_rgba}; border-radius: {s['border_radius']}px;"
        )
        # Header background
        hdr_rgba = _hex_to_rgba(s["header_color"], s["header_opacity"])
        self._handle.setStyleSheet(f"background: {hdr_rgba}; border-radius: 4px;")
        # Window opacity
        self.setWindowOpacity(s["window_opacity"] / 100.0)
        # Update all existing messages
        ChatMessage._current_style = s
        for msg in self._messages.values():
            msg.apply_style(s)

    def export_messages(self, mode: str, parent=None):
        """Export captured messages to a .txt file.

        mode: "original" | "translation" | "both"
        """
        if not self._messages:
            QMessageBox.information(parent or self, "Sublume", t("export_empty"))
            return

        from datetime import datetime
        suffix = {"original": "original", "translation": "translation", "both": "all"}.get(mode, "all")
        default_name = f"sublume_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suffix}.txt"
        path, _ = QFileDialog.getSaveFileName(
            parent or self,
            t("export_dialog_title"),
            default_name,
            t("export_filter"),
        )
        if not path:
            return

        lines = []
        for msg_id in sorted(self._messages.keys()):
            msg = self._messages[msg_id]
            ts = msg._timestamp
            orig = msg._original or ""
            trans = msg._translated or ""
            if mode == "original":
                lines.append(f"[{ts}] {orig}")
            elif mode == "translation":
                if trans:
                    lines.append(f"[{ts}] {trans}")
            else:
                lines.append(f"[{ts}] {orig}")
                if trans:
                    lines.append(f"  -> {trans}")
                lines.append("")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).rstrip() + "\n")
        except OSError as e:
            QMessageBox.critical(
                parent or self,
                "Sublume",
                t("export_failed").format(error=str(e)),
            )

    # Thread-safe public API
    def add_message(self, msg_id, timestamp, original, source_lang, asr_ms):
        self.add_message_signal.emit(msg_id, timestamp, original, source_lang, asr_ms)

    def update_translation(self, msg_id, translated, translate_ms):
        self.update_translation_signal.emit(msg_id, translated, translate_ms)

    def update_streaming(self, msg_id, partial_text):
        self.update_streaming_signal.emit(msg_id, partial_text)

    def update_monitor(self, rms, vad_conf, mic_rms=None):
        self.update_monitor_signal.emit(rms, vad_conf, mic_rms)

    def update_stats(self, asr_count, tl_count, prompt_tokens, completion_tokens, cost=0.0):
        self.update_stats_signal.emit(
            asr_count, tl_count, prompt_tokens, completion_tokens, cost
        )

    def update_asr_device(self, device: str):
        self.update_asr_device_signal.emit(device)

    def update_asr_status(self, status: str):
        """status: "" (ready) | "loading" | "unavailable". Thread-safe."""
        self.update_asr_status_signal.emit(status)

    def set_target_language(self, lang: str):
        self._handle.set_target_language(lang)

    def set_source_language(self, lang: str):
        self._handle.set_source_language(lang)

    def set_models(self, models: list, active_index: int = 0):
        self._handle.set_models(models, active_index)

    def clear(self):
        self.clear_signal.emit()
