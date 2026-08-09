"""
Sublume - Phase 0 Prototype
Real-time audio translation using WASAPI loopback + faster-whisper + LLM.

This module owns SublumeApp (pipeline wiring + runtime state); the
startup flow (QApplication, wizard, tray) lives in sublume/app.py.
"""

import logging
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
import time
import numpy as np

from sublume.model_manager import apply_cache_env

# Set cache env BEFORE importing torch so TORCH_HOME is respected
apply_cache_env()

import os

# torch must be imported before PyQt6 to avoid DLL conflicts on Windows.
# Optional since the torch-free profile: when torch is absent there is no
# DLL order to protect; when present, this import must keep its position.
try:
    import torch  # noqa: F401
except ImportError:
    pass

from sublume.core.audio_capture import AudioCapture
from sublume.core.pipeline import TranslationPipeline
from sublume.core.vad_processor import VADProcessor
from sublume.asr.supervisor import ASRSupervisor
from sublume.translation.translator import Translator, RepetitionError
from sublume.core.transcript_writer import TranscriptWriter

from PyQt6.QtCore import QTimer

from sublume.ui.overlay.subtitle_overlay import SubtitleOverlay
from sublume.ui.overlay.subtitle_window import SubtitleWindow
from sublume.ui.control_panel import ControlPanel
from sublume.i18n import t

log = logging.getLogger("Sublume")


class SublumeApp:
    def __init__(self, config):
        self._config = config
        self._running = False
        self._paused = False
        self._asr_ready = False  # True when ASR model is loaded

        self._audio = AudioCapture(
            device=config["audio"].get("device"),
            sample_rate=config["audio"]["sample_rate"],
            chunk_duration=config["audio"]["chunk_duration"],
        )
        self._vad = VADProcessor(
            sample_rate=config["audio"]["sample_rate"],
            threshold=config["asr"]["vad_threshold"],
            min_speech_duration=config["asr"]["min_speech_duration"],
            max_speech_duration=config["asr"]["max_speech_duration"],
            chunk_duration=config["audio"]["chunk_duration"],
        )
        # The ASR worker client and its lifecycle state are owned by ASRSupervisor
        # (created below); _asr_type stays here because the settings handler reads it.
        self._asr_type = None
        self._target_language = config["translation"]["target_language"]
        self._translator = Translator(
            api_base=config["translation"]["api_base"],
            api_key=config["translation"]["api_key"],
            model=config["translation"]["model"],
            target_language=self._target_language,
            max_tokens=config["translation"]["max_tokens"],
            temperature=config["translation"]["temperature"],
            streaming=config["translation"]["streaming"],
            system_prompt=config["translation"].get("system_prompt"),
        )
        self._translator.set_context_turns(
            config["translation"].get("context_window", 0)
        )
        self._overlay = None
        self._subwin = None
        self._panel = None
        self._capture_thread = None
        self._asr_thread = None
        self._asr_queue = queue.Queue(maxsize=16)
        self._tl_executor = ThreadPoolExecutor(max_workers=8)

        from sublume.paths import ROOT

        self._transcript = TranscriptWriter(ROOT / "transcripts")

        # Memory diagnostic state
        import psutil
        self._mem_proc = psutil.Process(os.getpid())
        self._mem_baseline_mb = self._mem_proc.memory_info().rss / 1024 / 1024
        self._mem_asr_call_count = 0
        self._mem_periodic_timer = None

        # Created last: the supervisor reads _config and _mem_baseline_mb at init.
        self._asr_sup = ASRSupervisor(self)
        # Audio -> VAD -> ASR -> translation block; owns the capture/ASR threads.
        self._pipeline = TranslationPipeline(self)

        self._asr_count = 0
        self._translate_count = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._input_price = 0.0
        self._output_price = 0.0

        # Incremental ASR state
        self._incremental_enabled = False
        self._interim_interval = 2.0
        self._interim_pending = ""
        self._interim_active = False
        self._last_interim_samples = 0
        self._last_interim_check_time = 0.0
        self._interim_committed_tail = ""

    def set_overlay(self, overlay: SubtitleOverlay):
        self._overlay = overlay

    def set_subtitle_window(self, subwin: SubtitleWindow):
        self._subwin = subwin

    def set_panel(self, panel: ControlPanel):
        self._panel = panel
        panel.settings_changed.connect(self._on_settings_changed)
        panel.model_changed.connect(self._on_model_changed)
        panel.models_list_changed.connect(self._on_models_list_changed)

    def _on_models_list_changed(self, models: list, active_idx: int):
        if self._overlay:
            self._overlay.set_models(models, active_idx)

    def _on_settings_changed(self, settings):
        self._vad.update_settings(settings)
        if "hf_token" in settings:
            from sublume.model_manager import apply_hf_token

            apply_hf_token(settings["hf_token"])
        if "style" in settings and self._overlay:
            self._overlay.apply_style(settings["style"])
        if "overlay_template" in settings and self._overlay:
            self._overlay.set_template(settings["overlay_template"])
        if "asr_language" in settings:
            self._set_asr_language(settings["asr_language"])
        if "sensevoice_pad_seconds" in settings:
            self._set_asr_padding("funasr", settings["sensevoice_pad_seconds"])
        if "whisper_pad_seconds" in settings:
            self._set_asr_padding("whisper", settings["whisper_pad_seconds"])
        if any(
            key in settings
            for key in (
                "asr_engine",
                "asr_device",
                "whisper_model_size",
                "funasr_model",
                "hub",
            )
        ):
            self._switch_asr_engine(
                settings.get(
                    "asr_engine",
                    self._asr_type or self._config["asr"].get("asr_engine", "funasr"),
                )
            )
        if "audio_device" in settings:
            old_device = self._audio._device_name
            self._audio.set_device(settings["audio_device"])
            if old_device != settings.get("audio_device"):
                self._vad.flush()
                self._vad._reset()
                if self._overlay:
                    self._overlay.update_monitor(0.0, 0.0)
        if "mic_device" in settings:
            self._audio.set_mic_device(settings["mic_device"])
        if "incremental_asr" in settings:
            self._incremental_enabled = settings["incremental_asr"]
        if "interim_interval" in settings:
            self._interim_interval = settings["interim_interval"]
        if "target_language" in settings:
            self._target_language = settings["target_language"]
            if self._overlay:
                self._overlay.set_target_language(self._target_language)
        if "timeout" in settings and self._translator:
            self._translator.set_timeout(settings["timeout"])
        if "auto_save_transcript" in settings:
            self._transcript.set_enabled(settings["auto_save_transcript"])

    # ── ASR supervision ──
    # Worker lifecycle, engine switching and memory management live in
    # ASRSupervisor (sublume/asr/supervisor.py). These delegators keep the
    # call sites in the pipeline threads, tray menu and panel wiring unchanged.

    def _set_asr_language(self, language: str):
        return self._asr_sup._set_asr_language(language)

    def _set_asr_padding(self, engine_type: str, pad_seconds):
        return self._asr_sup._set_asr_padding(engine_type, pad_seconds)

    def _switch_asr_engine(self, engine_type: str):
        return self._asr_sup._switch_asr_engine(engine_type)

    def _shutdown_asr_worker(self):
        return self._asr_sup._shutdown_asr_worker()

    def _run_asr(self, audio: np.ndarray, kind: str, **kwargs):
        return self._asr_sup._run_asr(audio, kind, **kwargs)

    def _maybe_recycle_asr_worker(self):
        return self._asr_sup._maybe_recycle_asr_worker()

    def _mem_snapshot(self) -> dict:
        return self._asr_sup._mem_snapshot()

    def _log_mem_periodic(self):
        return self._asr_sup._log_mem_periodic()

    def set_memory_warning_callback(self, callback):
        return self._asr_sup.set_memory_warning_callback(callback)

    def _on_target_language_changed(self, lang: str):
        self._target_language = lang
        log.info(f"Target language: {lang}")
        if self._translator:
            self._translator.set_target_language(lang)
        if self._panel:
            settings = self._panel.get_settings()
            settings["target_language"] = lang
            from sublume.ui.control_panel import _save_settings

            _save_settings(settings)

    def _on_model_changed(self, model_config: dict):
        log.info(
            f"Switching translator: {model_config['name']} ({model_config['model']})"
        )
        prompt = None
        if self._panel:
            prompt = self._panel.get_settings().get("system_prompt")
        if not prompt:
            prompt = self._config["translation"].get("system_prompt")
        timeout = 10
        if self._panel:
            timeout = self._panel.get_settings().get("timeout", 10)
        self._translator = Translator(
            api_base=model_config["api_base"],
            api_key=model_config["api_key"],
            model=model_config["model"],
            target_language=self._target_language,
            max_tokens=self._config["translation"]["max_tokens"],
            temperature=self._config["translation"]["temperature"],
            streaming=model_config.get("streaming", True),
            system_prompt=prompt,
            proxy=model_config.get("proxy", "none"),
            no_system_role=model_config.get("no_system_role", False),
            no_think=model_config.get("no_think", True),
            json_response=model_config.get("json_response", False),
            timeout=timeout,
            overrides=model_config.get("overrides"),
            extra_body=model_config.get("extra_body"),
        )
        context_turns = model_config.get(
            "context_turns", self._config["translation"].get("context_window", 0)
        )
        self._translator.set_context_turns(context_turns)
        self._input_price = model_config.get("input_price", 0)
        self._output_price = model_config.get("output_price", 0)

    def _compute_cost(self):
        if self._input_price > 0 or self._output_price > 0:
            return (self._total_prompt_tokens * self._input_price +
                    self._total_completion_tokens * self._output_price) / 1_000_000
        return 0.0

    def _translate_async(self, msg_id, text, source_lang, extra_langs=None):
        """Translate text and update UI with streaming display."""
        try:
            tl_start = time.perf_counter()
            translated = None
            for partial in self._translator.translate_iter(text, source_lang):
                translated = partial
                if self._overlay:
                    self._overlay.update_streaming(msg_id, partial)
            tl_ms = (time.perf_counter() - tl_start) * 1000
            self._translate_count += 1
            pt, ct = self._translator.last_usage
            self._total_prompt_tokens += pt
            self._total_completion_tokens += ct
            cost = self._compute_cost()
            log.info(f"Translate ({tl_ms:.0f}ms): {translated}")
            if translated:
                self._transcript.write_translation(msg_id, translated)
            else:
                self._transcript.finalize_no_translation(msg_id)
            if self._overlay:
                self._overlay.update_translation(msg_id, translated, tl_ms)
                self._overlay.update_stats(
                    self._asr_count,
                    self._translate_count,
                    self._total_prompt_tokens,
                    self._total_completion_tokens,
                    cost,
                )
            if self._subwin and self._subwin.isVisible() and translated:
                tl_dict = {self._target_language: translated}
                if extra_langs:
                    self._translate_extra_langs(text, source_lang, extra_langs, tl_dict)
                self._subwin.update_text(text, tl_dict)
        except RepetitionError:
            log.warning("Repetition loop detected, model may not support structured output well")
            self._transcript.finalize_no_translation(msg_id)
            if self._overlay:
                self._overlay.update_translation(
                    msg_id, f"[{t('error_repetition')}]", 0
                )
        except Exception as e:
            import openai
            if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError,
                              openai.AuthenticationError, openai.APIStatusError,
                              TimeoutError, ConnectionError)):
                log.warning(f"Translate error: {e}")
            else:
                log.error(f"Translate error: {e}", exc_info=True)
            self._transcript.finalize_no_translation(msg_id)
            if self._overlay:
                self._overlay.update_translation(msg_id, f"[error: {e}]", 0)

    def _translate_extra_langs(self, text, source_lang, extra_langs, tl_dict):
        """Translate into additional languages for subtitle window (parallel)."""
        from concurrent.futures import as_completed

        def _do_translate(lang):
            translator = self._translator.with_target_language(lang)
            return lang, translator.translate(text, source_lang)

        futures = []
        for lang in extra_langs:
            futures.append(self._tl_executor.submit(_do_translate, lang))

        for future in as_completed(futures):
            try:
                lang, result = future.result()
                tl_dict[lang] = result
                log.info(f"Extra translate [{lang}]: {result}")
            except Exception as e:
                import openai
                if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError,
                                  openai.AuthenticationError, openai.APIStatusError,
                                  TimeoutError, ConnectionError)):
                    log.warning(f"Extra translate error: {e}")
                else:
                    log.error(f"Extra translate error: {e}", exc_info=True)

    def _translate_subwin_only(self, text, source_lang, extra_langs):
        """Translate only for subtitle window when primary target == source language."""
        tl_dict = {self._target_language: text}  # same language, use original
        self._translate_extra_langs(text, source_lang, extra_langs, tl_dict)
        if self._subwin and self._subwin.isVisible():
            self._subwin.update_text(text, tl_dict)

    def start(self):
        if self._running:
            return
        n = len(self._subwin.get_target_languages()) if self._subwin else 1
        self._tl_executor = ThreadPoolExecutor(max_workers=max(8, n + 1))
        self._asr_queue = queue.Queue(maxsize=16)
        self._running = True
        self._paused = False
        self._audio.start()
        self._capture_thread = threading.Thread(
            target=self._pipeline._capture_loop, daemon=True
        )
        self._asr_thread = threading.Thread(
            target=self._pipeline._asr_loop, daemon=True
        )
        self._capture_thread.start()
        self._asr_thread.start()
        # Periodic memory snapshot every 30s
        if self._mem_periodic_timer is None:
            self._mem_periodic_timer = QTimer()
            self._mem_periodic_timer.timeout.connect(self._log_mem_periodic)
            self._mem_periodic_timer.start(30000)
        snap = self._mem_snapshot()
        log.info(
            f"MEM[start] RSS={snap['rss']:.1f}MB "
            f"GPU(alloc/reserved)={snap['gpu_alloc']:.0f}/{snap['gpu_reserved']:.0f}MB "
            f"(baseline for delta tracking)"
        )
        log.info("Pipeline started (capture + ASR threads)")

    def stop(self):
        self._running = False
        self._audio.stop()
        if self._capture_thread:
            self._capture_thread.join(timeout=3)
            self._capture_thread = None
        self._asr_queue.put(None)
        if self._asr_thread:
            self._asr_thread.join(timeout=10)
            if self._asr_thread.is_alive():
                log.warning("ASR thread still running after timeout, proceeding with cleanup")
            self._asr_thread = None
        # Flush remaining VAD buffer after pipeline threads are done
        if self._interim_active:
            remaining = self._vad.force_flush()
            if remaining is not None and self._asr_ready:
                self._pipeline._process_interim_final(remaining)
        else:
            remaining = self._vad.flush()
            if remaining is not None and self._asr_ready:
                self._pipeline._process_segment(remaining)
        self._interim_active = False
        self._interim_pending = ""
        self._last_interim_samples = 0
        self._last_interim_check_time = 0.0
        self._interim_committed_tail = ""
        self._tl_executor.shutdown(wait=True)
        self._transcript.close()
        if self._mem_periodic_timer is not None:
            try:
                self._mem_periodic_timer.stop()
            except Exception:
                pass
            self._mem_periodic_timer = None
        snap = self._mem_snapshot()
        total_delta = snap["rss"] - self._mem_baseline_mb
        log.info(
            f"MEM[stop] RSS={snap['rss']:.1f}MB ({total_delta:+.1f} since start) "
            f"GPU(alloc/reserved)={snap['gpu_alloc']:.0f}/{snap['gpu_reserved']:.0f}MB "
            f"asr_calls={self._mem_asr_call_count} outputs={self._asr_count}"
        )
        self._shutdown_asr_worker()
        log.info("Pipeline stopped")

    def pause(self):
        self._paused = True
        # The capture thread stops feeding the VAD while paused, so whatever it
        # had accumulated must leave the buffer now — otherwise the half-spoken
        # sentence resurfaces glued to the first sentence after resume. Send it
        # through ASR rather than dropping it, matching what stop() does. The
        # ASR thread stays alive while paused, so enqueueing keeps the Qt thread
        # unblocked; the loop clears the interim bookkeeping once it lands.
        with self._pipeline._vad_lock:
            remaining = self._vad.force_flush()
        if remaining is not None and self._running and self._asr_ready:
            log.info(
                f"Flushing {len(remaining) / 16000:.1f}s held at pause"
            )
            self._pipeline._enqueue_asr("vad_flush", remaining)
        else:
            self._interim_active = False
            self._interim_pending = ""
            self._last_interim_samples = 0
            self._last_interim_check_time = 0.0
            self._interim_committed_tail = ""
        if self._overlay:
            self._overlay.update_monitor(0.0, 0.0)
        log.info("Pipeline paused")

    def resume(self):
        self._paused = False
        log.info("Pipeline resumed")
