"""Memory-ceiling check: per-engine threshold, warn-once latch, re-arming.

Needs PyQt6 importable (supervisor pulls in engine_switch, which imports Qt at
module level) but no QApplication and no torch - the check is driven through an
unbound call with a SimpleNamespace supervisor. Runs in the torch-free CI jobs'
venvs; the plain CI job skips on PyQt6.
"""

import os
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from sublume.asr.supervisor import ASRSupervisor  # noqa: E402


def _fake_supervisor():
    """Everything _check_memory_threshold touches, and nothing else."""
    warnings = []
    fake = types.SimpleNamespace(
        _mem_warned=False,
        _mem_warning_callback=warnings.append,
        _asr_lock=threading.RLock(),
    )
    return fake, warnings


def test_anime_whisper_does_not_warn_below_its_raised_ceiling():
    # 5000MB is over the 4096 default but well under anime-whisper's 8192:
    # a loaded anime-whisper worker normally sits here.
    fake, warnings = _fake_supervisor()
    ASRSupervisor._check_memory_threshold(fake, 5000.0, "anime-whisper")
    assert warnings == []
    assert fake._mem_warned is False


def test_other_engines_warn_once_at_the_default_ceiling():
    fake, warnings = _fake_supervisor()
    ASRSupervisor._check_memory_threshold(fake, 5000.0, "funasr")
    assert warnings == [5000.0]
    assert fake._mem_warned is True
    # Latch: still over the ceiling, but no second toast.
    ASRSupervisor._check_memory_threshold(fake, 5100.0, "funasr")
    assert warnings == [5000.0]


def test_activation_rearms_the_latch():
    fake, warnings = _fake_supervisor()
    ASRSupervisor._check_memory_threshold(fake, 5000.0, "funasr")
    assert warnings == [5000.0]
    # A worker (re)activation resets the latch; the new worker gets its own warning.
    fake._mem_warned = False
    ASRSupervisor._check_memory_threshold(fake, 5100.0, "funasr")
    assert warnings == [5000.0, 5100.0]
    assert fake._mem_warned is True


def test_unknown_engine_uses_the_default_ceiling():
    fake, warnings = _fake_supervisor()
    ASRSupervisor._check_memory_threshold(fake, 3000.0, None)
    assert warnings == []
    ASRSupervisor._check_memory_threshold(fake, 4096.0, None)
    assert warnings == [4096.0]


def _restart_state():
    """Every key _start_worker_from_state indexes or .get()s."""
    return {
        "type": "funasr",
        "signature": ("funasr", "cuda", "paraformer"),
        "device": "cuda",
        "config": {"engine": "funasr"},
        "funasr_model_key": "paraformer",
        "whisper_model_size": "large-v3",
        "display_name": "FunASR",
        "device_label": "cuda",
    }


def test_worker_activation_rearms_the_latch():
    """A worker (re)start must clear the warn-once latch, so the new worker is
    judged against its own engine's ceiling instead of inheriting a warning
    already spent by the previous one. The matching reset in
    engine_switch._activate_asr is pinned by Task 2's promotion tests (that
    closure has to be promoted to a mixin method before it is reachable)."""
    fake = types.SimpleNamespace(
        _asr_lock=threading.RLock(),
        _asr_generation=0,
        _mem_warned=True,
        _asr_error_count=0,
        _set_asr_status=lambda *_: None,
        _load_engine_client=lambda cfg: types.SimpleNamespace(pid=4321),
        _app=types.SimpleNamespace(
            _running=True,
            _asr_ready=False,
            _overlay=None,
            _asr_type=None,
        ),
    )
    assert ASRSupervisor._start_worker_from_state(fake, _restart_state(), 0) is True
    assert fake._mem_warned is False
    assert fake._app._asr_ready is True
