# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from moduls.web_dashboard.web_plugins.win_tts import addon_runtime


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class AddonRuntimeTests(unittest.TestCase):
    def test_collect_addon_state_source_uses_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fake_python = root / "python" / "python.exe"
            fake_python.parent.mkdir(parents=True, exist_ok=True)
            fake_python.write_text("", encoding="utf-8")

            with mock.patch.object(
                addon_runtime,
                "resolve_base_python_executable",
                return_value={
                    "path": str(fake_python),
                    "source": "project_python",
                    "source_label": "Папка python проекта",
                },
            ):
                state = addon_runtime.collect_addon_state(base_dir=root, compiled_runtime=False)

            self.assertEqual(Path(str(state["addon_root"])), root / "data" / addon_runtime.ADDON_DIR_NAME)
            self.assertTrue(bool(state["can_install"]))

    def test_collect_addon_state_compiled_uses_data_near_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            exe_dir = root / "bin"
            exe_dir.mkdir(parents=True, exist_ok=True)
            fake_exe = exe_dir / "app.exe"
            fake_exe.write_text("", encoding="utf-8")
            fake_python = exe_dir / "python" / "python.exe"
            fake_python.parent.mkdir(parents=True, exist_ok=True)
            fake_python.write_text("", encoding="utf-8")

            with (
                mock.patch.object(addon_runtime.sys, "executable", str(fake_exe)),
                mock.patch.object(addon_runtime.sys, "argv", [str(fake_exe)]),
                mock.patch.object(
                    addon_runtime,
                    "resolve_base_python_executable",
                    return_value={
                        "path": str(fake_python),
                        "source": "project_python",
                        "source_label": "Папка python проекта",
                    },
                ),
            ):
                state = addon_runtime.collect_addon_state(base_dir=root, compiled_runtime=True)

            expected = exe_dir / "data" / addon_runtime.ADDON_DIR_NAME
            self.assertEqual(Path(str(state["addon_root"])), expected)

    def test_load_rhvoice_tts_class_from_custom_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            site_packages = Path(tmp_dir) / "site-packages"
            module_dir = site_packages / "rhvoice_wrapper"
            module_dir.mkdir(parents=True, exist_ok=True)
            (module_dir / "__init__.py").write_text(
                "class TTS:\n"
                "    def __init__(self, *args, **kwargs):\n"
                "        pass\n",
                encoding="utf-8",
            )

            state = {
                "installed": True,
                "venv_site_packages": str(site_packages),
            }

            addon_runtime.sys.modules.pop("rhvoice_wrapper", None)
            tts_class, error = addon_runtime.load_rhvoice_tts_class(state)
            self.assertEqual(error, "")
            self.assertIsNotNone(tts_class)
            self.assertEqual(getattr(tts_class, "__name__", ""), "TTS")

    def test_build_rhvoice_voice_catalog(self) -> None:
        class FakeTTS:
            def __init__(self, threads: int = 1) -> None:
                self.voice_profiles = ("Anna", "Alan")
                self.voices_info = {
                    "anna": {"lang": "Russian", "gender": "female"},
                    "alan": {"lang": "English", "gender": "male"},
                }

            def join(self) -> None:
                return None

        voice_map, language_map, warnings = addon_runtime.build_rhvoice_voice_catalog(FakeTTS)
        self.assertFalse(warnings)
        self.assertEqual(len(voice_map), 2)
        self.assertIn("ru", language_map)
        self.assertIn("en", language_map)

    def test_ensure_addon_environment_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            base_python = root / "python" / "python.exe"
            base_python.parent.mkdir(parents=True, exist_ok=True)
            base_python.write_text("", encoding="utf-8")

            calls: list[list[str]] = []

            def _fake_run(*args: object, **kwargs: object) -> types.SimpleNamespace:
                cmd = list(args[0])  # type: ignore[index]
                calls.append([str(item) for item in cmd])
                cwd = Path(str(kwargs.get("cwd") or root))
                if "-m" in cmd and "venv" in cmd:
                    venv_dir = Path(str(cmd[-1]))
                    scripts = venv_dir / ("Scripts" if os.name == "nt" else "bin")
                    site = (
                        venv_dir / "Lib" / "site-packages"
                        if os.name == "nt"
                        else venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
                    )
                    scripts.mkdir(parents=True, exist_ok=True)
                    site.mkdir(parents=True, exist_ok=True)
                    python_name = "python.exe" if os.name == "nt" else "python"
                    pip_name = "pip.exe" if os.name == "nt" else "pip"
                    (scripts / python_name).write_text("", encoding="utf-8")
                    (scripts / pip_name).write_text("", encoding="utf-8")
                    (venv_dir / "pyvenv.cfg").write_text("home = fake\n", encoding="utf-8")
                    return _completed(0, "venv ok", "")
                return _completed(0, "ok", "")

            with (
                mock.patch.object(
                    addon_runtime,
                    "resolve_base_python_executable",
                    return_value={
                        "path": str(base_python),
                        "source": "project_python",
                        "source_label": "Папка python проекта",
                    },
                ),
                mock.patch.object(addon_runtime.subprocess, "run", side_effect=_fake_run),
            ):
                first = addon_runtime.ensure_addon_environment(base_dir=root, compiled_runtime=False)
                second = addon_runtime.ensure_addon_environment(base_dir=root, compiled_runtime=False)

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            venv_calls = [cmd for cmd in calls if "-m" in cmd and "venv" in cmd]
            self.assertEqual(len(venv_calls), 1)


if __name__ == "__main__":
    unittest.main()
