"""_SubtitleTextWidget: QPainterPath outlined-text renderer for the subtitle window."""

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget
from pathlib import Path


def _resolve_image_path(path: str) -> str:
    """Resolve image path (relative to project dir or absolute)."""
    if not path:
        return ""
    p = Path(path)
    if p.is_absolute():
        return str(p) if p.exists() else ""
    from sublume.paths import ROOT

    resolved = ROOT / p
    return str(resolved) if resolved.exists() else ""


class _SubtitleTextWidget(QWidget):
    """Renders outlined text using QPainterPath, with automatic word-wrap.
    Supports entry/exit animations via custom properties.
    """

    height_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._wrapped_lines = []
        self._font = QFont("Microsoft YaHei", 24)
        self._color = QColor(255, 255, 255)
        self._outline_enabled = True
        self._outline_color = QColor(0, 0, 0)
        self._outline_width = 2
        self._align = "center"
        self._bg_pixmap = None
        self._text_cache = None
        self._last_width = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Animation state
        self._content_opacity_val = 1.0
        self._slide_offset_x_val = 0.0
        self._slide_offset_y_val = 0.0
        self._entry_animation = "none"
        self._exit_animation = "none"
        self._animation_duration = 300
        self._anim_group = None
        self._pending_text = None

    # --- pyqtProperty for content opacity ---
    def _get_content_opacity(self):
        return self._content_opacity_val

    def _set_content_opacity(self, val):
        self._content_opacity_val = val
        self.update()

    content_opacity = pyqtProperty(float, _get_content_opacity, _set_content_opacity)

    # --- pyqtProperty for slide offsets ---
    def _get_slide_offset_x(self):
        return self._slide_offset_x_val

    def _set_slide_offset_x(self, val):
        self._slide_offset_x_val = val
        self.update()

    slide_offset_x = pyqtProperty(float, _get_slide_offset_x, _set_slide_offset_x)

    def _get_slide_offset_y(self):
        return self._slide_offset_y_val

    def _set_slide_offset_y(self, val):
        self._slide_offset_y_val = val
        self.update()

    slide_offset_y = pyqtProperty(float, _get_slide_offset_y, _set_slide_offset_y)

    def set_config(self, cfg: dict):
        self._font = QFont(cfg.get("font_family", "Microsoft YaHei"), cfg.get("font_size", 24))
        c = QColor(cfg.get("color", "#FFFFFF"))
        c.setAlpha(cfg.get("opacity", 255))
        self._color = c
        self._outline_enabled = cfg.get("outline_enabled", True)
        self._outline_color = QColor(cfg.get("outline_color", "#000000"))
        self._outline_width = cfg.get("outline_width", 2)
        self._align = cfg.get("align", "center")
        resolved = _resolve_image_path(cfg.get("bg_image", ""))
        self._bg_pixmap = QPixmap(resolved) if resolved else None
        self._entry_animation = cfg.get("entry_animation", "none")
        self._exit_animation = cfg.get("exit_animation", "none")
        self._animation_duration = cfg.get("animation_duration", 300)
        self._text_cache = None
        self._update_height()
        self.update()

    def set_text(self, text: str):
        if self._text and text != self._text and self._exit_animation != "none":
            self._pending_text = text
            self._stop_all_animations()
            self.animate_out(callback=self._apply_pending_text)
            return

        self._apply_text_immediate(text)

    def _apply_pending_text(self):
        text = getattr(self, "_pending_text", "")
        self._pending_text = None
        self._apply_text_immediate(text)

    def _apply_text_immediate(self, text: str):
        # Stop any running animations and reset to final state
        self._stop_all_animations()
        self._content_opacity_val = 1.0
        self._slide_offset_x_val = 0.0
        self._slide_offset_y_val = 0.0
        self._pending_text = None

        self._text = text
        self._text_cache = None
        self._update_height()
        self.update()
        self.height_changed.emit()

        if text:
            self.animate_in()

    def _stop_all_animations(self):
        if self._anim_group and self._anim_group.state() != self._anim_group.State.Stopped:
            self._anim_group.stop()
        self._anim_group = None

    def animate_in(self):
        anim_type = self._entry_animation
        if anim_type == "none":
            self._content_opacity_val = 1.0
            self._slide_offset_x_val = 0.0
            self._slide_offset_y_val = 0.0
            self.update()
            return

        dur = self._animation_duration
        group = QParallelAnimationGroup(self)

        # Opacity animation (all types fade in)
        opacity_anim = QPropertyAnimation(self, b"content_opacity", self)
        opacity_anim.setDuration(dur)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity_anim)

        w = self.width() or 200
        h = self.height() or 40

        if anim_type == "slide_left":
            slide = QPropertyAnimation(self, b"slide_offset_x", self)
            slide.setDuration(dur)
            slide.setStartValue(float(-w))
            slide.setEndValue(0.0)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_right":
            slide = QPropertyAnimation(self, b"slide_offset_x", self)
            slide.setDuration(dur)
            slide.setStartValue(float(w))
            slide.setEndValue(0.0)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_up":
            slide = QPropertyAnimation(self, b"slide_offset_y", self)
            slide.setDuration(dur)
            slide.setStartValue(float(h))
            slide.setEndValue(0.0)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_down":
            slide = QPropertyAnimation(self, b"slide_offset_y", self)
            slide.setDuration(dur)
            slide.setStartValue(float(-h))
            slide.setEndValue(0.0)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(slide)

        self._content_opacity_val = 0.0
        self.update()
        self._anim_group = group
        group.start()

    def animate_out(self, callback=None, anim_type=None, duration=None):
        if anim_type is None:
            anim_type = self._exit_animation
        if duration is None:
            duration = self._animation_duration
        if anim_type == "none":
            self._content_opacity_val = 0.0
            self.update()
            if callback:
                callback()
            return

        self._stop_all_animations()

        group = QParallelAnimationGroup(self)

        opacity_anim = QPropertyAnimation(self, b"content_opacity", self)
        opacity_anim.setDuration(duration)
        opacity_anim.setStartValue(self._content_opacity_val)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        group.addAnimation(opacity_anim)

        w = self.width() or 200
        h = self.height() or 40

        if anim_type == "slide_left":
            slide = QPropertyAnimation(self, b"slide_offset_x", self)
            slide.setDuration(duration)
            slide.setStartValue(0.0)
            slide.setEndValue(float(-w))
            slide.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_right":
            slide = QPropertyAnimation(self, b"slide_offset_x", self)
            slide.setDuration(duration)
            slide.setStartValue(0.0)
            slide.setEndValue(float(w))
            slide.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_up":
            slide = QPropertyAnimation(self, b"slide_offset_y", self)
            slide.setDuration(duration)
            slide.setStartValue(0.0)
            slide.setEndValue(float(-h))
            slide.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(slide)
        elif anim_type == "slide_down":
            slide = QPropertyAnimation(self, b"slide_offset_y", self)
            slide.setDuration(duration)
            slide.setStartValue(0.0)
            slide.setEndValue(float(h))
            slide.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(slide)

        if callback:
            group.finished.connect(callback)

        self._anim_group = group
        group.start()

    def split_text(self, text: str) -> list:
        """Split text into segments that fit within available width."""
        fm = QFontMetrics(self._font)
        ow = self._outline_width if self._outline_enabled else 0
        avail_w = self.width() - ow * 2
        if avail_w <= 0 or fm.horizontalAdvance(text) <= avail_w:
            return [text]

        segments = []
        while text:
            if fm.horizontalAdvance(text) <= avail_w:
                segments.append(text)
                break

            best = 0
            for i in range(1, len(text) + 1):
                if fm.horizontalAdvance(text[:i]) > avail_w:
                    break
                best = i
            if best == 0:
                best = 1

            # Prefer breaking at word/punctuation boundary
            break_at = best
            for j in range(best - 1, max(best // 2, 0), -1):
                if text[j] in ' ,，。、!！?？;；:：.':
                    break_at = j + 1
                    break

            segments.append(text[:break_at].rstrip())
            text = text[break_at:].lstrip()

        return segments or [text]

    def _rewrap(self):
        """Recalculate wrapped lines from current text."""
        if not self._text:
            self._wrapped_lines = []
        else:
            self._wrapped_lines = self.split_text(self._text)

    def desired_height(self) -> int:
        fm = QFontMetrics(self._font)
        ow = self._outline_width if self._outline_enabled else 0
        n = max(len(self._wrapped_lines), 1)
        return fm.lineSpacing() * n + ow * 2 + 4

    def _update_height(self):
        self._rewrap()
        self.setFixedHeight(self.desired_height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if w != self._last_width:
            self._last_width = w
            self._rewrap()
            self._text_cache = None
            self.setFixedHeight(self.desired_height())

    def _render_text_pixmap(self):
        lines = self._wrapped_lines or [self._text]
        w = self.width()
        h = self.desired_height()
        if w <= 0 or h <= 0:
            self._text_cache = None
            return

        dpr = self.devicePixelRatioF()
        pw, ph = int(w * dpr), int(h * dpr)
        if pw <= 0 or ph <= 0:
            self._text_cache = None
            return

        pix = QPixmap(pw, ph)
        pix.setDevicePixelRatio(dpr)
        pix.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pix)
        if not painter.isActive():
            self._text_cache = None
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fm = QFontMetrics(self._font)
        ow = self._outline_width if self._outline_enabled else 0
        y = ow + fm.ascent()

        path = QPainterPath()
        for line in lines:
            text_w = fm.horizontalAdvance(line)
            if self._align == "center":
                lx = (w - text_w) / 2
            elif self._align == "right":
                lx = w - text_w - ow
            else:
                lx = ow
            path.addText(lx, y, self._font, line)
            y += fm.lineSpacing()

        if self._outline_enabled and self._outline_width > 0:
            pen = QPen(self._outline_color, self._outline_width * 2,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawPath(path)
        painter.end()

        self._text_cache = pix

    def paintEvent(self, event):
        if not self._text:
            return

        if self._text_cache is None:
            self._render_text_pixmap()
        if self._text_cache is None:
            return

        painter = QPainter(self)
        painter.setOpacity(self._content_opacity_val)

        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter.drawPixmap(self.rect(), self._bg_pixmap)

        painter.drawPixmap(
            int(self._slide_offset_x_val),
            int(self._slide_offset_y_val),
            self._text_cache,
        )

        painter.end()
