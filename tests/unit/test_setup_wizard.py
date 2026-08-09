"""First-launch wizard: the ASR engine choice.

The wizard is the only screen where the difference between the two SenseVoice
backends is a cost the user is about to pay — 239MB vs 936MB, and whether a
graphics card is needed. These tests pin that the choice is offered, defaults
to the one that works on any machine, is persisted at click time (so an
interrupted download resumes instead of re-running the wizard), and actually
drives which model gets fetched.

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

import livetranslate.ui.dialogs as dialogs  # noqa: E402

pytestmark = pytest.mark.local


class _FakeStore:
    def __init__(self):
        self.saved = None

    def save(self, settings):
        self.saved = dict(settings)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def wizard(qapp):
    """_start_download() installs a root logging handler and swaps sys.stderr,
    both bound to this dialog's Qt signals; production removes them in
    _check_done(), which the stubbed-thread tests never reach. Undo it here, or
    a later test logging anything writes into a deleted C++ object and takes the
    whole process down with an access violation."""
    import logging
    import sys as _sys

    store = _FakeStore()
    dlg = dialogs.SetupWizardDialog(store=store)
    dlg._store = store
    stderr_before = _sys.stderr
    try:
        yield dlg
    finally:
        logging.getLogger().removeHandler(dlg._log_handler)
        _sys.stderr = stderr_before
        dlg.deleteLater()


def test_the_cpu_engine_is_preselected(wizard):
    """Pre-select what runs everywhere; a machine without an NVIDIA card must
    not be steered into the GPU path by the default."""
    assert wizard._engine_onnx.isChecked() is True
    assert wizard._engine_torch.isChecked() is False
    assert wizard.selected_engine() == "sensevoice-onnx"


def test_selecting_the_torch_engine_switches_the_answer(wizard):
    wizard._engine_torch.setChecked(True)
    assert wizard.selected_engine() == "funasr"


def test_both_options_state_their_download_size_and_hardware(wizard):
    """The labels are the whole point: a user cannot choose between them
    without knowing what each one costs."""
    onnx, torch_ = wizard._engine_onnx.text(), wizard._engine_torch.text()
    assert "239" in onnx and "936" in torch_
    assert "CPU" in onnx
    assert "NVIDIA" in torch_
    # Timings live in the tooltips so the screen stays one glance.
    assert wizard._engine_onnx.toolTip()
    assert wizard._engine_torch.toolTip()


@pytest.mark.parametrize(
    "pick_torch,expected", [(False, "sensevoice-onnx"), (True, "funasr")]
)
def test_engine_is_persisted_when_download_starts(wizard, monkeypatch, pick_torch, expected):
    """Settings are written the moment the button is clicked, so closing the
    window mid-download resumes through the missing-model dialog."""
    started = {}
    monkeypatch.setattr(
        dialogs.setup_wizard.threading, "Thread", lambda **kw: _StubThread(started, **kw)
    )
    wizard._engine_torch.setChecked(pick_torch)
    wizard._start_download()
    assert wizard._store.saved["asr_engine"] == expected
    assert wizard._store.saved["download_proxy"] == "system"
    # The worker is handed the same engine that was persisted.
    assert started["args"] == ("system", expected)


class _StubThread:
    def __init__(self, sink, target=None, args=(), daemon=None):
        sink["args"] = args
        self._target = target

    def start(self):
        pass

    def is_alive(self):
        return False


@pytest.mark.parametrize(
    "engine,expect_call",
    [
        ("sensevoice-onnx", ("sensevoice-onnx",)),
        ("funasr", ("funasr",)),
    ],
)
def test_download_worker_fetches_the_chosen_model(wizard, monkeypatch, engine, expect_call):
    calls = []
    # Patch the module that reads the name (dialogs.setup_wizard), not the
    # package re-export — patching the latter would be a no-op (M3-1 lesson).
    monkeypatch.setattr(
        dialogs.setup_wizard, "download_silero", lambda **kw: calls.append(("silero",))
    )
    monkeypatch.setattr(
        dialogs.setup_wizard, "download_asr", lambda name, **kw: calls.append((name,))
    )
    wizard._download_worker("system", engine)
    assert wizard._error is None
    assert calls == [("silero",), expect_call]


def test_download_failure_is_reported_not_raised(wizard, monkeypatch):
    monkeypatch.setattr(dialogs.setup_wizard, "download_silero", lambda **kw: None)

    def boom(name, **kw):
        raise RuntimeError("network died\nsecond line with detail")

    monkeypatch.setattr(dialogs.setup_wizard, "download_asr", boom)
    wizard._download_worker("system", "sensevoice-onnx")
    # Only the first line reaches the dialog; the traceback stays in the log.
    assert wizard._error == "network died"
