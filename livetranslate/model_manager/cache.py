"""Local model cache: where models live, whether they are complete, and
how much disk they take. Everything that answers a question about the
models/ directory without downloading anything."""

import logging
import os
from pathlib import Path

from livetranslate.paths import ROOT
from livetranslate.model_manager.registry import (
    ASR_DISPLAY_NAMES,
    ASR_MODEL_IDS,
    FUNASR_LEGACY_ENGINE_ALIASES,
    SENSEVOICE_ONNX_FILES,
    _CACHE_MODELS,
    _MODEL_SIZE_BYTES,
    _WHISPER_SIZES,
    asr_model_id,
    funasr_model_id,
    funasr_profile,
    normalize_funasr_model_key,
)

log = logging.getLogger("LiveTranslate.ModelManager")


APP_DIR = ROOT
MODELS_DIR = APP_DIR / "models"


def _custom_whisper_path(value) -> Path | None:
    if not value or value in _WHISPER_SIZES:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = APP_DIR / path
    return path


def is_faster_whisper_model_dir(path) -> bool:
    """True when path looks like a CTranslate2 faster-whisper model directory."""
    if not path:
        return False
    path = Path(path)
    return (
        path.is_dir()
        and (path / "model.bin").is_file()
        and (path / "config.json").is_file()
    )


def resolve_custom_whisper_model(value) -> str | None:
    path = _custom_whisper_path(value)
    if path and is_faster_whisper_model_dir(path):
        return str(path.resolve())
    return None


def _is_builtin_whisper_cache(path: Path) -> bool:
    parts = set(path.parts)
    return any(f"models--Systran--faster-whisper-{s}" in parts for s in _WHISPER_SIZES)


def _hf_snapshot_name(path: Path) -> str | None:
    """Return 'org/repo' for .../models--org--repo/snapshots/<hash>."""
    if path.parent.name != "snapshots":
        return None
    repo_dir = path.parent.parent
    if not repo_dir.name.startswith("models--"):
        return None

    encoded = repo_dir.name.removeprefix("models--")
    parts = encoded.split("--", 1)
    if len(parts) != 2 or not all(parts):
        return None
    return f"{parts[0]}/{parts[1]}"


def list_local_faster_whisper_models() -> list[dict]:
    """Scan ./models for user-provided faster-whisper model directories."""
    if not MODELS_DIR.exists():
        return []

    entries: list[dict] = []
    name_counts: dict[str, int] = {}
    seen: set = set()
    try:
        model_bins = list(MODELS_DIR.rglob("model.bin"))
    except (OSError, PermissionError):
        return []

    for model_bin in model_bins:
        model_dir = model_bin.parent
        if _is_builtin_whisper_cache(model_dir):
            continue
        if not is_faster_whisper_model_dir(model_dir):
            continue
        try:
            resolved = str(model_dir.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        name = _hf_snapshot_name(model_dir) or model_dir.name
        name_counts[name] = name_counts.get(name, 0) + 1
        if name_counts[name] > 1:
            name = f"{name} ({model_dir.name[:8]})"
        entries.append({"name": name, "path": resolved})

    entries.sort(key=lambda item: item["name"].lower())
    return entries

def local_faster_whisper_display_name(path) -> str | None:
    """Return the same display name used by the local Whisper model selector."""
    resolved = resolve_custom_whisper_model(path)
    if not resolved:
        return None
    for item in list_local_faster_whisper_models():
        if item["path"] == resolved:
            return item["name"]
    return _hf_snapshot_name(Path(resolved)) or Path(resolved).name

def apply_cache_env():
    """Point all model caches to ./models/."""
    resolved = str(MODELS_DIR.resolve())
    os.environ["HF_HOME"] = os.path.join(resolved, "huggingface")
    os.environ["TORCH_HOME"] = os.path.join(resolved, "torch")
    # Windows without Developer Mode cannot create symlinks; huggingface_hub
    # then prints a scary (but harmless) warning wall on every download.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # The Xet-backed CDN intermittently fails with CAS/500 errors mid-download
    # (seen in the field at 86% of a SenseVoice fetch); the classic HTTP path
    # is slower but reliable. Users can re-enable Xet by setting the var to 0.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # huggingface_hub warns "You are sending unauthenticated requests to the HF
    # Hub" on every anonymous download, twice (its own stderr handler plus ours).
    # Setting the logger level cannot hold it: the library reconfigures its root
    # logger when it is first imported, which happens long after setup_logging.
    # This env var is read during that reconfiguration, so it survives.
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    log.info(f"Cache env set: {resolved}")


# True once apply_hf_token() has written the env vars itself. Guards the clear
# path so emptying the GUI field never deletes a token the user exported in
# their own shell/system environment.
_HF_TOKEN_FROM_SETTINGS = False


def apply_hf_token(token: str | None):
    """Publish the user's HuggingFace token to the env huggingface_hub reads.

    Anonymous downloads are rate-limited, which shows up as mid-download failures
    on slow links. The ASR worker is spawned after this runs and inherits the
    environment, so the token reaches it without being passed through the pipe."""
    global _HF_TOKEN_FROM_SETTINGS
    token = (token or "").strip()
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token
        _HF_TOKEN_FROM_SETTINGS = True
        log.info(f"HuggingFace token applied ({len(token)} chars)")
    elif _HF_TOKEN_FROM_SETTINGS:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
        _HF_TOKEN_FROM_SETTINGS = False
        log.info("HuggingFace token cleared")


def _has_silero_pkg() -> bool:
    """True when the silero-vad PyPI package (model bundled in wheel) is installed."""
    import importlib.util

    return importlib.util.find_spec("silero_vad") is not None


# The same file core/silero_onnx.py loads; the path is computed here
# independently so model_manager keeps zero imports from livetranslate.core.
_VENDORED_SILERO_ONNX = Path(__file__).resolve().parents[1] / "assets" / "silero_vad.onnx"


def is_silero_cached() -> bool:
    # The vendored ONNX model ships in the repo, so on a healthy checkout the
    # VAD never counts as missing — regardless of torch or the silero-vad
    # package. The package/hub checks remain for the torch (jit) profile and
    # for a checkout with the asset stripped.
    if _VENDORED_SILERO_ONNX.exists():
        return True
    if _has_silero_pkg():
        return True
    torch_hub = MODELS_DIR / "torch" / "hub"
    return any(torch_hub.glob("snakers4_silero-vad*")) if torch_hub.exists() else False


def _ms_model_path(org, name):
    """Return the first existing ModelScope cache path, or the default.

    Layouts by SDK version: {org}/{name} = <=1.37 with explicit cache_dir;
    models/{org}/{name} = 1.34~1.37 env-default cache, which >=1.38 keeps
    reusing as legacy even when cache_dir is passed (dots in names written
    as ___ by old SDKs); hub trees = older SDKs; models/{org}--{name}/
    snapshots/{revision} = >=1.38 fresh cache.
    """
    ms_root = MODELS_DIR / "modelscope"
    for sub in (
        ms_root / org / name,
        ms_root / "models" / org / name,
        ms_root / "models" / org / name.replace(".", "___"),
        ms_root / "hub" / "models" / org / name,
        ms_root / "hub" / org / name,
    ):
        if sub.exists():
            return sub
    snap_root = ms_root / "models" / f"{org}--{name}" / "snapshots"
    if snap_root.is_dir():
        snaps = sorted(d for d in snap_root.iterdir() if d.is_dir())
        if snaps:
            return snaps[-1]
    return ms_root / org / name


def _hf_repo_complete(org: str, name: str, min_bytes: int = 50_000_000) -> bool:
    """True if a HuggingFace repo cache exists AND finished downloading.

    A killed/aborted download leaves snapshot entries pointing at missing blobs
    (broken symlinks) or '.incomplete' blobs; treating that as cached makes the
    model load hang. Validate a snapshot where every file resolves (stat follows
    symlinks; a broken link raises) and the resolved bytes are substantial. This
    ignores orphan '.incomplete' blobs left behind by an earlier interrupted run.
    """
    snap_root = MODELS_DIR / "huggingface" / "hub" / f"models--{org}--{name}" / "snapshots"
    if not snap_root.exists():
        return False
    for snap in snap_root.iterdir():
        if not snap.is_dir():
            continue
        total = 0
        broken = False
        for f in snap.rglob("*"):
            if f.is_dir():
                continue
            try:
                total += f.stat().st_size
            except OSError:
                broken = True
                break
        if not broken and total >= min_bytes:
            return True
    return False


def is_asr_cached(engine_type, model_size="medium", hub="hf") -> bool:
    if engine_type == "funasr" or engine_type in FUNASR_LEGACY_ENGINE_ALIASES:
        model_key = (
            FUNASR_LEGACY_ENGINE_ALIASES[engine_type]
            if engine_type in FUNASR_LEGACY_ENGINE_ALIASES
            else normalize_funasr_model_key(model_size)
        )
        # Accept cache from either hub to avoid redundant downloads; the repo
        # namespace can differ between ModelScope and HuggingFace (SenseVoice).
        ms_org, ms_name = funasr_model_id(model_key, "ms").split("/")
        hf_org, hf_name = funasr_model_id(model_key, "hf").split("/")
        if not (
            _ms_model_path(ms_org, ms_name).exists()
            or _hf_repo_complete(hf_org, hf_name)
        ):
            return False
        # Nano's Qwen3-0.6B weights download separately; require them so the
        # download flow (not the deadline-bound worker) pulls them up-front.
        if funasr_profile(model_key)["family"] == "funasr-nano":
            model_dir = get_local_model_path(engine_type, hub, funasr_model=model_size)
            if not model_dir or not qwen_weights_present(model_dir):
                return False
        return True
    if engine_type == "sensevoice-onnx":
        return sensevoice_onnx_paths() is not None
    if engine_type == "anime-whisper":
        # HF-only (not published to ModelScope). Check that snapshots dir actually
        # contains weight files; an .incomplete blob means a prior run aborted mid-download.
        model_id = ASR_MODEL_IDS[engine_type]
        org, name = model_id.split("/")
        snap_root = (
            MODELS_DIR / "huggingface" / "hub" / f"models--{org}--{name}" / "snapshots"
        )
        if not snap_root.exists():
            return False
        for snap in snap_root.iterdir():
            if not snap.is_dir():
                continue
            has_weights = any(
                (snap / fn).exists()
                for fn in ("model.safetensors", "pytorch_model.bin")
            )
            has_config = (snap / "config.json").exists()
            if has_weights and has_config:
                return True
        return False
    elif engine_type == "whisper":
        if model_size not in _WHISPER_SIZES:
            return resolve_custom_whisper_model(model_size) is not None
        min_bytes = int(
            _MODEL_SIZE_BYTES.get(f"whisper-{model_size}", 50_000_000) * 0.5
        )
        return _hf_repo_complete(
            "Systran", f"faster-whisper-{model_size}", min_bytes=min_bytes
        )
    return True


def get_missing_models(engine, model_size, hub) -> list:
    missing = []
    if not is_silero_cached():
        missing.append(
            {
                "name": "Silero VAD",
                "type": "silero-vad",
                "estimated_bytes": _MODEL_SIZE_BYTES["silero-vad"],
            }
        )
    if not is_asr_cached(engine, model_size, hub):
        if engine == "whisper" and model_size not in _WHISPER_SIZES:
            return missing
        if engine == "funasr" or engine in FUNASR_LEGACY_ENGINE_ALIASES:
            model_key = (
                FUNASR_LEGACY_ENGINE_ALIASES[engine]
                if engine in FUNASR_LEGACY_ENGINE_ALIASES
                else normalize_funasr_model_key(model_size)
            )
            profile = funasr_profile(model_key)
            key = f"funasr:{model_key}"
            display = profile["display_name"]
            estimated_bytes = profile["estimated_bytes"]
        elif engine == "whisper":
            key = engine if engine != "whisper" else f"whisper-{model_size}"
            display = f"Whisper {model_size}"
            estimated_bytes = _MODEL_SIZE_BYTES.get(key, 0)
        else:
            key = engine
            display = ASR_DISPLAY_NAMES.get(engine, engine)
            estimated_bytes = _MODEL_SIZE_BYTES.get(key, 0)
        missing.append(
            {
                "name": display,
                "type": key,
                "estimated_bytes": estimated_bytes,
            }
        )
    return missing


def get_local_model_path(engine_type, hub="hf", funasr_model: str | None = None):
    """Return local snapshot path if model is cached, else None.

    Checks the preferred hub first, then falls back to the other hub.
    """
    if engine_type == "funasr" or engine_type in FUNASR_LEGACY_ENGINE_ALIASES:
        model_key = (
            FUNASR_LEGACY_ENGINE_ALIASES[engine_type]
            if engine_type in FUNASR_LEGACY_ENGINE_ALIASES
            else normalize_funasr_model_key(funasr_model)
        )
        ms_org, ms_name = funasr_model_id(model_key, "ms").split("/")
        hf_org, hf_name = funasr_model_id(model_key, "hf").split("/")
    elif engine_type in ASR_MODEL_IDS:
        ms_org, ms_name = asr_model_id(engine_type, "ms").split("/")
        hf_org, hf_name = asr_model_id(engine_type, "hf").split("/")
    else:
        return None

    def _try_ms():
        local = _ms_model_path(ms_org, ms_name)
        return str(local) if local.exists() else None

    def _try_hf():
        snap_dir = (
            MODELS_DIR
            / "huggingface"
            / "hub"
            / f"models--{hf_org}--{hf_name}"
            / "snapshots"
        )
        if snap_dir.exists():
            snaps = sorted(snap_dir.iterdir())
            if snaps:
                return str(snaps[-1])
        return None

    if hub == "ms":
        return _try_ms() or _try_hf()
    else:
        return _try_hf() or _try_ms()


def sensevoice_onnx_paths():
    """Locate the ONNX model + tokens in the HF cache, or None if incomplete.

    Returns (model_path, tokens_path). Only a snapshot holding BOTH files
    counts: an aborted download leaves one of them missing, and sherpa-onnx
    fails with an unhelpful error rather than reporting what is absent."""
    org, name = ASR_MODEL_IDS["sensevoice-onnx"].split("/")
    snap_root = (
        MODELS_DIR / "huggingface" / "hub" / f"models--{org}--{name}" / "snapshots"
    )
    if not snap_root.is_dir():
        return None
    for snap in sorted(snap_root.iterdir()):
        if not snap.is_dir():
            continue
        paths = [snap / f for f in SENSEVOICE_ONNX_FILES]
        try:
            if all(p.exists() and p.stat().st_size > 0 for p in paths):
                return tuple(str(p) for p in paths)
        except OSError:
            continue
    return None


def qwen_weights_present(model_dir) -> bool:
    """Whether a nano model's embedded Qwen3-0.6B weights are in place.

    Nano repos ship the Qwen3-0.6B config but not its weights. A variant without
    the subdir needs no Qwen weights, so absence of the subdir counts as present.
    """
    qwen_dir = Path(model_dir) / "Qwen3-0.6B"
    if not qwen_dir.is_dir():
        return True
    return any(f.suffix in (".safetensors", ".bin") for f in qwen_dir.iterdir())


def dir_size(path) -> int:
    total = 0
    try:
        for f in Path(path).rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except (OSError, PermissionError):
        pass
    return total


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.1f} MB"
    else:
        return f"{size_bytes / (1024**3):.2f} GB"


def get_cache_entries():
    """Scan ./models/ for cached models."""
    entries = []
    hf_base = MODELS_DIR / "huggingface" / "hub"
    torch_base = MODELS_DIR / "torch" / "hub"

    for entry in _CACHE_MODELS:
        if len(entry) == 3:
            name, engine, model_key = entry
        else:
            name, engine = entry
            model_key = None
        ms_org, ms_model = asr_model_id(engine, "ms", model_key).split("/")
        hf_org, hf_model = asr_model_id(engine, "hf", model_key).split("/")
        ms_path = _ms_model_path(ms_org, ms_model)
        hf_path = hf_base / f"models--{hf_org}--{hf_model}"
        if ms_path.exists():
            entries.append((f"{name} (ModelScope)", ms_path))
        if hf_path.exists():
            entries.append((f"{name} (HuggingFace)", hf_path))

    for size in _WHISPER_SIZES:
        hf_path = hf_base / f"models--Systran--faster-whisper-{size}"
        if hf_path.exists() and is_asr_cached("whisper", size, "hf"):
            entries.append((f"Whisper {size}", hf_path))

    for item in list_local_faster_whisper_models():
        entries.append((f"Whisper Local: {item['name']}", Path(item["path"])))

    if torch_base.exists():
        for d in sorted(torch_base.glob("snakers4_silero-vad*")):
            if d.is_dir():
                entries.append(("Silero VAD", d))
                break

    # Sweep for anything else the hub cache is holding: repos we do not have a
    # _CACHE_MODELS row for (Qwen3-0.6B leaves a refs-only stub behind, for one),
    # and known repos the loops above skipped because the download is incomplete.
    # Without this the tab under-reports disk usage and "delete all" leaves the
    # unlisted directories on disk, which reads as "the cache is empty" when it
    # is not.
    if hf_base.is_dir():
        claimed = {p.resolve() for _, p in entries}
        for d in sorted(hf_base.glob("models--*")):
            if not d.is_dir() or d.resolve() in claimed:
                continue
            entries.append((f"{d.name[len('models--'):].replace('--', '/')} (HuggingFace)", d))

    return entries
