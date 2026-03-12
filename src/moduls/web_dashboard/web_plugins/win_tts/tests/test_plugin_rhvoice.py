# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from flask import Flask

from moduls.web_dashboard.web_plugins.win_tts import plugin


def _audio_bytes(size: int) -> bytes:
    return b"\x00" * max(1, size)


def _write_test_wav(path: Path, leading_ms: int, tone_ms: int, trailing_ms: int, framerate: int = 24_000) -> None:
    leading_frames = max(0, int(round((leading_ms / 1000.0) * framerate)))
    tone_frames = max(1, int(round((tone_ms / 1000.0) * framerate)))
    trailing_frames = max(0, int(round((trailing_ms / 1000.0) * framerate)))

    silence = (b"\x00\x00") * leading_frames
    tone = bytearray()
    for index in range(tone_frames):
        sample = 7000 if (index % 2 == 0) else -7000
        tone.extend(int(sample).to_bytes(2, byteorder="little", signed=True))
    end_silence = (b"\x00\x00") * trailing_frames

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        handle.writeframes(silence + bytes(tone) + end_silence)


class PluginRHVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_state = {
            "ENGINE_OPTIONS": list(plugin.ENGINE_OPTIONS),
            "VOICE_OPTIONS": dict(plugin.VOICE_OPTIONS),
            "TTS_IMPORT_ERRORS": list(plugin.TTS_IMPORT_ERRORS),
            "RHVOICE_TTS_VOICE_MAP": dict(plugin.RHVOICE_TTS_VOICE_MAP),
            "RHVOICE_TTS_LANGUAGE_MAP": dict(plugin.RHVOICE_TTS_LANGUAGE_MAP),
            "RHVOICE_ADDON_STATE": dict(plugin.RHVOICE_ADDON_STATE),
            "TTS_INIT_DONE": bool(plugin.TTS_INIT_DONE),
            "_FFMPEG_PATH": plugin._FFMPEG_PATH,
        }

    def tearDown(self) -> None:
        plugin.ENGINE_OPTIONS = list(self._saved_state["ENGINE_OPTIONS"])
        plugin.VOICE_OPTIONS = dict(self._saved_state["VOICE_OPTIONS"])
        plugin.TTS_IMPORT_ERRORS = list(self._saved_state["TTS_IMPORT_ERRORS"])
        plugin.RHVOICE_TTS_VOICE_MAP = dict(self._saved_state["RHVOICE_TTS_VOICE_MAP"])
        plugin.RHVOICE_TTS_LANGUAGE_MAP = dict(self._saved_state["RHVOICE_TTS_LANGUAGE_MAP"])
        plugin.RHVOICE_ADDON_STATE = dict(self._saved_state["RHVOICE_ADDON_STATE"])
        plugin.TTS_INIT_DONE = bool(self._saved_state["TTS_INIT_DONE"])
        plugin._FFMPEG_PATH = self._saved_state["_FFMPEG_PATH"]

    def _patch_init_dependencies(self, addon_state: dict[str, object], rhvoice_voices: dict[str, str]) -> list[mock._patch]:
        google_label = "Google TTS: Русский (ru)"
        return [
            mock.patch.object(plugin, "_reset_dependency_diagnostics", return_value=None),
            mock.patch.object(plugin, "_detect_ffmpeg", return_value=None),
            mock.patch.object(plugin, "_prepare_tts_module_imports", return_value={}),
            mock.patch.object(plugin, "_FFMPEG_PATH", "ffmpeg"),
            mock.patch.object(plugin, "gTTS", object()),
            mock.patch.object(
                plugin,
                "_build_google_voice_catalog",
                return_value=(
                    {google_label: {"lang": "ru", "tld": "ru"}},
                    {"ru": [google_label]},
                    [],
                ),
            ),
            mock.patch.object(plugin, "pyttsx3", None),
            mock.patch.object(plugin, "_import_module_with_hints", return_value=(None, RuntimeError("edge missing"))),
            mock.patch.object(plugin, "_collect_addon_runtime_state", return_value=addon_state),
            mock.patch.object(plugin.addon_runtime, "load_rhvoice_tts_class", return_value=(object(), "")),
            mock.patch.object(
                plugin.addon_runtime,
                "build_rhvoice_voice_catalog",
                return_value=(rhvoice_voices, {"ru": list(rhvoice_voices.keys())}, []),
            ),
        ]

    def test_init_tts_engines_adds_rhvoice_only_when_addon_is_installed(self) -> None:
        rh_voice_label = "RHVoice: Anna (ru, женский)"
        patches = self._patch_init_dependencies(
            addon_state={"installed": True, "broken": False, "can_install": True},
            rhvoice_voices={rh_voice_label: "Anna"},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
            plugin.init_tts_engines(force=True)

        self.assertIn("RHVoice", plugin.ENGINE_OPTIONS)
        self.assertIn(rh_voice_label, plugin.VOICE_OPTIONS.get("RHVoice", []))

    def test_init_tts_engines_skips_rhvoice_when_addon_not_installed(self) -> None:
        rh_voice_label = "RHVoice: Anna (ru, женский)"
        patches = self._patch_init_dependencies(
            addon_state={"installed": False, "broken": False, "can_install": True},
            rhvoice_voices={rh_voice_label: "Anna"},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
            plugin.init_tts_engines(force=True)

        self.assertNotIn("RHVoice", plugin.ENGINE_OPTIONS)
        self.assertNotIn("RHVoice", plugin.VOICE_OPTIONS)

    def test_synthesize_rhvoice_maps_and_clamps_rate_pitch_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "rhvoice.wav"
            captured: dict[str, object] = {}

            def _fake_synth(**kwargs: object) -> str:
                captured.update(kwargs)
                Path(str(kwargs["out_path"])).write_bytes(_audio_bytes(plugin._MIN_AUDIO_BYTES + 32))
                return str(kwargs["out_path"])

            plugin.RHVOICE_TTS_VOICE_MAP = {"RHVoice: Anna (ru, женский)": "Anna"}
            plugin.RHVOICE_ADDON_STATE = {"installed": True, "venv_site_packages": "fake"}

            with mock.patch.object(plugin.addon_runtime, "synthesize_rhvoice_to_file", side_effect=_fake_synth):
                result = plugin._synthesize_rhvoice(
                    text="Привет",
                    voice_label="RHVoice: Anna (ru, женский)",
                    out_path=str(out_path),
                    edge_rate="250",
                    edge_pitch="-350",
                    edge_volume="+120",
                )

            self.assertEqual(result, str(out_path))
            self.assertEqual(captured.get("voice_profile"), "Anna")
            self.assertEqual(captured.get("edge_rate"), 100)
            self.assertEqual(captured.get("edge_pitch"), -100)
            self.assertEqual(captured.get("edge_volume"), 100)

    def test_dual_language_rhvoice_dispatches_primary_and_secondary_segments(self) -> None:
        class Segment:
            def __init__(self, role: str, text: str, language: str) -> None:
                self.role = role
                self.text = text
                self.language = language

        class Plan:
            def __init__(self) -> None:
                self.enabled = True
                self.active = True
                self.primary_language = "ru"
                self.secondary_language = "en"
                self.secondary_letters = 5
                self.summary = "Тестовый план двух языков."
                self.segments = [
                    Segment("primary", "Привет ", "ru"),
                    Segment("secondary", "hello", "en"),
                ]

            def as_payload(self) -> dict[str, object]:
                return {"active": True, "segments": 2}

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            file_stem = tmp_root / "dual_rhvoice"
            synth_calls: list[dict[str, object]] = []

            def _fake_segment_synth(**kwargs: object) -> str:
                synth_calls.append(dict(kwargs))
                Path(str(kwargs["out_path"])).write_bytes(_audio_bytes(plugin._MIN_AUDIO_BYTES + 8))
                return str(kwargs["out_path"])

            def _fake_concat(parts: list[str], out_path: str) -> bool:
                if len(parts) != 2:
                    return False
                Path(out_path).write_bytes(_audio_bytes(plugin._MIN_AUDIO_BYTES + 64))
                return True

            with (
                mock.patch.object(plugin, "build_dual_language_routing_plan", return_value=Plan()),
                mock.patch.object(plugin, "_synthesize_rhvoice", side_effect=_fake_segment_synth),
                mock.patch.object(plugin, "_concat_wav_parts", side_effect=_fake_concat),
            ):
                out_file, payload = plugin._synthesize_to_file_dual_language(
                    text="Привет hello",
                    engine="RHVoice",
                    voice="VoicePrimary",
                    file_stem=file_stem,
                    primary_language="ru",
                    dual_language={
                        "secondary_language": "en",
                        "secondary_voice": "VoiceSecondary",
                        "secondary_edge_rate": "-20",
                        "secondary_edge_pitch": "+10",
                        "secondary_edge_volume": "+30",
                    },
                    edge_rate="+5",
                    edge_pitch="-3",
                    edge_volume="+2",
                )

            self.assertTrue(payload.get("active"))
            self.assertEqual(out_file.suffix, ".wav")
            self.assertTrue(out_file.is_file())
            self.assertEqual(len(synth_calls), 2)
            self.assertEqual(synth_calls[0]["voice_label"], "VoicePrimary")
            self.assertEqual(synth_calls[1]["voice_label"], "VoiceSecondary")
            self.assertEqual(synth_calls[0]["edge_rate"], "+5%")
            self.assertEqual(synth_calls[1]["edge_rate"], "-20%")

    def test_dual_pause_wav_manual_normalization_applies_target_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            part_1 = tmp_root / "part1.wav"
            part_2 = tmp_root / "part2.wav"
            out_file = tmp_root / "out.wav"

            _write_test_wav(part_1, leading_ms=200, tone_ms=180, trailing_ms=250)
            _write_test_wav(part_2, leading_ms=180, tone_ms=220, trailing_ms=220)

            ok, details = plugin._concat_dual_parts_with_pause(
                parts=[str(part_1), str(part_2)],
                out_path=str(out_file),
                out_suffix=".wav",
                rendered_segments=[
                    {"text": "Привет", "role": "primary", "language": "ru"},
                    {"text": "hello", "role": "secondary", "language": "en"},
                ],
                pause_mode="manual",
                pause_ms=120,
            )

            self.assertTrue(ok)
            self.assertTrue(details.get("applied"))
            self.assertEqual(details.get("mode"), "manual")
            self.assertTrue(out_file.is_file())

            with wave.open(str(out_file), "rb") as handle:
                duration_ms = int(round((handle.getnframes() / float(handle.getframerate())) * 1000.0))

            self.assertGreater(duration_ms, 820)
            self.assertLess(duration_ms, 1080)

    def test_parse_request_dual_pause_defaults_to_auto(self) -> None:
        app = Flask(__name__)
        plugin.ENGINE_OPTIONS = ["Edge TTS"]
        plugin.VOICE_OPTIONS = {"Edge TTS": ["ru-RU-VoiceA", "en-US-VoiceB"]}
        plugin.EDGE_TTS_LANGUAGE_MAP = {
            "ru": ["ru-RU-VoiceA"],
            "en": ["en-US-VoiceB"],
        }

        payload = {
            "text": "Привет hello",
            "engine": "Edge TTS",
            "voice": "ru-RU-VoiceA",
            "dual_language": {
                "enabled": True,
                "secondary_language": "en",
                "secondary_voice": "en-US-VoiceB",
            },
        }

        with app.test_request_context("/plugins/wintts/synthesize", method="POST", json=payload):
            with mock.patch.object(plugin, "init_tts_engines", return_value=None):
                request_data, error_response = plugin._parse_synthesis_request(payload)

        self.assertIsNone(error_response)
        self.assertIsNotNone(request_data)
        assert request_data is not None
        dual = request_data.get("dual_language") or {}
        self.assertEqual(dual.get("pause_mode"), "auto")
        self.assertEqual(dual.get("pause_ms"), plugin._DUAL_PAUSE_MS_DEFAULT)

    def test_parse_request_dual_pause_manual_clamped(self) -> None:
        app = Flask(__name__)
        plugin.ENGINE_OPTIONS = ["Edge TTS"]
        plugin.VOICE_OPTIONS = {"Edge TTS": ["ru-RU-VoiceA", "en-US-VoiceB"]}
        plugin.EDGE_TTS_LANGUAGE_MAP = {
            "ru": ["ru-RU-VoiceA"],
            "en": ["en-US-VoiceB"],
        }

        payload = {
            "text": "Привет hello",
            "engine": "Edge TTS",
            "voice": "ru-RU-VoiceA",
            "dual_language": {
                "enabled": True,
                "secondary_language": "en",
                "secondary_voice": "en-US-VoiceB",
                "pause_mode": "manual",
                "pause_ms": 9_999,
            },
        }

        with app.test_request_context("/plugins/wintts/synthesize", method="POST", json=payload):
            with mock.patch.object(plugin, "init_tts_engines", return_value=None):
                request_data, error_response = plugin._parse_synthesis_request(payload)

        self.assertIsNone(error_response)
        self.assertIsNotNone(request_data)
        assert request_data is not None
        dual = request_data.get("dual_language") or {}
        self.assertEqual(dual.get("pause_mode"), "manual")
        self.assertEqual(dual.get("pause_ms"), plugin._DUAL_PAUSE_MS_MAX)

    def test_config_endpoint_returns_addon_runtime_payload(self) -> None:
        app = Flask(__name__)
        view = plugin.WinTTSView()

        def _fake_init(force: bool = False) -> None:
            plugin.ENGINE_OPTIONS = ["Google", "RHVoice"]
            plugin.VOICE_OPTIONS = {
                "Google": ["Google TTS: Русский (ru)"],
                "RHVoice": ["RHVoice: Anna (ru, женский)"],
            }
            plugin.TTS_IMPORT_ERRORS = []

        addon_state = {
            "status": "installed",
            "installed": True,
            "broken": False,
            "can_install": True,
        }

        with app.test_request_context("/plugins/wintts/config", method="GET"):
            with (
                mock.patch.object(plugin, "init_tts_engines", side_effect=_fake_init),
                mock.patch.object(plugin, "_cleanup_generated_files", return_value=None),
                mock.patch.object(plugin, "_dependency_diagnostics_payload", return_value={"raw": {}, "lines": []}),
                mock.patch.object(plugin, "_collect_addon_runtime_state", return_value=addon_state),
                mock.patch.object(plugin, "_default_engine_name", return_value="RHVoice"),
                mock.patch.object(plugin, "_is_compiled_runtime", return_value=False),
                mock.patch.object(plugin, "_get_ui_max_text_len", return_value=120000),
                mock.patch.object(plugin, "_get_text_limits_payload", return_value={"RHVoice": 120000}),
                mock.patch.object(plugin, "_get_edge_parallelism_payload", return_value={}),
                mock.patch.object(plugin, "_get_google_parallelism_payload", return_value={}),
                mock.patch.object(plugin, "_get_google_retry_count_payload", return_value={}),
                mock.patch.object(plugin, "_get_edge_options_payload", return_value={}),
                mock.patch.object(plugin, "_get_edge_text_normalizer_payload", return_value={}),
                mock.patch.object(plugin, "_get_edge_voice_catalog_payload", return_value={}),
                mock.patch.object(plugin, "_get_engine_options_payload", return_value={}),
                mock.patch.object(plugin, "_get_voice_catalog_payload", return_value={}),
                mock.patch.object(plugin, "_supported_import_extensions", return_value=[]),
            ):
                response = view.config.__wrapped__(view)

        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("RHVoice", data["engines"])
        self.assertTrue(data["can_install"])
        self.assertTrue(data["addon_runtime"]["installed"])
        self.assertIsInstance(data.get("dual_pause"), dict)
        self.assertEqual(data["dual_pause"].get("default_mode"), "auto")

    def test_install_endpoint_returns_updated_engine_list_without_restart(self) -> None:
        app = Flask(__name__)
        view = plugin.WinTTSView()

        def _fake_init(force: bool = False) -> None:
            plugin.ENGINE_OPTIONS = ["Google", "RHVoice"]
            plugin.VOICE_OPTIONS = {
                "Google": ["Google TTS: Русский (ru)"],
                "RHVoice": ["RHVoice: Anna (ru, женский)"],
            }
            plugin.TTS_IMPORT_ERRORS = []

        addon_state = {"status": "installed", "installed": True, "can_install": True}
        install_result = (True, "RHVoice-addon установлен и готов к работе.", ["pip ok"], addon_state)

        with app.test_request_context("/plugins/wintts/install", method="POST"):
            with (
                mock.patch.object(plugin, "_is_csrf_valid", return_value=True),
                mock.patch.object(plugin, "_install_tts_dependencies", return_value=install_result),
                mock.patch.object(plugin, "init_tts_engines", side_effect=_fake_init),
                mock.patch.object(plugin, "_dependency_diagnostics_payload", return_value={"raw": {}, "lines": []}),
                mock.patch.object(plugin, "_collect_addon_runtime_state", return_value=addon_state),
            ):
                response, status = view.install.__wrapped__(view)

        data = response.get_json()
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("RHVoice", data["engines"])
        self.assertTrue(data["addon_runtime"]["installed"])

    def test_human_size_uses_russian_units_without_mojibake(self) -> None:
        value = plugin._human_size(1)
        self.assertEqual(value, "1 Б")
        self.assertNotIn("Р'", value)
        self.assertNotIn("Р", value)

    def test_parse_request_keeps_normalizer_for_google_engine(self) -> None:
        app = Flask(__name__)
        plugin.ENGINE_OPTIONS = ["Google"]
        plugin.VOICE_OPTIONS = {"Google": ["Google TTS: Русский (ru)"]}
        plugin.GOOGLE_TTS_VOICE_MAP = {"Google TTS: Русский (ru)": {"lang": "ru", "tld": "ru"}}
        plugin.GOOGLE_TTS_LANGUAGE_MAP = {"ru": ["Google TTS: Русский (ru)"]}

        payload = {
            "text": "Привет ### world",
            "engine": "Google",
            "voice": "Google TTS: Русский (ru)",
            "text_normalizer": {
                "enabled": True,
                "preset": "balanced",
                "drop_symbol_only_tokens": True,
            },
        }

        with app.test_request_context("/plugins/wintts/synthesize", method="POST", json=payload):
            with mock.patch.object(plugin, "init_tts_engines", return_value=None):
                request_data, error_response = plugin._parse_synthesis_request(payload)

        self.assertIsNone(error_response)
        self.assertIsNotNone(request_data)
        assert request_data is not None
        self.assertIsInstance(request_data.get("edge_text_normalizer"), dict)
        self.assertEqual(
            request_data.get("edge_text_normalizer"),
            request_data.get("text_normalizer"),
        )

    def test_perform_synthesis_applies_normalizer_for_google_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            captured_text: dict[str, str] = {}

            def _fake_synthesize_to_file(**kwargs: object) -> Path:
                captured_text["value"] = str(kwargs.get("text") or "")
                out_path = Path(str(kwargs["file_stem"])).with_suffix(".mp3")
                out_path.write_bytes(_audio_bytes(plugin._MIN_AUDIO_BYTES + 64))
                return out_path

            with (
                mock.patch.object(plugin, "_cleanup_generated_files", return_value=None),
                mock.patch.object(plugin, "_output_root", return_value=out_dir),
                mock.patch.object(plugin, "_cleanup_partial_output_files", return_value=None),
                mock.patch.object(plugin, "_synthesize_to_file", side_effect=_fake_synthesize_to_file),
            ):
                result = plugin._perform_synthesis(
                    text="Привет ### world",
                    engine="Google",
                    voice="Google TTS: Русский (ru)",
                    edge_rate="+0%",
                    edge_volume="+0%",
                    edge_pitch="+0Hz",
                    edge_text_normalizer={
                        "enabled": True,
                        "preset": "balanced",
                        "drop_symbol_only_tokens": True,
                    },
                    user="tests",
                )

            self.assertIn("value", captured_text)
            self.assertNotIn("###", captured_text["value"])
            self.assertIsInstance(result.get("edge_text_normalizer_result"), dict)
            self.assertIsInstance(result.get("text_normalizer_result"), dict)
            self.assertEqual(result.get("edge_text_normalizer"), result.get("text_normalizer"))


if __name__ == "__main__":
    unittest.main()
