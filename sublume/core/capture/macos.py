"""macOS capture backend: reads a virtual loopback input device (BlackHole)
via sounddevice / CoreAudio.

Differences from the Windows backend, by design:

- The system mix arrives through a user-installed virtual INPUT device
  (BlackHole), so "output device" listing actually enumerates input-capable
  devices with BlackHole-family devices first (see base.py).
- macOS treats reading any input device as microphone access: the first
  capture triggers the system permission prompt (addressed to the launching
  terminal). A DENIED permission yields endless all-zero frames with no
  error, so this backend watches for that and raises a one-time hint
  (``permission_hint_active``) instead of staying silent.
- There is no default-output auto-follow: the source is an explicit virtual
  device, not whatever the speakers currently are.

sounddevice is imported lazily so importing this module is safe on any
platform (the CI import smoke runs it on Windows too).
"""

import logging
import queue
import threading
import time

import numpy as np

log = logging.getLogger("Sublume.Audio")

# Substring that marks a virtual loopback device we want to prefer/guide to.
BLACKHOLE_MARKER = "BlackHole"

# Continuous all-zero capture longer than this raises the permission hint.
PERMISSION_HINT_SECONDS = 10.0


def _sd():
    import sounddevice

    return sounddevice


def _input_devices():
    """(index, name, channels, default_samplerate) for input-capable devices."""
    sd = _sd()
    out = []
    try:
        devices = sd.query_devices()
    except Exception as e:
        log.warning(f"sounddevice device query failed: {e}")
        return out
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) > 0:
            out.append(
                (
                    idx,
                    str(dev.get("name", f"device {idx}")),
                    int(dev["max_input_channels"]),
                    float(dev.get("default_samplerate", 48000.0) or 48000.0),
                )
            )
    return out


def is_blackhole(name: str) -> bool:
    return BLACKHOLE_MARKER.lower() in name.lower()


def has_blackhole() -> bool:
    """True when a BlackHole-family input device is present (B2 guidance)."""
    return any(is_blackhole(name) for _, name, _, _ in _input_devices())


def list_output_devices():
    """System-audio source candidates: BlackHole-family devices first."""
    names = [name for _, name, _, _ in _input_devices()]
    return sorted(names, key=lambda n: (not is_blackhole(n), n))


def list_input_devices():
    """Microphone candidates: input devices minus the loopback family."""
    return [name for _, name, _, _ in _input_devices() if not is_blackhole(name)]


class AudioCapture:
    """Capture the system mix from a BlackHole input device via sounddevice."""

    def __init__(self, device=None, sample_rate=16000, chunk_duration=0.5):
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.audio_queue = queue.Queue(maxsize=100)
        self._stream = None
        self._running = False
        self._device_name = device
        self._read_thread = None
        self._native_channels = 2
        self._native_rate = 48000
        self._current_device_name = None
        self._loopback_disabled = False
        self._lock = threading.Lock()
        self._restart_event = threading.Event()
        # Microphone input
        self._mic_device_name = None
        self._mic_stream = None
        self._mic_native_rate = 48000
        self._mic_native_channels = 1
        self._mic_restart_event = threading.Event()
        self._mic_buf = np.array([], dtype=np.float32)
        # Permission watchdog (see module docstring)
        self._zero_seconds = 0.0
        self.permission_hint_active = False

    # ── device resolution ──

    def _find_source_device(self):
        devices = _input_devices()
        if self._device_name:
            for idx, name, ch, rate in devices:
                if self._device_name in name:
                    return idx, name, ch, rate
            raise RuntimeError(f"Audio source device not found: {self._device_name}")
        for idx, name, ch, rate in devices:
            if is_blackhole(name):
                return idx, name, ch, rate
        raise RuntimeError(
            "No BlackHole device found. Install it (brew install blackhole-2ch) "
            "and route system output through a Multi-Output Device."
        )

    def _find_mic_device(self):
        sd = _sd()
        devices = _input_devices()
        if self._mic_device_name in ("__default__", "default"):
            try:
                default_idx = sd.default.device[0]
            except Exception:
                default_idx = None
            if default_idx is not None and default_idx >= 0:
                for idx, name, ch, rate in devices:
                    if idx == default_idx:
                        return idx, name, ch, rate
            raise RuntimeError("No default input device")
        for idx, name, ch, rate in devices:
            if name == self._mic_device_name:
                return idx, name, ch, rate
        raise RuntimeError(f"Mic device not found: {self._mic_device_name}")

    # ── streams ──

    def _open_stream(self):
        sd = _sd()
        idx, name, ch, rate = self._find_source_device()
        self._native_channels = ch
        self._native_rate = int(rate)
        self._current_device_name = name
        log.info(f"Capture source: {name}")
        log.info(
            f"Native: {self._native_rate}Hz, {self._native_channels}ch -> {self.sample_rate}Hz mono"
        )
        native_chunk = int(self._native_rate * self.chunk_duration)
        self._stream = sd.InputStream(
            device=idx,
            channels=self._native_channels,
            samplerate=self._native_rate,
            dtype="float32",
            blocksize=native_chunk,
        )
        self._stream.start()

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _open_mic_stream(self):
        sd = _sd()
        idx, name, ch, rate = self._find_mic_device()
        self._mic_native_channels = ch
        self._mic_native_rate = int(rate)
        native_chunk = int(self._mic_native_rate * self.chunk_duration)
        log.info(f"Mic device: {name} ({self._mic_native_rate}Hz, {ch}ch)")
        self._mic_stream = sd.InputStream(
            device=idx,
            channels=ch,
            samplerate=self._mic_native_rate,
            dtype="float32",
            blocksize=native_chunk,
        )
        self._mic_stream.start()

    def _close_mic_stream(self):
        if self._mic_stream:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

    # ── runtime device switching (same semantics as the Windows backend) ──

    def set_mic_device(self, device_name):
        if device_name == self._mic_device_name:
            return
        log.info(f"Mic device changed: {self._mic_device_name} -> {device_name}")
        self._mic_device_name = device_name
        if self._running:
            self._mic_restart_event.set()

    def set_device(self, device_name):
        if device_name == self._device_name:
            return
        log.info(f"Audio device changed: {self._device_name} -> {device_name}")
        self._device_name = device_name
        self._loopback_disabled = device_name == "__disabled__"
        if self._running:
            self._restart_event.set()

    # ── helpers ──

    def _resample_to_mono(self, frames, native_channels, native_rate):
        """(frames, ch) float32 ndarray -> mono float32 at self.sample_rate."""
        audio = np.asarray(frames, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
        if native_rate != self.sample_rate:
            ratio = self.sample_rate / native_rate
            n_out = int(len(audio) * ratio)
            indices = np.arange(n_out) / ratio
            indices = np.clip(indices, 0, len(audio) - 1)
            idx_floor = indices.astype(np.int64)
            idx_ceil = np.minimum(idx_floor + 1, len(audio) - 1)
            frac = (indices - idx_floor).astype(np.float32)
            audio = audio[idx_floor] * (1 - frac) + audio[idx_ceil] * frac
        return audio

    def _note_chunk_for_permission_hint(self, chunk):
        """Denied mic permission = endless exact zeros with no error. Track
        continuous zero time and raise the one-time hint past the threshold."""
        if self.permission_hint_active:
            return
        if chunk.size and not np.any(chunk):
            self._zero_seconds += self.chunk_duration
            if self._zero_seconds >= PERMISSION_HINT_SECONDS:
                self.permission_hint_active = True
                log.warning(
                    "Capture has produced only silence since the stream opened - "
                    "if audio is playing, check System Settings > Privacy & "
                    "Security > Microphone and allow your terminal, then restart."
                )
        else:
            self._zero_seconds = 0.0

    def _restart_stream(self):
        with self._lock:
            self._close_stream()
            if not self._loopback_disabled:
                self._open_stream()
            else:
                log.info("Capture disabled (mic-only mode)")
            self._zero_seconds = 0.0
            self.permission_hint_active = False
            if self._mic_device_name:
                self._close_mic_stream()
                try:
                    self._open_mic_stream()
                except Exception as e:
                    log.warning(f"Failed to re-open mic after restart: {e}")

    # ── read loop ──

    def _read_loop(self):
        while self._running:
            if self._restart_event.is_set():
                self._restart_event.clear()
                try:
                    self._restart_stream()
                    while not self.audio_queue.empty():
                        try:
                            self.audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    log.info(f"Audio capture restarted on: {self._current_device_name}")
                except Exception as e:
                    log.error(f"Restart after device change failed: {e}")
                    time.sleep(0.5)
                continue

            if self._mic_restart_event.is_set():
                self._mic_restart_event.clear()
                self._close_mic_stream()
                self._mic_buf = np.array([], dtype=np.float32)
                if self._mic_device_name:
                    try:
                        self._open_mic_stream()
                        log.info(f"Mic stream opened: {self._mic_device_name}")
                    except Exception as e:
                        log.error(f"Failed to open mic: {e}")
                else:
                    log.info("Mic disabled")

            loopback_audio = None
            if self._loopback_disabled:
                time.sleep(self.chunk_duration)
                n_samples = int(self.sample_rate * self.chunk_duration)
                loopback_audio = np.zeros(n_samples, dtype=np.float32)
            else:
                native_chunk = int(self._native_rate * self.chunk_duration)
                try:
                    frames = None
                    with self._lock:
                        if not self._stream:
                            time.sleep(0.005)
                            continue
                        frames, overflowed = self._stream.read(native_chunk)
                        if overflowed:
                            log.debug("Capture overflow (frames dropped upstream)")
                    if frames is not None:
                        loopback_audio = self._resample_to_mono(
                            frames, self._native_channels, self._native_rate
                        )
                        self._note_chunk_for_permission_hint(loopback_audio)
                except Exception as e:
                    if self._restart_event.is_set():
                        continue
                    log.warning(f"Read error (device may have changed): {e}")
                    try:
                        time.sleep(0.5)
                        self._restart_stream()
                        log.info("Stream restarted after read error")
                    except Exception as re:
                        log.error(f"Restart failed: {re}")
                        time.sleep(1)
                    continue

            if self._mic_stream:
                try:
                    avail = self._mic_stream.read_available
                    if avail > 0:
                        mic_frames, _ = self._mic_stream.read(avail)
                        mic_16k = self._resample_to_mono(
                            mic_frames, self._mic_native_channels, self._mic_native_rate
                        )
                        self._mic_buf = np.concatenate([self._mic_buf, mic_16k])
                except Exception as e:
                    log.warning(f"Mic read error: {e}")

            if loopback_audio is None:
                time.sleep(0.005)
                continue

            audio = loopback_audio
            mic_rms = None
            if len(self._mic_buf) > 0:
                n = len(loopback_audio)
                if len(self._mic_buf) >= n:
                    mic_chunk = self._mic_buf[:n]
                    self._mic_buf = self._mic_buf[n:]
                else:
                    mic_chunk = np.zeros(n, dtype=np.float32)
                    mic_chunk[: len(self._mic_buf)] = self._mic_buf
                    self._mic_buf = np.array([], dtype=np.float32)
                mic_rms = float(np.sqrt(np.mean(mic_chunk**2)))
                audio = loopback_audio + mic_chunk

            try:
                self.audio_queue.put_nowait((audio, mic_rms))
            except queue.Full:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait((audio, mic_rms))

    # ── lifecycle ──

    def start(self):
        self._loopback_disabled = self._device_name == "__disabled__"
        if not self._loopback_disabled:
            self._open_stream()
        else:
            log.info("Capture disabled (mic-only mode)")
        if self._mic_device_name:
            try:
                self._open_mic_stream()
            except Exception as e:
                log.warning(f"Failed to open mic on start: {e}")
        self._running = True
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()
        log.info("Audio capture started")

    def stop(self):
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=3)
        self._close_stream()
        self._close_mic_stream()
        log.info("Audio capture stopped")

    def get_audio(self, timeout=1.0):
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
