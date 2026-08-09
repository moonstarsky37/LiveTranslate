"""Model registry: ids, profiles, display names and the normalization
rules that map settings values onto them. Pure data and pure functions -
nothing here touches the filesystem or the network."""


ASR_MODEL_IDS = {
    "sensevoice": "iic/SenseVoiceSmall",
    "funasr-nano": "FunAudioLLM/Fun-ASR-Nano-2512",
    "funasr-mlt-nano": "FunAudioLLM/Fun-ASR-MLT-Nano-2512",
    "anime-whisper": "litagin/anime-whisper",
    # The sherpa-onnx export of SenseVoiceSmall. Same model, int8 ONNX: 239MB
    # instead of 936MB, loads in under a second, and needs no torch.
    "sensevoice-onnx": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
}

# The repo also ships a 937MB fp32 model.onnx and test wavs we do not need;
# only these two files are fetched.
SENSEVOICE_ONNX_FILES = ("model.int8.onnx", "tokens.txt")

FUNASR_MODEL_PROFILES = {
    "sensevoice-small": {
        "display_name": "SenseVoice Small",
        "family": "sensevoice",
        "legacy_engine": "sensevoice",
        "modelscope_id": "iic/SenseVoiceSmall",
        "huggingface_id": "FunAudioLLM/SenseVoiceSmall",
        "estimated_bytes": 940_000_000,
        "supports_padding": True,
        "supports_language": True,
    },
    "funasr-nano-2512": {
        "display_name": "Fun-ASR-Nano",
        "family": "funasr-nano",
        "legacy_engine": "funasr-nano",
        "modelscope_id": "FunAudioLLM/Fun-ASR-Nano-2512",
        "huggingface_id": "FunAudioLLM/Fun-ASR-Nano-2512",
        # includes the separately-fetched Qwen3-0.6B weights (~1.5GB)
        "estimated_bytes": 3_500_000_000,
        "supports_padding": False,
        "supports_language": True,
    },
    "funasr-mlt-nano-2512": {
        "display_name": "Fun-ASR-MLT-Nano",
        "family": "funasr-nano",
        "legacy_engine": "funasr-mlt-nano",
        "modelscope_id": "FunAudioLLM/Fun-ASR-MLT-Nano-2512",
        "huggingface_id": "FunAudioLLM/Fun-ASR-MLT-Nano-2512",
        # includes the separately-fetched Qwen3-0.6B weights (~1.5GB)
        "estimated_bytes": 3_500_000_000,
        "supports_padding": False,
        "supports_language": True,
    },
}

DEFAULT_FUNASR_MODEL = "sensevoice-small"

FUNASR_LEGACY_ENGINE_ALIASES = {
    "sensevoice": "sensevoice-small",
    "funasr-nano": "funasr-nano-2512",
    "funasr-mlt-nano": "funasr-mlt-nano-2512",
}

# HuggingFace repo ids for engines whose namespace differs from ModelScope.
# SenseVoice lives under `iic/` on ModelScope but `FunAudioLLM/` on HuggingFace.
ASR_MODEL_IDS_HF = {
    "sensevoice": "FunAudioLLM/SenseVoiceSmall",
}


def asr_model_id(
    engine_type: str, hub: str = "hf", funasr_model: str | None = None
) -> str:
    """Repo id for an engine. Downloads are HF-only; "ms" is accepted solely
    to resolve ids when scanning legacy ModelScope cache directories."""
    if engine_type == "funasr":
        return funasr_model_id(funasr_model, hub)
    if engine_type in FUNASR_LEGACY_ENGINE_ALIASES:
        return funasr_model_id(FUNASR_LEGACY_ENGINE_ALIASES[engine_type], hub)
    if hub != "ms" and engine_type in ASR_MODEL_IDS_HF:
        return ASR_MODEL_IDS_HF[engine_type]
    return ASR_MODEL_IDS[engine_type]

ASR_DISPLAY_NAMES = {
    "funasr": "FunASR",
    "sensevoice": "SenseVoice Small",
    "funasr-nano": "Fun-ASR-Nano",
    "funasr-mlt-nano": "Fun-ASR-MLT-Nano",
    "whisper": "Whisper",
    "anime-whisper": "Anime-Whisper",
    "remote-whisper": "Remote-Whisper",
    "sensevoice-onnx": "SenseVoice ONNX",
}

_MODEL_SIZE_BYTES = {
    "silero-vad": 2_000_000,
    "sensevoice": 940_000_000,
    "funasr-nano": 1_050_000_000,
    "funasr-mlt-nano": 1_050_000_000,
    "whisper-tiny": 78_000_000,
    "whisper-base": 148_000_000,
    "whisper-small": 488_000_000,
    "whisper-medium": 1_530_000_000,
    "whisper-large-v3": 3_100_000_000,
    "anime-whisper": 3_100_000_000,
    "sensevoice-onnx": 240_000_000,
}

_WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

_CACHE_MODELS = [
    ("SenseVoice Small", "funasr", "sensevoice-small"),
    ("Fun-ASR-Nano", "funasr", "funasr-nano-2512"),
    ("Fun-ASR-MLT-Nano", "funasr", "funasr-mlt-nano-2512"),
    ("Anime-Whisper", "anime-whisper"),
    ("SenseVoice ONNX", "sensevoice-onnx"),
]


def normalize_funasr_model_key(model_key: str | None) -> str:
    if model_key in FUNASR_MODEL_PROFILES:
        return model_key
    if model_key in FUNASR_LEGACY_ENGINE_ALIASES:
        return FUNASR_LEGACY_ENGINE_ALIASES[model_key]
    return DEFAULT_FUNASR_MODEL


def normalize_asr_engine_selection(
    engine_type: str | None, funasr_model: str | None = None
) -> tuple[str, str]:
    if engine_type in FUNASR_LEGACY_ENGINE_ALIASES:
        return "funasr", FUNASR_LEGACY_ENGINE_ALIASES[engine_type]
    if engine_type == "funasr":
        return "funasr", normalize_funasr_model_key(funasr_model)
    return engine_type or "funasr", normalize_funasr_model_key(funasr_model)


def migrate_funasr_settings(settings: dict | None) -> dict | None:
    if not settings:
        return settings
    engine, model_key = normalize_asr_engine_selection(
        settings.get("asr_engine"), settings.get("funasr_model")
    )
    settings["asr_engine"] = engine
    if engine == "funasr":
        settings["funasr_model"] = model_key
    else:
        settings.setdefault("funasr_model", DEFAULT_FUNASR_MODEL)
    return settings


def funasr_profile(model_key: str | None) -> dict:
    return FUNASR_MODEL_PROFILES[normalize_funasr_model_key(model_key)]


def funasr_model_options() -> list[tuple[str, str]]:
    return [
        (key, str(profile["display_name"]))
        for key, profile in FUNASR_MODEL_PROFILES.items()
    ]


def funasr_display_name(model_key: str | None) -> str:
    return funasr_profile(model_key)["display_name"]


def funasr_supports_padding(model_key: str | None) -> bool:
    return bool(funasr_profile(model_key).get("supports_padding"))


def funasr_model_id(model_key: str | None, hub: str = "hf") -> str:
    # Downloads are HF-only (enforced in download_asr); "ms" is accepted solely
    # for resolving ids when scanning legacy ModelScope cache directories.
    profile = funasr_profile(model_key)
    return profile["huggingface_id"] if hub != "ms" else profile["modelscope_id"]
