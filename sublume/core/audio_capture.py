"""Platform-dispatching shim for audio capture (import path kept stable).

The actual backends live in sublume.core.capture: windows.py (WASAPI
loopback via pyaudiowpatch, the former content of this module moved
verbatim) and macos.py (BlackHole input device via sounddevice). See
capture/base.py for the shared contract.
"""

from sublume.core.capture import (
    AudioCapture,
    list_input_devices,
    list_output_devices,
)

__all__ = ["AudioCapture", "list_input_devices", "list_output_devices"]
