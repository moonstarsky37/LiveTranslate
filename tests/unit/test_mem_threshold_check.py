"""Memory-ceiling check and worker activation: per-engine threshold, warn-once
latch and its re-arming, plus the device label the activation paths publish.

Needs PyQt6 importable (supervisor pulls in engine_switch, which imports Qt at
module level) but no QApplication and no torch - the paths are driven through
unbound calls and a hand-seeded supervisor. The activation tests subclass the
real EngineSwitchMixin on purpose, so the real resolver runs instead of a stub
asserting itself. Runs in the torch-free CI jobs' venvs; the plain CI job skips
on PyQt6.
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

from sublume.asr.engine_switch import EngineSwitchMixin  # noqa: E402
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
    """Every key the activation paths index or .get()."""
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


class _Fake(EngineSwitchMixin):
    """A supervisor stand-in that keeps the real mixin behaviour under test
    (_resolve_ready_device_label, _activate_asr) and fakes nothing but the
    attributes and collaborators those paths reach for."""


def _fake_activation_target(client):
    """Seed only what the activation paths READ; writes land on the instance."""
    fake = _Fake()
    fake._asr_lock = threading.RLock()
    fake._asr_generation = 0
    fake._mem_warned = True
    fake._asr_error_count = 0
    fake._set_asr_status = lambda *_: None
    fake._load_engine_client = lambda cfg: client
    fake._app = types.SimpleNamespace(
        _running=True,
        _asr_ready=False,
        _overlay=None,
        _asr_type=None,
    )
    return fake


def test_worker_activation_rearms_the_latch():
    """A worker (re)start must clear the warn-once latch, so the new worker is
    judged against its own engine's ceiling instead of inheriting a warning
    already spent by the previous one."""
    fake = _fake_activation_target(types.SimpleNamespace(pid=4321))
    state = _restart_state()
    assert ASRSupervisor._start_worker_from_state(fake, state, 0) is True
    assert fake._mem_warned is False
    assert fake._app._asr_ready is True
    # No ready_info on this client (remote shim shape): label left untouched.
    assert state["device_label"] == "cuda"


def test_engine_switch_activation_resolves_the_label_and_rearms_the_latch():
    """Engine-switch path: the worker reported it actually loaded on CPU, so the
    saved restart state (and the label the call site renders) must say cpu."""
    fake = _fake_activation_target(None)
    state = _restart_state()
    fake._activate_asr(types.SimpleNamespace(ready_info={"device": "cpu"}), state)
    assert state["device_label"] == "cpu"
    assert fake._asr_restart_state["device_label"] == "cpu"
    assert fake._mem_warned is False


def test_resolver_leaves_the_label_alone_when_the_client_reports_nothing():
    """remote-whisper is an in-process shim with no ready_info at all; its URL
    label must survive. Same for a client whose payload carries no device."""
    fake = _fake_activation_target(None)
    remote_state = _restart_state()
    remote_state["device_label"] = "http://127.0.0.1:8765"
    fake._resolve_ready_device_label(types.SimpleNamespace(), remote_state)
    assert remote_state["device_label"] == "http://127.0.0.1:8765"

    state = _restart_state()
    for ready_info in (None, {}, {"device": None}, {"device": ""}):
        fake._resolve_ready_device_label(
            types.SimpleNamespace(ready_info=ready_info), state
        )
        assert state["device_label"] == "cuda"


def test_worker_restart_resolves_the_label():
    """Worker-death restart path (exactly the CUDA-fell-over case): the label
    must follow the restarted worker onto CPU."""
    client = types.SimpleNamespace(ready_info={"device": "cpu"}, pid=4321)
    fake = _fake_activation_target(client)
    state = _restart_state()
    assert ASRSupervisor._start_worker_from_state(fake, state, 0) is True
    assert state["device_label"] == "cpu"
    assert fake._asr_restart_state["device_label"] == "cpu"
    assert fake._mem_warned is False
