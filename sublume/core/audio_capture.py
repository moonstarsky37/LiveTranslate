"""Platform-dispatching shim for audio capture (import path kept stable).

The actual backends live in sublume.core.capture: windows.py (WASAPI
loopback via pyaudiowpatch, the former content of this module moved
verbatim) and macos.py (BlackHole input device via sounddevice). See
capture/base.py for the shared contract.
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
