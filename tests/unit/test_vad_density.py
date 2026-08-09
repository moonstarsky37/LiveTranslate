"""Speech density filter: the 25% floor is a setting, not a constant.

A fixed floor silently ate whole segments from heavily-paused sources (clipped
video, slow speakers) with no way to loosen it. These tests pin the tunable
behaviour, including the off switch.

Marked `local`: vad_processor imports torch at module level, which the CI
environment intentionally does not install.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from sublume.core.vad_processor import VADProcessor  # noqa: E402

pytestmark = pytest.mark.local

CHUNK = 512  # 32ms at 16kHz


def make_vad(**kwargs):
    vad = VADProcessor(sample_rate=16000, min_speech_duration=0.1, **kwargs)
    vad.mode = "energy"  # confidence is patched per feed() anyway
    vad._silence_mode = "fixed"
    vad._silence_limit = vad._seconds_to_chunks(0.3)
    return vad


def feed(vad, confidence, n_chunks):
    vad._get_confidence = lambda _chunk: confidence  # type: ignore[method-assign]
    out = []
    for _ in range(n_chunks):
        seg = vad.process_chunk(np.zeros(CHUNK, dtype=np.float32))
        if seg is not None:
            out.append(seg)
    return out


def sparse_flush(vad):
    """2 voiced chunks, then silence flushes at the 9-chunk limit -> 2/11 = 18%."""
    feed(vad, 0.9, 2)
    return feed(vad, 0.1, 16)


def test_default_density_still_drops_sparse_segments():
    assert sparse_flush(make_vad()) == []


def test_density_zero_disables_the_filter():
    vad = make_vad(min_density=0.0)
    segments = sparse_flush(vad)
    assert len(segments) == 1
    assert len(segments[0]) == 11 * CHUNK  # flushes at the silence limit (2 + 9)


def test_raised_density_drops_a_segment_the_default_would_keep():
    # 10 voiced of 19 chunks -> ~53%: kept at the 25% default, dropped at 60%.
    def run(vad):
        feed(vad, 0.9, 10)
        return feed(vad, 0.1, 9)

    assert len(run(make_vad())) == 1
    assert run(make_vad(min_density=0.6)) == []


def test_update_settings_applies_and_clamps():
    vad = make_vad()
    vad.update_settings({"vad_min_density": 0.4})
    assert vad.min_density == pytest.approx(0.4)
    vad.update_settings({"vad_min_density": 5})
    assert vad.min_density == 1.0
    vad.update_settings({"vad_min_density": -1})
    assert vad.min_density == 0.0
    vad.update_settings({"vad_min_density": "not a number"})
    assert vad.min_density == 0.0  # unchanged by garbage input


def test_reset_clears_the_held_buffer():
    """pause() drops the buffer through this, so stale audio cannot resurface."""
    vad = make_vad()
    feed(vad, 0.9, 10)
    assert vad._speech_samples > 0
    vad.reset()
    assert vad._speech_samples == 0
    assert vad._speech_buffer == []
    assert vad._is_speaking is False
    assert vad.peek_buffer() is None
