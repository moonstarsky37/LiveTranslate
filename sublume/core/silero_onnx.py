"""Silero VAD v6 over onnxruntime — the torch-free VAD path.

Reference implementation: ``utils_vad.OnnxWrapper`` in the silero-vad 6.2.1
wheel, specialized to the only case this app has (16 kHz, batch 1). The model
does NOT take a bare 512-sample chunk: it sees 64 samples of rolling context
concatenated with the chunk — ``input [1, 576] float32`` — plus the LSTM
``state [2, 1, 128] float32`` and ``sr int64``. It returns the speech
probability and the new state; the context becomes the last 64 samples of the
576 fed in, and both context and state carry across calls until
``reset_states()`` zeroes them.

The model file is vendored (sublume/assets/, provenance in the README
there), so this path needs no network and no torch.
"""

from pathlib import Path

import numpy as np

SILERO_ONNX_PATH = Path(__file__).resolve().parents[1] / "assets" / "silero_vad.onnx"

_SAMPLE_RATE = 16000
_CHUNK_SIZE = 512  # samples per call at 16 kHz (32 ms)
_CONTEXT_SIZE = 64  # trailing samples kept between calls at 16 kHz


class SileroOnnxModel:
    """Callable with the shared VAD-model contract: numpy chunk in, float out."""

    def __init__(self, model_path: str | Path = SILERO_ONNX_PATH):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # Mirrors the reference OnnxWrapper (and the jit path's
        # torch.set_num_threads(1)): one 32 ms chunk per call from the capture
        # thread must not fan out into a thread pool.
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SIZE), dtype=np.float32)

    def reset_states(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SIZE), dtype=np.float32)

    def __call__(self, chunk: np.ndarray, sr: int = _SAMPLE_RATE) -> float:
        if sr != _SAMPLE_RATE:
            raise ValueError(f"SileroOnnxModel supports {_SAMPLE_RATE} Hz only, got {sr}")
        x = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != _CHUNK_SIZE:
            raise ValueError(f"expected {_CHUNK_SIZE} samples, got {x.shape[1]}")
        x = np.concatenate([self._context, x], axis=1)
        prob, self._state = self._session.run(
            None,
            {
                "input": x,
                "state": self._state,
                "sr": np.array(sr, dtype=np.int64),
            },
        )
        self._context = x[:, -_CONTEXT_SIZE:]
        return float(np.asarray(prob).reshape(-1)[0])
