"""تست‌های ترجمه‌گر رایگان MyMemory (بدون تماس واقعی با شبکه)."""
from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from trarium.pdf_optimized import _parse_marked_translation
from trarium.translators.free_web import FreeWebTranslator


def _translator(monkey_calls=None, value="ترجمه"):
    translator = FreeWebTranslator(min_interval=0.0)

    def fake_request(text, target_lang):
        if monkey_calls is not None:
            monkey_calls.append(text)
        return value

    translator._request = fake_request  # type: ignore[assignment]
    return translator


def test_marker_prompt_is_parsed_and_noise_not_sent():
    sent = []
    translator = _translator(sent, value="سلام دنیا")
    prompt = (
        "Translate every marked block completely and faithfully. Output only translated blocks. "
        "Keep [TRARIUM_BLOCK_n] markers exactly. Preserve every __TRARIUM_PH_####__ token exactly.\n"
        "Target language: fa\n\n"
        "[TRARIUM_BLOCK_0]\nHello world\n\n"
        "[TRARIUM_BLOCK_1]\nSecond line"
    )
    out = translator.translate(prompt, "fa")
    assert sent == ["Hello world", "Second line"]
    parsed = _parse_marked_translation(out, 2)
    assert parsed == {0: "سلام دنیا", 1: "سلام دنیا"}


def test_placeholders_never_reach_the_api():
    sent = []
    translator = _translator(sent, value="X")
    translator.translate("[TRARIUM_BLOCK_0]\nSee __TRARIUM_PH_0001__ on page", "fa")
    assert sent == ["See", "on page"]


def test_long_text_is_split_under_api_limit():
    sent = []
    translator = _translator(sent, value="ب")
    long_text = " ".join(["This sentence has several words in it."] * 40)
    translator.translate(long_text, "fa")
    assert sent and all(len(chunk) <= translator.max_chars for chunk in sent)
    assert sum(len(c) for c in sent) >= len(long_text) // 2


def test_failed_chunk_returns_source_instead_of_garbage():
    translator = FreeWebTranslator(min_interval=0.0)
    translator._request = lambda text, target: None  # type: ignore[assignment]
    assert translator.translate("Hello world", "fa") == "Hello world"


def test_short_listing_lines_are_translated_line_by_line():
    sent = []
    translator = _translator(sent, value="سطر")
    translator.translate("[TRARIUM_BLOCK_0]\nCHAPTER I\nCHAPTER II", "fa")
    assert sent == ["CHAPTER I", "CHAPTER II"]


def test_google_payload_variants_are_flattened():
    assert FreeWebTranslator._flatten_google(["یک"]) == "یک"
    assert FreeWebTranslator._flatten_google([["یک"]]) == "یک"
    assert FreeWebTranslator._flatten_google([["یک", "en"]]) == "یک"
    assert FreeWebTranslator._flatten_google(["یک ", "دو"]) == "یک دو"
    assert FreeWebTranslator._flatten_google({"bad": 1}) == ""


def test_google_is_preferred_and_mymemory_is_the_fallback(monkeypatch):
    translator = FreeWebTranslator(min_interval=0.0)
    calls = []
    monkeypatch.setattr(translator, "_google_request",
                        lambda text, target: (calls.append("g"), None)[1])
    monkeypatch.setattr(translator, "_mymemory_request",
                        lambda text, target: (calls.append("m"), "از حافظه")[1])
    assert translator.translate("[TRARIUM_BLOCK_0]\nHello", "fa") .endswith("از حافظه")
    assert calls == ["g", "m"]


def test_exhausted_quota_is_not_retried_again(monkeypatch):
    translator = FreeWebTranslator(min_interval=0.0)
    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(str(request.full_url))
        raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert translator._mymemory_request("hello", "fa") is None
    assert "mymemory" in translator._dead
    assert translator._mymemory_request("again", "fa") is None
    assert len(calls) <= 2


def test_letter_spaced_heading_is_joined_before_translation():
    sent = []
    translator = _translator(sent, value="فصل ششم")
    translator.translate("[TRARIUM_BLOCK_0]\nC H A P T E R VI", "fa")
    assert sent == ["CHAPTER VI"]


def test_archaic_verbs_are_modernized_before_the_request():
    sent = []
    translator = _translator(sent, value="از یک عروسی می گوید")
    translator.translate("[TRARIUM_BLOCK_0]\nTELLETH OF A WEDDING", "fa")
    assert sent == ["TELLS OF A WEDDING"]
    # Real words that merely end in -eth must survive untouched.
    sent.clear()
    translator = _translator(sent, value="x")
    translator.translate("death and teeth on the earth", "fa")
    assert sent == ["death and teeth on the earth"]


def test_ocr_capital_i_lookalike_is_restored():
    sent = []
    translator = _translator(sent, value="x")
    translator.translate("How they fared in ClTY and RlNGSTETTEN", "fa")
    assert sent == ["How they fared in CITY and RINGSTETTEN"]


def test_scan_artifacts_are_normalized():
    sent = []
    translator = _translator(sent, value="x")
    translator.translate("HOW UNDINE CAME HOME 1 2", "fa")
    assert sent == ["HOW UNDINE CAME HOME 12"]
    sent.clear()
    translator = _translator(sent, value="x")
    translator.translate("II. HOW THEY FARED", "fa")
    assert sent == ["2. HOW THEY FARED"]
    # A pronoun "I" inside a sentence is not a chapter number.
    sent.clear()
    translator = _translator(sent, value="x")
    translator.translate("I warn ye, said she", "fa")
    assert sent == ["I warn ye, said she"]


def test_leftover_latin_words_are_translated_one_by_one():
    answers = {"City": "شهر", "Undine": "اوندین"}
    translator = FreeWebTranslator(min_interval=0.0)
    translator._request = lambda text, target: answers.get(text, text)  # type: ignore[assignment]
    out = translator._translate_chunked("They came to the City with Undine", "fa")
    assert "شهر" in out and "اوندین" in out
    assert "City" not in out


def test_prompt_preamble_stripped_for_single_block_retry():
    sent = []
    translator = _translator(sent, value="متن")
    translator.translate(
        "Translate this literary text completely and faithfully. Output only the translation.\n"
        "Target language: fa\n\nOnce upon a time.", "fa")
    assert sent == ["Once upon a time."]
