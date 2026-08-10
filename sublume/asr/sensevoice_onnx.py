"""SenseVoice via ONNX Runtime (sherpa-onnx) — the fast-start ASR backend.

Why this exists alongside asr/sensevoice.py: the torch path pays ~56s from
worker spawn to ready on a cold start (funasr import, hub scan, then a 936MB
checkpoint), and the first inference after that costs several more seconds.
The same model exported to int8 ONNX loads in under a second and needs no
torch at all, which also keeps the ASR worker off the GPU entirely.

Measured on the maintainer's machine (RTX 4060 Ti, int8, CPU, 4 threads):

    import sherpa_onnx      134 ms
    load model            ~800 ms
    1s of silence           24 ms
    7.2s of speech       95-113 ms   (the torch path needs a GPU for 200-350ms)

The output contract matches SenseVoiceEngine.transcribe() exactly, so the
pipeline cannot tell the two apart.
"""

import logging
import re

import numpy as np

log = logging.getLogger("Sublume.SenseVoiceONNX")

SAMPLE_RATE = 16000

# sherpa-onnx reports the detected language as the same tag the torch backend
# embeds in its text, so both backends can share one mapping.
LANG_MAP = {
    "<|zh|>": "zh",
    "<|en|>": "en",
    "<|ja|>": "ja",
    "<|ko|>": "ko",
    "<|yue|>": "yue",
}

_TAG_RE = re.compile(r"<\|[^|]*\|>")

# The recognizer is built per language, so keep the set the model actually
# supports; anything else falls back to auto-detect rather than erroring.
SUPPORTED_LANGUAGES = frozenset(LANG_MAP.values())


def normalize_language(language) -> str:
    """Map our language codes onto what sherpa-onnx expects ("" = auto)."""
    if not language or language == "auto":
        return ""
    lang = str(language)
    # The worker already folds zh-TW/zh-CN/zh-HK to "zh"; be defensive anyway.
    if lang.startswith("zh"):
        lang = "zh"
    return lang if lang in SUPPORTED_LANGUAGES else ""


def parse_result(text: str, lang_tag: str) -> dict | None:
    """Turn a sherpa-onnx result into the pipeline's transcript dict.

    Kept module level and free of sherpa imports so it can be unit-tested
    without the runtime or the model installed.
    """
    detected = LANG_MAP.get(lang_tag or "", "auto")
    if detected == "auto":
        # Older sherpa builds leave the tag inside the text instead.
        for tag, lang in LANG_MAP.items():
            if tag in text:
                detected = lang
                break
    clean = _TAG_RE.sub("", text or "").strip()
    if not clean:
        return None
    return {"text": clean, "language": detected, "language_name": detected}


class SenseVoiceONNXEngine:
    """SenseVoice inference through sherpa-onnx. CPU only, no torch."""

    def __init__(
        self,
        model_path: str,
        tokens_path: str,
        language=None,
        num_threads: int = 4,
        use_itn: bool = True,
        provider: str = "cpu",
    ):
        self._model_path = str(model_path)
        self._tokens_path = str(tokens_path)
        self._num_threads = max(1, int(num_threads))
        self._use_itn = bool(use_itn)
        self._provider = provider
        self.language = language
        self._recognizer = None
        self._build()

    @property
    def device(self) -> str:
        """The sherpa-onnx execution provider this recognizer runs on."""
        return self._provider

    def _build(self):
        """(Re)create the recognizer. sherpa-onnx takes the language hint at
        construction time, so a language change means building again — which
        costs under a second, unlike reloading a torch checkpoint."""
        import sherpa_onnx

        lang = normalize_language(self.language)
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self._model_path,
            tokens=self._tokens_path,
            language=lang,
            use_itn=self._use_itn,
            num_threads=self._num_threads,
            provider=self._provider,
            debug=False,
        )
        log.info(
            f"SenseVoice ONNX loaded: {self._model_path} "
            f"(provider={self._provider}, threads={self._num_threads}, "
            f"language={lang or 'auto'})"
        )

    def set_language(self, language):
        normalized = normalize_language(language)
        if normalized == normalize_language(self.language):
            self.language = language
            return
        log.info(f"SenseVoice ONNX language: {self.language} -> {language}")
        self.language = language
        self._build()

    def transcribe(self, audio: np.ndarray):
        if self._recognizer is None or audio is None or len(audio) == 0:
            return None
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._recognizer.decode_stream(stream)
        result = stream.result
        parsed = parse_result(
            getattr(result, "text", "") or "", getattr(result, "lang", "") or ""
        )
        if parsed is not None:
            log.debug(f"Raw: {getattr(result, 'lang', '')}{getattr(result, 'text', '')}")
        return parsed

    def unload(self):
        self._recognizer = None
