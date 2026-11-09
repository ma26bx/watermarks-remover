"""Tests for small-server text analysis, without optional dependencies."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service" / "scripts"))
from text_analysis import MAX_CHARS, analyze_text, normalize_text


class TextAnalysisTests(unittest.TestCase):
    def test_portuguese_words_and_sentence_lengths(self):
        r = analyze_text("Você lê bem.\n\nA ação começa amanhã!")
        self.assertEqual(r["counts"]["words"], 7)
        self.assertEqual(r["counts"]["paragraphs"], 2)
        self.assertEqual(r["counts"]["sentences"], 2)
        self.assertEqual(r["readability"]["mean_words_per_sentence"], 3.5)
        self.assertEqual(r["stylometry"]["status"], "insufficient_length")
        self.assertIsNone(r["stylometry"]["score"])

    def test_fenced_code_excluded_and_repeats_do_not_cross_sentences(self):
        r = analyze_text("gato pula. gato pula.\n\n```python\nignorar código\n```\n\n# Título")
        self.assertEqual(r["counts"]["words"], 5)
        phrases = {p["text"]: p["count"] for p in r["repetitions"]["phrases"]}
        self.assertEqual(phrases, {"gato pula": 2})
        self.assertEqual(r["markdown"]["headings"], 1)

    def test_unicode_offsets_and_normalize_preserve_emoji(self):
        text = "Olá\ntexto\u200b com\u00a0espaço 👨‍👩‍👧"
        report = analyze_text(text)
        hit = next(h for h in report["unicode"]["hits"] if h["codepoint"] == "U+200B")
        self.assertEqual(hit["positions"][0], {"offset": 9, "line": 2, "column": 6})
        r = normalize_text(text)
        self.assertNotIn("\u200b", r["text"])
        self.assertNotIn("\u00a0", r["text"])
        self.assertIn("👨‍👩‍👧", r["text"])
        self.assertTrue(r["changed"])
        self.assertIn("\u200b", text)

    def test_no_implicit_aggressive_normalization(self):
        self.assertEqual(normalize_text("Ａ") ["text"], "Ａ")
        self.assertEqual(normalize_text("Ａ", {"nfkc": True})["text"], "A")
        for options in ({"nfkc": "false"}, {"unknown": True}, []):
            with self.assertRaises(ValueError):
                normalize_text("text", options)

    def test_empty_text_has_no_fabricated_readability_or_score(self):
        r = analyze_text("")
        self.assertEqual(r["counts"]["words"], 0)
        self.assertIsNone(r["readability"]["mean_words_per_sentence"])
        self.assertIsNone(r["stylometry"]["score"])

    def test_analysis_is_bounded_and_cleaning_rejects_truncation(self):
        text = "palavra " * (MAX_CHARS // 8 + 1)
        r = analyze_text(text)
        self.assertTrue(r["scope"]["truncated"])
        self.assertEqual(r["scope"]["analyzed_characters"], MAX_CHARS)
        with self.assertRaises(ValueError):
            normalize_text(text)

    def test_gumbel_is_not_falsely_enabled_and_key_never_returned(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(analyze_text("um texto sem chave")["gumbel"]["available"])
        secret = "synthetic-key-for-unit-tests"
        with patch.dict(os.environ, {"WATERMARKS_GUMBEL_KEY": secret}):
            r = analyze_text("um texto com várias palavras para este teste de chave gumbel")
            self.assertNotIn(secret, str(r))
            self.assertTrue(r["gumbel"]["available"])


if __name__ == "__main__":
    unittest.main()
