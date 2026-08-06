"""ASR worker supervision: lifecycle, engine switching and memory management.

Extracted verbatim from LiveTranslateApp (Phase 3 split). ASRSupervisor is a
host-object helper: it owns the ASR worker client and its bookkeeping, and
reaches back into the owning LiveTranslateApp (``self._app``) for the state that
the audio/translation pipeline also touches (overlay, panel, VAD, run flags).
"""

import gc
import logging
import threading
import time
from pathlib import Path

import numpy as np

from livetranslate.model_manager import (
    DEFAULT_FUNASR_MODEL,
    ASR_DISPLAY_NAMES,
    MODELS_DIR,
    funasr_display_name,
    funasr_supports_padding,
    get_missing_models,
    is_asr_cached,
    local_faster_whisper_display_name,
    normalize_asr_engine_selection,
    normalize_funasr_model_key,
    resolve_custom_whisper_model,
)

# torch must be imported before PyQt6 to avoid DLL conflicts on Windows
import torch

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QMessageBox

from livetranslate.asr.client import (
    ASRClient,
    ASRWorkerError,
    ASRWorkerExited,
    ASRWorkerTimeout,
)
from livetranslate.ui.dialogs import ModelDownloadDialog, _ModelLoadDialog
from livetranslate.i18n import t

log = logging.getLogger("LiveTranslate")

_NO_PENDING = object()


class ASRSupervisor:
    """Owns the ASR worker process/client and everything around keeping it alive."""

    def __init__(self, app):
        self._app = app
        config = app._config
        self._asr = None
        self._asr_signature = None
        self._asr_config = None
        self._asr_error_count = 0
        self._asr_device = config["asr"]["device"]
        self._whisper_model_size = config["asr"]["model_size"]
        self._funasr_model_key = normalize_funasr_model_key(
            config["asr"].get("funasr_model", DEFAULT_FUNASR_MODEL)
        )
        self._asr_lock = threading.RLock()
        # Settings changed from the Qt thread are deferred here and applied by the
        # ASR thread before its next transcribe, so the UI never blocks on the
        # worker pipe (which may be busy with an in-flight cross-process call).
        # Padding is keyed by engine_type because one settings save updates both
        # the funasr and whisper padding and they must not clobber each other.
        self._asr_pending_lock = threading.Lock()
        self._asr_pending_language = _NO_PENDING
        self._asr_pending_padding = {}
        # Auto-restart bookkeeping for a worker that dies mid-session. _asr_generation
        # is bumped on every (de)activation so a slow background (re)start can detect
        # that a newer engine switch superseded it and discard its stale worker.
        self._asr_restart_state = None
        self._asr_restart_count = 0
        self._asr_restart_max = 3
        self._asr_generation = 0
        self._asr_recycling = False
        # Proactively recycle the worker once its RSS grows this far past the
        # post-load baseline, to bound native-side (FunASR/CTranslate2) leaks that
        # accumulate in the long-lived worker process.
        self._asr_worker_baseline_mb = None
        self._asr_recycle_delta_mb = 2048
        self._mem_last_mb = app._mem_baseline_mb
        # Memory ceiling: warn once when combined RSS (main + ASR worker) exceeds
        # threshold. The ASR backend now runs in a worker process and keeps
        # native-side workspaces/caches that Python GC cannot always reclaim, so the
        # ceiling must include the worker's RSS (see _mem_snapshot).
        self._mem_threshold_mb = 4096
        self._mem_warned = False
        self._mem_warning_callback = None

    def _mark_asr_unavailable(self, reason: str, client=None):
        with self._asr_lock:
            current = client or self._asr
            if client is not None and self._asr is not client:
                return
            self._app._asr_ready = False
            self._asr = None
            self._app._asr_type = None
            self._asr_signature = None
            self._asr_config = None
            self._asr_error_count = 0
            self._asr_restart_state = None
            self._asr_worker_baseline_mb = None
            self._asr_generation += 1
        if current is not None:
            try:
                current.shutdown()
            except Exception:
                try:
                    current.terminate()
                except Exception:
                    pass
        log.warning(f"ASR worker unavailable: {reason}")
        if self._app._overlay:
            self._app._overlay.update_asr_device("ASR unavailable")

    def _shutdown_asr_worker(self):
        with self._asr_lock:
            client = self._asr
            self._asr = None
            self._app._asr_ready = False
            self._app._asr_type = None
            self._asr_signature = None
            self._asr_config = None
            self._asr_error_count = 0
            self._asr_restart_state = None
            self._asr_worker_baseline_mb = None
            self._asr_generation += 1
        if client is not None:
            log.info(f"Shutting down ASR worker: pid={client.pid}")
            client.shutdown()

    def _set_asr_language(self, language: str):
        with self._asr_pending_lock:
            self._asr_pending_language = language

    def _set_asr_padding(self, engine_type: str, pad_seconds):
        with self._asr_pending_lock:
            self._asr_pending_padding[engine_type] = pad_seconds

    def _apply_pending_asr_settings(self, client, asr_type, funasr_key):
        """Apply deferred language/padding on the ASR thread, just before a transcribe.
        A pending value is cleared only once delivered; worker-death exceptions
        propagate with the pending intact so the restarted worker re-applies it. The
        applied value is written back into the restart config so an auto-restart or
        recycle does not revert a runtime override to the engine-switch-time value."""
        with self._asr_pending_lock:
            language = self._asr_pending_language
            pad_seconds = self._asr_pending_padding.get(asr_type, _NO_PENDING)
        if language is not _NO_PENDING:
            try:
                client.set_language(language)
            except ASRWorkerError as exc:
                log.warning(f"ASR language update failed: {exc}")
            self._update_restart_config(language=language)
            self._clear_pending_language(language)
        if pad_seconds is not _NO_PENDING:
            if not (asr_type == "funasr" and not funasr_supports_padding(funasr_key)):
                try:
                    client.set_input_padding(pad_seconds)
                except ASRWorkerError as exc:
                    log.warning(f"ASR padding update failed: {exc}")
                self._update_restart_config(pad_seconds=pad_seconds)
            self._clear_pending_padding(asr_type, pad_seconds)

    def _clear_pending_language(self, language):
        with self._asr_pending_lock:
            if self._asr_pending_language is language:
                self._asr_pending_language = _NO_PENDING

    def _clear_pending_padding(self, asr_type, pad_seconds):
        with self._asr_pending_lock:
            if self._asr_pending_padding.get(asr_type) == pad_seconds:
                del self._asr_pending_padding[asr_type]

    def _update_restart_config(self, **kwargs):
        with self._asr_lock:
            if self._asr_restart_state and self._asr_restart_state.get("config"):
                self._asr_restart_state["config"].update(kwargs)

    def _load_engine_client(self, config: dict):
        """Build the ASR backend for a worker config. Local engines run in an isolated
        worker subprocess (ASRClient); remote-whisper is a thin in-process HTTP client
        that needs no subprocess isolation (no native deps, no GPU model to load)."""
        if config.get("engine_type") == "remote-whisper":
            from livetranslate.asr.remote import RemoteASREngine

            url = config.get("remote_asr_url") or "http://127.0.0.1:8765"
            engine = RemoteASREngine(server_url=url)
            language = config.get("language")
            if language:
                engine.set_language(language)
            return engine
        return self._load_asr_client(config)

    def _load_asr_client(self, worker_config: dict) -> ASRClient:
        # request_timeout bounds how long a hung worker can stall the realtime path
        # before it is killed and auto-restarted. VAD caps segments at a few seconds,
        # so 60s is generous for a healthy transcribe yet far below the old 120s.
        client = ASRClient(worker_config, request_timeout=60.0)
        try:
            client.start()
            client.wait_ready()
            return client
        except Exception:
            client.shutdown()
            raise

    def _switch_asr_engine(self, engine_type: str):
        settings = self._app._panel.get_settings() if self._app._panel else {}
        engine_type, funasr_model = normalize_asr_engine_selection(
            engine_type, settings.get("funasr_model", self._funasr_model_key)
        )
        device = settings.get("asr_device", self._asr_device)
        # This fork downloads exclusively from HuggingFace; legacy "ms" is treated as "hf"
        hub = "hf"
        download_proxy = "system"
        if self._app._panel:
            download_proxy = settings.get("download_proxy", "system")

        model_size = self._app._config["asr"]["model_size"]
        if self._app._panel:
            model_size = settings.get("whisper_model_size", model_size)
        model_path = None
        cache_model_key = model_size
        if engine_type == "whisper":
            model_path = resolve_custom_whisper_model(model_size)
            if model_path:
                cache_model_key = model_path
        elif engine_type == "funasr":
            cache_model_key = funasr_model

        remote_asr_url = settings.get(
            "remote_asr_url",
            self._app._config["asr"].get("remote_asr_url", "http://127.0.0.1:8765"),
        )

        compute = self._app._config["asr"]["compute_type"]
        if engine_type == "whisper":
            signature_model = cache_model_key
        elif engine_type == "funasr":
            signature_model = funasr_model
        elif engine_type == "remote-whisper":
            # URL is part of the identity so editing it triggers a reconnect.
            signature_model = remote_asr_url
        else:
            signature_model = engine_type
        signature = (engine_type, signature_model, device, hub, compute)

        with self._asr_lock:
            current_asr = self._asr
            current_ready = (
                self._app._asr_ready
                and current_asr is not None
                and current_asr.status == "ready"
            )
            if current_ready and self._asr_signature == signature:
                return
            if not current_ready:
                self._app._asr_ready = False

        log.info(f"Switching ASR worker: {self._app._asr_type} -> {engine_type}")
        # Reset interim state for the engine boundary. The active worker is
        # stopped before the target worker starts loading.
        self._app._interim_active = False
        self._app._interim_pending = ""
        self._app._last_interim_samples = 0
        self._app._last_interim_check_time = 0.0
        self._app._interim_committed_tail = ""
        self._app._vad.flush()
        self._app._vad._reset()

        cached = is_asr_cached(engine_type, cache_model_key, hub)
        display_name = ASR_DISPLAY_NAMES.get(engine_type, engine_type)
        if engine_type == "whisper":
            display_model = (
                local_faster_whisper_display_name(model_size)
                if model_path
                else model_size
            ) or Path(model_size).name
            display_name = f"Whisper {display_model}"
        elif engine_type == "funasr":
            display_name = funasr_display_name(funasr_model)

        parent = (
            self._app._panel if self._app._panel and self._app._panel.isVisible() else self._app._overlay
        )

        worker_config = {
            "engine_type": engine_type,
            "funasr_model": funasr_model,
            "model_size": cache_model_key,
            "device": device,
            "compute_type": compute,
            "hub": hub,
            "language": settings.get(
                "asr_language", self._app._config["asr"].get("language", "auto")
            ),
            "pad_seconds": (
                settings.get(
                    "sensevoice_pad_seconds",
                    self._app._config["asr"].get("sensevoice_pad_seconds", 0.5),
                )
                if engine_type == "funasr"
                else settings.get(
                    "whisper_pad_seconds",
                    self._app._config["asr"].get("whisper_pad_seconds", 0.5),
                )
                if engine_type == "whisper"
                else None
            ),
            "download_root": str((MODELS_DIR / "huggingface" / "hub").resolve()),
            "display_name": display_name,
            "remote_asr_url": remote_asr_url,
        }
        target_state = {
            "type": engine_type,
            "signature": signature,
            "device": device,
            "funasr_model_key": funasr_model
            if engine_type == "funasr"
            else self._funasr_model_key,
            "whisper_model_size": model_size
            if engine_type == "whisper"
            else self._whisper_model_size,
            "config": worker_config,
            "display_name": display_name,
            "device_label": (
                remote_asr_url if engine_type == "remote-whisper" else device
            ),
        }

        if not cached:
            missing = get_missing_models(engine_type, cache_model_key, hub)
            missing = [m for m in missing if m["type"] != "silero-vad"]
            if missing:
                dlg = ModelDownloadDialog(
                    missing, hub=hub, proxy=download_proxy, parent=parent
                )
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    log.info(f"Download cancelled/failed: {engine_type}")
                    with self._asr_lock:
                        self._app._asr_ready = (
                            self._asr is not None and self._asr.status == "ready"
                        )
                    return

        with self._asr_lock:
            old_asr = self._asr
            old_config = dict(self._asr_config) if self._asr_config else None
            old_state = {
                "type": self._app._asr_type,
                "signature": self._asr_signature,
                "device": self._asr_device,
                "funasr_model_key": self._funasr_model_key,
                "whisper_model_size": self._whisper_model_size,
                "config": old_config,
                "display_name": (old_config or {}).get("display_name"),
                "device_label": (
                    (old_config or {}).get("remote_asr_url")
                    if self._app._asr_type == "remote-whisper"
                    else self._asr_device
                ),
            }
            self._asr = None
            self._app._asr_ready = False
            self._app._asr_type = None
            self._asr_signature = None
            self._asr_config = None
            self._asr_error_count = 0
            self._asr_restart_state = None
            self._asr_worker_baseline_mb = None
            self._asr_generation += 1

        dlg = _ModelLoadDialog(
            t("loading_model").format(name=display_name), parent=parent
        )

        new_asr = [None]
        restored_asr = [None]
        load_error = [None]
        restore_error = [None]

        def _load():
            if old_asr is not None:
                log.info(f"Stopping old ASR worker before switch: pid={old_asr.pid}")
                old_asr.shutdown()
                self._release_memory_caches()
            try:
                new_asr[0] = self._load_engine_client(worker_config)
            except Exception as e:
                load_error[0] = str(e)
                # A remote server that is simply down is an expected, user-actionable
                # condition, not a bug, so skip the noisy traceback for it.
                expected = isinstance(e, ConnectionError)
                log.error(
                    f"Failed to load ASR worker: {e}", exc_info=not expected
                )
                if old_config:
                    try:
                        log.info("Restoring previous ASR worker after switch failure")
                        restored_asr[0] = self._load_engine_client(old_config)
                    except Exception as restore_exc:
                        restore_error[0] = str(restore_exc)
                        log.error(
                            f"Failed to restore previous ASR worker: {restore_exc}",
                            exc_info=True,
                        )

        thread = threading.Thread(target=_load, daemon=True)
        thread.start()

        poll_timer = QTimer()

        def _check():
            if not thread.is_alive():
                poll_timer.stop()
                dlg.accept()

        poll_timer.setInterval(100)
        poll_timer.timeout.connect(_check)
        poll_timer.start()

        dlg.exec()
        poll_timer.stop()

        def _activate_asr(client, state):
            with self._asr_lock:
                self._asr = client
                self._app._asr_type = state["type"]
                self._asr_signature = state["signature"]
                self._asr_device = state["device"]
                self._asr_config = dict(state["config"]) if state["config"] else None
                self._funasr_model_key = state["funasr_model_key"]
                self._whisper_model_size = state["whisper_model_size"]
                self._app._asr_ready = True
                self._asr_error_count = 0
                self._asr_restart_state = dict(state)
                self._asr_restart_count = 0
                self._asr_worker_baseline_mb = None
                self._asr_generation += 1

        if new_asr[0] is not None:
            _activate_asr(new_asr[0], target_state)
            if self._app._overlay:
                self._app._overlay.update_asr_device(
                    f"{display_name} [{target_state['device_label']}]"
                )
            log.info(f"ASR worker ready: {engine_type} on {device}")
            return

        if restored_asr[0] is not None:
            _activate_asr(restored_asr[0], old_state)
            restored_name = old_state.get("display_name") or old_state.get("type")
            if self._app._overlay:
                self._app._overlay.update_asr_device(
                    f"{restored_name} [{old_state.get('device_label', old_state['device'])}]"
                )
            QMessageBox.warning(
                parent,
                t("error_title"),
                t("error_load_asr").format(
                    error=(
                        f"{load_error[0] or 'unknown error'}\n"
                        f"{t('asr_restore_succeeded')}"
                    )
                ),
            )
            log.info(
                f"Previous ASR worker restored: "
                f"{old_state.get('type')} on {old_state.get('device')}"
            )
            return

        error = load_error[0] or "unknown error"
        if restore_error[0]:
            error = (
                f"{error}\n"
                f"{t('asr_restore_failed').format(error=restore_error[0])}"
            )
        QMessageBox.warning(
            parent,
            t("error_title"),
            t("error_load_asr").format(error=error),
        )

        if self._app._overlay:
            self._app._overlay.update_asr_device("ASR unavailable")

    def _mem_snapshot(self) -> dict:
        rss_mb = self._app._mem_proc.memory_info().rss / 1024 / 1024
        # The ASR model (and its native-side leak) lives in the worker process now,
        # so sample its RSS too; the main process holds only VAD + Qt.
        worker_rss_mb = 0.0
        client = self._asr
        if client is not None and client.pid is not None:
            try:
                import psutil

                worker_rss_mb = (
                    psutil.Process(client.pid).memory_info().rss / 1024 / 1024
                )
            except Exception:
                worker_rss_mb = 0.0
        gpu_alloc_mb = 0.0
        gpu_reserved_mb = 0.0
        try:
            if torch.cuda.is_available():
                gpu_alloc_mb = torch.cuda.memory_allocated() / 1024 / 1024
                gpu_reserved_mb = torch.cuda.memory_reserved() / 1024 / 1024
        except Exception:
            pass
        msgs = len(self._app._overlay._messages) if self._app._overlay else 0
        vad_buf = len(self._app._vad._speech_buffer)
        return {
            "rss": rss_mb,
            "worker_rss": worker_rss_mb,
            "total_rss": rss_mb + worker_rss_mb,
            "gpu_alloc": gpu_alloc_mb,
            "gpu_reserved": gpu_reserved_mb,
            "msgs": msgs,
            "vad_buf": vad_buf,
        }

    def _log_mem_after_asr(self, kind: str, audio_seconds: float, asr_ms: float):
        self._app._mem_asr_call_count += 1
        snap = self._mem_snapshot()
        delta = snap["rss"] - self._mem_last_mb
        total_delta = snap["rss"] - self._app._mem_baseline_mb
        self._mem_last_mb = snap["rss"]
        log.info(
            f"MEM[asr#{self._app._mem_asr_call_count}:{kind}] RSS={snap['rss']:.1f}MB "
            f"(Δ{delta:+.2f} since last, {total_delta:+.1f} since start) "
            f"worker_rss={snap['worker_rss']:.0f}MB "
            f"GPU(main alloc/reserved)={snap['gpu_alloc']:.0f}/{snap['gpu_reserved']:.0f}MB "
            f"audio={audio_seconds:.1f}s asr={asr_ms:.0f}ms "
            f"outputs={self._app._asr_count} msgs={snap['msgs']} vad_buf={snap['vad_buf']}"
        )
        self._check_memory_threshold(snap["total_rss"])

    def _release_memory_caches(self):
        gc.collect()
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _run_asr(self, audio: np.ndarray, kind: str, **kwargs):
        audio_seconds = len(audio) / 16000
        asr_start = time.perf_counter()
        # Snapshot the active client under the lock, then release it: the blocking
        # cross-process transcribe must not hold _asr_lock, or a slow/hung worker
        # would freeze the Qt thread on every settings change. ASRClient serializes
        # its own pipe access, and only this (single) ASR thread calls transcribe.
        with self._asr_lock:
            if not self._app._asr_ready or self._asr is None:
                return None, 0.0
            client = self._asr
            asr_type = self._app._asr_type
            funasr_key = self._funasr_model_key
        try:
            self._apply_pending_asr_settings(client, asr_type, funasr_key)
            result = client.transcribe(audio, **kwargs)
        except (ASRWorkerExited, ASRWorkerTimeout) as exc:
            asr_ms = (time.perf_counter() - asr_start) * 1000
            self._log_mem_after_asr(f"{kind}:error", audio_seconds, asr_ms)
            self._recover_asr_worker(client, str(exc))
            raise
        except ASRWorkerError as exc:
            asr_ms = (time.perf_counter() - asr_start) * 1000
            self._log_mem_after_asr(f"{kind}:error", audio_seconds, asr_ms)
            fatal = False
            with self._asr_lock:
                if self._asr is client:
                    self._asr_error_count += 1
                    fatal = not exc.recoverable or self._asr_error_count >= 3
            if fatal:
                self._mark_asr_unavailable(str(exc), client)
            raise
        except Exception:
            asr_ms = (time.perf_counter() - asr_start) * 1000
            self._log_mem_after_asr(f"{kind}:error", audio_seconds, asr_ms)
            raise
        with self._asr_lock:
            if self._asr is client:
                self._asr_error_count = 0
                self._asr_restart_count = 0
        asr_ms = (time.perf_counter() - asr_start) * 1000
        self._log_mem_after_asr(kind, audio_seconds, asr_ms)
        return result, asr_ms

    def _start_worker_from_state(self, state: dict, expected_gen: int) -> bool:
        """Load a worker from a saved state and activate it only if no newer engine
        switch happened in the meantime (generation guard). Runs on the ASR thread;
        the load is intentionally done outside _asr_lock. Returns True on activation."""
        try:
            client = self._load_engine_client(state["config"])
        except Exception as e:
            log.error(f"ASR worker (re)start failed: {e}", exc_info=True)
            return False
        stale = None
        with self._asr_lock:
            if self._asr_generation != expected_gen or not self._app._running:
                stale = client
            else:
                self._asr = client
                self._app._asr_type = state["type"]
                self._asr_signature = state["signature"]
                self._asr_device = state["device"]
                self._asr_config = dict(state["config"]) if state["config"] else None
                self._funasr_model_key = state["funasr_model_key"]
                self._whisper_model_size = state["whisper_model_size"]
                self._app._asr_ready = True
                self._asr_error_count = 0
                self._asr_restart_state = dict(state)
                self._asr_worker_baseline_mb = None
                self._asr_generation += 1
        if stale is not None:
            log.info("Discarding superseded ASR worker (newer switch won the race)")
            try:
                stale.shutdown()
            except Exception:
                pass
            return False
        name = state.get("display_name") or state.get("type")
        if self._app._overlay:
            self._app._overlay.update_asr_device(
                f"{name} [{state.get('device_label', state['device'])}]"
            )
        return True

    def _recover_asr_worker(self, dead_client, reason: str):
        """Auto-restart a worker that died mid-session. Without this, a single crash
        or transcribe timeout would leave ASR permanently silent for the session."""
        with self._asr_lock:
            if self._asr is not dead_client:
                return  # an engine switch already replaced/cleared it
            state = dict(self._asr_restart_state) if self._asr_restart_state else None
            attempt = self._asr_restart_count + 1
            give_up = (
                state is None
                or not state.get("config")
                or attempt > self._asr_restart_max
            )
            self._asr_restart_count = attempt
            self._asr = None
            self._app._asr_ready = False
            self._app._asr_type = None
            self._asr_signature = None
            self._asr_config = None
            self._asr_error_count = 0
            self._asr_worker_baseline_mb = None
            self._asr_generation += 1
            gen = self._asr_generation
        try:
            dead_client.shutdown()
        except Exception:
            try:
                dead_client.terminate()
            except Exception:
                pass
        if not self._app._running:
            return  # shutting down; do not spawn a replacement worker
        if give_up:
            log.error(
                f"ASR worker died and auto-restart gave up after "
                f"{self._asr_restart_max} attempts: {reason}"
            )
            if self._app._overlay:
                self._app._overlay.update_asr_device("ASR unavailable")
            return
        log.warning(
            f"ASR worker died ({reason}); auto-restart attempt "
            f"{attempt}/{self._asr_restart_max}"
        )
        self._release_memory_caches()
        if self._start_worker_from_state(state, gen):
            log.info(
                f"ASR worker auto-restarted: {state.get('type')} on "
                f"{state.get('device')}"
            )
        elif self._asr is None and self._app._overlay:
            self._app._overlay.update_asr_device("ASR unavailable")

    def _maybe_recycle_asr_worker(self):
        """Recycle the worker once its RSS grows well past the post-load baseline, to
        bound native-side leaks that accumulate in the long-lived worker process.
        Called from the ASR thread between segments so the reload gap costs no audio
        beyond what arrives during it."""
        if not self._app._running:
            return
        with self._asr_lock:
            client = self._asr
            if not self._app._asr_ready or client is None or self._asr_recycling:
                return
            state = dict(self._asr_restart_state) if self._asr_restart_state else None
        if state is None or not state.get("config") or client.pid is None:
            return
        try:
            import psutil

            rss = psutil.Process(client.pid).memory_info().rss / 1024 / 1024
        except Exception:
            return
        if self._asr_worker_baseline_mb is None:
            self._asr_worker_baseline_mb = rss
            return
        if rss < self._asr_worker_baseline_mb + self._asr_recycle_delta_mb:
            return
        log.warning(
            f"ASR worker RSS={rss:.0f}MB grew "
            f"{rss - self._asr_worker_baseline_mb:.0f}MB over baseline; recycling"
        )
        self._recycle_asr_worker(client, state)

    def _recycle_asr_worker(self, old_client, state: dict):
        # Graceful stop-then-start (no VRAM doubling). The generation guard makes a
        # concurrent engine switch win over this recycle.
        with self._asr_lock:
            if self._asr is not old_client:
                return
            self._asr = None
            self._app._asr_ready = False
            self._asr_recycling = True
            self._asr_worker_baseline_mb = None
            self._asr_generation += 1
            gen = self._asr_generation
        try:
            old_client.shutdown()
        except Exception:
            try:
                old_client.terminate()
            except Exception:
                pass
        self._release_memory_caches()
        if not self._app._running:
            with self._asr_lock:
                self._asr_recycling = False
            return
        try:
            started = self._start_worker_from_state(state, gen)
        finally:
            with self._asr_lock:
                self._asr_recycling = False
        if started:
            log.info(f"ASR worker recycled: {state.get('type')} on {state.get('device')}")
        else:
            log.error("ASR worker recycle failed to restart")
            if self._asr is None and self._app._overlay:
                self._app._overlay.update_asr_device("ASR unavailable")

    def _check_memory_threshold(self, rss_mb: float):
        if self._mem_warned or rss_mb < self._mem_threshold_mb:
            return
        self._mem_warned = True
        log.warning(
            f"Memory ceiling reached: combined RSS (main+worker)={rss_mb:.0f}MB "
            f"(threshold {self._mem_threshold_mb}MB). "
            f"Recommend restarting LiveTranslate to free C-side allocator caches."
        )
        if self._mem_warning_callback is not None:
            try:
                self._mem_warning_callback(rss_mb)
            except Exception as e:
                log.warning(f"Memory warning callback failed: {e}")

    def set_memory_warning_callback(self, callback):
        self._mem_warning_callback = callback

    def _log_mem_periodic(self):
        snap = self._mem_snapshot()
        total_delta = snap["rss"] - self._app._mem_baseline_mb
        log.info(
            f"MEM[tick] RSS={snap['rss']:.1f}MB ({total_delta:+.1f} since start) "
            f"worker_rss={snap['worker_rss']:.0f}MB "
            f"GPU(main alloc/reserved)={snap['gpu_alloc']:.0f}/{snap['gpu_reserved']:.0f}MB "
            f"msgs={snap['msgs']} asr_calls={self._app._mem_asr_call_count} "
            f"asr_count={self._app._asr_count} tl_count={self._app._translate_count}"
        )
        self._check_memory_threshold(snap["total_rss"])
