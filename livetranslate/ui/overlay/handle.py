"""DragHandle: the overlay 2-row header bar (buttons, checkboxes, combos)."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from livetranslate.i18n import LANGUAGES, t

# Header layouts the user can pick between (setting: overlay_template).
#   classic  every control on the bar — the original layout
#   compact  bigger buttons, the four window toggles move into the More menu
#   minimal  one row: run/pause + settings + More; everything else in the menu
OVERLAY_TEMPLATES = ("classic", "compact", "minimal")
DEFAULT_OVERLAY_TEMPLATE = "classic"

# (button height, header height) per template. Classic keeps the original 20px
# buttons; the other two lift them to at least the 24px minimum target size.
_TEMPLATE_METRICS = {
    "classic": (20, 62),
    "compact": (24, 52),
    "minimal": (26, 32),
}


_BTN_CSS = """
    QPushButton {
        background: rgba(255,255,255,20);
        border: 1px solid rgba(255,255,255,40);
        border-radius: 3px;
        color: #aaa;
        font-size: 11px;
        padding: 0 6px;
    }
    QPushButton:hover {
        background: rgba(255,255,255,40);
        color: #ddd;
    }
"""


_COMBO_CSS = """
    QComboBox {
        background: rgba(255,255,255,20);
        border: 1px solid rgba(255,255,255,40);
        border-radius: 3px;
        color: #aaa;
        font-size: 11px;
        padding: 0 4px;
    }
    QComboBox:hover { background: rgba(255,255,255,40); color: #ddd; }
    QComboBox::drop-down { border: none; width: 14px; }
    QComboBox::down-arrow { image: none; border: none; }
    QComboBox QAbstractItemView {
        background: #2a2a3a; color: #ccc; selection-background-color: #444;
    }
"""


_CHECK_CSS = (
    "QCheckBox { color: #888; background: transparent; spacing: 3px; }"
    "QCheckBox::indicator { width: 12px; height: 12px; }"
)


class _DragArea(QWidget):
    """Small draggable area (title + grip)."""

    drag_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self._drag_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        if self._drag_pos:
            self._drag_pos = None
            self.drag_finished.emit()


class DragHandle(QWidget):
    """Top bar: row1=title+buttons, row2=checkboxes+combos."""

    settings_clicked = pyqtSignal()
    subtitle_clicked = pyqtSignal()
    click_through_toggled = pyqtSignal(bool)
    topmost_toggled = pyqtSignal(bool)
    auto_scroll_toggled = pyqtSignal(bool)
    taskbar_toggled = pyqtSignal(bool)
    target_language_changed = pyqtSignal(str)
    source_language_changed = pyqtSignal(str)
    model_changed = pyqtSignal(int)
    start_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    hide_clicked = pyqtSignal()
    quit_clicked = pyqtSignal()
    mode_changed = pyqtSignal(str)  # "full" or "compact"
    position_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "full"
        self._template = DEFAULT_OVERLAY_TEMPLATE
        self._buttons = []
        self.setFixedHeight(62)
        self.setStyleSheet("background: rgba(60, 60, 80, 200); border-radius: 4px;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 2)
        outer.setSpacing(2)

        # Row 1: drag title + action buttons
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)

        drag = _DragArea()
        drag.drag_finished.connect(self.position_changed)
        drag.setStyleSheet("background: transparent;")
        drag_layout = QHBoxLayout(drag)
        drag_layout.setContentsMargins(0, 0, 4, 0)
        drag_layout.setSpacing(6)

        title = QLabel("\u2630 LiveTranslate")
        title.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #aaa; background: transparent;")
        drag_layout.addWidget(title)
        drag_layout.addStretch()
        row1.addWidget(drag, 1)

        def _btn(text, tip=None):
            b = QPushButton(text)
            b.setFixedHeight(20)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFont(QFont("Consolas", 8))
            b.setStyleSheet(_BTN_CSS)
            if tip:
                b.setToolTip(tip)
            self._buttons.append(b)
            return b

        self._hide_btn = _btn(t("hide"))
        self._hide_btn.clicked.connect(self.hide_clicked.emit)
        row1.addWidget(self._hide_btn)

        self._subtitle_btn = _btn(t("subtitle"))
        self._subtitle_btn.clicked.connect(self.subtitle_clicked.emit)
        row1.addWidget(self._subtitle_btn)

        self._running = False
        self._start_stop_btn = _btn(t("paused"))
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        row1.addWidget(self._start_stop_btn)

        self._clear_btn = _btn(t("clear"))
        self._clear_btn.clicked.connect(self.clear_clicked.emit)
        row1.addWidget(self._clear_btn)

        # Mode toggle button
        self._mode_btn = _btn(t("mode_full"))
        self._mode_btn.clicked.connect(self._toggle_mode)
        row1.addWidget(self._mode_btn)

        # Model / language summary — replaces the combo row in the minimal
        # template, where there is no second row to put them on.
        self._summary_lbl = QLabel("")
        self._summary_lbl.setFont(QFont("Consolas", 8))
        self._summary_lbl.setStyleSheet("color: #8a8f9c; background: transparent;")
        self._summary_lbl.setVisible(False)
        row1.addWidget(self._summary_lbl)

        self._settings_btn = _btn(t("settings"))
        self._settings_btn.clicked.connect(self.settings_clicked.emit)
        row1.addWidget(self._settings_btn)

        # Overflow menu. Holds whatever the active template took off the bar, so
        # the four window toggles stay one click away instead of moving into
        # another window — they are things you reach for while watching.
        self._more_btn = _btn("⋯", t("more_actions"))
        self._more_btn.clicked.connect(self._show_overflow_menu)
        self._more_btn.setVisible(False)
        row1.addWidget(self._more_btn)

        self._quit_btn = _btn(t("quit"))
        self._quit_btn.setStyleSheet(
            _BTN_CSS.replace("rgba(255,255,255,20)", "rgba(200,60,60,40)").replace(
                "rgba(255,255,255,40)", "rgba(200,60,60,80)"
            )
        )
        self._quit_btn.clicked.connect(self.quit_clicked.emit)
        row1.addWidget(self._quit_btn)

        outer.addLayout(row1)

        # Row 2 area: checkboxes (row 2a) + model/lang combos (row 2b)
        self._row2_widget = QWidget()
        self._row2_widget.setStyleSheet("background: transparent;")
        row2_outer = QVBoxLayout(self._row2_widget)
        row2_outer.setContentsMargins(0, 0, 0, 0)
        row2_outer.setSpacing(2)

        # Row 2a: checkboxes (own widget so a template can hide just this row)
        self._checks_widget = QWidget()
        self._checks_widget.setStyleSheet("background: transparent;")
        row2a = QHBoxLayout(self._checks_widget)
        row2a.setContentsMargins(0, 0, 0, 0)
        row2a.setSpacing(6)

        self._ct_check = QCheckBox(t("click_through"))
        self._ct_check.setFont(QFont("Consolas", 8))
        self._ct_check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._ct_check.setStyleSheet(_CHECK_CSS)
        self._ct_check.toggled.connect(self.click_through_toggled.emit)
        row2a.addWidget(self._ct_check)

        self._topmost_check = QCheckBox(t("top_most"))
        self._topmost_check.setFont(QFont("Consolas", 8))
        self._topmost_check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._topmost_check.setStyleSheet(_CHECK_CSS)
        self._topmost_check.setChecked(True)
        self._topmost_check.toggled.connect(self.topmost_toggled.emit)
        row2a.addWidget(self._topmost_check)

        self._auto_scroll = QCheckBox(t("auto_scroll"))
        self._auto_scroll.setFont(QFont("Consolas", 8))
        self._auto_scroll.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._auto_scroll.setStyleSheet(_CHECK_CSS)
        self._auto_scroll.setChecked(True)
        self._auto_scroll.toggled.connect(self.auto_scroll_toggled.emit)
        row2a.addWidget(self._auto_scroll)

        self._taskbar_check = QCheckBox(t("taskbar"))
        self._taskbar_check.setFont(QFont("Consolas", 8))
        self._taskbar_check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._taskbar_check.setStyleSheet(_CHECK_CSS)
        self._taskbar_check.setChecked(False)
        self._taskbar_check.toggled.connect(self.taskbar_toggled.emit)
        row2a.addWidget(self._taskbar_check)

        row2a.addStretch()
        row2_outer.addWidget(self._checks_widget)

        # Row 2b: model + source language + target language combos (stretch to fill)
        self._combos_widget = QWidget()
        self._combos_widget.setStyleSheet("background: transparent;")
        row2b = QHBoxLayout(self._combos_widget)
        row2b.setContentsMargins(0, 0, 0, 0)
        row2b.setSpacing(4)

        _lbl_css = "color: #888; background: transparent;"
        _lbl_font = QFont("Consolas", 8)
        _combo_font = QFont("Consolas", 8)

        model_lbl = QLabel(t("model_label"))
        model_lbl.setFont(_lbl_font)
        model_lbl.setStyleSheet(_lbl_css)
        row2b.addWidget(model_lbl)

        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(18)
        self._model_combo.setFont(_combo_font)
        self._model_combo.setStyleSheet(_COMBO_CSS)
        self._model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._model_combo.currentIndexChanged.connect(self.model_changed.emit)
        row2b.addWidget(self._model_combo, 3)

        src_lbl = QLabel(t("source_label"))
        src_lbl.setFont(_lbl_font)
        src_lbl.setStyleSheet(_lbl_css)
        row2b.addWidget(src_lbl)

        self._source_lang = QComboBox()
        self._source_lang.setFixedHeight(18)
        self._source_lang.setFont(_combo_font)
        self._source_lang.setStyleSheet(_COMBO_CSS)
        self._source_lang.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for code, native in LANGUAGES:
            label = t("asr_lang_auto") if code == "auto" else native
            self._source_lang.addItem(f"{code} - {label}", code)
        self._source_lang.currentIndexChanged.connect(
            lambda idx: self.source_language_changed.emit(
                self._source_lang.currentData() or "auto"
            )
        )
        row2b.addWidget(self._source_lang, 2)

        tgt_lbl = QLabel(t("target_label"))
        tgt_lbl.setFont(_lbl_font)
        tgt_lbl.setStyleSheet(_lbl_css)
        row2b.addWidget(tgt_lbl)

        self._target_lang = QComboBox()
        self._target_lang.setFixedHeight(18)
        self._target_lang.setFont(_combo_font)
        self._target_lang.setStyleSheet(_COMBO_CSS)
        self._target_lang.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for code, native in LANGUAGES:
            if code == "auto":
                continue
            self._target_lang.addItem(f"{code} - {native}", code)
        self._target_lang.currentIndexChanged.connect(
            lambda idx: self.target_language_changed.emit(
                self._target_lang.currentData() or "zh"
            )
        )
        row2b.addWidget(self._target_lang, 2)

        row2_outer.addWidget(self._combos_widget)

        outer.addWidget(self._row2_widget)
        self._apply_template()

    def _on_start_stop(self):
        if self._running:
            self.stop_clicked.emit()
        else:
            self.start_clicked.emit()

    _PAUSED_CSS = _BTN_CSS.replace(
        "rgba(255,255,255,20)", "rgba(220,180,60,50)"
    ).replace("color: #aaa", "color: #ddb")

    def set_target_language(self, lang: str):
        idx = self._target_lang.findData(lang)
        if idx >= 0:
            self._target_lang.blockSignals(True)
            self._target_lang.setCurrentIndex(idx)
            self._target_lang.blockSignals(False)
            self._update_summary()

    def set_source_language(self, lang: str):
        idx = self._source_lang.findData(lang)
        if idx >= 0:
            self._source_lang.blockSignals(True)
            self._source_lang.setCurrentIndex(idx)
            self._source_lang.blockSignals(False)
            self._update_summary()

    def set_models(self, models: list, active_index: int = 0):
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for m in models:
            self._model_combo.addItem(m.get("name", m.get("model", "?")))
        if 0 <= active_index < self._model_combo.count():
            self._model_combo.setCurrentIndex(active_index)
        self._model_combo.blockSignals(False)
        self._update_summary()

    @property
    def auto_scroll(self) -> bool:
        return self._auto_scroll.isChecked()

    def set_running(self, running: bool):
        self._running = running
        if running:
            self._start_stop_btn.setText(t("running"))
            self._start_stop_btn.setStyleSheet(_BTN_CSS)
        else:
            self._start_stop_btn.setText(t("paused"))
            self._start_stop_btn.setStyleSheet(self._PAUSED_CSS)

    # ── Header templates ────────────────────────────────────────────

    def set_template(self, template: str):
        """Switch header layout. See OVERLAY_TEMPLATES for what each one shows."""
        if template not in OVERLAY_TEMPLATES:
            template = DEFAULT_OVERLAY_TEMPLATE
        if template == self._template:
            return
        self._template = template
        self._apply_template()

    def _apply_template(self):
        btn_h, header_h = _TEMPLATE_METRICS[self._template]
        for b in self._buttons:
            b.setFixedHeight(btn_h)
        for combo in (self._model_combo, self._source_lang, self._target_lang):
            combo.setFixedHeight(max(18, btn_h - 4))

        classic = self._template == "classic"
        minimal = self._template == "minimal"

        # Only classic keeps the four window toggles on the bar; the others move
        # them into the overflow menu, which is why that button appears there.
        self._checks_widget.setVisible(classic)
        self._combos_widget.setVisible(not minimal)
        self._summary_lbl.setVisible(minimal)
        self._more_btn.setVisible(not classic)
        for b in (
            self._hide_btn,
            self._subtitle_btn,
            self._clear_btn,
            self._mode_btn,
            self._quit_btn,
        ):
            b.setVisible(not minimal)

        self._row2_widget.setVisible(not minimal)
        self._update_summary()
        # A template change re-derives the height, but compact mode wins while it
        # is on: the user collapsed the bar deliberately.
        self.setFixedHeight(24 if self._mode == "compact" else header_h)

    def _update_summary(self):
        # Gate on the template, not isVisible(): during construction the overlay
        # has not been shown yet, so isVisible() is still False everywhere.
        if self._template != "minimal":
            return
        model = self._model_combo.currentText() or "-"
        src = self._source_lang.currentData() or "auto"
        tgt = self._target_lang.currentData() or "-"
        self._summary_lbl.setText(f"  {model} · {src} → {tgt}  ")

    def _show_overflow_menu(self):
        """Everything the active template took off the bar, rebuilt on each open
        so the toggle states never drift from the checkboxes that own them."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2a2a3a; color: #ccc; border: 1px solid #444; }"
            "QMenu::item:selected { background: #444; }"
        )

        if self._template == "minimal":
            for label, signal in (
                (t("subtitle"), self.subtitle_clicked),
                (t("clear"), self.clear_clicked),
                (t("hide"), self.hide_clicked),
            ):
                menu.addAction(label, signal.emit)
            menu.addAction(
                t("mode_compact") if self._mode == "compact" else t("mode_full"),
                self._toggle_mode,
            )
            menu.addSeparator()

        for check in (
            self._ct_check,
            self._topmost_check,
            self._auto_scroll,
            self._taskbar_check,
        ):
            action = menu.addAction(check.text())
            action.setCheckable(True)
            action.setChecked(check.isChecked())
            action.toggled.connect(check.setChecked)

        if self._template == "minimal":
            menu.addSeparator()
            menu.addAction(t("quit"), self.quit_clicked.emit)

        menu.exec(self._more_btn.mapToGlobal(self._more_btn.rect().bottomLeft()))

    def _toggle_mode(self):
        new_mode = "compact" if self._mode == "full" else "full"
        self._apply_mode(new_mode)
        self.mode_changed.emit(new_mode)

    def _apply_mode(self, mode: str):
        self._mode = mode
        compact = mode == "compact"
        _, header_h = _TEMPLATE_METRICS[self._template]
        self._row2_widget.setVisible(not compact and self._template != "minimal")
        self._clear_btn.setVisible(not compact and self._template != "minimal")
        self._subtitle_btn.setVisible(not compact and self._template != "minimal")
        self._mode_btn.setText(t("mode_compact") if compact else t("mode_full"))
        self.setFixedHeight(24 if compact else header_h)

    def set_mode(self, mode: str):
        if mode != self._mode:
            self._apply_mode(mode)

    def set_subtitle_checked(self, checked: bool):
        self._subtitle_btn.setStyleSheet(
            _BTN_CSS.replace("rgba(255,255,255,20)", "rgba(80,180,80,40)").replace(
                "rgba(255,255,255,40)", "rgba(80,180,80,80)"
            ) if checked else _BTN_CSS
        )
