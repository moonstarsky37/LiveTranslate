"""Per-line edit dialog and shared pickers for the subtitle window settings."""

import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from livetranslate.i18n import LANGUAGES, t
from livetranslate.paths import ROOT

_PROJECT_DIR = ROOT


class _ColorButton(QPushButton):
    """Small button that shows a color and opens a picker on click."""

    color_changed = pyqtSignal(str)

    def __init__(self, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(28, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick)

    def _update_style(self):
        self.setStyleSheet(
            f"background: {self._color}; border: 1px solid #888; border-radius: 3px;"
        )

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self.window())
        if c.isValid():
            self._color = c.name()
            self._update_style()
            self.color_changed.emit(self._color)

    def color(self):
        return self._color

    def set_color(self, c):
        self._color = c
        self._update_style()


def _make_image_rows(current_path: str, on_change):
    """Create background image selector (2-row layout). Returns (layout, line_edit)."""
    box = QVBoxLayout()
    box.setSpacing(2)

    line_edit = QLineEdit()
    line_edit.setReadOnly(True)
    line_edit.setText(current_path)
    box.addWidget(line_edit)

    btn_row = QHBoxLayout()
    select_btn = QPushButton(t("subwin_bg_image_select"))

    def _select():
        path, _ = QFileDialog.getOpenFileName(
            line_edit.window(),
            t("subwin_bg_image_select"),
            "",
            "Images (*.png *.webp *.jpg *.jpeg *.bmp)",
        )
        if path:
            try:
                rel = os.path.relpath(path, _PROJECT_DIR)
                if not rel.startswith(".."):
                    path = rel.replace("\\", "/")
            except ValueError:
                pass
            line_edit.setText(path)
            on_change()

    select_btn.clicked.connect(_select)
    btn_row.addWidget(select_btn)

    clear_btn = QPushButton(t("subwin_bg_image_clear"))

    def _clear():
        line_edit.setText("")
        on_change()

    clear_btn.clicked.connect(_clear)
    btn_row.addWidget(clear_btn)
    box.addLayout(btn_row)

    return box, line_edit


class LineEditDialog(QDialog):
    """Dialog for editing a single subtitle text line configuration."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = dict(cfg)
        self._color_controls = []
        self.setWindowTitle(t("subwin_edit_line"))
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        grid = QGridLayout()
        r = 0

        grid.addWidget(QLabel(t("subwin_enabled")), r, 0)
        self._enabled = QCheckBox()
        self._enabled.setChecked(self._cfg.get("enabled", True))
        grid.addWidget(self._enabled, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_line_type")), r, 0)
        self._type_combo = QComboBox()
        self._type_combo.addItem(t("subwin_original"), "original")
        self._type_combo.addItem(t("subwin_translation"), "translation")
        idx = self._type_combo.findData(self._cfg.get("type", "original"))
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._type_combo.currentIndexChanged.connect(self._update_lang_visibility)
        grid.addWidget(self._type_combo, r, 1)
        r += 1

        self._lang_label = QLabel(t("subwin_target_lang"))
        grid.addWidget(self._lang_label, r, 0)
        self._lang_combo = QComboBox()
        for code, native in LANGUAGES:
            if code == "auto":
                continue
            self._lang_combo.addItem(f"{code} - {native}", code)
        idx = self._lang_combo.findData(self._cfg.get("lang", "zh"))
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        grid.addWidget(self._lang_combo, r, 1)
        self._lang_row = r
        r += 1

        grid.addWidget(QLabel(t("subwin_font")), r, 0)
        self._font_combo = QComboBox()
        self._font_combo.addItems(QFontDatabase.families())
        idx = self._font_combo.findText(self._cfg.get("font_family", "Microsoft YaHei"))
        if idx >= 0:
            self._font_combo.setCurrentIndex(idx)
        grid.addWidget(self._font_combo, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_font_size")), r, 0)
        self._size_spin = QSpinBox()
        self._size_spin.setRange(8, 120)
        self._size_spin.setSuffix(" pt")
        self._size_spin.setValue(self._cfg.get("font_size", 24))
        grid.addWidget(self._size_spin, r, 1)
        r += 1

        lbl_color = QLabel(t("subwin_color"))
        grid.addWidget(lbl_color, r, 0)
        self._color_btn = _ColorButton(self._cfg.get("color", "#FFFFFF"))
        grid.addWidget(self._color_btn, r, 1)
        self._color_controls.append(lbl_color)
        self._color_controls.append(self._color_btn)
        r += 1

        grid.addWidget(QLabel(t("subwin_opacity")), r, 0)
        self._opacity_spin = QSpinBox()
        self._opacity_spin.setRange(0, 100)
        self._opacity_spin.setSuffix("%")
        self._opacity_spin.setValue(round(self._cfg.get("opacity", 255) / 255 * 100))
        grid.addWidget(self._opacity_spin, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_align")), r, 0)
        self._align_combo = QComboBox()
        self._align_combo.addItem(t("subwin_align_left"), "left")
        self._align_combo.addItem(t("subwin_align_center"), "center")
        self._align_combo.addItem(t("subwin_align_right"), "right")
        idx = self._align_combo.findData(self._cfg.get("align", "center"))
        if idx >= 0:
            self._align_combo.setCurrentIndex(idx)
        grid.addWidget(self._align_combo, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_outline")), r, 0)
        self._outline_check = QCheckBox()
        self._outline_check.setChecked(self._cfg.get("outline_enabled", True))
        grid.addWidget(self._outline_check, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_outline_color")), r, 0)
        self._outline_color_btn = _ColorButton(self._cfg.get("outline_color", "#000000"))
        grid.addWidget(self._outline_color_btn, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_outline_width")), r, 0)
        self._outline_width = QSpinBox()
        self._outline_width.setRange(0, 10)
        self._outline_width.setSuffix(" px")
        self._outline_width.setValue(self._cfg.get("outline_width", 2))
        grid.addWidget(self._outline_width, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_bg_image")), r, 0)
        img_row, self._bg_image_edit = _make_image_rows(self._cfg.get("bg_image", ""), lambda: None)
        grid.addLayout(img_row, r, 1)
        r += 1

        anim_items = [
            (t("subwin_anim_none"), "none"),
            (t("subwin_anim_fade"), "fade"),
            (t("subwin_anim_slide_left"), "slide_left"),
            (t("subwin_anim_slide_right"), "slide_right"),
            (t("subwin_anim_slide_up"), "slide_up"),
            (t("subwin_anim_slide_down"), "slide_down"),
        ]

        grid.addWidget(QLabel(t("subwin_entry_anim")), r, 0)
        self._entry_anim_combo = QComboBox()
        for label, val in anim_items:
            self._entry_anim_combo.addItem(label, val)
        idx = self._entry_anim_combo.findData(self._cfg.get("entry_animation", "none"))
        if idx >= 0:
            self._entry_anim_combo.setCurrentIndex(idx)
        grid.addWidget(self._entry_anim_combo, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_exit_anim")), r, 0)
        self._exit_anim_combo = QComboBox()
        for label, val in anim_items:
            self._exit_anim_combo.addItem(label, val)
        idx = self._exit_anim_combo.findData(self._cfg.get("exit_animation", "none"))
        if idx >= 0:
            self._exit_anim_combo.setCurrentIndex(idx)
        grid.addWidget(self._exit_anim_combo, r, 1)
        r += 1

        grid.addWidget(QLabel(t("subwin_anim_duration")), r, 0)
        self._anim_duration_spin = QSpinBox()
        self._anim_duration_spin.setRange(50, 3000)
        self._anim_duration_spin.setSuffix(" ms")
        self._anim_duration_spin.setValue(self._cfg.get("animation_duration", 300))
        grid.addWidget(self._anim_duration_spin, r, 1)

        layout.addLayout(grid)
        self._update_lang_visibility()

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton(t("subwin_cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _update_lang_visibility(self):
        is_translation = self._type_combo.currentData() == "translation"
        self._lang_label.setEnabled(is_translation)
        self._lang_combo.setEnabled(is_translation)

    def get_config(self) -> dict:
        cfg = {
            **self._cfg,
            "type": self._type_combo.currentData() or "original",
            "enabled": self._enabled.isChecked(),
            "font_family": self._font_combo.currentText(),
            "font_size": self._size_spin.value(),
            "color": self._color_btn.color(),
            "opacity": round(self._opacity_spin.value() / 100 * 255),
            "align": self._align_combo.currentData() or "center",
            "outline_enabled": self._outline_check.isChecked(),
            "outline_color": self._outline_color_btn.color(),
            "outline_width": self._outline_width.value(),
            "bg_image": self._bg_image_edit.text(),
            "entry_animation": self._entry_anim_combo.currentData() or "none",
            "exit_animation": self._exit_anim_combo.currentData() or "none",
            "animation_duration": self._anim_duration_spin.value(),
        }
        if cfg["type"] == "translation":
            cfg["lang"] = self._lang_combo.currentData() or "zh"
        return cfg
