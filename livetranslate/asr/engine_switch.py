"""ASR engine switching: worker construction and the switch/restore UI flow.

Extracted verbatim from ASRSupervisor (Phase 3, Sprint 4). EngineSwitchMixin is
mixed into ASRSupervisor, so all attributes stay on the single supervisor
instance; methods here reach the owning LiveTranslateApp via ``self._app``
exactly as before the split.
"""

import importlib.util
import logging
import threading
from pathlib import Path

from livetranslate.model_manager import (
    ASR_DISPLAY_NAMES,
    MODELS_DIR,
    funasr_display_name,
    get_missing_models,
    is_asr_cached,
    local_faster_whisper_display_name,
    normalize_asr_engine_selection,
    resolve_custom_whisper_model,
)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QMessageBox

from livetranslate.asr.client import ASRClient
from livetranslate.ui.dialogs import ModelDownloadDialog, _ModelLoadDialog
from livetranslate.i18n import t

log = logging.getLogger("LiveTranslate")

# Engines whose worker imports torch. whisper (ctranslate2), remote-whisper
# and sensevoice-onnx all run without it.
_TORCH_ENGINES = {"funasr", "anime-whisper"}


class EngineSwitchMixin:
    """Engine-switch methods, mixed into ASRSupervisor."""

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
        # Torch-profile engines cannot start without torch; refuse the switch
        # up front (the current worker keeps running) instead of letting the
        # worker subprocess die on the import and going through restore.
        if engine_type in _TORCH_ENGINES and importlib.util.find_spec("torch") is None:
            log.warning(f"ASR engine {engine_type} requires torch, which is not installed")
            QMessageBox.warning(
                self._app._panel,
                t("error_title"),
                t("error_engine_needs_torch"),
            )
            return
        device = settings.get("asr_device", self._asr_device)
        if engine_type == "sensevoice-onnx":
            # This backend runs on CPU by design. Without pinning it here the
            # monitor bar would claim "[cuda:0]", and changing the GPU picker
            # would change the worker signature and trigger a pointless reload.
            device = "cpu"
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

        self._set_asr_status("loading")
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
            self._set_asr_status("")
            log.info(f"ASR worker ready: {engine_type} on {device}")
            return

        if restored_asr[0] is not None:
            _activate_asr(restored_asr[0], old_state)
            restored_name = old_state.get("display_name") or old_state.get("type")
            if self._app._overlay:
                self._app._overlay.update_asr_device(
                    f"{restored_name} [{old_state.get('device_label', old_state['device'])}]"
                )
            self._set_asr_status("")
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
        self._set_asr_status("unavailable")
