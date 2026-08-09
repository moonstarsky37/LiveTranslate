"""SenseVoice ONNX backend: output contract and model-cache detection.

The ONNX engine has to be indistinguishable from the torch one downstream —
core/pipeline.py reads result["text"] and result["language"] and cannot tell
which backend produced them. These tests pin that contract plus the cache
checks, all without sherpa-onnx or a 239MB model: the parsing and path logic
is deliberately free of runtime imports so it stays testable in CI.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sublume.model_manager as mm  # noqa: E402
from sublume.asr.sensevoice_onnx import (  # noqa: E402
    LANG_MAP,
    SUPPORTED_LANGUAGES,
    normalize_language,
    parse_result,
)


# --------------------------------------------------------------------------
# parse_result — the transcript dict the pipeline consumes
# --------------------------------------------------------------------------


def test_language_comes_from_the_lang_attribute():
    out = parse_result("うちの中学は弁当制。", "<|ja|>")
    assert out == {
        "text": "うちの中学は弁当制。",
        "language": "ja",
        "language_name": "ja",
    }


def test_language_falls_back_to_a_tag_left_inside_the_text():
    """Older sherpa builds embed the tag instead of exposing result.lang."""
    out = parse_result("<|zh|><|NEUTRAL|><|Speech|>开放时间早上9点。", "")
    assert out["language"] == "zh"
    assert out["text"] == "开放时间早上9点。"


def test_all_tags_are_stripped_from_the_text():
    out = parse_result("<|en|><|HAPPY|><|BGM|><|withitn|>Hello there.", "<|en|>")
    assert out["text"] == "Hello there."
    assert "<|" not in out["text"]


def test_blank_and_tag_only_results_are_dropped():
    """A tag-only result means nothing was said; emitting it would put an empty
    bubble on the overlay."""
    assert parse_result("   ", "<|ja|>") is None
    assert parse_result("<|ja|><|EMO_UNKNOWN|><|Speech|>", "<|ja|>") is None
    assert parse_result("", "") is None


def test_unknown_language_tag_reports_auto():
    out = parse_result("something", "<|xx|>")
    assert out["language"] == "auto"


@pytest.mark.parametrize("tag,expected", list(LANG_MAP.items()))
def test_every_mapped_tag_round_trips(tag, expected):
    assert parse_result("text", tag)["language"] == expected


# --------------------------------------------------------------------------
# normalize_language — what sherpa-onnx accepts ("" means auto-detect)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ""),
        ("", ""),
        ("auto", ""),
        ("ja", "ja"),
        ("en", "en"),
        ("yue", "yue"),
        # The worker folds these already; folding again must be harmless.
        ("zh", "zh"),
        ("zh-TW", "zh"),
        ("zh-CN", "zh"),
        ("zh-HK", "zh"),
        # An unsupported language must degrade to auto-detect, not raise.
        ("de", ""),
        ("nonsense", ""),
    ],
)
def test_normalize_language(value, expected):
    assert normalize_language(value) == expected


def test_supported_languages_match_the_tag_map():
    assert SUPPORTED_LANGUAGES == set(LANG_MAP.values())


# --------------------------------------------------------------------------
# model registry / cache detection (tmp_path only)
# --------------------------------------------------------------------------


def test_engine_is_registered_with_a_display_name_and_size():
    assert "sensevoice-onnx" in mm.ASR_MODEL_IDS
    assert mm.ASR_DISPLAY_NAMES["sensevoice-onnx"] == "SenseVoice ONNX"
    # Only the int8 export is fetched; the fp32 one in the same repo is 937MB.
    assert mm._MODEL_SIZE_BYTES["sensevoice-onnx"] < 400_000_000


def test_only_the_two_needed_files_are_declared():
    assert mm.SENSEVOICE_ONNX_FILES == ("model.int8.onnx", "tokens.txt")


def _snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(mm.cache, "MODELS_DIR", tmp_path)
    org, name = mm.ASR_MODEL_IDS["sensevoice-onnx"].split("/")
    snap = (
        tmp_path / "huggingface" / "hub" / f"models--{org}--{name}" / "snapshots" / "abc"
    )
    snap.mkdir(parents=True)
    return snap


def test_paths_are_none_when_nothing_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(mm.cache, "MODELS_DIR", tmp_path)
    assert mm.sensevoice_onnx_paths() is None
    assert mm.is_asr_cached("sensevoice-onnx") is False


def test_a_half_finished_download_does_not_count_as_cached(tmp_path, monkeypatch):
    """One file present is the shape of an aborted download; sherpa-onnx would
    fail with an error that names neither file."""
    snap = _snapshot(tmp_path, monkeypatch)
    (snap / "model.int8.onnx").write_bytes(b"x")
    assert mm.sensevoice_onnx_paths() is None
    assert mm.is_asr_cached("sensevoice-onnx") is False


def test_zero_byte_files_do_not_count_as_cached(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch)
    (snap / "model.int8.onnx").write_bytes(b"")
    (snap / "tokens.txt").write_bytes(b"")
    assert mm.sensevoice_onnx_paths() is None


def test_both_files_present_reports_cached_with_paths(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch)
    (snap / "model.int8.onnx").write_bytes(b"x")
    (snap / "tokens.txt").write_bytes(b"y")
    paths = mm.sensevoice_onnx_paths()
    assert paths == (str(snap / "model.int8.onnx"), str(snap / "tokens.txt"))
    assert mm.is_asr_cached("sensevoice-onnx") is True


def test_missing_models_reports_the_engine_with_its_size(tmp_path, monkeypatch):
    # Patch on mm.cache, not mm: get_missing_models reads its own module global,
    # so patching the re-exported name is a no-op. It looks like it works on a
    # machine that has silero-vad installed, because then the real function
    # returns True anyway - CI, which does not, is where that lie shows up.
    monkeypatch.setattr(mm.cache, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mm.cache, "is_silero_cached", lambda: True)
    missing = mm.get_missing_models("sensevoice-onnx", None, "hf")
    assert [m["type"] for m in missing] == ["sensevoice-onnx"]
    assert missing[0]["estimated_bytes"] == mm._MODEL_SIZE_BYTES["sensevoice-onnx"]


def test_silero_patch_actually_reaches_get_missing_models(tmp_path, monkeypatch):
    """Guards the test above: if the patch target drifts again, the previous
    test would silently depend on whether silero-vad happens to be installed."""
    monkeypatch.setattr(mm.cache, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(mm.cache, "is_silero_cached", lambda: False)
    missing = mm.get_missing_models("sensevoice-onnx", None, "hf")
    assert [m["type"] for m in missing] == ["silero-vad", "sensevoice-onnx"]


def test_cache_tab_lists_the_onnx_model(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch)
    (snap / "model.int8.onnx").write_bytes(b"x")
    (snap / "tokens.txt").write_bytes(b"y")
    names = [name for name, _ in mm.get_cache_entries()]
    assert "SenseVoice ONNX (HuggingFace)" in names


def test_engine_selection_is_not_rewritten_to_funasr():
    """normalize_asr_engine_selection folds legacy aliases into funasr; a new
    engine must pass through untouched or the picker would snap back."""
    engine, _ = mm.normalize_asr_engine_selection("sensevoice-onnx", None)
    assert engine == "sensevoice-onnx"
