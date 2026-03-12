# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from moduls.web_dashboard.web_plugins.win_tts.dual_language_router import (
    build_dual_language_routing_plan,
    normalize_language_code,
    resolve_script_family,
    resolve_voice_language_code,
)


class DualLanguageRouterTests(unittest.TestCase):
    def test_disabled_mode_uses_primary_only(self) -> None:
        plan = build_dual_language_routing_plan(
            text="Privet, мир!",
            enabled=False,
            primary_language="ru",
            secondary_language="en",
        )
        self.assertFalse(plan.active)
        self.assertEqual(len(plan.segments), 1)
        self.assertEqual(plan.segments[0].role, "primary")
        self.assertEqual(plan.segments[0].text, "Privet, мир!")

    def test_mixed_ru_en_routes_latin_to_secondary(self) -> None:
        plan = build_dual_language_routing_plan(
            text="Привет, friend! Как дела?",
            enabled=True,
            primary_language="ru",
            secondary_language="en",
        )
        self.assertTrue(plan.enabled)
        self.assertTrue(plan.active)
        self.assertGreaterEqual(len(plan.segments), 2)
        self.assertTrue(any(item.role == "secondary" for item in plan.segments))
        self.assertGreater(plan.secondary_letters, 0)

    def test_same_script_falls_back_to_primary_voice(self) -> None:
        plan = build_dual_language_routing_plan(
            text="Привет, друже!",
            enabled=True,
            primary_language="ru",
            secondary_language="uk",
        )
        self.assertFalse(plan.active)
        self.assertEqual(len(plan.segments), 1)
        self.assertEqual(plan.segments[0].role, "primary")

    def test_secondary_script_absent_falls_back_to_primary(self) -> None:
        plan = build_dual_language_routing_plan(
            text="Только кириллица и числа 12345",
            enabled=True,
            primary_language="ru",
            secondary_language="en",
        )
        self.assertFalse(plan.active)
        self.assertEqual(len(plan.segments), 1)
        self.assertEqual(plan.segments[0].role, "primary")

    def test_punctuation_is_preserved_during_switching(self) -> None:
        plan = build_dual_language_routing_plan(
            text="Привет!!! Hello??? Пока.",
            enabled=True,
            primary_language="ru",
            secondary_language="en",
        )
        self.assertTrue(plan.active)
        joined = "".join(item.text for item in plan.segments)
        self.assertEqual(joined, "Привет!!! Hello??? Пока.")
        self.assertTrue(any(item.role == "secondary" for item in plan.segments))

    def test_language_helpers(self) -> None:
        self.assertEqual(normalize_language_code("en-US"), "en")
        self.assertEqual(resolve_script_family("ru-RU"), "cyrillic")
        self.assertEqual(resolve_script_family("en-US"), "latin")

    def test_voice_language_resolution_uses_catalog_before_fallback(self) -> None:
        catalog = {
            "ru": ["Voice RU"],
            "en": ["Voice EN"],
        }
        self.assertEqual(resolve_voice_language_code("Voice EN", catalog), "en")
        self.assertEqual(
            resolve_voice_language_code("en-US-JennyNeural", {}),
            "en",
        )


if __name__ == "__main__":
    unittest.main()
