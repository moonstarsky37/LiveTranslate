"""Characterization tests for model_manager.py (Phase 0 safety net).

Covers the pure id/normalization/migration helpers only. Nothing here triggers
a download, a network call, or a torch import; filesystem helpers are exercised
against pytest tmp_path so the real ./models cache is never touched.

Anything that looks like a bug is captured as-is and flagged with a NOTE
comment instead of being fixed.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import livetranslate.model_manager as mm  # noqa: E402


# --------------------------------------------------------------------------
# asr_model_id / funasr_model_id
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine,expected",
    [
        # Legacy engine aliases resolve through the FunASR profile table.
        ("sensevoice", "FunAudioLLM/SenseVoiceSmall"),
        ("funasr-nano", "FunAudioLLM/Fun-ASR-Nano-2512"),
        ("funasr-mlt-nano", "FunAudioLLM/Fun-ASR-MLT-Nano-2512"),
        # "funasr" with no model key falls back to the default profile.
        ("funasr", "FunAudioLLM/SenseVoiceSmall"),
        # Non-FunASR engines come straight from ASR_MODEL_IDS.
        ("anime-whisper", "litagin/anime-whisper"),
    ],
)
def test_asr_model_id_defaults_to_huggingface(engine, expected):
    assert mm.asr_model_id(engine) == expected
    assert mm.asr_model_id(engine, "hf") == expected


@pytest.mark.parametrize(
    "engine,expected",
    [
        # SenseVoice lives under a different namespace on ModelScope.
        ("sensevoice", "iic/SenseVoiceSmall"),
        ("funasr", "iic/SenseVoiceSmall"),
        ("funasr-nano", "FunAudioLLM/Fun-ASR-Nano-2512"),
        ("funasr-mlt-nano", "FunAudioLLM/Fun-ASR-MLT-Nano-2512"),
        # anime-whisper is HF-only, so "ms" returns the same id.
        ("anime-whisper", "litagin/anime-whisper"),
    ],
)
def test_asr_model_id_ms_hub_is_kept_for_legacy_cache_scanning(engine, expected):
    assert mm.asr_model_id(engine, "ms") == expected


def test_asr_model_id_honours_explicit_funasr_model_key():
    assert (
        mm.asr_model_id("funasr", "hf", "funasr-nano-2512")
        == "FunAudioLLM/Fun-ASR-Nano-2512"
    )
    assert (
        mm.asr_model_id("funasr", "ms", "funasr-mlt-nano-2512")
        == "FunAudioLLM/Fun-ASR-MLT-Nano-2512"
    )


def test_asr_model_id_unknown_funasr_key_falls_back_to_default_profile():
    assert mm.asr_model_id("funasr", "hf", "nope") == "FunAudioLLM/SenseVoiceSmall"


def test_asr_model_id_rejects_engines_without_a_repo_id():
    # "whisper" resolves through Systran/faster-whisper-<size> elsewhere and has
    # no entry here, so it raises rather than returning a sentinel.
    with pytest.raises(KeyError):
        mm.asr_model_id("whisper")
    with pytest.raises(KeyError):
        mm.asr_model_id("remote-whisper")


def test_asr_model_ids_hf_override_table_is_currently_unreachable():
    """NOTE (captured, not fixed): ASR_MODEL_IDS_HF only holds "sensevoice",
    which the legacy-alias branch intercepts first, so the override branch in
    asr_model_id() is dead code today. Kept as a tripwire in case the table
    grows a key that is not also a FunASR alias."""
    assert set(mm.ASR_MODEL_IDS_HF) <= set(mm.FUNASR_LEGACY_ENGINE_ALIASES)


@pytest.mark.parametrize(
    "model_key,hf_id,ms_id",
    [
        ("sensevoice-small", "FunAudioLLM/SenseVoiceSmall", "iic/SenseVoiceSmall"),
        (
            "funasr-nano-2512",
            "FunAudioLLM/Fun-ASR-Nano-2512",
            "FunAudioLLM/Fun-ASR-Nano-2512",
        ),
        (
            "funasr-mlt-nano-2512",
            "FunAudioLLM/Fun-ASR-MLT-Nano-2512",
            "FunAudioLLM/Fun-ASR-MLT-Nano-2512",
        ),
    ],
)
def test_funasr_model_id_per_hub(model_key, hf_id, ms_id):
    assert mm.funasr_model_id(model_key) == hf_id  # default hub is "hf"
    assert mm.funasr_model_id(model_key, "hf") == hf_id
    assert mm.funasr_model_id(model_key, "ms") == ms_id


def test_funasr_model_id_treats_any_non_ms_hub_as_huggingface():
    # The check is `hub != "ms"`, so unknown hub strings behave like "hf".
    assert mm.funasr_model_id("sensevoice-small", "bogus") == "FunAudioLLM/SenseVoiceSmall"


def test_funasr_model_id_none_uses_the_default_profile():
    assert mm.funasr_model_id(None) == "FunAudioLLM/SenseVoiceSmall"


def test_every_profile_id_is_org_slash_repo():
    # Callers split ids on "/" (get_local_model_path, is_asr_cached).
    for profile in mm.FUNASR_MODEL_PROFILES.values():
        for key in ("huggingface_id", "modelscope_id"):
            assert len(profile[key].split("/")) == 2


# --------------------------------------------------------------------------
# normalize_funasr_model_key / funasr_profile
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sensevoice-small", "sensevoice-small"),
        ("funasr-nano-2512", "funasr-nano-2512"),
        ("funasr-mlt-nano-2512", "funasr-mlt-nano-2512"),
        # Legacy engine names normalize into profile keys.
        ("sensevoice", "sensevoice-small"),
        ("funasr-nano", "funasr-nano-2512"),
        ("funasr-mlt-nano", "funasr-mlt-nano-2512"),
        # Anything unrecognized falls back to the default profile.
        (None, "sensevoice-small"),
        ("", "sensevoice-small"),
        ("whisper", "sensevoice-small"),
        ("Fun-ASR-Nano", "sensevoice-small"),  # case sensitive
    ],
)
def test_normalize_funasr_model_key(raw, expected):
    assert mm.normalize_funasr_model_key(raw) == expected


def test_default_funasr_model_is_a_real_profile_key():
    assert mm.DEFAULT_FUNASR_MODEL in mm.FUNASR_MODEL_PROFILES


def test_funasr_profile_returns_the_normalized_profile():
    assert mm.funasr_profile("sensevoice") is mm.FUNASR_MODEL_PROFILES["sensevoice-small"]
    assert mm.funasr_profile("garbage") is mm.FUNASR_MODEL_PROFILES[mm.DEFAULT_FUNASR_MODEL]


def test_funasr_profile_exposes_the_fields_callers_rely_on():
    profile = mm.funasr_profile("funasr-nano-2512")
    assert profile["display_name"] == "Fun-ASR-Nano"
    assert profile["family"] == "funasr-nano"
    assert profile["legacy_engine"] == "funasr-nano"
    assert profile["estimated_bytes"] > 0
    assert profile["supports_language"] is True


def test_profile_families_and_legacy_engines_round_trip():
    for key, profile in mm.FUNASR_MODEL_PROFILES.items():
        assert mm.FUNASR_LEGACY_ENGINE_ALIASES[profile["legacy_engine"]] == key


@pytest.mark.parametrize(
    "model_key,expected",
    [
        ("sensevoice-small", True),
        ("sensevoice", True),
        ("funasr-nano-2512", False),
        ("funasr-mlt-nano-2512", False),
        (None, True),  # default profile
    ],
)
def test_funasr_supports_padding(model_key, expected):
    assert mm.funasr_supports_padding(model_key) is expected


@pytest.mark.parametrize(
    "model_key,expected",
    [
        ("sensevoice-small", "SenseVoice Small"),
        ("funasr-nano", "Fun-ASR-Nano"),
        ("funasr-mlt-nano-2512", "Fun-ASR-MLT-Nano"),
        ("unknown", "SenseVoice Small"),
    ],
)
def test_funasr_display_name(model_key, expected):
    assert mm.funasr_display_name(model_key) == expected


def test_funasr_model_options_order_and_shape():
    assert mm.funasr_model_options() == [
        ("sensevoice-small", "SenseVoice Small"),
        ("funasr-nano-2512", "Fun-ASR-Nano"),
        ("funasr-mlt-nano-2512", "Fun-ASR-MLT-Nano"),
    ]


# --------------------------------------------------------------------------
# normalize_asr_engine_selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine,model_key,expected",
    [
        ("sensevoice", None, ("funasr", "sensevoice-small")),
        ("funasr-nano", None, ("funasr", "funasr-nano-2512")),
        ("funasr-mlt-nano", "sensevoice-small", ("funasr", "funasr-mlt-nano-2512")),
        ("funasr", "funasr-nano-2512", ("funasr", "funasr-nano-2512")),
        ("funasr", None, ("funasr", "sensevoice-small")),
        ("funasr", "bogus", ("funasr", "sensevoice-small")),
        # Non-FunASR engines keep their name but still get a FunASR model key.
        ("whisper", None, ("whisper", "sensevoice-small")),
        ("anime-whisper", "funasr-nano-2512", ("anime-whisper", "funasr-nano-2512")),
        # A missing engine defaults to funasr.
        (None, None, ("funasr", "sensevoice-small")),
        ("", None, ("funasr", "sensevoice-small")),
    ],
)
def test_normalize_asr_engine_selection(engine, model_key, expected):
    assert mm.normalize_asr_engine_selection(engine, model_key) == expected


def test_legacy_alias_wins_over_an_explicit_model_key():
    """NOTE (captured, not fixed): when the engine is a legacy alias, the
    supplied funasr_model argument is discarded entirely."""
    assert mm.normalize_asr_engine_selection("sensevoice", "funasr-nano-2512") == (
        "funasr",
        "sensevoice-small",
    )


# --------------------------------------------------------------------------
# migrate_funasr_settings
# --------------------------------------------------------------------------


@pytest.mark.parametrize("falsy", [None, {}])
def test_migrate_funasr_settings_passes_falsy_through_untouched(falsy):
    result = mm.migrate_funasr_settings(falsy)
    assert result is falsy
    if falsy is not None:
        assert result == {}  # no keys are injected into an empty dict


def test_migrate_funasr_settings_mutates_in_place_and_returns_same_object():
    settings = {"asr_engine": "sensevoice"}
    result = mm.migrate_funasr_settings(settings)
    assert result is settings


@pytest.mark.parametrize(
    "legacy_engine,expected_model",
    [
        ("sensevoice", "sensevoice-small"),
        ("funasr-nano", "funasr-nano-2512"),
        ("funasr-mlt-nano", "funasr-mlt-nano-2512"),
    ],
)
def test_migrate_funasr_settings_normalizes_legacy_engine_names(
    legacy_engine, expected_model
):
    settings = mm.migrate_funasr_settings({"asr_engine": legacy_engine})
    assert settings == {"asr_engine": "funasr", "funasr_model": expected_model}


def test_migrate_funasr_settings_repairs_an_invalid_funasr_model():
    settings = mm.migrate_funasr_settings({"asr_engine": "funasr", "funasr_model": "nope"})
    assert settings["funasr_model"] == "sensevoice-small"


def test_migrate_funasr_settings_preserves_unrelated_keys():
    settings = mm.migrate_funasr_settings(
        {"asr_engine": "sensevoice", "ui_lang": "zh-TW", "vad_threshold": 0.5}
    )
    assert settings["ui_lang"] == "zh-TW"
    assert settings["vad_threshold"] == 0.5


def test_migrate_funasr_settings_fills_a_default_model_for_other_engines():
    settings = mm.migrate_funasr_settings({"asr_engine": "whisper"})
    assert settings == {"asr_engine": "whisper", "funasr_model": "sensevoice-small"}


def test_migrate_funasr_settings_leaves_a_bogus_model_alone_for_other_engines():
    """NOTE (captured, not fixed): the non-FunASR branch uses setdefault, so an
    invalid funasr_model survives migration untouched and only gets normalized
    later, when the engine is switched back to funasr."""
    settings = mm.migrate_funasr_settings({"asr_engine": "whisper", "funasr_model": "nope"})
    assert settings["funasr_model"] == "nope"


def test_migrate_funasr_settings_adds_engine_key_when_only_other_keys_exist():
    settings = mm.migrate_funasr_settings({"ui_lang": "en"})
    assert settings["asr_engine"] == "funasr"
    assert settings["funasr_model"] == "sensevoice-small"


# --------------------------------------------------------------------------
# Pure path / formatting helpers (no network, tmp_path only)
# --------------------------------------------------------------------------


def test_is_faster_whisper_model_dir(tmp_path):
    assert mm.is_faster_whisper_model_dir(None) is False
    assert mm.is_faster_whisper_model_dir(tmp_path) is False
    (tmp_path / "model.bin").write_bytes(b"x")
    assert mm.is_faster_whisper_model_dir(tmp_path) is False  # config.json missing
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert mm.is_faster_whisper_model_dir(tmp_path) is True


def test_resolve_custom_whisper_model(tmp_path):
    # Built-in size names are not custom paths.
    assert mm.resolve_custom_whisper_model("medium") is None
    assert mm.resolve_custom_whisper_model("") is None
    assert mm.resolve_custom_whisper_model(None) is None
    # A path that is not a faster-whisper dir resolves to None.
    assert mm.resolve_custom_whisper_model(str(tmp_path)) is None
    (tmp_path / "model.bin").write_bytes(b"x")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert mm.resolve_custom_whisper_model(str(tmp_path)) == str(tmp_path.resolve())


@pytest.mark.parametrize(
    "path,expected",
    [
        ("cache/models--FunAudioLLM--SenseVoiceSmall/snapshots/abc123", "FunAudioLLM/SenseVoiceSmall"),
        ("cache/models--org--repo--extra/snapshots/deadbeef", "org/repo--extra"),
        ("cache/models--org--repo/blobs/abc", None),  # parent is not "snapshots"
        ("cache/org--repo/snapshots/abc", None),  # missing models-- prefix
        ("cache/models--onlyorg/snapshots/abc", None),  # no "--" separator
    ],
)
def test_hf_snapshot_name(path, expected):
    assert mm._hf_snapshot_name(Path(path)) == expected


@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024**3, "1.00 GB"),
        (3_100_000_000, "2.89 GB"),
    ],
)
def test_format_size(size, expected):
    assert mm.format_size(size) == expected


def test_dir_size_sums_files_and_survives_missing_paths(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"0123456789")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"012")
    assert mm.dir_size(tmp_path) == 13
    assert mm.dir_size(tmp_path / "missing") == 0


def test_qwen_weights_present(tmp_path):
    # No embedded Qwen subdir at all -> counted as "present" (nothing to fetch).
    assert mm.qwen_weights_present(tmp_path) is True
    qwen = tmp_path / "Qwen3-0.6B"
    qwen.mkdir()
    (qwen / "config.json").write_text("{}", encoding="utf-8")
    assert mm.qwen_weights_present(tmp_path) is False
    (qwen / "model.safetensors").write_bytes(b"x")
    assert mm.qwen_weights_present(tmp_path) is True


def test_neutralize_funasr_requirements_renames_the_file(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("gradio\n", encoding="utf-8")
    mm.neutralize_funasr_requirements(tmp_path)
    assert not req.exists()
    assert (tmp_path / "requirements.txt.bundled").read_text(encoding="utf-8") == "gradio\n"
    # No-ops on a missing dir/file instead of raising.
    mm.neutralize_funasr_requirements(tmp_path)
    mm.neutralize_funasr_requirements(None)


def test_models_dir_is_under_the_app_dir():
    assert mm.MODELS_DIR == mm.APP_DIR / "models"


# --------------------------------------------------------------------------
# get_cache_entries (tmp_path only — never touches the real ./models)
# --------------------------------------------------------------------------


def _hub(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MODELS_DIR", tmp_path)
    hub = tmp_path / "huggingface" / "hub"
    hub.mkdir(parents=True)
    return hub


def test_get_cache_entries_names_known_repos(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    (hub / "models--FunAudioLLM--SenseVoiceSmall").mkdir()
    names = [name for name, _ in mm.get_cache_entries()]
    assert names == ["SenseVoice Small (HuggingFace)"]


def test_get_cache_entries_lists_unknown_repos_too(tmp_path, monkeypatch):
    """A repo with no _CACHE_MODELS row must still be listed, or "delete all"
    silently leaves it on disk (Qwen3-0.6B leaves a refs-only stub behind)."""
    hub = _hub(tmp_path, monkeypatch)
    (hub / "models--Qwen--Qwen3-0.6B" / "refs").mkdir(parents=True)
    entries = mm.get_cache_entries()
    assert [name for name, _ in entries] == ["Qwen/Qwen3-0.6B (HuggingFace)"]
    assert entries[0][1] == hub / "models--Qwen--Qwen3-0.6B"


def test_get_cache_entries_does_not_list_a_repo_twice(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    (hub / "models--FunAudioLLM--SenseVoiceSmall").mkdir()
    (hub / "models--Qwen--Qwen3-0.6B").mkdir()
    paths = [p for _, p in mm.get_cache_entries()]
    assert len(paths) == len(set(paths)) == 2


def test_get_cache_entries_surfaces_an_incomplete_whisper_download(tmp_path, monkeypatch):
    """The whisper loop skips a dir that is not fully cached; the sweep must
    still show it, otherwise the aborted download is invisible disk usage."""
    hub = _hub(tmp_path, monkeypatch)
    (hub / "models--Systran--faster-whisper-medium" / "blobs").mkdir(parents=True)
    names = [name for name, _ in mm.get_cache_entries()]
    assert names == ["Systran/faster-whisper-medium (HuggingFace)"]


def test_get_cache_entries_ignores_non_repo_cache_files(tmp_path, monkeypatch):
    hub = _hub(tmp_path, monkeypatch)
    (hub / "CACHEDIR.TAG").write_text("Signature", encoding="utf-8")
    (hub / ".locks").mkdir()
    assert mm.get_cache_entries() == []
