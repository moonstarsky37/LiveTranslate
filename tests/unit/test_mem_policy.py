"""Per-engine memory ceiling table.

Qt/torch-free: mem_policy is a plain data module so the threshold can be
read (and tested) anywhere, including the Linux/mac CI jobs.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sublume.asr.mem_policy import (  # noqa: E402
    MEM_THRESHOLD_DEFAULT_MB,
    mem_threshold_for,
)


def test_anime_whisper_gets_the_raised_ceiling():
    # A loaded anime-whisper worker sits at 4.2-4.7GB, so the flat 4096 default
    # would fire on every session.
    assert mem_threshold_for("anime-whisper") == 8192


def test_other_engines_keep_the_default_ceiling():
    for asr_type in ("funasr", "whisper", "sensevoice-onnx"):
        assert mem_threshold_for(asr_type) == MEM_THRESHOLD_DEFAULT_MB


def test_default_ceiling_is_4096():
    assert MEM_THRESHOLD_DEFAULT_MB == 4096


def test_no_engine_falls_back_to_the_default():
    # No worker loaded (or an engine we have no measurement for): stay safe.
    assert mem_threshold_for(None) == MEM_THRESHOLD_DEFAULT_MB
    assert mem_threshold_for("") == MEM_THRESHOLD_DEFAULT_MB
    assert mem_threshold_for("some-engine-we-never-shipped") == MEM_THRESHOLD_DEFAULT_MB
