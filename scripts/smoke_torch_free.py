"""Torch-free profile smoke: import every GUI-process module and prove the
VAD takes the vendored ONNX path. Run against a base-profile environment
(requirements.txt only - no torch, no silero-vad package).

Everything sits under the __main__ guard on purpose: the ASR worker is a
multiprocessing *spawn* child that re-imports the entry module, and unguarded
module-level code would boot a second app inside a worker instead of letting
it reach its target function. This script spawns no workers today, but it is
the template for heavier smokes that will.
"""

import os
import sys

MODULES = [
    "sublume.model_manager",
    "sublume.config.store",
    "sublume.core.vad_processor",
    "sublume.core.audio_capture",
    "sublume.core.pipeline",
    "sublume.core.transcript_writer",
    "sublume.translation.translator",
    "sublume.asr.client",
    "sublume.asr.supervisor",
    "sublume.asr.engine_switch",
    "sublume.ui.dialogs",
    "sublume.ui.changelog",
    "sublume.ui.control_panel",
    "sublume.ui.overlay.subtitle_overlay",
    "sublume.ui.overlay.subtitle_window",
    "sublume.main",
    "sublume.app",
]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    os.chdir(root)

    failed = []
    for name in MODULES:
        try:
            __import__(name)
            print(f"OK   {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")

    if "torch" in sys.modules:
        print("FAIL torch leaked into the import graph")
        return 1
    print("torch in sys.modules: False")

    import numpy as np

    from sublume.core.vad_processor import VADProcessor

    vad = VADProcessor()
    model_cls = type(vad._model).__name__
    conf = vad._silero_confidence(np.zeros(512, dtype=np.float32))
    print(f"VADProcessor model: {model_cls}, silence confidence = {conf:.4f}")
    if model_cls != "SileroOnnxModel":
        print(f"FAIL expected SileroOnnxModel, got {model_cls}")
        return 1
    if "torch" in sys.modules:
        print("FAIL torch appeared after VAD construction")
        return 1

    print("SMOKE-TORCH-FREE: " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
