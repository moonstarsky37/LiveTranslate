"""Header layout templates and the run/pause button label.

The overlay header is the one place where a wrong label is a functional bug:
the button used to show the *state* while behaving as an *action*, so its text
was the exact inverse of what clicking it did. These tests pin the corrected
labels and the three selectable layouts.

Marked `local`: needs PyQt6, which CI does not install.
"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from livetranslate.i18n import t  # noqa: E402
from livetranslate.ui.overlay.handle import (  # noqa: E402
    DEFAULT_OVERLAY_TEMPLATE,
    OVERLAY_TEMPLATES,
    DragHandle,
)

pytestmark = pytest.mark.local


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def handle(qapp):
    return DragHandle()


def test_run_button_shows_the_action_not_the_state(handle):
    """Clicking while running pauses, so the label must read as "pause"."""
    handle.set_running(True)
    assert handle._start_stop_btn.text() == t("running")
    handle.set_running(False)
    assert handle._start_stop_btn.text() == t("paused")
    # And the two labels must differ, or the button says nothing at all.
    assert t("running") != t("paused")


def test_run_button_emits_the_opposite_of_its_state(handle, qapp):
    seen = []
    handle.start_clicked.connect(lambda: seen.append("start"))
    handle.stop_clicked.connect(lambda: seen.append("stop"))
    handle.set_running(True)
    handle._start_stop_btn.click()
    handle.set_running(False)
    handle._start_stop_btn.click()
    assert seen == ["stop", "start"]


def test_classic_is_the_default_and_keeps_every_control(handle):
    assert handle._template == DEFAULT_OVERLAY_TEMPLATE == "classic"
    assert handle.height() == 62
    assert handle._checks_widget.isVisibleTo(handle) is True
    assert handle._combos_widget.isVisibleTo(handle) is True
    assert handle._more_btn.isVisibleTo(handle) is False


def test_compact_enlarges_buttons_and_moves_toggles_into_the_menu(handle):
    handle.set_template("compact")
    assert handle.height() == 52
    # 24px is the WCAG 2.5.8 minimum target size; classic's 20px misses it.
    assert all(b.height() == 24 for b in handle._buttons)
    assert handle._checks_widget.isVisibleTo(handle) is False
    assert handle._more_btn.isVisibleTo(handle) is True
    # The combos stay on the bar — switching model mid-stream must stay one click.
    assert handle._combos_widget.isVisibleTo(handle) is True


def test_minimal_keeps_only_the_primary_controls(handle):
    handle.set_template("minimal")
    assert handle.height() == 32
    assert handle._combos_widget.isVisibleTo(handle) is False
    assert handle._summary_lbl.isVisibleTo(handle) is True
    for hidden in (handle._hide_btn, handle._clear_btn, handle._quit_btn):
        assert hidden.isVisibleTo(handle) is False
    for shown in (handle._start_stop_btn, handle._settings_btn, handle._more_btn):
        assert shown.isVisibleTo(handle) is True


def test_minimal_summary_reflects_model_and_languages(handle):
    handle.set_template("minimal")
    handle.set_models([{"name": "translategemma"}], 0)
    handle.set_source_language("auto")
    handle.set_target_language("zh-TW")
    text = handle._summary_lbl.text()
    assert "translategemma" in text
    assert "auto" in text and "zh-TW" in text


def test_unknown_template_falls_back_to_classic(handle):
    handle.set_template("minimal")
    handle.set_template("does-not-exist")
    assert handle._template == "classic"
    assert handle.height() == 62


def test_every_template_has_metrics_and_a_translated_label(handle):
    for key in OVERLAY_TEMPLATES:
        handle.set_template(key)
        assert handle._template == key
        assert handle.height() > 0
        label = t(f"overlay_template_{key}")
        assert label and not label.startswith("overlay_template_")


def test_compact_mode_still_collapses_whatever_the_template_is(handle):
    for key in OVERLAY_TEMPLATES:
        handle.set_template(key)
        handle.set_mode("compact")
        assert handle.height() == 24
        handle.set_mode("full")
        assert handle.height() > 24
