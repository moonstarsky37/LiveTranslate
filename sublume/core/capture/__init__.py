"""Audio capture backends, selected by platform at import time.

Windows captures the system mix directly via WASAPI loopback
(pyaudiowpatch); macOS reads a virtual input device (BlackHole) via
sounddevice. Both expose the same surface — see base.py for the contract.
Callers keep importing from sublume.core.audio_capture, which re-exports
whatever this package selected.
"""

import sys

if sys.platform == "darwin":
    from sublume.core.capture.macos import (
        AudioCapture,
        list_input_devices,
        list_output_devices,
    )
else:
    from sublume.core.capture.windows import (
        AudioCapture,
        list_input_devices,
        list_output_devices,
    )

__all__ = ["AudioCapture", "list_input_devices", "list_output_devices"]
