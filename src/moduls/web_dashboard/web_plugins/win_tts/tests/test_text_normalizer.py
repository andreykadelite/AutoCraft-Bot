# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from moduls.web_dashboard.web_plugins.win_tts.text_normalizer import (
    analyze_edge_text_readability,
    normalize_edge_text,
    parse_edge_text_normalizer_settings,
)


class EdgeTextNormalizerTests(unittest.TestCase):
    def test_soft_preset_preserves_markdown_links_when_unwrap_disabled(self) -> None:
        settings = parse_edge_text_normalizer_settings({"preset": "soft"})
        source = "Ссылка [пример](https://example.com) и ![img](https://img.example/pic.png)"

        result = normalize_edge_text(source, settings)

        self.assertIn("[пример](https://example.com)", result.text)
        self.assertIn("![img](https://img.example/pic.png)", result.text)
        self.assertEqual(result.stats["markdown_links_unwrapped"], 0)
        self.assertEqual(result.stats["markdown_images_unwrapped"], 0)

    def test_balanced_preset_unwraps_markdown_links_and_images(self) -> None:
        settings = parse_edge_text_normalizer_settings({"preset": "balanced"})
        source = "Ссылка [пример](https://example.com) и ![img](https://img.example/pic.png)"

        result = normalize_edge_text(source, settings)

        self.assertNotIn("[пример](", result.text)
        self.assertNotIn("![img](", result.text)
        self.assertIn("пример", result.text)
        self.assertIn("img", result.text)
        self.assertEqual(result.stats["markdown_links_unwrapped"], 1)
        self.assertEqual(result.stats["markdown_images_unwrapped"], 1)

    def test_symbol_token_cleanup_removes_brackets_and_braces(self) -> None:
        settings = parse_edge_text_normalizer_settings(
            {
                "preset": "balanced",
                "strip_markdown": False,
                "strip_urls": False,
                "strip_emails": False,
                "collapse_repeated_symbols": False,
                "collapse_repeated_punctuation": False,
            }
        )
        source = "Шумовые токены: [] {} ## <<>>"

        result = normalize_edge_text(source, settings)

        self.assertNotIn("[]", result.text)
        self.assertNotIn("{}", result.text)
        self.assertNotIn("##", result.text)
        self.assertNotIn("<<>>", result.text)
        self.assertGreaterEqual(result.stats["symbol_tokens_removed"], 1)

    def test_analyzer_recommends_enabling_symbol_token_cleanup(self) -> None:
        settings = parse_edge_text_normalizer_settings(
            {
                "preset": "soft",
                "drop_symbol_only_tokens": False,
                "strip_markdown": False,
                "unwrap_markdown_links": False,
                "strip_urls": False,
                "strip_emails": False,
            }
        )
        analysis = analyze_edge_text_readability("Текст с шумом [] {} ##", settings)

        self.assertTrue(
            any(
                "удаление шумовых токенов" in str(item or "").lower()
                for item in analysis.get("recommendations", [])
            )
        )
        leftovers = analysis.get("symbols", {}).get("leftovers", [])
        self.assertTrue(any(str(item.get("symbol")) in {"[", "]", "{", "}"} for item in leftovers))

    def test_custom_symbol_input_removes_user_defined_symbols(self) -> None:
        settings = parse_edge_text_normalizer_settings(
            {
                "preset": "soft",
                "strip_markdown": False,
                "strip_urls": False,
                "strip_emails": False,
                "drop_symbols": "#@$%^&",
            }
        )
        result = normalize_edge_text("A# B@ C$ D% E^ F&", settings)

        self.assertNotIn("#", result.text)
        self.assertNotIn("@", result.text)
        self.assertNotIn("$", result.text)
        self.assertNotIn("%", result.text)
        self.assertNotIn("^", result.text)
        self.assertNotIn("&", result.text)
        self.assertGreaterEqual(result.stats["manual_symbols_removed"], 6)

    def test_analyzer_reports_symbol_totals(self) -> None:
        settings = parse_edge_text_normalizer_settings({"preset": "balanced"})
        analysis = analyze_edge_text_readability("Тест ## [] !!", settings)

        totals = analysis.get("symbols", {}).get("totals", {})
        self.assertIn("before", totals)
        self.assertIn("after", totals)
        self.assertIn("removed", totals)
        self.assertGreaterEqual(int(totals.get("before", 0)), int(totals.get("after", 0)))

    def test_aggressive_profile_preserves_ellipsis_by_default(self) -> None:
        settings = parse_edge_text_normalizer_settings({"preset": "aggressive"})
        source = "Некий цикл «Героев...» — это размышления."

        result = normalize_edge_text(source, settings)

        self.assertIn("Героев…", result.text)
        self.assertGreaterEqual(int(result.stats.get("ellipsis_preserved", 0)), 1)

    def test_aggressive_profile_can_collapse_ellipsis_to_dot_when_disabled(self) -> None:
        settings = parse_edge_text_normalizer_settings(
            {
                "preset": "aggressive",
                "preserve_ellipsis": False,
            }
        )
        source = "Некий цикл «Героев...» — это размышления."

        result = normalize_edge_text(source, settings)

        self.assertIn("Героев.", result.text)
        self.assertNotIn("Героев…", result.text)

    def test_mixed_cyrillic_and_latin_text_is_preserved(self) -> None:
        settings = parse_edge_text_normalizer_settings({"preset": "balanced"})
        source = "Привет *** hello ### мир"

        result = normalize_edge_text(source, settings)

        self.assertIn("Привет", result.text)
        self.assertIn("hello", result.text)
        self.assertIn("мир", result.text)
        self.assertGreaterEqual(int(result.stats.get("symbol_tokens_removed", 0)), 1)


if __name__ == "__main__":
    unittest.main()
