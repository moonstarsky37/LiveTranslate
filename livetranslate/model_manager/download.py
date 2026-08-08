"""Model downloads: proxy plumbing and the fetch routines. This is the
only module here that talks to the network."""

import contextlib
import logging
import os
from pathlib import Path

from livetranslate.model_manager.registry import (
    ASR_MODEL_IDS,
    FUNASR_LEGACY_ENGINE_ALIASES,
    SENSEVOICE_ONNX_FILES,
    _WHISPER_SIZES,
    funasr_model_id,
    funasr_profile,
    normalize_funasr_model_key,
)
from livetranslate.model_manager.cache import (
    MODELS_DIR,
    _has_silero_pkg,
    get_local_model_path,
)

log = logging.getLogger("LiveTranslate.ModelManager")


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@contextlib.contextmanager
def _proxy_env(proxy: str):
    """Temporarily route all download backends through a proxy.

    proxy:
        "system" / "" / None -> leave ambient env & OS proxy untouched
        "none"               -> force-disable any proxy for this download
        a URL                -> send urllib/requests/httpx traffic through it

    Covers torch.hub (urllib), huggingface_hub and modelscope (requests),
    which all honor the *_PROXY env vars; urllib additionally gets an explicit
    opener so a previously cached default opener cannot bypass the setting.
    """
    import urllib.request

    if proxy in ("system", "", None):
        yield
        return
    saved_env: dict = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    saved_no_proxy = os.environ.get("NO_PROXY")
    saved_opener = getattr(urllib.request, "_opener", None)
    try:
        if proxy == "none":
            for key in _PROXY_ENV_KEYS:
                os.environ.pop(key, None)
            os.environ["NO_PROXY"] = "*"
            handler = urllib.request.ProxyHandler({})
        else:
            for key in _PROXY_ENV_KEYS:
                os.environ[key] = proxy
            os.environ.pop("NO_PROXY", None)
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        urllib.request.install_opener(urllib.request.build_opener(handler))
        log.info(f"Download proxy active: {proxy}")
        yield
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if saved_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = saved_no_proxy
        urllib.request.install_opener(saved_opener)


def download_silero(proxy: str = "system"):
    if _has_silero_pkg():
        log.info("Silero VAD bundled by silero-vad package, no download needed")
        return
    import torch

    log.info("Downloading Silero VAD...")
    with _proxy_env(proxy):
        try:
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad:master",
                model="silero_vad",
                trust_repo=True,
            )
        except Exception as exc:
            if "CERTIFICATE_VERIFY" not in str(exc):
                raise
            log.warning("SSL strict verification failed, retrying with relaxed flags")
            model, _ = _load_silero_relaxed_ssl()
    del model
    log.info("Silero VAD downloaded")


def _load_silero_relaxed_ssl():
    # Python 3.13 enables VERIFY_X509_STRICT by default, rejecting certificates
    # without an Authority Key Identifier (common behind SSL-inspecting proxies).
    import ssl

    import torch

    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    original = ssl._create_default_https_context

    def relaxed_context(*args, **kwargs):
        ctx = ssl.create_default_context(*args, **kwargs)
        ctx.verify_flags &= ~strict
        return ctx

    ssl._create_default_https_context = relaxed_context
    try:
        return torch.hub.load(
            repo_or_dir="snakers4/silero-vad:master",
            model="silero_vad",
            trust_repo=True,
            force_reload=True,
        )
    finally:
        ssl._create_default_https_context = original


def ensure_qwen_weights(model_dir, hub: str = "hf") -> None:
    """Fetch Qwen3-0.6B weights into a nano model's embedded subdir (one-time).

    Kept off the ASR worker startup path: its 180s ready timeout would otherwise
    kill the process mid-download on slow links.
    """
    qwen_dir = Path(model_dir) / "Qwen3-0.6B"
    if not qwen_dir.is_dir():
        return
    if any(f.suffix in (".safetensors", ".bin") for f in qwen_dir.iterdir()):
        return
    log.info("Downloading Qwen3-0.6B weights (one-time)...")
    from huggingface_hub import snapshot_download

    snapshot_download(
        "Qwen/Qwen3-0.6B",
        local_dir=str(qwen_dir),
        ignore_patterns=["*.gguf"],
    )
    log.info("Qwen3-0.6B weights downloaded")


def download_asr(engine, model_size="medium", hub="hf", proxy="system"):
    # This fork downloads exclusively from HuggingFace (the hub parameter is
    # kept only for caller compatibility). Legacy ModelScope caches remain
    # usable via get_local_model_path()/is_asr_cached() scanning.
    resolved = str(MODELS_DIR.resolve())
    hf_cache = os.path.join(resolved, "huggingface", "hub")
    with _proxy_env(proxy):
        if engine == "funasr" or engine in FUNASR_LEGACY_ENGINE_ALIASES:
            model_key = (
                FUNASR_LEGACY_ENGINE_ALIASES[engine]
                if engine in FUNASR_LEGACY_ENGINE_ALIASES
                else normalize_funasr_model_key(model_size)
            )
            from huggingface_hub import snapshot_download

            model_id = funasr_model_id(model_key)
            log.info(f"Downloading {model_id} from HuggingFace...")
            snapshot_download(repo_id=model_id, cache_dir=hf_cache)
            funasr_dir = get_local_model_path("funasr", hub="hf", funasr_model=model_key)
            neutralize_funasr_requirements(funasr_dir)
            if funasr_dir and funasr_profile(model_key)["family"] == "funasr-nano":
                ensure_qwen_weights(funasr_dir)
        elif engine == "sensevoice-onnx":
            from huggingface_hub import snapshot_download

            model_id = ASR_MODEL_IDS[engine]
            log.info(f"Downloading {model_id} from HuggingFace...")
            # allow_patterns keeps this at 239MB; the repo also holds a 937MB
            # fp32 model.onnx and sample wavs that we never load.
            snapshot_download(
                repo_id=model_id,
                cache_dir=hf_cache,
                allow_patterns=list(SENSEVOICE_ONNX_FILES),
            )
        elif engine == "anime-whisper":
            # HF-only, ignore hub setting
            from huggingface_hub import snapshot_download

            model_id = ASR_MODEL_IDS[engine]
            log.info(f"Downloading {model_id} from HuggingFace...")
            snapshot_download(repo_id=model_id, cache_dir=hf_cache)
        elif engine == "whisper":
            if model_size not in _WHISPER_SIZES:
                raise ValueError(f"Invalid local faster-whisper model: {model_size}")
            from huggingface_hub import snapshot_download

            model_id = f"Systran/faster-whisper-{model_size}"
            log.info(f"Downloading {model_id} from HuggingFace...")
            snapshot_download(repo_id=model_id, cache_dir=hf_cache)
    log.info(f"ASR model downloaded: {engine}")


def neutralize_funasr_requirements(model_dir) -> None:
    """Skip FunASR's load-time `pip install -r requirements.txt`.

    With trust_remote_code=True, FunASR detects requirements.txt in the model
    dir and runs pip in a subprocess whose output is swallowed (PIPE). On a slow
    or proxy-blocked PyPI this hangs indefinitely with no log output, and it can
    pull heavy unused deps (e.g. gradio). All real deps already live in the venv,
    so rename the file out of the way to make the check miss.
    """
    if not model_dir:
        return
    req = Path(model_dir) / "requirements.txt"
    if req.exists():
        try:
            req.replace(req.with_name("requirements.txt.bundled"))
            log.info(f"Skipped FunASR requirements install: {req}")
        except OSError as exc:
            log.warning(f"Failed to neutralize {req}: {exc}")
