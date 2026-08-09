"""Idle-tail flush: a held sub-min_speech segment must surface once the audio
source stays silent (e.g. the video is paused), instead of waiting forever
for a next speech onset to merge with.

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

CHUNK = 512  # 32ms at 16kHz — matches the app's capture chunk size


def make_vad():
    vad = VADProcessor(sample_rate=16000, min_speech_duration=1.0)
    vad.mode = "energy"  # anything but silero; confidence is patched anyway
    # 0.3s silence limit mirrors the adaptive floor — the regime where real
    # held tails occur (with a long 0.8s limit, any held tail is mostly
    # silence and the density filter would discard it anyway).
    vad._silence_mode = "fixed"
    vad._silence_limit = vad._seconds_to_chunks(0.3)
    return vad


def feed(vad, confidence, n_chunks):
    """Feed n silent-content chunks with a scripted VAD confidence."""
    vad._get_confidence = lambda _chunk: confidence  # type: ignore[method-assign]
    out = []
    for _ in range(n_chunks):
        seg = vad.process_chunk(np.zeros(CHUNK, dtype=np.float32))
        if seg is not None:
            out.append(seg)
    return out


def test_held_tail_is_flushed_after_prolonged_silence():
    vad = make_vad()
    # 0.58s of speech: below min_speech (1.0s) -> held for merge at silence end
    assert feed(vad, 0.9, 18) == []
    # 0.3s silence triggers the hold (returns nothing yet)
    assert feed(vad, 0.1, 9) == []
    # ~2s more silence: the held tail must flush exactly once
    segments = feed(vad, 0.1, 70)
    assert len(segments) == 1
    # Held content = speech chunks + the silence chunks appended pre-hold
    assert len(segments[0]) == (18 + 9) * CHUNK
    # Buffer fully reset — continued silence produces nothing further
    assert feed(vad, 0.1, 200) == []


def test_speech_resuming_before_tail_flush_still_merges():
    vad = make_vad()
    assert feed(vad, 0.9, 18) == []          # short speech -> held
    assert feed(vad, 0.1, 9) == []           # hold point
    assert feed(vad, 0.1, 30) == []          # silence, but under the 2s tail limit
    assert feed(vad, 0.9, 20) == []          # speech resumes -> merged into buffer
    segments = feed(vad, 0.1, 9)             # normal silence flush; now above min
    assert len(segments) == 1
    assert len(segments[0]) > 40 * CHUNK     # contains both speech bursts


def test_pure_noise_tail_is_still_dropped_by_density_filter():
    vad = make_vad()
    # 2 voiced chunks then silence: density 2/(2+9) < 25% -> the tail flush
    # path must discard it (silently) rather than emit noise
    assert feed(vad, 0.9, 2) == []
    assert feed(vad, 0.1, 9) == []           # hold
    segments = feed(vad, 0.1, 70)            # tail flush fires -> density drop
    assert segments == []
    assert vad._speech_samples == 0          # buffer really cleared
