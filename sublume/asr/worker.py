import gc
import inspect
import logging
import sys
import traceback
from typing import Any

import numpy as np

log = logging.getLogger("Sublume.ASRWorker")


def _setup_logging():
    if logging.getLogger().handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("Sublume").setLevel(logging.DEBUG)


def _error_response(msg_id: str | None, exc: BaseException, recoverable: bool) -> dict:
    return {
        "id": msg_id,
        "ok": False,
        "type": "error",
        "error": {
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "recoverable": recoverable,
        },
    }


def _ok_response(msg_id: str | None, response_type: str, payload: Any = None) -> dict:
    return {
        "id": msg_id,
        "ok": True,
        "type": response_type,
        "payload": payload,
    }


def _parse_device(device: str) -> tuple[str, int]:
    device = str(device or "cpu").split(" (", 1)[0].strip()
    if device.startswith("cuda:"):
        index = int(device.split(":", 1)[1])
        return "cuda", index
    return device, 0


def _ready_payload(engine, config: dict) -> dict:
    """Build the ready report, with the device the engine ACTUALLY loaded on.

    A CPU-only torch build silently turns a "cuda" request into cpu, and the
    logs/monitor bar would otherwise keep repeating the config value. Only a
    real fallback changes the label: an engine reporting the same device at a
    different granularity (bare "cuda" vs "cuda:0") keeps the config format.

    The probe is guarded against Exception, comparison included (getattr only
    swallows AttributeError, so a raising property or an unparsable device
    string would escape): a loaded engine is never failed by label resolution.
    The reported value is stringified because this payload crosses the pipe —
    an engine handing back some exotic device object must not break the send.
    """
    config_device = config.get("device")
    actual = None
    try:
        actual = getattr(engine, "device", None)
        same = actual is None or _parse_device(actual) == _parse_device(config_device)
    except Exception:
        same = True
    return {
        "engine_type": config.get("engine_type"),
        "display_name": config.get("display_name"),
        "device": config_device if same else str(actual),
    }


def _load_engine(config: dict):
    from sublume.model_manager import MODELS_DIR, apply_cache_env

    apply_cache_env()

    engine_type = config["engine_type"]
    device = config.get("device", "cpu")
    hub = "hf"  # this fork downloads exclusively from HuggingFace
    language = config.get("language", "auto")
    # ASR engines (whisper/SenseVoice) only accept bare "zh" — normalize the
    # UI-level language codes here at the single worker entry point.
    if language in ("zh-TW", "zh-CN", "zh-HK"):
        language = "zh"
    pad_seconds = config.get("pad_seconds")

    parsed_device, device_index = _parse_device(device)

    if engine_type == "sensevoice-onnx":
        # Deliberately first and self-contained: this branch must not reach any
        # torch-importing module, that being the whole point of the ONNX path.
        from sublume.model_manager import sensevoice_onnx_paths
        from sublume.asr.sensevoice_onnx import SenseVoiceONNXEngine

        paths = sensevoice_onnx_paths()
        if paths is None:
            raise FileNotFoundError(
                "SenseVoice ONNX model not found in the cache; "
                "download it from Settings before selecting this engine."
            )
        model_path, tokens_path = paths
        engine = SenseVoiceONNXEngine(
            model_path=model_path,
            tokens_path=tokens_path,
            language=language,
            num_threads=int(config.get("onnx_num_threads") or 4),
        )
        return engine

    if engine_type == "funasr":
        from sublume.asr.funasr import FunASREngine

        engine = FunASREngine(
            model_key=config.get("funasr_model"),
            device=device,
            hub=hub,
            pad_seconds=pad_seconds,
        )
    elif engine_type == "anime-whisper":
        from sublume.asr.anime_whisper import AnimeWhisperEngine

        worker_device = parsed_device if parsed_device == "cpu" else f"cuda:{device_index}"
        engine = AnimeWhisperEngine(device=worker_device, hub=hub)
    else:
        from sublume.asr.whisper import ASREngine

        compute_type = config.get("compute_type", "float16")
        if parsed_device == "cpu" and compute_type == "float16":
            compute_type = "int8"
        download_root = config.get("download_root")
        if not download_root:
            download_root = str((MODELS_DIR / "huggingface" / "hub").resolve())
        engine = ASREngine(
            model_size=config["model_size"],
            device=parsed_device,
            device_index=device_index,
            compute_type=compute_type,
            language=language,
            download_root=download_root,
            pad_seconds=pad_seconds,
        )

    if hasattr(engine, "set_language"):
        engine.set_language(language)
    return engine


def _warmup(engine):
    """Run one throwaway inference before reporting ready.

    The first real transcribe otherwise pays 5-6s of lazy init (CUDA context,
    kernel autotuning, cuDNN/CTranslate2 workspace allocation) — right when the
    user has just started talking. Doing it here moves that cost inside the
    loading dialog. Failures are non-fatal: a worker that cannot warm up can
    still serve requests.
    """
    import time

    audio = np.zeros(16000, dtype=np.float32)
    started = time.perf_counter()
    try:
        engine.transcribe(audio)
    except Exception as exc:
        log.warning(f"ASR warm-up failed (non-fatal): {exc}")
        return
    log.info(f"ASR warm-up done in {(time.perf_counter() - started) * 1000:.0f}ms")


def _transcribe(engine, payload: dict):
    audio = payload.get("audio")
    if not isinstance(audio, np.ndarray):
        raise TypeError("transcribe payload audio must be a numpy.ndarray")

    kwargs = {}
    signature = inspect.signature(engine.transcribe)
    if "word_timestamps" in signature.parameters:
        kwargs["word_timestamps"] = bool(payload.get("word_timestamps", False))
    return engine.transcribe(audio, **kwargs)


def _cleanup_engine(engine):
    if engine is not None and hasattr(engine, "unload"):
        try:
            engine.unload()
        except Exception:
            log.warning("ASR engine unload failed", exc_info=True)
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def worker_main(conn, config: dict):
    _setup_logging()
    engine = None
    try:
        log.info(
            "ASR worker loading: "
            f"{config.get('engine_type')} on {config.get('device')} "
            f"(pid config={config.get('display_name', '')})"
        )
        engine = _load_engine(config)
        _warmup(engine)
        conn.send(_ok_response(None, "ready", _ready_payload(engine, config)))
    except BaseException as exc:
        log.error(f"ASR worker load failed: {exc}", exc_info=True)
        try:
            conn.send(_error_response(None, exc, recoverable=False))
        finally:
            _cleanup_engine(engine)
            conn.close()
        return

    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                break

            msg_id = msg.get("id")
            msg_type = msg.get("type")
            payload = msg.get("payload") or {}

            try:
                if msg_type == "shutdown":
                    conn.send(_ok_response(msg_id, "shutdown"))
                    break
                if msg_type == "transcribe":
                    result = _transcribe(engine, payload)
                    conn.send(_ok_response(msg_id, "result", result))
                    continue
                if msg_type == "set_language":
                    if hasattr(engine, "set_language"):
                        engine.set_language(payload.get("language", "auto"))
                    conn.send(_ok_response(msg_id, "ack"))
                    continue
                if msg_type == "set_input_padding":
                    if hasattr(engine, "set_input_padding"):
                        engine.set_input_padding(payload.get("pad_seconds"))
                    conn.send(_ok_response(msg_id, "ack"))
                    continue
                raise ValueError(f"Unknown ASR worker command: {msg_type}")
            except Exception as exc:
                log.error(f"ASR worker command failed: {msg_type}: {exc}", exc_info=True)
                conn.send(_error_response(msg_id, exc, recoverable=True))
    finally:
        _cleanup_engine(engine)
        try:
            conn.close()
        except Exception:
            pass
        log.info("ASR worker stopped")
