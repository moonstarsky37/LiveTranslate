"""SettingsStore — the single owner of user_settings.json / config.yaml.

Phase 2 goal: one module owns file paths, atomic writes, merge precedence and
migrations. These tests define that contract before the implementation exists.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sublume.config.store import SettingsStore  # noqa: E402


def make_store(tmp_path: Path) -> SettingsStore:
    factory = tmp_path / "config.yaml"
    factory.write_text(
        "translation:\n  api_base: \"http://localhost:1234/v1\"\n"
        "  target_language: \"zh-TW\"\n"
        "asr:\n  model_size: \"medium\"\n",
        encoding="utf-8",
    )
    return SettingsStore(
        settings_path=tmp_path / "user_settings.json",
        factory_path=factory,
    )


def test_load_returns_none_when_file_missing(tmp_path):
    store = make_store(tmp_path)
    assert store.exists() is False
    assert store.load() is None


def test_load_returns_none_on_corrupted_json(tmp_path):
    store = make_store(tmp_path)
    store.settings_path.write_text("{not json", encoding="utf-8")
    assert store.load() is None


def test_save_then_load_round_trips(tmp_path):
    store = make_store(tmp_path)
    # funasr_model included: load() applies in-memory funasr normalization,
    # which fills that key for funasr engines — a complete dict round-trips 1:1.
    data = {
        "hub": "hf",
        "asr_engine": "funasr",
        "funasr_model": "sensevoice-small",
        "models": [{"name": "m"}],
    }
    store.save(data)
    assert store.exists() is True
    assert store.load() == data


def test_save_is_atomic_no_tmp_residue(tmp_path):
    store = make_store(tmp_path)
    store.save({"hub": "hf"})
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert json.loads(store.settings_path.read_text(encoding="utf-8")) == {"hub": "hf"}


def test_fork_migrations_applied_and_written_back(tmp_path):
    store = make_store(tmp_path)
    legacy = {
        "hub": "ms",
        "target_language": "zh",
        "ui_lang": "zh",
        "asr_engine": "funasr",
    }
    store.settings_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load()
    assert loaded["hub"] == "hf"
    assert loaded["target_language"] == "zh-TW"
    assert loaded["ui_lang"] == "zh-TW"
    # Migration must persist so the next launch does not re-migrate
    on_disk = json.loads(store.settings_path.read_text(encoding="utf-8"))
    assert on_disk["hub"] == "hf"
    assert on_disk["target_language"] == "zh-TW"


def test_no_write_back_when_nothing_to_migrate(tmp_path):
    store = make_store(tmp_path)
    current = {"hub": "hf", "target_language": "zh-TW"}
    store.settings_path.write_text(json.dumps(current), encoding="utf-8")
    before = store.settings_path.read_text(encoding="utf-8")
    store.load()
    assert store.settings_path.read_text(encoding="utf-8") == before


def test_legacy_funasr_engine_names_migrate(tmp_path):
    # Legacy engine aliases (e.g. "sensevoice") normalize to funasr + model key,
    # same as model_manager.migrate_funasr_settings does today.
    store = make_store(tmp_path)
    store.settings_path.write_text(
        json.dumps({"asr_engine": "sensevoice"}), encoding="utf-8"
    )
    loaded = store.load()
    assert loaded["asr_engine"] == "funasr"
    assert loaded["funasr_model"] == "sensevoice-small"


def test_factory_defaults_reads_config_yaml(tmp_path):
    store = make_store(tmp_path)
    factory = store.factory_defaults()
    assert factory["translation"]["api_base"] == "http://localhost:1234/v1"
    assert factory["asr"]["model_size"] == "medium"


def test_factory_defaults_is_read_only_copy(tmp_path):
    store = make_store(tmp_path)
    first = store.factory_defaults()
    first["translation"]["api_base"] = "mutated"
    assert store.factory_defaults()["translation"]["api_base"] == "http://localhost:1234/v1"
