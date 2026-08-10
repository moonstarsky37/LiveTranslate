"""Characterization tests for translator.py (Phase 0 safety net).

These pin down the CURRENT behavior of the prompt builder, the single request
assembly point and the pure helpers, so the upcoming repackaging can be proven
behavior-preserving. Nothing here imports PyQt6 or torch, and no HTTP is ever
performed: `make_openai_client` is monkeypatched with a stub client.

Anything that looks like a bug is captured as-is and flagged with a NOTE
comment instead of being fixed.
"""

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sublume.translation.translator as translator_mod  # noqa: E402
from sublume.translation.translator import (  # noqa: E402
    DEFAULT_PROMPT,
    LANGUAGE_DISPLAY,
    PROMPT_PRESETS,
    RepetitionError,
    Translator,
    _OVERRIDE_KEYS,
)


# --------------------------------------------------------------------------
# Test doubles (no network)
# --------------------------------------------------------------------------


class _FakeCompletions:
    """Records every create() kwargs dict and replays a scripted response."""

    def __init__(self):
        self.calls: list[dict] = []
        self.response = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def last_kwargs(self) -> dict:
        return self.calls[-1]


class _FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions())
        self.copy_calls: list[dict] = []

    def copy(self, **kwargs):
        self.copy_calls.append(kwargs)
        return self


def _sync_response(content, prompt_tokens=None, completion_tokens=None):
    usage = None
    if prompt_tokens is not None:
        usage = types.SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(content=content))
        ],
        usage=usage,
    )


def _chunk(content=None, usage=None):
    choices = []
    if content is not None:
        choices = [
            types.SimpleNamespace(delta=types.SimpleNamespace(content=content))
        ]
    return types.SimpleNamespace(choices=choices, usage=usage)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(translator_mod, "make_openai_client", lambda *a, **k: client)
    return client


@pytest.fixture
def make_translator(fake_client):
    """Build a Translator wired to the stub client."""

    def _make(**kwargs):
        params = {
            "api_base": "http://localhost:8080/v1",
            "api_key": "dummy-key",
            "model": "test-model",
        }
        params.update(kwargs)
        return Translator(**params)

    return _make


# --------------------------------------------------------------------------
# LANGUAGE_DISPLAY
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("zh", "Traditional Chinese (Taiwan)"),
        ("zh-TW", "Traditional Chinese (Taiwan)"),
        ("zh-CN", "Simplified Chinese"),
        ("en", "English"),
        ("ja", "Japanese"),
        ("ko", "Korean"),
    ],
)
def test_language_display_codes(code, expected):
    assert LANGUAGE_DISPLAY[code] == expected


def test_language_display_never_says_bare_chinese():
    """The Traditional/Simplified split is deliberate: small models emit
    Simplified characters when told only "Chinese"."""
    assert "Chinese" not in set(LANGUAGE_DISPLAY.values())


def test_language_display_has_no_lowercase_zh_tw_variant():
    # Only the exact casing used by the UI language list is present.
    assert "zh-tw" not in LANGUAGE_DISPLAY
    assert "zh_TW" not in LANGUAGE_DISPLAY


# --------------------------------------------------------------------------
# Translator._build_system_prompt
# --------------------------------------------------------------------------


def test_build_system_prompt_substitutes_display_names(make_translator):
    tr = make_translator(target_language="zh-TW")
    prompt = tr._build_system_prompt("ja")
    assert "Translate Japanese into Traditional Chinese (Taiwan)." in prompt
    assert "{source_lang}" not in prompt
    assert "{target_lang}" not in prompt


def test_build_system_prompt_passes_unknown_codes_through_verbatim(make_translator):
    # An unmapped code is inserted raw rather than raising.
    tr = make_translator(target_language="xx-YY")
    prompt = tr._build_system_prompt("qq")
    assert "Translate qq into xx-YY." in prompt


def test_build_system_prompt_uses_custom_template(make_translator):
    tr = make_translator(
        system_prompt="From {source_lang} to {target_lang}. Go.",
        target_language="zh-CN",
    )
    assert tr._build_system_prompt("en") == "From English to Simplified Chinese. Go."


def test_build_system_prompt_falls_back_on_unknown_placeholder(make_translator):
    tr = make_translator(system_prompt="Translate this {bogus} now", target_language="zh-TW")
    prompt = tr._build_system_prompt("en")
    assert prompt == DEFAULT_PROMPT.format(
        source_lang="English", target_lang="Traditional Chinese (Taiwan)"
    )
    assert "bogus" not in prompt


def test_build_system_prompt_falls_back_on_literal_braces(make_translator):
    """NOTE (captured, not fixed): a user template containing a literal JSON
    example (unescaped braces) is treated as malformed and silently replaced by
    DEFAULT_PROMPT -- str.format reads `{"t"}` as a field name."""
    tr = make_translator(
        system_prompt='Translate {source_lang}. Reply like {"t": "..."}',
        target_language="zh-TW",
    )
    prompt = tr._build_system_prompt("en")
    assert prompt == DEFAULT_PROMPT.format(
        source_lang="English", target_lang="Traditional Chinese (Taiwan)"
    )


def test_build_system_prompt_falls_back_on_positional_placeholder(make_translator):
    # `{}` raises IndexError inside format(), which is also caught.
    tr = make_translator(system_prompt="Translate {} please")
    assert tr._build_system_prompt("en").startswith("You are a real-time subtitle translator.")


def test_build_system_prompt_appends_json_instruction(make_translator):
    tr = make_translator(json_response=True)
    prompt = tr._build_system_prompt("en")
    assert prompt.endswith('\nRespond in JSON format: {"t": "translated text"}')


def test_build_system_prompt_appends_json_instruction_after_fallback(make_translator):
    tr = make_translator(system_prompt="{bogus}", json_response=True)
    prompt = tr._build_system_prompt("en")
    assert prompt.startswith("You are a real-time subtitle translator.")
    assert prompt.endswith('\nRespond in JSON format: {"t": "translated text"}')


def test_build_system_prompt_renders_context_placeholder(make_translator):
    tr = make_translator(system_prompt="Ctx:\n{context}\n--{source_lang}>{target_lang}")
    tr.set_context_turns(2)
    tr._append_history("hello", "你好")
    tr._append_history("world", "世界")
    prompt = tr._build_system_prompt("en")
    assert "Source: hello\nTranslation: 你好" in prompt
    assert "Source: world\nTranslation: 世界" in prompt


def test_context_is_empty_without_turns_or_history(make_translator):
    tr = make_translator(system_prompt="[{context}]{source_lang}{target_lang}")
    # context_turns defaults to 0 -> no context even if history exists
    tr._history.append(("a", "b"))
    assert tr._format_context() == ""
    assert tr._build_system_prompt("en").startswith("[]")


def test_format_context_keeps_only_the_last_n_turns(make_translator):
    tr = make_translator()
    tr.set_context_turns(1)
    tr._history.extend([("old", "OLD"), ("new", "NEW")])
    rendered = tr._format_context()
    assert "old" not in rendered
    assert rendered == "Source: new\nTranslation: NEW"


def test_set_context_turns_zero_clears_history(make_translator):
    tr = make_translator()
    tr.set_context_turns(2)
    tr._append_history("a", "A")
    assert tr._history
    tr.set_context_turns(0)
    assert tr._history == []


@pytest.mark.parametrize("preset", sorted(PROMPT_PRESETS))
def test_all_prompt_presets_format_cleanly(make_translator, preset):
    tr = make_translator(system_prompt=PROMPT_PRESETS[preset], target_language="zh-TW")
    prompt = tr._build_system_prompt("ja")
    assert "Japanese" in prompt and "Traditional Chinese (Taiwan)" in prompt
    # A preset that failed to format would have been replaced by DEFAULT_PROMPT.
    assert prompt != DEFAULT_PROMPT.format(
        source_lang="Japanese", target_lang="Traditional Chinese (Taiwan)"
    )


# --------------------------------------------------------------------------
# Translator._build_messages
# --------------------------------------------------------------------------


def test_build_messages_default_shape(make_translator):
    tr = make_translator()
    msgs = tr._build_messages("SYS", "hello")
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hello"},
    ]


def test_build_messages_no_system_role_merges_into_one_user_turn(make_translator):
    tr = make_translator(no_system_role=True)
    assert tr._build_messages("SYS", "hello") == [
        {"role": "user", "content": "SYS\nhello"}
    ]


def test_build_messages_appends_history_when_template_has_no_context(make_translator):
    tr = make_translator()
    tr.set_context_turns(2)
    tr._append_history("hi", "HI")
    msgs = tr._build_messages("SYS", "next")
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "HI"},
        {"role": "user", "content": "next"},
    ]


def test_build_messages_skips_history_when_template_uses_context(make_translator):
    # History is rendered into the system prompt instead, never duplicated.
    tr = make_translator(system_prompt="{source_lang}{target_lang}{context}")
    tr.set_context_turns(2)
    tr._append_history("hi", "HI")
    msgs = tr._build_messages("SYS", "next")
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "next"},
    ]


def test_build_messages_drops_history_in_no_system_role_mode(make_translator):
    """NOTE (captured, not fixed): with no_system_role=True the conversation
    history is never sent, even when context turns are enabled."""
    tr = make_translator(no_system_role=True)
    tr.set_context_turns(3)
    tr._append_history("hi", "HI")
    assert tr._build_messages("SYS", "next") == [
        {"role": "user", "content": "SYS\nnext"}
    ]


# --------------------------------------------------------------------------
# Translator._build_request_kwargs -- the single request assembly point
# --------------------------------------------------------------------------


def test_request_kwargs_defaults(make_translator):
    tr = make_translator(max_tokens=256, temperature=0.3)
    kwargs = tr._build_request_kwargs("SYS", "text")
    assert kwargs == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "text"},
        ],
        "max_tokens": 256,
        "temperature": 0.3,
    }
    assert "extra_body" not in kwargs
    assert "response_format" not in kwargs
    assert "stream" not in kwargs


def test_request_kwargs_stream_flag(make_translator):
    tr = make_translator()
    assert tr._build_request_kwargs("SYS", "text", stream=True)["stream"] is True


def test_request_kwargs_sends_only_present_overrides(make_translator):
    tr = make_translator(overrides={"top_p": 0.9, "seed": 42})
    kwargs = tr._build_request_kwargs("SYS", "text")
    assert kwargs["top_p"] == 0.9
    assert kwargs["seed"] == 42
    for key in _OVERRIDE_KEYS:
        if key not in ("top_p", "seed", "temperature", "max_tokens"):
            assert key not in kwargs


def test_request_kwargs_drops_none_valued_overrides(make_translator):
    tr = make_translator(overrides={"top_p": None, "seed": 0, "frequency_penalty": None})
    kwargs = tr._build_request_kwargs("SYS", "text")
    assert "top_p" not in kwargs
    assert "frequency_penalty" not in kwargs
    # 0 is not None, so it survives the filter.
    assert kwargs["seed"] == 0


def test_request_kwargs_overrides_replace_constructor_defaults(make_translator):
    tr = make_translator(
        max_tokens=256, temperature=0.3, overrides={"temperature": 0.0, "max_tokens": 64}
    )
    kwargs = tr._build_request_kwargs("SYS", "text")
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 64


def test_request_kwargs_ignores_unknown_override_keys(make_translator):
    tr = make_translator(overrides={"top_k": 40, "repetition_penalty": 1.1})
    kwargs = tr._build_request_kwargs("SYS", "text")
    assert "top_k" not in kwargs
    assert "repetition_penalty" not in kwargs


def test_request_kwargs_no_think_only(make_translator):
    tr = make_translator(no_think=True)
    assert tr._build_request_kwargs("SYS", "text")["extra_body"] == {
        "enable_thinking": False
    }


def test_request_kwargs_extra_body_only(make_translator):
    tr = make_translator(extra_body={"chat_template_kwargs": {"thinking": False}})
    assert tr._build_request_kwargs("SYS", "text")["extra_body"] == {
        "chat_template_kwargs": {"thinking": False}
    }


def test_request_kwargs_merges_no_think_with_extra_body(make_translator):
    tr = make_translator(no_think=True, extra_body={"cache_prompt": True})
    assert tr._build_request_kwargs("SYS", "text")["extra_body"] == {
        "enable_thinking": False,
        "cache_prompt": True,
    }


def test_request_kwargs_extra_body_wins_over_no_think(make_translator):
    """NOTE (captured, not fixed): extra_body is merged last, so an explicit
    enable_thinking=True in extra_body silently defeats no_think=True."""
    tr = make_translator(no_think=True, extra_body={"enable_thinking": True})
    assert tr._build_request_kwargs("SYS", "text")["extra_body"] == {
        "enable_thinking": True
    }


def test_request_kwargs_extra_body_is_copied_not_aliased(make_translator):
    supplied = {"cache_prompt": True}
    tr = make_translator(extra_body=supplied)
    supplied["cache_prompt"] = False
    assert tr._build_request_kwargs("SYS", "t")["extra_body"] == {"cache_prompt": True}


def test_request_kwargs_json_response_schema(make_translator):
    tr = make_translator(json_response=True)
    assert tr._build_request_kwargs("SYS", "text")["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "translation",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"t": {"type": "string"}},
                "required": ["t"],
                "additionalProperties": False,
            },
        },
    }


def test_request_kwargs_all_features_together(make_translator):
    tr = make_translator(
        json_response=True,
        no_think=True,
        extra_body={"cache_prompt": True},
        overrides={"top_p": 0.8},
    )
    kwargs = tr._build_request_kwargs("SYS", "text", stream=True)
    assert set(kwargs) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "top_p",
        "extra_body",
        "response_format",
        "stream",
    }


# --------------------------------------------------------------------------
# Pure helpers: repetition detection and JSON extraction
# --------------------------------------------------------------------------


def test_repetition_error_is_an_exception():
    assert issubclass(RepetitionError, Exception)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        (None, False),
        ("short repeated abab", False),
        # Under the 40-char floor, repetition is deliberately not reported.
        ("abcdefgh" * 4, False),  # 32 chars
        ("0123456789" * 4, True),  # exactly 40 chars, period 10
        ("abcdefghij" * 6, True),
        ("你好世界" * 10, True),
        (
            "The quick brown fox jumps over the lazy dog near the riverbank at dawn.",
            False,
        ),
    ],
)
def test_check_repetition(text, expected):
    assert Translator._check_repetition(text) is expected


def test_check_repetition_is_a_static_pure_function():
    # Callable without an instance -- safe to move during repackaging.
    assert Translator._check_repetition("ab" * 40) is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"t": "hello"}', "hello"),
        ('{"t": ""}', ""),
        ('  {"t": "hi"}', "hi"),
        ('{"text": "hi"}', '{"text": "hi"}'),  # missing key -> raw passthrough
        ("not json at all", "not json at all"),
        ('["t"]', '["t"]'),  # JSON list is not a dict -> raw passthrough
        ("", ""),
    ],
)
def test_extract_json_translation(make_translator, raw, expected):
    tr = make_translator(json_response=True)
    assert tr._extract_json_translation(raw) == expected


# --------------------------------------------------------------------------
# translate() / translate_iter() against the stub client
# --------------------------------------------------------------------------


def test_translate_sync_returns_stripped_content(make_translator, fake_client):
    tr = make_translator(streaming=False)
    fake_client.chat.completions.response = _sync_response("  你好  ", 11, 3)
    assert tr.translate("hello", "en") == "你好"
    assert tr.last_usage == (11, 3)
    sent = fake_client.chat.completions.last_kwargs
    assert sent["model"] == "test-model"
    assert "stream" not in sent


def test_translate_sync_zeroes_usage_when_response_has_none(make_translator, fake_client):
    tr = make_translator(streaming=False)
    fake_client.chat.completions.response = _sync_response("ok")
    tr.translate("hello")
    assert tr.last_usage == (0, 0)


def test_translate_defaults_source_language_to_english(make_translator, fake_client):
    tr = make_translator(streaming=False)
    fake_client.chat.completions.response = _sync_response("ok")
    tr.translate("hello")
    system_msg = fake_client.chat.completions.last_kwargs["messages"][0]["content"]
    assert "Translate English into" in system_msg


def test_translate_sync_extracts_json_payload(make_translator, fake_client):
    tr = make_translator(streaming=False, json_response=True)
    fake_client.chat.completions.response = _sync_response('{"t": "你好"}')
    assert tr.translate("hello") == "你好"


def test_translate_raises_repetition_error_and_skips_history(make_translator, fake_client):
    tr = make_translator(streaming=False)
    tr.set_context_turns(2)
    fake_client.chat.completions.response = _sync_response("abcdefghij" * 6)
    with pytest.raises(RepetitionError):
        tr.translate("hello")
    assert tr._history == []


def test_translate_appends_history_only_when_context_enabled(make_translator, fake_client):
    fake_client.chat.completions.response = _sync_response("HI")

    off = make_translator(streaming=False)
    off.translate("hi")
    assert off._history == []

    on = make_translator(streaming=False)
    on.set_context_turns(1)
    on.translate("hi")
    assert on._history == [("hi", "HI")]


def test_translate_streaming_joins_chunks_and_records_usage(make_translator, fake_client):
    tr = make_translator(streaming=True)
    usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=2)
    fake_client.chat.completions.response = [
        _chunk("你"),
        _chunk("好 "),
        _chunk(usage=usage),
    ]
    assert tr.translate("hello") == "你好"
    assert tr.last_usage == (7, 2)
    # The first attempt asks for usage via stream_options.
    assert fake_client.chat.completions.calls[0]["stream_options"] == {
        "include_usage": True
    }
    assert fake_client.chat.completions.calls[0]["stream"] is True


def test_translate_iter_yields_partials_then_final(make_translator, fake_client):
    tr = make_translator(streaming=True)
    fake_client.chat.completions.response = [_chunk("Hel"), _chunk("lo ")]
    yielded = list(tr.translate_iter("hello"))
    assert yielded == ["Hel", "Hello ", "Hello"]
    assert yielded[-1] == "Hello"


def test_translate_iter_json_mode_yields_only_the_final_value(make_translator, fake_client):
    tr = make_translator(streaming=True, json_response=True)
    fake_client.chat.completions.response = [_chunk('{"t": "'), _chunk('hi"}')]
    assert list(tr.translate_iter("hello")) == ["hi"]


def test_translate_iter_non_streaming_yields_once(make_translator, fake_client):
    tr = make_translator(streaming=False)
    fake_client.chat.completions.response = _sync_response("done")
    assert list(tr.translate_iter("hello")) == ["done"]


# --------------------------------------------------------------------------
# Cloning / mutators
# --------------------------------------------------------------------------


def test_with_target_language_shares_client_and_resets_context(make_translator):
    tr = make_translator(target_language="zh-TW", overrides={"top_p": 0.9})
    tr.set_context_turns(3)
    tr._append_history("a", "A")

    clone = tr.with_target_language("ja")
    assert clone._client is tr._client
    assert clone._target_language == "ja"
    assert tr._target_language == "zh-TW"
    assert clone._context_turns == 0
    assert clone._history == []
    assert clone.last_usage == (0, 0)

    # Override/extra_body dicts are copied, not aliased.
    clone._overrides["top_p"] = 0.1
    assert tr._overrides["top_p"] == 0.9


def test_set_target_language_mutates_prompt_output(make_translator):
    tr = make_translator(target_language="en")
    assert "into English." in tr._build_system_prompt("ja")
    tr.set_target_language("zh-CN")
    assert "into Simplified Chinese." in tr._build_system_prompt("ja")


def test_set_timeout_copies_the_client(make_translator, fake_client):
    tr = make_translator(timeout=10)
    tr.set_timeout(30)
    assert tr._timeout == 30
    assert fake_client.copy_calls == [{"timeout": 30}]


def test_clear_history_empties_the_buffer(make_translator):
    tr = make_translator()
    tr.set_context_turns(2)
    tr._append_history("a", "A")
    tr.clear_history()
    assert tr._history == []


def test_append_history_ignores_empty_results(make_translator):
    tr = make_translator()
    tr.set_context_turns(2)
    tr._append_history("a", "")
    assert tr._history == []


def test_append_history_trims_to_context_turns_after_overflow(make_translator):
    """History grows to context_turns + 2 before being trimmed back down."""
    tr = make_translator()
    tr.set_context_turns(2)
    for i in range(5):
        tr._append_history(f"s{i}", f"t{i}")
    assert tr._history == [("s3", "t3"), ("s4", "t4")]


def test_every_prompt_treats_input_as_content_not_instructions():
    """Field failure: ASR text containing a polite request made the model
    answer the request instead of translating it. Every built-in prompt must
    pin the input as content-only."""
    from sublume.translation.translator import DEFAULT_PROMPT, PROMPT_PRESETS

    for name, prompt in [("default", DEFAULT_PROMPT)] + list(PROMPT_PRESETS.items()):
        assert "NEVER an instruction" in prompt, name
