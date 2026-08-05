"""Characterization tests for i18n.py fallback + detection (Phase 0 safety net).

Complements tests/unit/test_i18n.py (locale file consistency) by pinning down
the runtime behavior of set_lang()/t()/_detect_system_lang(). The module keeps
global state, so every test restores the previously active language.

No PyQt6, no torch, no network.
"""

import locale
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import livetranslate.i18n as i18n  # noqa: E402

I18N_DIR = ROOT / "livetranslate" / "i18n"

# A key that exists in every locale file and has a distinct value per language.
SAMPLE_KEY = "translating"


def _locale_data(name: str) -> dict:
    return yaml.safe_load((I18N_DIR / name).read_text("utf-8")) or {}


EN = _locale_data("en.yaml")
ZH_TW = _locale_data("zh-TW.yaml")
ZH_CN = _locale_data("zh-CN.yaml")


@pytest.fixture(autouse=True)
def restore_language():
    """Guard the module-level language state so test order cannot leak."""
    original = i18n.get_lang()
    try:
        yield
    finally:
        i18n.set_lang(original)


# --------------------------------------------------------------------------
# Preconditions for the fallback assertions below
# --------------------------------------------------------------------------


def test_sample_key_differs_across_locales():
    assert SAMPLE_KEY in EN and SAMPLE_KEY in ZH_TW and SAMPLE_KEY in ZH_CN
    assert EN[SAMPLE_KEY] != ZH_TW[SAMPLE_KEY]
    assert ZH_TW[SAMPLE_KEY] != ZH_CN[SAMPLE_KEY]


def test_only_three_locale_files_ship():
    assert sorted(p.name for p in I18N_DIR.glob("*.yaml")) == [
        "en.yaml",
        "zh-CN.yaml",
        "zh-TW.yaml",
    ]


# --------------------------------------------------------------------------
# set_lang fallback chain
# --------------------------------------------------------------------------


def test_unknown_language_code_falls_back_to_zh_tw_strings():
    i18n.set_lang("nonexistent-code")
    assert i18n.t(SAMPLE_KEY) == ZH_TW[SAMPLE_KEY]
    assert i18n.t(SAMPLE_KEY) != EN[SAMPLE_KEY]


def test_unknown_language_code_still_reports_itself_from_get_lang():
    """NOTE (captured, not fixed): get_lang() echoes back whatever code was
    requested, even when the strings actually loaded came from the zh-TW
    fallback file. Callers that persist get_lang() will store a dead code."""
    i18n.set_lang("nonexistent-code")
    assert i18n.get_lang() == "nonexistent-code"


@pytest.mark.parametrize("bogus", ["fr", "ja", "de-CH", "xx", "zh_TW"])
def test_locales_without_a_yaml_all_land_on_zh_tw(bogus):
    # Only en/zh-TW/zh-CN ship; every other code (including an underscore
    # separator instead of a hyphen) silently gets Traditional Chinese strings.
    i18n.set_lang(bogus)
    assert i18n.t(SAMPLE_KEY) == ZH_TW[SAMPLE_KEY]


def test_language_code_casing_follows_the_filesystem():
    """NOTE (captured, not fixed): set_lang() resolves the locale file with a
    bare Path.exists(), so code casing is only significant on a case-sensitive
    filesystem. On Windows "EN" loads en.yaml; on Linux it would fall back to
    zh-TW. The assertion follows the host filesystem so it stays honest either
    way -- worth normalizing the code before the lookup during repackaging."""
    i18n.set_lang("EN")
    if (I18N_DIR / "EN.yaml").exists():
        assert i18n.t(SAMPLE_KEY) == EN[SAMPLE_KEY]
    else:
        assert i18n.t(SAMPLE_KEY) == ZH_TW[SAMPLE_KEY]


def test_empty_language_code_falls_back_to_zh_tw():
    i18n.set_lang("")
    assert i18n.get_lang() == ""
    assert i18n.t(SAMPLE_KEY) == ZH_TW[SAMPLE_KEY]


def test_bare_zh_loads_zh_tw_strings():
    i18n.set_lang("zh")
    assert i18n.get_lang() == "zh-TW"
    assert i18n.t(SAMPLE_KEY) == ZH_TW[SAMPLE_KEY]


# --------------------------------------------------------------------------
# t() lookups
# --------------------------------------------------------------------------


def test_set_lang_en_then_t_returns_english_values():
    i18n.set_lang("en")
    assert i18n.get_lang() == "en"
    assert i18n.t(SAMPLE_KEY) == EN[SAMPLE_KEY]
    assert i18n.t("window_control_panel") == EN["window_control_panel"]


def test_set_lang_zh_cn_then_t_returns_simplified_values():
    i18n.set_lang("zh-CN")
    assert i18n.t(SAMPLE_KEY) == ZH_CN[SAMPLE_KEY]


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN"])
def test_unknown_key_returns_the_key_itself(lang):
    i18n.set_lang(lang)
    assert i18n.t("definitely_not_a_real_key") == "definitely_not_a_real_key"
    assert i18n.t("") == ""


def test_switching_language_replaces_the_whole_string_table():
    i18n.set_lang("en")
    english = i18n.t(SAMPLE_KEY)
    i18n.set_lang("zh-TW")
    assert i18n.t(SAMPLE_KEY) != english


def test_every_locale_resolves_all_english_keys():
    for lang in ("en", "zh-TW", "zh-CN"):
        i18n.set_lang(lang)
        unresolved = [key for key in EN if i18n.t(key) == key and EN[key] != key]
        assert not unresolved, f"{lang} leaves keys unresolved: {unresolved}"


# --------------------------------------------------------------------------
# _detect_system_lang
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system_locale,expected",
    [
        ("zh_TW", "zh-TW"),
        ("zh_HK", "zh-TW"),
        ("zh_MO", "zh-TW"),
        ("zh-TW", "zh-TW"),  # hyphen form is normalized to underscore first
        ("zh_tw", "zh-TW"),  # region casing is ignored
        ("zh_CN", "zh-CN"),
        ("zh_SG", "zh-CN"),
        ("zh", "zh-CN"),  # bare zh has no region -> Simplified
        ("Chinese_Taiwan", "en"),  # only a "zh" prefix is recognized
        ("en_US", "en"),
        ("ja_JP", "en"),
        ("", "en"),
    ],
)
def test_detect_system_lang_mapping(monkeypatch, system_locale, expected):
    monkeypatch.setattr(locale, "getdefaultlocale", lambda: (system_locale, "UTF-8"))
    assert i18n._detect_system_lang() == expected


def test_detect_system_lang_treats_bcp47_script_tag_as_simplified(monkeypatch):
    """NOTE (captured, not fixed): a script-tagged locale puts "Hant" in the
    region slot, so zh-Hant-TW is misdetected as Simplified Chinese. Windows
    reports "zh_TW" here, which is why this has not bitten in practice."""
    monkeypatch.setattr(locale, "getdefaultlocale", lambda: ("zh-Hant-TW", "UTF-8"))
    assert i18n._detect_system_lang() == "zh-CN"


def test_detect_system_lang_handles_a_missing_locale(monkeypatch):
    monkeypatch.setattr(locale, "getdefaultlocale", lambda: (None, None))
    assert i18n._detect_system_lang() == "en"


def test_detect_system_lang_swallows_errors(monkeypatch):
    def _boom():
        raise ValueError("unknown locale")

    monkeypatch.setattr(locale, "getdefaultlocale", _boom)
    assert i18n._detect_system_lang() == "en"


def test_detect_system_lang_does_not_mutate_the_active_language(monkeypatch):
    i18n.set_lang("en")
    monkeypatch.setattr(locale, "getdefaultlocale", lambda: ("zh_TW", "UTF-8"))
    i18n._detect_system_lang()
    assert i18n.get_lang() == "en"


def test_import_time_detection_left_a_loadable_language():
    # The module calls set_lang(_detect_system_lang()) at import.
    assert i18n.get_lang()
    assert i18n.t(SAMPLE_KEY) != SAMPLE_KEY


# --------------------------------------------------------------------------
# Shared language tables
# --------------------------------------------------------------------------


def test_common_lang_codes_are_a_subset_of_languages():
    codes = {code for code, _name in i18n.LANGUAGES}
    assert i18n.COMMON_LANG_CODES <= codes


def test_auto_is_the_only_language_without_a_native_name():
    missing = [code for code, name in i18n.LANGUAGES if not name]
    assert missing == ["auto"]


def test_language_codes_are_unique():
    codes = [code for code, _name in i18n.LANGUAGES]
    assert len(codes) == len(set(codes))
