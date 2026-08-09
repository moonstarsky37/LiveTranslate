"""ASR engine interface — makes the implicit contract of the concrete engines
explicit. Engines are instantiated ONLY inside the ASR worker process
(sublume.asr.worker); the GUI process talks to them via ASRClient.
"""

from typing import Protocol

import numpy as np


class ASREngine(Protocol):
    """Contract every concrete ASR backend implements."""

    language: str | None

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a mono 16 kHz float32 buffer and return plain text
        (empty string when nothing intelligible was recognized)."""
        ...
