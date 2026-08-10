"""Audio capture backends.

windows.py — WASAPI loopback via pyaudiowpatch (the pre-split module,
moved verbatim). macos.py — BlackHole virtual input device via sounddevice.
See base.py for the shared contract.

This package deliberately imports NOTHING at package level: importing
sublume.core.capture.macos must not drag in the Windows backend (whose
pyaudiowpatch dependency does not exist in minimal environments). Platform
dispatch lives in sublume.core.audio_capture.
"""
