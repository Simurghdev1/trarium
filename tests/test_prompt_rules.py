"""The short Persian word-order rule reaches every prompt.

Persian puts the noun before its description ("blue car" → «ماشین آبی»); a model
that copies the English order produces text a Persian speaker would not say. The
rule is one short line added to every prompt, so it is tested through the prompt
builders rather than by reading the constant.
"""
from __future__ import annotations

from trarium.prompt_rules import PERSIAN_NATURAL_ORDER, persian_rules


def test_the_rule_states_the_persian_order_with_an_example():
    assert "ماشین آبی" in PERSIAN_NATURAL_ORDER
    assert "blue car" in PERSIAN_NATURAL_ORDER


def test_persian_rules_keeps_the_base_and_adds_the_rule():
    text = persian_rules("Use natural Persian.")
    assert text.startswith("Use natural Persian.")
    assert PERSIAN_NATURAL_ORDER in text
    assert persian_rules("") == PERSIAN_NATURAL_ORDER


def test_the_pdf_block_prompt_carries_the_rule_for_persian_only():
    from trarium.pdf_optimized import _translate_marker_batch

    prompts: list[str] = []

    class _Translator:
        provider = "stub"
        model = "stub"

        def translate(self, text, target_lang="fa"):
            prompts.append(text)
            return "[TRARIUM_BLOCK_0]\nترجمه"

    _translate_marker_batch(_Translator(), [(0, 0, "blue car")], "fa")
    _translate_marker_batch(_Translator(), [(0, 0, "blue car")], "en")
    assert PERSIAN_NATURAL_ORDER in prompts[0]
    assert PERSIAN_NATURAL_ORDER not in prompts[1]
