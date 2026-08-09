"""Audio -> VAD -> ASR -> translation pipeline.

Extracted verbatim from SublumeApp (Phase 3 split). TranslationPipeline is
a host-object helper: it owns the segment/interim bookkeeping and the two worker
loops (capture + ASR), and reaches back into the owning SublumeApp
(``self._app``) for the shared state — VAD, audio capture, ASR supervision,
overlay/panel/subtitle widgets, translation executor and the run flags.

This module must stay free of PyQt6 and torch imports at module level: every Qt
signal emit and every torch-touching call goes out through ``self._app``, which
keeps the pipeline importable (and unit-testable) in a bare environment.
"""

import logging
import queue
import threading
import time
from datetime import datetime

import numpy as np

from sublume.core.segmentation import (
    is_short_utterance,
    split_sentences,
    strip_committed_overlap,
)

log = logging.getLogger("Sublume")


class TranslationPipeline:
    """Owns the capture/ASR worker loops and the segment -> translation handoff."""

    def __init__(self, app):
        self._app = app
        # Guards the VAD buffer, which the capture thread and the ASR thread both
        # reach into (process_chunk / peek_buffer / trim_front).
        self._vad_lock = threading.Lock()
        self._msg_id = 0
        self._last_original = ""
        self._last_msg_id = 0

    def _process_segment(self, speech_segment):
        """Run ASR + translation on a speech segment. Called from ASR thread and stop()."""
        seg_len = len(speech_segment) / 16000
        log.info(f"Speech segment: {seg_len:.1f}s")

        try:
            result, asr_ms = self._app._run_asr(speech_segment, "segment")
        except Exception as e:
            log.error(f"ASR error: {e}", exc_info=True)
            return
        if asr_ms == 0:
            return
        if asr_ms > 10000:
            log.warning(f"ASR took {asr_ms:.0f}ms, possible hang")
        if result is None:
            return

        original_text = result["text"].strip()
        # Skip empty or punctuation-only ASR results
        if not original_text or not any(c.isalnum() for c in original_text):
            log.debug(
                f"ASR returned empty/punctuation-only, skipping: '{result['text']}'"
            )
            return

        # Skip suspiciously short text from long segments (likely noise)
        alnum_chars = sum(1 for c in original_text if c.isalnum())
        if seg_len >= 2.0 and alnum_chars <= 3:
            log.debug(
                f"Noise filter: {seg_len:.1f}s segment produced only '{original_text}', skipping"
            )
            return

        source_lang = result["language"]
        asr_lang_setting = self._app._panel.get_settings().get("asr_language", "auto") if self._app._panel else "auto"
        if asr_lang_setting != "auto" and source_lang != asr_lang_setting:
            log.info(
                f"Language filter: expected '{asr_lang_setting}' but got '{source_lang}', "
                f"discarding: {original_text[:60]}"
            )
            return

        self._app._asr_count += 1
        self._msg_id += 1
        msg_id = self._msg_id
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.info(f"ASR [{source_lang}] ({asr_ms:.0f}ms): {original_text}")

        if self._app._overlay:
            self._app._overlay.add_message(
                msg_id, timestamp, original_text, source_lang, asr_ms
            )
        self._app._transcript.write_original(msg_id, timestamp, original_text)

        # Store for subtitle window (translation will be added later)
        self._last_original = original_text
        self._last_msg_id = msg_id

        target_lang = self._app._target_language

        # Collect extra languages needed by subtitle window (beyond the primary target)
        extra_langs = set()
        if self._app._subwin and self._app._subwin.isVisible():
            subwin_langs = self._app._subwin.get_target_languages()
            # Remove primary target and source (no need to translate those)
            extra_langs = subwin_langs - {target_lang, source_lang}

        if source_lang == target_lang:
            log.info(f"Same language ({source_lang}), no translation")
            self._app._transcript.finalize_no_translation(msg_id)
            if self._app._overlay:
                self._app._overlay.update_translation(msg_id, "", 0)
                self._app._overlay.update_stats(
                    self._app._asr_count,
                    self._app._translate_count,
                    self._app._total_prompt_tokens,
                    self._app._total_completion_tokens,
                    self._app._compute_cost(),
                )
            if self._app._subwin and self._app._subwin.isVisible():
                # Primary is same language; still need to translate extra langs
                if extra_langs:
                    try:
                        self._app._tl_executor.submit(
                            self._app._translate_subwin_only, original_text, source_lang, extra_langs
                        )
                    except RuntimeError:
                        pass
                else:
                    self._app._subwin.update_text(original_text, {target_lang: original_text})
        else:
            try:
                self._app._tl_executor.submit(
                    self._app._translate_async, msg_id, original_text, source_lang,
                    extra_langs or None,
                )
            except RuntimeError:
                log.warning("Translation executor shut down, skipping")

    # ── Incremental ASR ──
    # Text utilities live in sublume.core.segmentation (pure, unit-tested)

    def _do_interim_asr(self) -> bool:
        """Run ASR on current VAD buffer, output complete sentences, trim consumed audio.
        Returns True if any sentences were committed."""
        with self._vad_lock:
            peek = self._app._vad.peek_buffer()
        if peek is None:
            return False
        audio, duration = peek

        # Don't bother with very short buffers
        if duration < 1.5:
            return False

        # Word timestamp alignment is expensive for repeated interim passes.
        # The proportional trim path below is less exact but keeps long runs stable.
        use_word_ts = False

        try:
            result, asr_ms = self._app._run_asr(
                audio, "interim", word_timestamps=use_word_ts
            ) if use_word_ts else self._app._run_asr(audio, "interim")
        except Exception as e:
            log.error(f"Interim ASR error: {e}", exc_info=True)
            return False

        if asr_ms == 0:
            return False

        if result is None:
            return False

        full_text = result["text"].strip()
        if not full_text or not any(c.isalnum() for c in full_text):
            return False

        # Strip echo from previous commit's overlap
        full_text = strip_committed_overlap(full_text, self._app._interim_committed_tail)
        if not full_text:
            return False

        split_start = time.perf_counter()
        sentences = split_sentences(full_text, result["language"])
        split_ms = (time.perf_counter() - split_start) * 1000
        if len(sentences) <= 1:
            return False
        log.debug(f"Interim split [{result['language']}] ({split_ms:.1f}ms): {len(sentences)} parts -> {sentences}")

        # All but last are complete; last is still being spoken
        complete = sentences[:-1]

        committed_text = ""
        for sent in complete:
            committed_text += sent

        if not committed_text.strip():
            return False

        # Determine trim point
        total_samples = len(audio)
        if use_word_ts and result.get("words"):
            words = result["words"]
            committed_lower = committed_text.lower().rstrip()
            char_pos = 0
            last_word_end = 0.0
            for w in words:
                word_text = w["word"].strip()
                idx = committed_lower.find(word_text.lower(), char_pos)
                if idx >= 0:
                    char_pos = idx + len(word_text)
                    last_word_end = w["end"]
                if char_pos >= len(committed_lower):
                    break
            trim_samples = int(last_word_end * 16000)
        else:
            # Proportional trim with safety margin to reduce echo
            ratio = len(committed_text) / max(len(full_text), 1)
            margin = int(0.3 * 16000)  # 0.3s extra trim to avoid re-recognition
            trim_samples = int(ratio * total_samples) + margin
            # Don't over-trim: keep at least 0.5s for the remaining sentence
            max_trim = total_samples - int(0.5 * 16000)
            trim_samples = min(trim_samples, max(max_trim, 0))
            # Minimum trim to prevent re-recognition loops
            min_trim = int(0.3 * 16000)
            if trim_samples < min_trim and trim_samples > 0:
                trim_samples = min(min_trim, total_samples // 2)

        # Output committed sentences
        actually_committed = False
        for sent in complete:
            text = sent.strip()
            if not text:
                continue
            if is_short_utterance(text):
                self._app._interim_pending += text
                log.debug(f"Interim short utterance buffered: '{text}', pending='{self._app._interim_pending}'")
                continue

            if self._app._interim_pending:
                text = self._app._interim_pending + text
                self._app._interim_pending = ""

            self._process_segment_text(text, result["language"], asr_ms)
            actually_committed = True

        if not actually_committed:
            return False

        if trim_samples > 0:
            with self._vad_lock:
                self._app._vad.trim_front(trim_samples)

        # Track committed text tail for echo dedup
        self._app._interim_committed_tail = committed_text[-50:] if len(committed_text) > 50 else committed_text

        self._app._interim_active = True
        log.info(f"Interim ASR: committed {len(complete)} sentence(s), trimmed {trim_samples / 16000:.2f}s")
        return True

    def _process_segment_text(self, text: str, source_lang: str, asr_ms: float = 0):
        """Output a text result (from interim or final) — similar to _process_segment but skips ASR."""
        original_text = text.strip()
        if not original_text or not any(c.isalnum() for c in original_text):
            return

        asr_lang_setting = self._app._panel.get_settings().get("asr_language", "auto") if self._app._panel else "auto"
        if asr_lang_setting != "auto" and source_lang != asr_lang_setting:
            log.info(f"Language filter: expected '{asr_lang_setting}' but got '{source_lang}', discarding: {original_text[:60]}")
            return

        self._app._asr_count += 1
        self._msg_id += 1
        msg_id = self._msg_id
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.info(f"ASR [{source_lang}] ({asr_ms:.0f}ms, interim): {original_text}")

        if self._app._overlay:
            self._app._overlay.add_message(msg_id, timestamp, original_text, source_lang, asr_ms)
        self._app._transcript.write_original(msg_id, timestamp, original_text)

        self._last_original = original_text
        self._last_msg_id = msg_id

        target_lang = self._app._target_language
        extra_langs = set()
        if self._app._subwin and self._app._subwin.isVisible():
            subwin_langs = self._app._subwin.get_target_languages()
            extra_langs = subwin_langs - {target_lang, source_lang}

        if source_lang == target_lang:
            log.info(f"Same language ({source_lang}), no translation")
            self._app._transcript.finalize_no_translation(msg_id)
            if self._app._overlay:
                self._app._overlay.update_translation(msg_id, "", 0)
                self._app._overlay.update_stats(self._app._asr_count, self._app._translate_count, self._app._total_prompt_tokens, self._app._total_completion_tokens, self._app._compute_cost())
            if self._app._subwin and self._app._subwin.isVisible():
                if extra_langs:
                    try:
                        self._app._tl_executor.submit(self._app._translate_subwin_only, original_text, source_lang, extra_langs)
                    except RuntimeError:
                        pass
                else:
                    self._app._subwin.update_text(original_text, {target_lang: original_text})
        else:
            try:
                self._app._tl_executor.submit(self._app._translate_async, msg_id, original_text, source_lang, extra_langs or None)
            except RuntimeError:
                log.warning("Translation executor shut down, skipping")

    def _process_interim_final(self, speech_segment):
        """Handle VAD flush after interim outputs were already made."""
        seg_len = len(speech_segment) / 16000
        log.info(f"Interim final segment: {seg_len:.1f}s")

        try:
            result, asr_ms = self._app._run_asr(speech_segment, "interim_final")
        except Exception as e:
            log.error(f"Interim final ASR error: {e}", exc_info=True)
            return
        if asr_ms == 0:
            return

        if result is None:
            # Flush any remaining pending
            if self._app._interim_pending:
                text = self._app._interim_pending
                self._app._interim_pending = ""
                lang = self._app._panel.get_settings().get("asr_language", "auto") if self._app._panel else "auto"
                if lang == "auto":
                    lang = "unknown"
                self._process_segment_text(text, lang)
            return

        original_text = result["text"].strip()

        # Strip echo from previous commit's overlap
        original_text = strip_committed_overlap(original_text, self._app._interim_committed_tail)

        # Prepend any remaining pending short utterances
        if self._app._interim_pending:
            original_text = self._app._interim_pending + original_text
            self._app._interim_pending = ""

        if not original_text or not any(c.isalnum() for c in original_text):
            return

        # Apply noise filter like _process_segment
        alnum_chars = sum(1 for c in original_text if c.isalnum())
        if seg_len >= 2.0 and alnum_chars <= 3:
            log.debug(f"Noise filter: {seg_len:.1f}s segment produced only '{original_text}', skipping")
            return

        self._process_segment_text(original_text, result["language"], asr_ms)

    def _capture_loop(self):
        silence_chunk = np.zeros(
            int(
                self._app._config["audio"]["sample_rate"]
                * self._app._config["audio"]["chunk_duration"]
            ),
            dtype=np.float32,
        )
        while self._app._running:
            item = self._app._audio.get_audio(timeout=1.0)
            if item is None:
                if self._app._vad._is_speaking and not self._app._paused:
                    n = self._app._vad._get_effective_silence_limit() + 1
                    for _ in range(n):
                        with self._vad_lock:
                            seg = self._app._vad.process_chunk(silence_chunk)
                        if seg is not None and self._app._asr_ready:
                            self._enqueue_asr("vad_flush", seg)
                            break
                continue

            chunk, mic_rms = item

            if self._app._paused:
                continue

            rms = float(np.sqrt(np.mean(chunk**2)))

            if self._app._overlay:
                self._app._overlay.update_monitor(rms, self._app._vad.last_confidence, mic_rms)

            with self._vad_lock:
                speech_segment = self._app._vad.process_chunk(chunk)

            if speech_segment is None:
                # Still accumulating — check for interim ASR
                if (self._app._incremental_enabled and self._app._asr_ready
                        and self._app._vad._is_speaking):
                    buf_samples = self._app._vad._speech_samples
                    total_dur = buf_samples / 16000
                    elapsed = (buf_samples - self._app._last_interim_samples) / 16000
                    now = time.perf_counter()
                    cooldown = now - self._app._last_interim_check_time
                    if total_dur >= self._app._interim_interval and elapsed >= self._app._interim_interval and cooldown >= 1.0:
                        self._app._last_interim_check_time = now
                        self._enqueue_asr("interim", None)
                continue

            if not self._app._asr_ready:
                log.debug("ASR not ready, dropping segment")
                continue

            self._enqueue_asr("vad_flush", speech_segment)

    def _enqueue_asr(self, seg_type: str, segment):
        try:
            self._app._asr_queue.put_nowait((seg_type, segment))
        except queue.Full:
            try:
                dropped = self._app._asr_queue.get_nowait()
                log.warning(f"ASR queue full, dropped {dropped[0]} segment")
            except queue.Empty:
                pass
            try:
                self._app._asr_queue.put_nowait((seg_type, segment))
            except queue.Full:
                log.warning("ASR queue still full after drop, skipping segment")

    def _asr_loop(self):
        while self._app._running:
            try:
                item = self._app._asr_queue.get(timeout=1.0)
            except queue.Empty:
                # Idle moment: recycle a bloated worker while no audio is waiting.
                # Guarded so an unexpected error can never kill this thread (which
                # would itself silence ASR permanently).
                try:
                    self._app._maybe_recycle_asr_worker()
                except Exception:
                    log.error("ASR worker recycle check failed", exc_info=True)
                continue

            if item is None:
                break

            seg_type, segment = item

            if seg_type == "vad_flush":
                if self._app._interim_active:
                    self._process_interim_final(segment)
                else:
                    self._process_segment(segment)
                self._app._interim_active = False
                self._app._interim_pending = ""
                self._app._last_interim_samples = 0
                self._app._last_interim_check_time = 0.0
                self._app._interim_committed_tail = ""
            elif seg_type == "interim":
                self._drain_interim_duplicates()
                self._do_interim_asr()
                with self._vad_lock:
                    self._app._last_interim_samples = self._app._vad._speech_samples

    def _drain_interim_duplicates(self):
        while True:
            try:
                item = self._app._asr_queue.get_nowait()
            except queue.Empty:
                break
            if item is None or item[0] != "interim":
                self._app._asr_queue.put(item)
                break
