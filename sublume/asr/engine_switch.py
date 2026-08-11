"""ASR engine switching: worker construction and the switch/restore UI flow.

Extracted verbatim from ASRSupervisor (Phase 3, Sprint 4). EngineSwitchMixin is
mixed into ASRSupervisor, so all attributes stay on the single supervisor
instance; methods here reach the owning SublumeApp via ``self._app``
exactly as before the split.
"""

import importlib.util
import logging
import subprocess
import sys
import threading
from pathlib import Path

from sublume.paths import ROOT

from sublume.model_manager import (
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
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from sublume.asr.client import ASRClient
from sublume.ui.dialogs import ModelDownloadDialog, _ModelLoadDialog
from sublume.i18n import t

log = logging.getLogger("Sublume")

# Engines whose worker imports torch. whisper (ctranslate2), remote-whisper
# and sensevoice-onnx all run without it.
_TORCH_ENGINES = {"funasr", "anime-whisper"}


def _torch_install_hint_mode(root: Path = ROOT) -> str:
    """How to guide a torch-less user to the torch profile.

    A git-clone install on Windows ships scripts/install.ps1, which can add
    the torch profile incrementally ("installer"); the portable zip drops
    scripts/, so there the guidance stays manual pip commands ("pip"). The
    macOS installer is Lightweight-only for now — no Full mode to launch —
    so macOS always gets "pip" even though install.ps1 exists in a checkout."""
    if sys.platform == "darwin":
        return "pip"
    return "installer" if (root / "scripts" / "install.ps1").exists() else "pip"


def _launch_installer(root: Path = ROOT) -> None:
    """Open install.bat in its own console, preselecting the Full profile.

    Started via `cmd /c start` so the installer window outlives this process:
    the app must exit before torch is added, or in-use .pyd files could block
    package upgrades."""
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "Sublume Installer",
         str(root / "install.bat"), "-Profile", "full"],
        cwd=str(root),
    )


class EngineSwitchMixin:
    """Engine-switch methods, mixed into ASRSupervisor."""

    def _prompt_torch_install(self):
        """Tell the user how to get the torch profile, per install kind.

        Source installs get a "launch installer" button: install.bat -Profile
        full adds torch incrementally (settings and downloaded models are
        untouched), and the app closes so no loaded .pyd can block the
        install. Cancel keeps the current engine running. Portable installs
        keep the manual pip instructions."""
        parent = self._app._panel
        if _torch_install_hint_mode() == "pip":
            QMessageBox.warning(parent, t("error_title"), t("error_engine_needs_torch"))
            return

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("error_title"))
        box.setText(t("error_engine_needs_torch_installer"))
        launch_btn = box.addButton(
            t("btn_launch_installer"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not launch_btn:
            return

        log.info("Launching the installer for the torch profile; closing the app")
        _launch_installer()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _load_engine_client(self, config: dict):
        """Build the ASR backend for a worker config. Local engines run in an isolated
        worker subprocess (ASRClient); remote-whisper is a thin in-process HTTP client
        that needs no subprocess isolation (no native deps, no GPU model to load)."""
        if config.get("engine_type") == "remote-whisper":
            from sublume.asr.remote import RemoteASREngine

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

    def _resolve_ready_device_label(self, client, state: dict):
        """Replace the requested device label with what the worker actually loaded on.

        remote-whisper is an in-process shim with no ready_info at all (its label
        is the server URL), so the lookup stays defensive on both sides."""
        info = getattr(client, "ready_info", None)
        actual = (info or {}).get("device")
        if actual and state.get("device_label") != actual:
            log.info(
                f"ASR device label resolved: {state.get('device_label')} -> {actual}"
            )
            state["device_label"] = actual

    def _activate_asr(self, client, state: dict):
        """Publish a loaded worker as the active one. Resolving the label first is
        structural: _asr_restart_state below snapshots the state, so a later fix
        would not reach the copy an auto-restart or recycle reuses."""
        self._resolve_ready_device_label(client, state)
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
            self._mem_warned = False
            self._asr_generation += 1

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
            self._prompt_torch_install()
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

        if new_asr[0] is not None:
            self._activate_asr(new_asr[0], target_state)
            if self._app._overlay:
                self._app._overlay.update_asr_device(
                    f"{display_name} [{target_state['device_label']}]"
                )
            self._set_asr_status("")
            log.info(
                f"ASR worker ready: {engine_type} on {target_state['device_label']}"
            )
            return

        if restored_asr[0] is not None:
            self._activate_asr(restored_asr[0], old_state)
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
                f"Previous ASR worker restored: {old_state.get('type')} on "
                f"{old_state.get('device_label', old_state.get('device'))}"
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
