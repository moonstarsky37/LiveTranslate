"""The capture backend contract (documentation module — no runtime deps).

Every backend module provides:

    list_output_devices() -> list[str]
        Names the user can pick as the SYSTEM-AUDIO SOURCE. On Windows these
        are WASAPI output devices (captured via their loopback twins); on
        macOS they are input-capable devices, with virtual loopback devices
        (BlackHole) listed first because that is what actually carries the
        system mix there. The historical name is kept for interface
        stability.

    list_input_devices() -> list[str]
        Microphone device names for the mic mix-in picker.

    class AudioCapture:
        __init__(device=None, sample_rate=16000, chunk_duration=0.5)
        start() / stop()
        get_audio(timeout=1.0) -> (np.float32 mono chunk, mic_rms | None) | None
        set_device(name | None | "__disabled__")   # None = platform default,
                                                   # "__disabled__" = mic-only
        set_mic_device(name | "__default__" | None)
        audio_queue                                # bounded, drop-oldest

        Chunks are chunk_duration seconds of 16 kHz mono float32; a backend
        must keep producing silence chunks in mic-only mode so the pipeline
        clock keeps ticking. Device changes at runtime go through the
        set_* methods and are applied by the backend's own read loop.
"""
