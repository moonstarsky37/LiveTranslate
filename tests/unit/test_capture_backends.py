"""Capture backend split: platform dispatch, the macOS backend's device
logic, and the mic-permission silence watchdog — all runnable anywhere by
injecting a fake sounddevice module (macos.py imports it lazily).
"""

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")

import sublume.core.capture.macos as mac  # noqa: E402


def _fake_sd(monkeypatch, devices):
    """Install a fake sounddevice with the given query_devices() payload."""
    fake = types.SimpleNamespace(
        query_devices=lambda: devices,
        default=types.SimpleNamespace(device=(0, 0)),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


DEVICES = [
    {"name": "MacBook Pro Microphone", "max_input_channels": 1, "default_samplerate": 48000.0},
    {"name": "BlackHole 2ch", "max_input_channels": 2, "default_samplerate": 48000.0},
    {"name": "External Display", "max_input_channels": 0, "default_samplerate": 48000.0},
]


def test_source_list_puts_blackhole_first(monkeypatch):
    _fake_sd(monkeypatch, DEVICES)
    names = mac.list_output_devices()
    assert names[0] == "BlackHole 2ch"
    assert "External Display" not in names  # no input channels


def test_mic_list_excludes_the_loopback_family(monkeypatch):
    _fake_sd(monkeypatch, DEVICES)
    assert mac.list_input_devices() == ["MacBook Pro Microphone"]


def test_has_blackhole(monkeypatch):
    _fake_sd(monkeypatch, DEVICES)
    assert mac.has_blackhole() is True
    _fake_sd(monkeypatch, [DEVICES[0]])
    assert mac.has_blackhole() is False


def test_source_resolution_prefers_blackhole_and_errors_without_it(monkeypatch):
    _fake_sd(monkeypatch, DEVICES)
    cap = mac.AudioCapture()
    idx, name, ch, rate = cap._find_source_device()
    assert name == "BlackHole 2ch" and ch == 2
    _fake_sd(monkeypatch, [DEVICES[0]])
    with pytest.raises(RuntimeError, match="BlackHole"):
        mac.AudioCapture()._find_source_device()


def test_resample_matches_the_windows_math(monkeypatch):
    """Same linear interpolation as the Windows backend, fed ndarray frames."""
    _fake_sd(monkeypatch, DEVICES)
    cap = mac.AudioCapture(sample_rate=16000)
    frames = np.ones((48000, 2), dtype=np.float32)  # 1s stereo @48k
    mono = cap._resample_to_mono(frames, 2, 48000)
    assert mono.shape == (16000,)
    assert np.allclose(mono, 1.0)


def test_silence_watchdog_raises_the_permission_hint(monkeypatch):
    _fake_sd(monkeypatch, DEVICES)
    cap = mac.AudioCapture(chunk_duration=0.5)
    zero = np.zeros(8000, dtype=np.float32)
    loud = np.full(8000, 0.1, dtype=np.float32)
    for _ in range(19):  # 9.5s of silence: below the 10s threshold
        cap._note_chunk_for_permission_hint(zero)
    assert cap.permission_hint_active is False
    cap._note_chunk_for_permission_hint(loud)  # real signal resets the clock
    for _ in range(19):
        cap._note_chunk_for_permission_hint(zero)
    assert cap.permission_hint_active is False
    cap._note_chunk_for_permission_hint(zero)  # 10.0s reached
    assert cap.permission_hint_active is True


def test_shim_dispatches_by_platform():
    """The stable import path resolves to the platform's backend."""
    from sublume.core import audio_capture

    if sys.platform == "darwin":
        assert audio_capture.AudioCapture is mac.AudioCapture
    else:
        import sublume.core.capture.windows as win

        assert audio_capture.AudioCapture is win.AudioCapture
        assert audio_capture.list_output_devices is win.list_output_devices
