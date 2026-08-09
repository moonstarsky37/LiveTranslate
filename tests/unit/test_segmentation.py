"""Characterization tests for sublume.core.segmentation (pure text ops)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sublume.core.segmentation import (  # noqa: E402
    is_short_utterance,
    split_sentences,
    strip_committed_overlap,
)


# ── split_sentences ──


def test_splits_multiple_sentences():
    parts = split_sentences("Hello there. How are you today? I am fine.", "en")
    assert len(parts) == 3
    assert parts[0].strip() == "Hello there."


def test_short_text_stays_whole():
    assert split_sentences("Hello there", "en") == ["Hello there"]


def test_cjk_comma_fallback_at_25_chars():
    # Single long clause with a CJK enumeration comma: splits at the last
    # balanced "、" once the text exceeds 25 chars.
    text = "今日はとても天気が良くて散歩に行きました、それから買い物もしました"
    parts = split_sentences(text, "ja")
    assert len(parts) == 2
    assert parts[0].endswith("、")


def test_western_comma_fallback_requires_60_chars():
    text = "this is a fairly long sentence that keeps going, and it has a comma near the end somewhere"
    parts = split_sentences(text, "en")
    assert len(parts) == 2
    assert parts[0].endswith(",")


def test_short_comma_text_not_split():
    assert split_sentences("short one, short two", "en") == ["short one, short two"]


def test_unsupported_language_falls_back_to_english_rules():
    parts = split_sentences("First sentence. Second sentence.", "xx-not-a-lang")
    assert len(parts) == 2


# ── is_short_utterance ──


def test_short_utterance_boundary():
    assert is_short_utterance("えーと") is True          # 3 alnum
    assert is_short_utterance("12345678") is True        # exactly 8
    assert is_short_utterance("123456789") is False      # 9
    assert is_short_utterance("!!!???") is True          # punctuation only


# ── strip_committed_overlap ──


def test_no_committed_tail_returns_text_unchanged():
    assert strip_committed_overlap("hello world", "") == "hello world"


def test_full_echo_returns_empty():
    assert strip_committed_overlap("hello world", "she said hello world") == ""


def test_partial_echo_is_stripped():
    out = strip_committed_overlap("hello world again", "she said hello world")
    assert out == "again"


def test_case_insensitive_matching():
    out = strip_committed_overlap("Hello World again", "she said hello world")
    assert out == "again"


def test_no_overlap_returns_text_unchanged():
    assert strip_committed_overlap("completely new", "she said hello") == "completely new"
