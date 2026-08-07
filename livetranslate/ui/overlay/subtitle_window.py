"""
Subtitle window - clean text-only window for OBS capture.
Uses QPainterPath for outlined text rendering.

Usage:
  - Middle-click drag to move the window
  - Configure via tray menu → Subtitle Mode → Settings
  - OBS: Window Capture → select "LiveTranslate Subtitle" → check "Allow Transparency"
"""

import json
import time
from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
from livetranslate.ui.overlay.subtitle_text import _SubtitleTextWidget, _resolve_image_path
from livetranslate.ui.overlay.theming import _hex_to_rgba
from livetranslate.ui.overlay.win32 import set_click_through


DEFAULT_SUBTITLE_WIN_SETTINGS = {
    "enabled": False,
    "sentences": 1,
    "window_width": 1000,
    "line_spacing": 8,
    "bg_color": "#000000",
    "bg_opacity": 76,
    "bg_image": "",
    "border_radius": 8,
    "auto_hide_timeout": 5,
    "auto_hide_animation": "fade",
    "auto_hide_duration": 300,
    "click_through": False,
    "lines": [
        {
            "type": "original",
            "enabled": True,
            "font_family": "Microsoft YaHei",
            "font_size": 24,
            "color": "#FFFFFF",
            "opacity": 255,
            "outline_enabled": True,
            "outline_color": "#000000",
            "outline_width": 2,
            "align": "center",
            "bg_image": "",
            "entry_animation": "none",
            "exit_animation": "none",
            "animation_duration": 300,
        },
        {
            "type": "translation",
            "lang": "zh",
            "enabled": True,
            "font_family": "Microsoft YaHei",
            "font_size": 28,
            "color": "#FFD700",
            "opacity": 255,
            "outline_enabled": True,
            "outline_color": "#000000",
            "outline_width": 2,
            "align": "center",
            "bg_image": "",
            "entry_animation": "none",
            "exit_animation": "none",
            "animation_duration": 300,
        },
    ],
}


def _merge_settings(base, override):
    result = {**base}
    for k, v in (override or {}).items():
        if k == "lines" and isinstance(v, list):
            result["lines"] = v
        else:
            result[k] = v
    return result


class SubtitleWindow(QWidget):
    """Clean text-only subtitle window for OBS capture.

    Middle-click anywhere to drag. Window width is fixed (set in settings),
    height auto-fits to text content.
    """

    update_text_signal = pyqtSignal(str, str)  # original, translations_json
    position_changed = pyqtSignal()
    window_closed = pyqtSignal()

    def __init__(self, settings=None):
        super().__init__()
        self._settings = _merge_settings(DEFAULT_SUBTITLE_WIN_SETTINGS, settings)
        self._text_widgets = []
        self._sentences = []  # [(original, {lang: text, ...}), ...]
        self._drag_pos = None
        self._bg_pixmap = None
        self._click_through = bool(self._settings.get("click_through", False))
        # Qt clears the extended style on show/raise/flag changes, so re-assert it
        # on a timer rather than only once.
        self._ct_timer = QTimer(self)
        self._ct_timer.setInterval(500)
        self._ct_timer.timeout.connect(self._apply_click_through)
        # Auto-hide state
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._on_auto_hide_timeout)
        self._is_hidden_by_timeout = False
        # Pending overflow segments for delayed insertion
        self._pending_segment_timers = []
        # Minimum display time: queue rapid updates instead of replacing instantly
        self._last_insert_time = 0.0
        self._min_display_ms = 1500  # minimum ms before a sentence can be replaced
        self._height_anim = None

        self._setup_ui()
        self.update_text_signal.connect(self._on_update_text)

    @staticmethod
    def _is_pos_visible(x, y, margin=50):
        for screen in QApplication.screens():
            geo = screen.availableGeometry()
            if geo.left() <= x + margin and x < geo.right() and geo.top() <= y + margin and y < geo.bottom():
                return True
        return False

    def _clamp_to_screen(self):
        x, y = self.x(), self.y()
        if self._is_pos_visible(x, y):
            return
        screen = QApplication.screenAt(QPoint(x, y))
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        nx = max(geo.left(), min(x, geo.right() - self.width()))
        ny = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(nx, ny)

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("LiveTranslate Subtitle")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        s = self._settings
        w = s.get("window_width", 1000)
        saved_x = s.get("window_x")
        saved_y = s.get("window_y")
        if saved_x is not None and saved_y is not None:
            if self._is_pos_visible(saved_x, saved_y):
                self.move(saved_x, saved_y)
            else:
                self.move(100, 100)
        else:
            self.move(100, 100)
        self.setFixedWidth(w)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Content area
        self._content = QWidget()
        self._content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 8, 16, 8)
        self._content_layout.setSpacing(s.get("line_spacing", 8))

        self._rebuild_text_widgets()

        self._main_layout.addWidget(self._content)

        self._apply_background()
        self._fit_height_animated()
        self.set_click_through(self._click_through)

    def _rebuild_text_widgets(self):
        for w in self._text_widgets:
            self._content_layout.removeWidget(w)
            w.deleteLater()
        self._text_widgets = []

        for line_cfg in self._settings.get("lines", []):
            if not line_cfg.get("enabled", True):
                continue
            tw = _SubtitleTextWidget()
            tw.set_config(line_cfg)
            tw.height_changed.connect(self._fit_height_animated)
            self._text_widgets.append(tw)
            self._content_layout.addWidget(tw)

    def _apply_background(self):
        s = self._settings
        resolved = _resolve_image_path(s.get("bg_image", ""))
        if resolved:
            self._bg_pixmap = QPixmap(resolved)
            self._content.setStyleSheet("background: transparent;")
        else:
            self._bg_pixmap = None
            color = s.get("bg_color", "#000000")
            opacity = s.get("bg_opacity", 0)
            if opacity == 0:
                self._content.setStyleSheet("background: transparent;")
            else:
                rgba = _hex_to_rgba(color, opacity)
                radius = s.get("border_radius", 8)
                self._content.setStyleSheet(f"background: {rgba}; border-radius: {radius}px;")
        self.update()

    def _calc_target_height(self):
        margins = self._content_layout.contentsMargins()
        spacing = self._content_layout.spacing()
        total = margins.top() + margins.bottom()
        for i, tw in enumerate(self._text_widgets):
            total += tw.desired_height()
            if i > 0:
                total += spacing
        return max(total, 20)

    def _fit_height_snap(self):
        new_h = self._calc_target_height()
        old_h = self.height()
        if new_h == old_h:
            return
        if self._height_anim and self._height_anim.state() != QPropertyAnimation.State.Stopped:
            self._height_anim.stop()
        self.move(self.x(), self.y() - (new_h - old_h) // 2)
        self.setFixedHeight(new_h)
        self._clamp_to_screen()
        self.position_changed.emit()

    def _fit_height_animated(self):
        new_h = self._calc_target_height()
        old_h = self.height()
        if new_h == old_h:
            return
        if self._height_anim and self._height_anim.state() != QPropertyAnimation.State.Stopped:
            self._height_anim.stop()

        target_y = self.y() - (new_h - old_h) // 2
        self.setMinimumHeight(min(old_h, new_h))
        self.setMaximumHeight(max(old_h, new_h))

        anim = QPropertyAnimation(self, b"geometry")
        anim.setDuration(150)
        anim.setStartValue(QRect(self.x(), self.y(), self.width(), old_h))
        anim.setEndValue(QRect(self.x(), target_y, self.width(), new_h))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def on_finished():
            self.setFixedHeight(new_h)
            self._clamp_to_screen()
            self.position_changed.emit()
        anim.finished.connect(on_finished)

        self._height_anim = anim
        anim.start()

    def apply_settings(self, settings: dict):
        self._settings = _merge_settings(DEFAULT_SUBTITLE_WIN_SETTINGS, settings)

        for w in self._text_widgets:
            self._content_layout.removeWidget(w)
            w.deleteLater()
        self._text_widgets = []

        for line_cfg in self._settings.get("lines", []):
            if not line_cfg.get("enabled", True):
                continue
            tw = _SubtitleTextWidget()
            tw.set_config(line_cfg)
            tw.height_changed.connect(self._fit_height_animated)
            self._text_widgets.append(tw)
            self._content_layout.addWidget(tw)

        self._content_layout.setSpacing(self._settings.get("line_spacing", 8))

        w = self._settings.get("window_width", 1000)
        self.setFixedWidth(w)

        self._apply_background()
        self._refresh_display()
        self.set_click_through(self._settings.get("click_through", False))

        # Reset auto-hide timer with new settings
        self._restart_auto_hide_timer()

    # --- Auto-hide ---
    def _restart_auto_hide_timer(self):
        timeout = self._settings.get("auto_hide_timeout", 0)
        self._auto_hide_timer.stop()
        if timeout > 0 and self._sentences:
            self._auto_hide_timer.setInterval(timeout * 1000)
            self._auto_hide_timer.start()

    def _on_auto_hide_timeout(self):
        if self._is_hidden_by_timeout:
            return
        self._is_hidden_by_timeout = True
        anim_type = self._settings.get("auto_hide_animation", "fade")
        duration = self._settings.get("auto_hide_duration", 300)
        for tw in self._text_widgets:
            tw.animate_out(anim_type=anim_type, duration=duration)

    def _restore_from_auto_hide(self):
        if not self._is_hidden_by_timeout:
            return
        self._is_hidden_by_timeout = False
        anim_type = self._settings.get("auto_hide_animation", "fade")
        duration = self._settings.get("auto_hide_duration", 300)
        # Reverse the hide animation type for restore
        restore_type = anim_type
        if anim_type == "slide_down":
            restore_type = "slide_up"
        elif anim_type == "slide_up":
            restore_type = "slide_down"
        elif anim_type == "slide_left":
            restore_type = "slide_right"
        elif anim_type == "slide_right":
            restore_type = "slide_left"

        for tw in self._text_widgets:
            tw._stop_all_animations()
            # Set hidden state
            tw._content_opacity_val = 0.0
            if restore_type == "slide_left":
                tw._slide_offset_x_val = float(-(tw.width() or 200))
            elif restore_type == "slide_right":
                tw._slide_offset_x_val = float(tw.width() or 200)
            elif restore_type == "slide_up":
                tw._slide_offset_y_val = float(tw.height() or 40)
            elif restore_type == "slide_down":
                tw._slide_offset_y_val = float(-(tw.height() or 40))
            tw.update()
            # Use the entry animation mechanism with overridden type
            old_entry = tw._entry_animation
            old_dur = tw._animation_duration
            tw._entry_animation = restore_type if restore_type != "none" else "fade"
            tw._animation_duration = duration
            tw.animate_in()
            tw._entry_animation = old_entry
            tw._animation_duration = old_dur

    # --- Click-through ---
    def set_click_through(self, enabled: bool):
        self._click_through = bool(enabled)
        if self._click_through:
            if not self._ct_timer.isActive():
                self._ct_timer.start()
        else:
            self._ct_timer.stop()
        self._apply_click_through()

    def _apply_click_through(self):
        try:
            set_click_through(int(self.winId()), self._click_through)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # Qt resets the extended style across hide/show, so re-assert it here.
        self._apply_click_through()

    # --- Middle-click drag ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.MiddleButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._drag_pos:
                self._drag_pos = None
                self.position_changed.emit()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        self.window_closed.emit()
        super().closeEvent(event)

    def paintEvent(self, event):
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg_pixmap)
            painter.end()
        super().paintEvent(event)

    # --- Text updates ---
    def update_text(self, original: str, translations: dict | str):
        """Thread-safe text update.

        translations: dict mapping lang code to translated text,
                      or a plain string (backward compat, treated as primary target).
        """
        if isinstance(translations, str):
            # Backward compat: wrap in dict with empty key
            translations = {"": translations}
        self.update_text_signal.emit(original, json.dumps(translations, ensure_ascii=False))

    @pyqtSlot(str, str)
    def _on_update_text(self, original: str, translations_json: str):
        translations = json.loads(translations_json)
        self._cancel_pending_segments()

        # Respect minimum display time: delay if previous sentence was inserted recently
        now_ms = time.monotonic() * 1000
        elapsed = now_ms - self._last_insert_time
        base_delay = max(0, int(self._min_display_ms - elapsed)) if self._last_insert_time > 0 else 0

        if base_delay == 0:
            self._insert_sentence(original, translations)
        else:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(base_delay)
            timer.timeout.connect(lambda o=original, t=translations: self._insert_sentence(o, t))
            timer.start()
            self._pending_segment_timers.append(timer)

    def _insert_sentence(self, original: str, translations: dict):
        """Insert a single sentence and refresh display."""

        max_sentences = self._settings.get("sentences", 1)
        self._sentences.append((original, translations))
        if len(self._sentences) > max_sentences:
            self._sentences = self._sentences[-max_sentences:]

        if self._is_hidden_by_timeout:
            self._restore_from_auto_hide()

        self._refresh_display()
        self._restart_auto_hide_timer()
        self._last_insert_time = time.monotonic() * 1000

    def _cancel_pending_segments(self):
        """Cancel any pending delayed segment insertions."""
        for timer in self._pending_segment_timers:
            timer.stop()
            timer.deleteLater()
        self._pending_segment_timers.clear()


    def _refresh_display(self):
        if not self._sentences:
            for tw in self._text_widgets:
                tw.set_text("")
            self._fit_height_snap()
            return

        lines_cfg = [ln for ln in self._settings.get("lines", []) if ln.get("enabled", True)]
        wi = 0

        for cfg in lines_cfg:
            if wi >= len(self._text_widgets):
                break
            tw = self._text_widgets[wi]
            line_type = cfg.get("type", "original")

            if line_type == "original":
                texts = [s[0] for s in self._sentences if s[0]]
            else:
                lang = cfg.get("lang", "")
                texts = []
                for _, tl_dict in self._sentences:
                    if isinstance(tl_dict, str):
                        if tl_dict:
                            texts.append(tl_dict)
                    elif lang and lang in tl_dict:
                        texts.append(tl_dict[lang])
                    elif "" in tl_dict and tl_dict[""]:
                        texts.append(tl_dict[""])
                    else:
                        for v in tl_dict.values():
                            if v:
                                texts.append(v)
                                break
            tw.set_text(" | ".join(texts) if len(texts) > 1 else (texts[0] if texts else ""))
            wi += 1

    def get_target_languages(self) -> set:
        """Return set of unique target language codes from enabled translation lines."""
        langs = set()
        for cfg in self._settings.get("lines", []):
            if cfg.get("enabled", True) and cfg.get("type") == "translation":
                lang = cfg.get("lang", "")
                if lang:
                    langs.add(lang)
        return langs

    def clear(self):
        self._sentences.clear()
        self._cancel_pending_segments()
        self._auto_hide_timer.stop()
        self._is_hidden_by_timeout = False
        for tw in self._text_widgets:
            tw._stop_all_animations()
            tw._content_opacity_val = 1.0
            tw._slide_offset_x_val = 0.0
            tw._slide_offset_y_val = 0.0
            tw.set_text("")
        self._fit_height_snap()
