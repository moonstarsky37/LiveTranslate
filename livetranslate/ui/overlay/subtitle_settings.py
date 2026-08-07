"""Subtitle window settings UI (grid layout, line list, apply/close dialog)."""

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from livetranslate.i18n import t
from livetranslate.ui.overlay.subtitle_window import DEFAULT_SUBTITLE_WIN_SETTINGS
from livetranslate.ui.overlay.subtitle_line_edit import LineEditDialog, _ColorButton, _make_image_rows

class SubtitleSettingsWidget(QWidget):
    """Embeddable subtitle settings panel (used as a tab in ControlPanel)."""

    settings_changed = pyqtSignal(dict)

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self._settings = {**DEFAULT_SUBTITLE_WIN_SETTINGS, **(current_settings or {})}
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(200)
        self._debounce_timer.timeout.connect(self._emit_settings)
        self._build_ui()

    def update_settings(self, settings: dict):
        self._settings = {**DEFAULT_SUBTITLE_WIN_SETTINGS, **(settings or {})}
        self._spacing_spin.setValue(self._settings.get("line_spacing", 8))
        self._width_spin.setValue(self._settings.get("window_width", 1000))
        self._bg_color_btn.set_color(self._settings.get("bg_color", "#000000"))
        self._bg_opacity_spin.setValue(round(self._settings.get("bg_opacity", 0) / 255 * 100))
        self._border_radius_spin.setValue(self._settings.get("border_radius", 8))
        self._win_bg_image_edit.setText(self._settings.get("bg_image", ""))
        self._auto_hide_spin.setValue(self._settings.get("auto_hide_timeout", 0))
        idx = self._hide_anim_combo.findData(self._settings.get("auto_hide_animation", "fade"))
        if idx >= 0:
            self._hide_anim_combo.setCurrentIndex(idx)
        self._hide_duration_spin.setValue(self._settings.get("auto_hide_duration", 300))
        self._refresh_lines_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(4, 4, 4, 4)

        # === Window settings ===
        win_group = QGroupBox(t("subwin_basic"))
        g = QGridLayout(win_group)
        g.setColumnStretch(0, 1)
        g.setColumnMinimumWidth(1, 180)
        r = 0

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton(t("subwin_reset"))
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        g.addLayout(btn_row, r, 0, 1, 2)
        r += 1

        g.addWidget(QLabel(t("subwin_window_width")), r, 0)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(200, 3840)
        self._width_spin.setSuffix(" px")
        self._width_spin.setValue(self._settings.get("window_width", 1000))
        self._width_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._width_spin, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_line_spacing")), r, 0)
        self._spacing_spin = QSpinBox()
        self._spacing_spin.setRange(0, 40)
        self._spacing_spin.setSuffix(" px")
        self._spacing_spin.setValue(self._settings.get("line_spacing", 8))
        self._spacing_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._spacing_spin, r, 1)
        r += 1

        self._bg_color_label = QLabel(t("subwin_bg_color"))
        g.addWidget(self._bg_color_label, r, 0)
        self._bg_color_btn = _ColorButton(self._settings.get("bg_color", "#000000"))
        self._bg_color_btn.color_changed.connect(self._on_change)
        g.addWidget(self._bg_color_btn, r, 1)
        r += 1

        self._bg_opacity_label_title = QLabel(t("subwin_bg_opacity"))
        g.addWidget(self._bg_opacity_label_title, r, 0)
        self._bg_opacity_spin = QSpinBox()
        self._bg_opacity_spin.setRange(0, 100)
        self._bg_opacity_spin.setSuffix("%")
        self._bg_opacity_spin.setValue(round(self._settings.get("bg_opacity", 0) / 255 * 100))
        self._bg_opacity_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._bg_opacity_spin, r, 1)
        r += 1

        self._bg_color_controls = [
            self._bg_color_label, self._bg_color_btn,
            self._bg_opacity_label_title, self._bg_opacity_spin,
        ]

        g.addWidget(QLabel(t("subwin_border_radius")), r, 0)
        self._border_radius_spin = QSpinBox()
        self._border_radius_spin.setRange(0, 30)
        self._border_radius_spin.setSuffix(" px")
        self._border_radius_spin.setValue(self._settings.get("border_radius", 8))
        self._border_radius_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._border_radius_spin, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_bg_image")), r, 0)
        img_row, self._win_bg_image_edit = _make_image_rows(
            self._settings.get("bg_image", ""), self._on_win_bg_image_change
        )
        g.addLayout(img_row, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_auto_hide")), r, 0)
        self._auto_hide_spin = QSpinBox()
        self._auto_hide_spin.setRange(0, 120)
        self._auto_hide_spin.setSuffix(" " + t("subwin_auto_hide_sec"))
        self._auto_hide_spin.setValue(self._settings.get("auto_hide_timeout", 0))
        self._auto_hide_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._auto_hide_spin, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_hide_animation")), r, 0)
        self._hide_anim_combo = QComboBox()
        for label, val in [(t("subwin_anim_none"), "none"), (t("subwin_anim_fade"), "fade"), (t("subwin_anim_slide_down"), "slide_down")]:
            self._hide_anim_combo.addItem(label, val)
        idx = self._hide_anim_combo.findData(self._settings.get("auto_hide_animation", "fade"))
        if idx >= 0:
            self._hide_anim_combo.setCurrentIndex(idx)
        self._hide_anim_combo.currentIndexChanged.connect(self._on_change)
        g.addWidget(self._hide_anim_combo, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_hide_duration")), r, 0)
        self._hide_duration_spin = QSpinBox()
        self._hide_duration_spin.setRange(50, 3000)
        self._hide_duration_spin.setSuffix(" ms")
        self._hide_duration_spin.setValue(self._settings.get("auto_hide_duration", 300))
        self._hide_duration_spin.valueChanged.connect(self._on_change)
        g.addWidget(self._hide_duration_spin, r, 1)
        r += 1

        g.addWidget(QLabel(t("subwin_click_through")), r, 0)
        self._click_through_check = QCheckBox(t("subwin_click_through_hint"))
        self._click_through_check.setChecked(
            self._settings.get("click_through", False)
        )
        self._click_through_check.toggled.connect(self._on_change)
        g.addWidget(self._click_through_check, r, 1)

        self._update_win_bg_controls_state()
        layout.addWidget(win_group)

        # === Text lines (list + edit dialog) ===
        lines_group = QGroupBox(t("subwin_text_lines"))
        lines_layout = QVBoxLayout(lines_group)

        self._lines_list = QListWidget()
        self._lines_list.itemDoubleClicked.connect(self._edit_line)
        self._refresh_lines_list()
        lines_layout.addWidget(self._lines_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("btn_add"))
        add_btn.clicked.connect(self._add_line)
        btn_row.addWidget(add_btn)
        edit_btn = QPushButton(t("btn_edit"))
        edit_btn.clicked.connect(self._edit_current_line)
        btn_row.addWidget(edit_btn)
        del_btn = QPushButton(t("btn_remove"))
        del_btn.clicked.connect(self._remove_line)
        btn_row.addWidget(del_btn)
        up_btn = QPushButton(t("subwin_move_up"))
        up_btn.clicked.connect(self._move_line_up)
        btn_row.addWidget(up_btn)
        down_btn = QPushButton(t("subwin_move_down"))
        down_btn.clicked.connect(self._move_line_down)
        btn_row.addWidget(down_btn)
        lines_layout.addLayout(btn_row)

        layout.addWidget(lines_group)

    def _on_reset(self):
        ret = QMessageBox.question(
            self, t("subwin_reset"), t("subwin_reset_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._settings = dict(DEFAULT_SUBTITLE_WIN_SETTINGS)
            self.update_settings(self._settings)
            self._schedule_emit()

    def _on_win_bg_image_change(self):
        self._update_win_bg_controls_state()
        self._on_change()

    def _update_win_bg_controls_state(self):
        has_image = bool(self._win_bg_image_edit.text())
        for ctrl in self._bg_color_controls:
            ctrl.setEnabled(not has_image)

    def _refresh_lines_list(self):
        self._lines_list.clear()
        lines = self._settings.get("lines", DEFAULT_SUBTITLE_WIN_SETTINGS["lines"])
        for cfg in lines:
            line_type = cfg.get("type", "original")
            enabled = cfg.get("enabled", True)
            label = t("subwin_original") if line_type == "original" else t("subwin_translation")
            if line_type == "translation":
                label += f" ({cfg.get('lang', 'zh')})"
            font = cfg.get("font_family", "Microsoft YaHei")
            size = cfg.get("font_size", 24)
            color = cfg.get("color", "#FFF")
            align = cfg.get("align", "center")
            outline = t("subwin_outline") if cfg.get("outline_enabled", True) else ""
            entry = cfg.get("entry_animation", "none")
            parts = [
                "✓" if enabled else "✗",
                label,
                f"{font} {size}pt",
                color,
                align,
            ]
            if outline:
                parts.append(outline)
            if entry != "none":
                parts.append(entry)
            text = "  |  ".join(parts)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, cfg)
            self._lines_list.addItem(item)

    def _edit_line(self, item):
        cfg = item.data(Qt.ItemDataRole.UserRole)
        row = self._lines_list.row(item)
        dlg = LineEditDialog(cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_cfg = dlg.get_config()
            lines = self._settings.get("lines", [])[:]
            lines[row] = new_cfg
            self._settings["lines"] = lines
            self._refresh_lines_list()
            self._schedule_emit()

    def _edit_current_line(self):
        item = self._lines_list.currentItem()
        if item:
            self._edit_line(item)

    def _add_line(self):
        new_line = {
            "type": "translation",
            "lang": "en",
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
        }
        dlg = LineEditDialog(new_line, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            lines = self._settings.get("lines", [])[:]
            lines.append(dlg.get_config())
            self._settings["lines"] = lines
            self._refresh_lines_list()
            self._schedule_emit()

    def _remove_line(self):
        row = self._lines_list.currentRow()
        lines = self._settings.get("lines", [])[:]
        if len(lines) > 1 and 0 <= row < len(lines):
            lines.pop(row)
            self._settings["lines"] = lines
            self._refresh_lines_list()
            self._schedule_emit()

    def _move_line_up(self):
        row = self._lines_list.currentRow()
        lines = self._settings.get("lines", [])[:]
        if row > 0:
            lines[row], lines[row - 1] = lines[row - 1], lines[row]
            self._settings["lines"] = lines
            self._refresh_lines_list()
            self._lines_list.setCurrentRow(row - 1)
            self._schedule_emit()

    def _move_line_down(self):
        row = self._lines_list.currentRow()
        lines = self._settings.get("lines", [])[:]
        if 0 <= row < len(lines) - 1:
            lines[row], lines[row + 1] = lines[row + 1], lines[row]
            self._settings["lines"] = lines
            self._refresh_lines_list()
            self._lines_list.setCurrentRow(row + 1)
            self._schedule_emit()

    def _on_change(self, *_):
        self._schedule_emit()

    def _schedule_emit(self):
        self._debounce_timer.start()

    def _emit_settings(self):
        s = {
            "line_spacing": self._spacing_spin.value(),
            "window_width": self._width_spin.value(),
            "bg_color": self._bg_color_btn.color(),
            "bg_opacity": round(self._bg_opacity_spin.value() / 100 * 255),
            "border_radius": self._border_radius_spin.value(),
            "bg_image": self._win_bg_image_edit.text(),
            "auto_hide_timeout": self._auto_hide_spin.value(),
            "auto_hide_animation": self._hide_anim_combo.currentData() or "fade",
            "auto_hide_duration": self._hide_duration_spin.value(),
            "click_through": self._click_through_check.isChecked(),
            "lines": self._settings.get("lines", DEFAULT_SUBTITLE_WIN_SETTINGS["lines"]),
        }
        self._settings.update(s)
        self.settings_changed.emit(self._settings)

    def get_settings(self) -> dict:
        self._emit_settings()
        return dict(self._settings)


class SubtitleSettingsDialog(QDialog):
    """Standalone dialog wrapper for SubtitleSettingsWidget."""

    settings_changed = pyqtSignal(dict)

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._widget = SubtitleSettingsWidget(current_settings, self)
        self._widget.settings_changed.connect(self.settings_changed.emit)
        layout.addWidget(self._widget)
        self.setWindowTitle(t("subwin_settings"))
        self.setMinimumSize(520, 500)
        self.resize(560, 640)

    def get_settings(self) -> dict:
        return self._widget.get_settings()
