"""Locale file consistency + language detection/mapping (spec section 5.2)."""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = ROOT / "sublume" / "i18n"
LOCALES = ("en.yaml", "zh-TW.yaml", "zh-CN.yaml")

sys.path.insert(0, str(ROOT))


def _keys(name: str) -> set[str]:
    data = yaml.safe_load((I18N_DIR / name).read_text("utf-8")) or {}
    return set(data.keys())


def test_locale_key_sets_are_identical():
    en = _keys("en.yaml")
    for name in LOCALES[1:]:
        keys = _keys(name)
        assert keys == en, (
            f"{name} key set differs from en.yaml: "
            f"missing={sorted(en - keys)} extra={sorted(keys - en)}"
        )


def test_hub_source_keys_are_gone():
    removed = {
        "hub_modelscope",
        "hub_modelscope_full",
        "hub_huggingface",
        "hub_huggingface_full",
        "group_download_source",
    }
    for name in LOCALES:
        assert not (_keys(name) & removed), f"{name} still contains hub-source keys"


def test_no_locale_value_is_empty():
    for name in LOCALES:
        data = yaml.safe_load((I18N_DIR / name).read_text("utf-8")) or {}
        empty = [k for k, v in data.items() if v is None or str(v).strip() == ""]
        assert not empty, f"{name} has empty values: {empty}"


def test_set_lang_maps_bare_zh_to_zh_tw():
    import sublume.i18n as i18n

    original = i18n.get_lang()
    try:
        i18n.set_lang("zh")
        assert i18n.get_lang() == "zh-TW"
    finally:
        i18n.set_lang(original)


def test_languages_list_splits_chinese():
    import sublume.i18n as i18n

    codes = [code for code, _name in i18n.LANGUAGES]
    assert "zh-TW" in codes
    assert "zh-CN" in codes
    assert "zh" not in codes
