"""Torch-free Silero VAD path: the vendored ONNX model and its wrapper.

The parity test is the standing A/B gate from the torch-free spec: the jit
model (torch profile) and the vendored ONNX model must produce the same
per-chunk confidences on the same stream, or an asset/package upgrade has
silently changed the VAD. The remaining tests pin the wrapper's contract
(context + state semantics, input validation) without needing torch.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")

from sublume.core.silero_onnx import (  # noqa: E402
    SILERO_ONNX_PATH,
    SileroOnnxModel,
)

CHUNK = 512
SR = 16000


def _mixed_stream(n_chunks: int, seed: int = 7) -> list:
    """Chunks covering the confidence range: silence, noise at several
    levels, and voiced-like harmonic bursts with a slow envelope."""
    rng = np.random.default_rng(seed)
    total = n_chunks * CHUNK
    t = np.arange(total) / SR
    voiced = np.zeros(total, dtype=np.float64)
    f0 = 140.0
    for k in range(1, 9):
        voiced += (0.6 / k) * np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28))
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 0.7 * t - 1.57))  # syllable-ish AM
    voiced *= envelope
    noise = rng.normal(0, 1, total)

    signal = np.zeros(total, dtype=np.float64)
    for i in range(n_chunks):
        lo, hi = i * CHUNK, (i + 1) * CHUNK
        phase = i % 8
        if phase < 2:
            signal[lo:hi] = 0.0
        elif phase < 4:
            signal[lo:hi] = 0.01 * noise[lo:hi]
        elif phase < 5:
            signal[lo:hi] = 0.1 * noise[lo:hi]
        else:
            signal[lo:hi] = 0.3 * voiced[lo:hi] + 0.005 * noise[lo:hi]
    signal = signal.astype(np.float32)
    return [signal[i * CHUNK:(i + 1) * CHUNK] for i in range(n_chunks)]


def test_vendored_model_exists_and_yields_probabilities():
    assert SILERO_ONNX_PATH.exists(), "vendored model missing from sublume/assets"
    model = SileroOnnxModel()
    for chunk in _mixed_stream(16):
        prob = model(chunk, SR)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0


def test_reset_states_restores_determinism():
    """State and context carry across calls; reset must bring the model back
    to its initial deterministic response."""
    model = SileroOnnxModel()
    chunk = _mixed_stream(1)[0]
    first = model(chunk, SR)
    model(chunk, SR)  # mutate state/context
    model.reset_states()
    assert model(chunk, SR) == first


def test_rejects_wrong_rate_and_wrong_size():
    model = SileroOnnxModel()
    with pytest.raises(ValueError):
        model(np.zeros(CHUNK, dtype=np.float32), 8000)
    with pytest.raises(ValueError):
        model(np.zeros(256, dtype=np.float32), SR)


def test_model_resolution_falls_back_to_onnx(monkeypatch):
    """Without the silero-vad package the loader must hand back the vendored
    ONNX model. Patch the adapter on the module that reads it (vad_processor),
    not a re-export."""
    import sublume.core.vad_processor as vp

    def _no_package(*args, **kwargs):
        raise ImportError("silero_vad not installed")

    monkeypatch.setattr(vp, "_SileroJitAdapter", _no_package)
    model = vp._load_silero_model()
    assert isinstance(model, SileroOnnxModel)


@pytest.mark.local
def test_parity_with_jit():
    """A/B gate: same stream through the package jit model (via the
    production adapter) and the vendored ONNX model; confidences must agree
    to 1e-4 per chunk or the two paths are no longer the same VAD."""
    pytest.importorskip("torch")
    pytest.importorskip("silero_vad")
    from sublume.core.vad_processor import _SileroJitAdapter

    jit = _SileroJitAdapter()
    onnx = SileroOnnxModel()
    deltas = [
        abs(jit(chunk, SR) - onnx(chunk, SR)) for chunk in _mixed_stream(200)
    ]
    assert max(deltas) <= 1e-4, f"max per-chunk delta {max(deltas):.3e} exceeds 1e-4"
