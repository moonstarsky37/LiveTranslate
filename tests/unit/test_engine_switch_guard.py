"""Torch-engine guard UX: which guidance a torch-less user gets, and what
the "launch installer" button actually launches.

Needs PyQt6 importable (engine_switch imports it at module level) but no
QApplication and no torch - the dialog is driven through a fake QMessageBox.
Runs in the torch-free CI job's venv; the plain CI job skips on PyQt6.
"""

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

import sublume.asr.engine_switch as es  # noqa: E402


# --------------------------------------------------------------------------
# guidance mode: source install vs portable zip
# --------------------------------------------------------------------------


def test_hint_mode_is_installer_when_install_ps1_exists(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "install.ps1").write_text("# stub", encoding="utf-8")
    assert es._torch_install_hint_mode(tmp_path) == "installer"


def test_hint_mode_is_pip_for_a_portable_tree(tmp_path):
    # The portable zip drops scripts/ entirely.
    assert es._torch_install_hint_mode(tmp_path) == "pip"


def test_repo_checkout_resolves_to_installer_mode():
    """This repo IS a source checkout, so the default-root call must say so."""
    assert es._torch_install_hint_mode() == "installer"


# --------------------------------------------------------------------------
# installer launch command
# --------------------------------------------------------------------------


def test_launch_installer_starts_install_bat_with_full_profile(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        es.subprocess, "Popen", lambda args, **kw: calls.append((args, kw))
    )
    es._launch_installer(tmp_path)
    assert len(calls) == 1
    args, kw = calls[0]
    assert args[:4] == ["cmd.exe", "/c", "start", "Sublume Installer"]
    assert args[4] == str(tmp_path / "install.bat")
    assert args[5:] == ["-Profile", "full"]
    assert kw["cwd"] == str(tmp_path)


# --------------------------------------------------------------------------
# dialog flow (fake QMessageBox - no QApplication involved)
# --------------------------------------------------------------------------


class _FakeBox:
    """Just enough QMessageBox: records buttons, 'clicks' one on exec()."""

    click_launch = True  # class-level knob set per test

    class Icon:
        Warning = object()

    class ButtonRole:
        AcceptRole = object()

    class StandardButton:
        Cancel = object()

    def __init__(self, parent=None):
        self._buttons = []
        self._clicked = None

    def setIcon(self, *_):
        pass

    def setWindowTitle(self, *_):
        pass

    def setText(self, *_):
        pass

    def addButton(self, *args):
        marker = object()
        self._buttons.append(marker)
        return marker

    def exec(self):
        # First addButton call is the launch button in production code.
        self._clicked = self._buttons[0] if _FakeBox.click_launch else self._buttons[-1]

    def clickedButton(self):
        return self._clicked


def _fake_supervisor():
    return types.SimpleNamespace(_app=types.SimpleNamespace(_panel=None))


@pytest.fixture
def dialog_env(monkeypatch):
    """Patch the module-level names engine_switch reads (never sip-wrapped Qt
    classes themselves - setting attributes on those can be rejected)."""
    launched = []
    quit_scheduled = []
    monkeypatch.setattr(es, "QMessageBox", _FakeBox)
    monkeypatch.setattr(es, "_launch_installer", lambda: launched.append(True))
    monkeypatch.setattr(
        es, "QApplication",
        types.SimpleNamespace(
            instance=lambda: types.SimpleNamespace(quit=lambda: None)
        ),
    )
    monkeypatch.setattr(
        es, "QTimer",
        types.SimpleNamespace(singleShot=lambda ms, fn: quit_scheduled.append(ms)),
    )
    return launched, quit_scheduled


def test_launch_button_launches_installer_and_schedules_quit(monkeypatch, dialog_env):
    launched, quit_scheduled = dialog_env
    monkeypatch.setattr(es, "_torch_install_hint_mode", lambda *a: "installer")
    _FakeBox.click_launch = True
    es.EngineSwitchMixin._prompt_torch_install(_fake_supervisor())
    assert launched == [True]
    assert quit_scheduled == [0]


def test_cancel_keeps_the_app_running(monkeypatch, dialog_env):
    launched, quit_scheduled = dialog_env
    monkeypatch.setattr(es, "_torch_install_hint_mode", lambda *a: "installer")
    _FakeBox.click_launch = False
    es.EngineSwitchMixin._prompt_torch_install(_fake_supervisor())
    assert launched == []
    assert quit_scheduled == []


def test_portable_mode_shows_the_pip_text_and_never_launches(monkeypatch, dialog_env):
    launched, quit_scheduled = dialog_env
    warnings = []
    monkeypatch.setattr(es, "_torch_install_hint_mode", lambda *a: "pip")
    monkeypatch.setattr(
        es.QMessageBox, "warning",
        staticmethod(lambda parent, title, text: warnings.append(text)),
        raising=False,
    )
    es.EngineSwitchMixin._prompt_torch_install(_fake_supervisor())
    assert len(warnings) == 1
    assert launched == []
    assert quit_scheduled == []
