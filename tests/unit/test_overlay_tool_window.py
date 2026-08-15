"""Overlay window-behaviour contract: the chat overlay is a Qt::Tool window,
which macOS maps to an NSPanel that AppKit hides whenever the application goes
inactive. Click-through makes every click activate the app underneath, so on
darwin the overlay must opt out via WA_MacAlwaysShowToolWindow or it disappears
the first time it is clicked through.

Needs a real widget (the attribute lives on the QWidget), so it runs under the
offscreen QPA plugin and skips where PyQt6 is absent - the CI-safe `check` job
installs no PyQt6, the two torch-free jobs do.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

pytest.importorskip("PyQt6")

# Must be set before the first QApplication: CI runners have no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import sublume.ui.overlay.subtitle_overlay as overlay_mod  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_overlay():
    return overlay_mod.SubtitleOverlay({})


def test_overlay_is_a_tool_window(qapp):
    """The Tool flag is what keeps the overlay off the taskbar; the darwin
    opt-out below only matters while this holds."""
    o = _make_overlay()
    try:
        assert bool(o.windowFlags() & Qt.WindowType.Tool)
    finally:
        o.deleteLater()


def test_always_show_tool_window_set_on_darwin(qapp, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    o = _make_overlay()
    try:
        assert o.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    finally:
        o.deleteLater()


def test_always_show_tool_window_not_set_off_darwin(qapp, monkeypatch):
    """Windows must be untouched: the attribute is a no-op there, but leaving
    it unset keeps the flag set identical to the pre-macOS behaviour."""
    monkeypatch.setattr(sys, "platform", "win32")
    o = _make_overlay()
    try:
        assert not o.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    finally:
        o.deleteLater()
