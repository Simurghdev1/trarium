"""Tests for Persian typography, styles and the translation checker."""
import unittest

from trarium.fa_style import normalize, styling_issues
from trarium.styles import STYLES, choices, style_instruction
from trarium.translation_qc import check, check_block, repair_list


class PersianTypographyTests(unittest.TestCase):
    def test_mi_prefix_gets_a_half_space(self):
        self.assertEqual(normalize("او می تواند بیاید"), "او می‌تواند بیاید")

    def test_plural_ha_joins_the_word(self):
        self.assertEqual(normalize("کتاب ها روی میز است"), "کتاب‌ها روی میز است")

    def test_no_space_before_punctuation(self):
        self.assertEqual(normalize("سلام ، خوبی ؟"), "سلام، خوبی؟")

    def test_arabic_letters_become_persian(self):
        self.assertEqual(normalize("يك كتاب"), "یک کتاب")

    def test_straight_quotes_become_guillemets(self):
        self.assertEqual(normalize('او گفت "برو"'), "او گفت «برو»")

    def test_normalize_is_idempotent(self):
        text = "او می گوید که کتاب ها را , دوست دارد ?"
        once = normalize(text)
        self.assertEqual(normalize(once), once)

    def test_persian_digits_only_when_asked(self):
        self.assertIn("3", normalize("سال 3"))
        self.assertIn("۳", normalize("سال 3", persian_digits=True))

    def test_english_text_is_left_alone(self):
        self.assertEqual(normalize("This is a test."), "This is a test.")

    def test_styling_issues_flag_real_problems(self):
        problems = styling_issues("او می تواند بيايد , خوب است")
        self.assertTrue(problems)


class StyleTests(unittest.TestCase):
    def test_every_style_has_a_label(self):
        for key, value in STYLES.items():
            self.assertTrue(value["label"])
            self.assertEqual(choices()[0][0], "neutral")

    def test_neutral_adds_nothing(self):
        self.assertEqual(style_instruction("neutral"), "")

    def test_custom_style_is_passed_through(self):
        self.assertIn("poetic", style_instruction("poetic and calm"))

    def test_style_reaches_the_models_inside_a_fallback(self):
        from trarium.styles import apply

        class Model:
            pass

        class Fallback:
            def __init__(self):
                self.candidates = [Model(), Model()]

        fallback = Fallback()
        apply(fallback, "manga")
        for model in fallback.candidates:
            self.assertIn("speech bubbles", model.style_hint)


class TranslationCheckerTests(unittest.TestCase):
    SOURCE = (
        "The knight rode into the forest and stopped by the lake. "
        "He listened to the water for a long while. Then he went home."
    )

    def test_good_translation_has_no_issues(self):
        good = (
            "شوالیه به جنگل رفت و کنار دریاچه ایستاد. "
            "مدت زیادی به صدای آب گوش داد. بعد به خانه برگشت."
        )
        self.assertEqual(check_block(self.SOURCE, good), [])

    def test_short_translation_is_flagged_as_dropped(self):
        issues = check_block(self.SOURCE, "شوالیه رفت.")
        kinds = {issue["kind"] for issue in issues}
        self.assertIn("dropped", kinds)

    def test_english_answer_is_flagged_as_untranslated(self):
        kinds = {issue["kind"] for issue in check_block(self.SOURCE, self.SOURCE)}
        self.assertIn("untranslated", kinds)

    def test_duplicate_translations_are_counted(self):
        pairs = [
            (0, 0, "First long source paragraph about the lake.", "یک پاراگراف بلند فارسی دربارهٔ دریاچه و جنگل و شوالیه."),
            (0, 1, "Second long source paragraph about the wood.", "یک پاراگراف بلند فارسی دربارهٔ دریاچه و جنگل و شوالیه."),
        ]
        result = check(pairs)
        self.assertGreaterEqual(result["duplicates"], 1)
        self.assertFalse(result["ok"])

    def test_glossary_mismatch_is_reported(self):
        issues = check_block("Undine met the knight.", "اوندین شوالیه را دید.", {"Undine": "اوندین"})
        self.assertEqual(issues, [])

    def test_repair_list_targets_broken_blocks_only(self):
        pairs = [
            (0, 0, self.SOURCE, "شوالیه رفت."),
            (0, 1, "Short line.", "خط کوتاه."),
        ]
        result = check(pairs)
        targets = repair_list(result, pairs)
        self.assertIn((0, 0), targets)
        self.assertNotIn((0, 1), targets)


if __name__ == "__main__":
    unittest.main()
