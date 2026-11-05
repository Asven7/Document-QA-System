"""Tests for the Unicode-safe text utilities used by the retrieval pipeline."""

from django.test import SimpleTestCase

from core.text import normalize_for_search, persian_ratio, tokenize_for_search


class NormalizeForSearchTests(SimpleTestCase):
    def test_arabic_yeh_and_kaf_mapped_to_persian(self):
        # "كيك" uses Arabic code points ي (U+064A) and ك (U+0643).
        self.assertEqual(normalize_for_search("كيك"), "کیک")

    def test_persian_text_unchanged(self):
        text = "مرخصی استحقاقی"
        self.assertEqual(normalize_for_search(text), text)

    def test_zwnj_replaced_with_space(self):
        self.assertEqual(normalize_for_search("می‌شود"), "می شود")

    def test_diacritics_removed(self):
        self.assertEqual(normalize_for_search("مُدرِس"), "مدرس")

    def test_tatweel_removed(self):
        self.assertEqual(normalize_for_search("مـــدرس"), "مدرس")

    def test_persian_digits_converted(self):
        self.assertEqual(normalize_for_search("۲۶ روز"), "26 روز")

    def test_arabic_digits_converted(self):
        self.assertEqual(normalize_for_search("٢٦"), "26")

    def test_latin_casefold_and_whitespace(self):
        self.assertEqual(normalize_for_search("  Annual   Leave "), "annual leave")

    def test_empty_and_none_like_inputs(self):
        self.assertEqual(normalize_for_search(""), "")
        self.assertEqual(normalize_for_search("   "), "")

    def test_punctuation_preserved_by_normalize_but_dropped_by_tokenize(self):
        normalized = normalize_for_search("سلام، دنیا!")
        self.assertIn("،", normalized)
        self.assertEqual(tokenize_for_search("سلام، دنیا!"), ["سلام", "دنیا"])

    def test_original_text_is_not_mutated(self):
        original = "می‌شود"
        normalize_for_search(original)
        self.assertEqual(original, "می‌شود")


class TokenizeTests(SimpleTestCase):
    def test_mixed_persian_english(self):
        tokens = tokenize_for_search("Django و Python هر دو framework هستند.")
        self.assertEqual(tokens, ["django", "و", "python", "هر", "دو", "framework", "هستند"])

    def test_zwnj_and_space_forms_match(self):
        self.assertEqual(
            tokenize_for_search("می‌شود"), tokenize_for_search("می شود")
        )

    def test_arabic_and_persian_forms_match(self):
        self.assertEqual(tokenize_for_search("كتاب"), tokenize_for_search("کتاب"))

    def test_digits_shared_across_scripts(self):
        self.assertEqual(tokenize_for_search("۲۶"), tokenize_for_search("26"))


class PersianRatioTests(SimpleTestCase):
    def test_english_text_has_zero_ratio(self):
        self.assertEqual(persian_ratio("annual leave"), 0.0)

    def test_persian_text_has_ratio_one(self):
        self.assertEqual(persian_ratio("مرخصی"), 1.0)

    def test_mixed_text_ratio_between_zero_and_one(self):
        ratio = persian_ratio("مرخصی annual leave")
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 1.0)

    def test_empty_text(self):
        self.assertEqual(persian_ratio(""), 0.0)
