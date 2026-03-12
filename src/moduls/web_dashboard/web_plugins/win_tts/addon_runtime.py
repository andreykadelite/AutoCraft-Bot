# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ...external_plugins import resolve_base_python_executable

ADDON_DIR_NAME = "addon_win_ttts"
VENV_DIR_NAME = "venv"
RHVOICE_PACKAGES = ("rhvoice-wrapper==0.8.0", "rhvoice-wrapper-bin==0.5.0")

_LANGUAGE_CODE_RE = re.compile(r"([a-z]{2,3})(?:[-_][a-z0-9]{2,8})?", re.IGNORECASE)
_LANGUAGE_ALIASES = {
    "russian": "ru",
    "русский": "ru",
    "english": "en",
    "английский": "en",
    "ukrainian": "uk",
    "украинский": "uk",
    "belarusian": "be",
    "белорусский": "be",
    "georgian": "ka",
    "kyrgyz": "ky",
    "polish": "pl",
    "slovak": "sk",
    "czech": "cs",
    "esperanto": "eo",
    "macedonian": "mk",
    "tatar": "tt",
    "uzbek": "uz",
    "albanian": "sq",
    "brazilian-portuguese": "pt",
    "portuguese": "pt",
}


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return Path(str(path))


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        resolved = _safe_resolve(item)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _ensure_base_dir(base_dir: str | Path | None) -> Path:
    if base_dir:
        return _safe_resolve(Path(str(base_dir)))
    try:
        return _safe_resolve(Path.cwd())
    except Exception:
        return Path(".")


def _addon_dir_in_data(base_path: Path) -> Path:
    return base_path / "data" / ADDON_DIR_NAME


def resolve_addon_candidates(base_dir: str | Path | None, compiled_runtime: bool) -> list[Path]:
    base_root = _ensure_base_dir(base_dir)
    candidates: list[Path] = []

    try:
        exe_dir = _safe_resolve(Path(sys.executable).parent)
    except Exception:
        exe_dir = base_root
    try:
        argv_dir = _safe_resolve(Path(sys.argv[0]).parent)
    except Exception:
        argv_dir = base_root
    try:
        cwd_dir = _safe_resolve(Path.cwd())
    except Exception:
        cwd_dir = base_root

    if compiled_runtime:
        candidates.extend(
            [
                _addon_dir_in_data(exe_dir),
                _addon_dir_in_data(argv_dir),
                _addon_dir_in_data(base_root),
                _addon_dir_in_data(cwd_dir),
            ]
        )
    else:
        candidates.extend(
            [
                _addon_dir_in_data(base_root),
                _addon_dir_in_data(argv_dir),
                _addon_dir_in_data(cwd_dir),
            ]
        )

    return _unique_paths(candidates)


def _venv_paths(addon_root: Path) -> dict[str, Path]:
    venv_dir = addon_root / VENV_DIR_NAME
    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_pip = venv_dir / "Scripts" / "pip.exe"
        site_packages = venv_dir / "Lib" / "site-packages"
    else:
        venv_python = venv_dir / "bin" / "python"
        venv_pip = venv_dir / "bin" / "pip"
        site_packages = venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return {
        "venv_dir": venv_dir,
        "venv_python": venv_python,
        "venv_pip": venv_pip,
        "venv_site_packages": site_packages,
    }


def _probe_write_access(path: Path) -> bool:
    target = path if path.exists() else path.parent
    try:
        current = _safe_resolve(target)
    except Exception:
        current = target

    while not current.exists() and current.parent != current:
        current = current.parent
    if not current.exists() or not current.is_dir():
        return False

    probe_dir = path if path.exists() and path.is_dir() else current
    try:
        fd, tmp = tempfile.mkstemp(prefix="wintts_addon_", dir=str(probe_dir))
        os.close(fd)
        os.unlink(tmp)
        return True
    except Exception:
        return False


def _pick_addon_root(candidates: list[Path]) -> tuple[Path | None, bool]:
    for candidate in candidates:
        if _probe_write_access(candidate):
            return candidate, True
    if candidates:
        return candidates[0], False
    return None, False


def collect_addon_state(
    base_dir: str | Path | None,
    compiled_runtime: bool,
    forced_root: Path | None = None,
) -> dict[str, Any]:
    base_root = _ensure_base_dir(base_dir)
    python_info = resolve_base_python_executable(str(base_root))
    base_python = str(python_info.get("path") or "").strip()
    candidates = resolve_addon_candidates(base_root, compiled_runtime)

    writable = False
    if forced_root is not None:
        addon_root = _safe_resolve(forced_root)
        writable = _probe_write_access(addon_root)
    else:
        addon_root, writable = _pick_addon_root(candidates)

    addon_root = addon_root or _addon_dir_in_data(base_root)
    paths = _venv_paths(addon_root)
    marker = paths["venv_dir"] / "pyvenv.cfg"

    installed = (
        marker.is_file()
        and paths["venv_python"].is_file()
        and paths["venv_pip"].is_file()
        and paths["venv_site_packages"].is_dir()
    )
    broken = paths["venv_dir"].exists() and not installed

    can_install = bool(base_python) and writable
    status = "installed" if installed else ("error" if broken else "not_installed")
    message = (
        "Addon-окружение RHVoice готово."
        if installed
        else (
            "Addon-окружение RHVoice повреждено."
            if broken
            else (
                "Addon-окружение RHVoice ещё не установлено."
                if can_install
                else "Не найден доступный Python или путь для установки RHVoice."
            )
        )
    )

    return {
        "compiled_runtime": bool(compiled_runtime),
        "status": status,
        "message": message,
        "installed": bool(installed),
        "broken": bool(broken),
        "can_install": bool(can_install),
        "base_dir": str(base_root),
        "base_python": base_python,
        "base_python_source": str(python_info.get("source") or ""),
        "base_python_source_label": str(python_info.get("source_label") or ""),
        "addon_root": str(addon_root),
        "path_candidates": [str(path) for path in candidates],
        "selected_writable": bool(writable),
        "venv_dir": str(paths["venv_dir"]),
        "venv_python": str(paths["venv_python"]),
        "venv_pip": str(paths["venv_pip"]),
        "venv_site_packages": str(paths["venv_site_packages"]),
    }


def can_install_addon(base_dir: str | Path | None, compiled_runtime: bool) -> bool:
    state = collect_addon_state(base_dir, compiled_runtime)
    return bool(state.get("can_install"))


def _trim_command_output(value: str, limit: int = 1500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _run_command(args: list[str], cwd: Path, timeout_seconds: int) -> tuple[int, list[str]]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout_seconds,
    )
    details = [f"$ {' '.join(args)}", f"Код завершения: {proc.returncode}"]
    stdout = _trim_command_output(proc.stdout)
    stderr = _trim_command_output(proc.stderr)
    if stdout:
        details.append(stdout)
    if stderr:
        details.append(stderr)
    return proc.returncode, details


def ensure_addon_environment(
    base_dir: str | Path | None,
    compiled_runtime: bool,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    initial_state = collect_addon_state(base_dir, compiled_runtime)
    details: list[str] = []

    base_python = str(initial_state.get("base_python") or "").strip()
    if not base_python:
        return {
            "ok": False,
            "message": "Не найден Python для установки RHVoice-addon.",
            "details": details,
            "state": initial_state,
        }

    candidates = [Path(str(item)) for item in initial_state.get("path_candidates") or []]
    install_root, writable = _pick_addon_root(candidates)
    if install_root is None:
        return {
            "ok": False,
            "message": "Не найден путь для размещения addon-окружения RHVoice.",
            "details": details,
            "state": initial_state,
        }
    if not writable:
        return {
            "ok": False,
            "message": f"Нет прав записи в папку addon-окружения: {install_root}",
            "details": details,
            "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
        }

    try:
        install_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Не удалось создать папку addon-окружения: {exc}",
            "details": details,
            "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
        }

    state = collect_addon_state(base_dir, compiled_runtime, forced_root=install_root)
    venv_dir = Path(str(state["venv_dir"]))
    venv_python = Path(str(state["venv_python"]))

    if not venv_python.is_file():
        code, output = _run_command(
            [base_python, "-m", "venv", str(venv_dir)],
            install_root,
            timeout_seconds,
        )
        details.extend(output)
        if code != 0:
            return {
                "ok": False,
                "message": "Не удалось создать виртуальное окружение RHVoice-addon.",
                "details": details,
                "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
            }
        state = collect_addon_state(base_dir, compiled_runtime, forced_root=install_root)
        venv_python = Path(str(state["venv_python"]))

    if not venv_python.is_file():
        return {
            "ok": False,
            "message": "После создания окружения не найден python внутри venv RHVoice-addon.",
            "details": details,
            "state": state,
        }

    code, output = _run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        install_root,
        timeout_seconds,
    )
    details.extend(output)
    if code != 0:
        return {
            "ok": False,
            "message": "Не удалось обновить pip/setuptools/wheel в RHVoice-addon.",
            "details": details,
            "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
        }

    code, output = _run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", *RHVOICE_PACKAGES],
        install_root,
        timeout_seconds,
    )
    details.extend(output)
    if code != 0:
        return {
            "ok": False,
            "message": "Не удалось установить пакеты RHVoice-addon.",
            "details": details,
            "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
        }

    code, output = _run_command(
        [str(venv_python), "-c", "import rhvoice_wrapper, rhvoice_wrapper_bin; print('ok')"],
        install_root,
        timeout_seconds,
    )
    details.extend(output)
    if code != 0:
        return {
            "ok": False,
            "message": "RHVoice-addon установлен, но импорт модулей не прошёл.",
            "details": details,
            "state": collect_addon_state(base_dir, compiled_runtime, forced_root=install_root),
        }

    final_state = collect_addon_state(base_dir, compiled_runtime, forced_root=install_root)
    if not final_state.get("installed"):
        return {
            "ok": False,
            "message": "RHVoice-addon создан, но итоговая проверка окружения не пройдена.",
            "details": details,
            "state": final_state,
        }

    return {
        "ok": True,
        "message": "RHVoice-addon установлен и готов к работе.",
        "details": details,
        "state": final_state,
    }


def _ensure_site_packages_on_path(site_packages_path: str) -> None:
    path = str(site_packages_path or "").strip()
    if not path:
        return
    try:
        resolved = str(_safe_resolve(Path(path)))
    except Exception:
        resolved = path
    try:
        normalized = os.path.normcase(resolved)
        current = {os.path.normcase(str(item)) for item in sys.path if item}
    except Exception:
        normalized = resolved
        current = set(sys.path)
    if normalized not in current:
        sys.path.insert(0, resolved)
    importlib.invalidate_caches()


def load_rhvoice_tts_class(state: dict[str, Any]) -> tuple[Any | None, str]:
    if not isinstance(state, dict):
        return None, "Состояние RHVoice-addon не передано."
    if not bool(state.get("installed")):
        return None, "RHVoice-addon ещё не установлен."
    site_packages = str(state.get("venv_site_packages") or "").strip()
    if not site_packages:
        return None, "Не найден путь site-packages RHVoice-addon."
    site_path = Path(site_packages)
    if not site_path.is_dir():
        return None, f"Путь site-packages не существует: {site_path}"

    _ensure_site_packages_on_path(str(site_path))
    try:
        module = importlib.import_module("rhvoice_wrapper")
    except Exception as exc:
        return None, f"Ошибка импорта rhvoice_wrapper: {type(exc).__name__}: {exc}"
    tts_class = getattr(module, "TTS", None)
    if tts_class is None:
        return None, "В модуле rhvoice_wrapper не найден класс TTS."
    return tts_class, ""


def _normalize_language_code(value: Any, fallback: str = "und") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    if text in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[text]
    match = _LANGUAGE_CODE_RE.search(text)
    if match:
        code = str(match.group(1) or "").strip().lower()
        if code in _LANGUAGE_ALIASES:
            return _LANGUAGE_ALIASES[code]
        if code:
            return code
    for alias, code in _LANGUAGE_ALIASES.items():
        if alias in text:
            return code
    return fallback


def _rhvoice_label(profile: str, lang_code: str, info: dict[str, Any]) -> str:
    clean_profile = str(profile or "").strip() or "Voice"
    clean_lang = _normalize_language_code(lang_code, fallback="und")
    gender = str(info.get("gender") or "").strip().lower()
    gender_ru = "женский" if gender == "female" else ("мужской" if gender == "male" else "")
    details: list[str] = [clean_lang]
    if gender_ru:
        details.append(gender_ru)
    return f"RHVoice: {clean_profile} ({', '.join(details)})"


def build_rhvoice_voice_catalog(tts_class: Any) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    warnings: list[str] = []
    voice_map: dict[str, str] = {}
    language_map: dict[str, list[str]] = {}
    tts_obj: Any = None
    try:
        tts_obj = tts_class(threads=1)
        raw_profiles = list(getattr(tts_obj, "voice_profiles", ()) or ())
        raw_info = dict(getattr(tts_obj, "voices_info", {}) or {})
        profiles: list[str] = [str(item or "").strip() for item in raw_profiles if str(item or "").strip()]
        if not profiles:
            profiles = [str(item or "").strip() for item in raw_info.keys() if str(item or "").strip()]
        if not profiles:
            warnings.append("RHVoice не вернул список голосов.")
            return {}, {}, warnings

        duplicate_guard: dict[str, int] = {}
        for profile in profiles:
            info = {}
            if isinstance(raw_info, dict):
                info = raw_info.get(profile.lower()) or raw_info.get(profile) or {}
            lang_raw = ""
            if isinstance(info, dict):
                lang_raw = str(info.get("lang") or info.get("language") or "").strip()
            language_code = _normalize_language_code(lang_raw or profile, fallback="und")
            label = _rhvoice_label(profile, language_code, info if isinstance(info, dict) else {})
            count = duplicate_guard.get(label, 0)
            duplicate_guard[label] = count + 1
            final_label = f"{label} [{count + 1}]" if count else label

            voice_map[final_label] = profile
            language_map.setdefault(language_code, []).append(final_label)
    except Exception as exc:
        warnings.append(f"Не удалось построить каталог голосов RHVoice: {type(exc).__name__}: {exc}")
    finally:
        if tts_obj is not None:
            try:
                tts_obj.join()
            except Exception:
                pass

    return voice_map, language_map, warnings


def _to_slider_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except Exception:
        parsed = default
    if parsed < -100:
        return -100
    if parsed > 100:
        return 100
    return parsed


def slider_to_rhvoice_absolute(value: Any) -> float:
    slider = _to_slider_int(value, default=0)
    if slider >= 0:
        return round((slider / 100.0) * 2.5, 4)
    return round((slider / 100.0) * 2.0, 4)


def synthesize_rhvoice_to_file(
    text: str,
    voice_profile: str,
    out_path: str,
    edge_rate: Any = 0,
    edge_pitch: Any = 0,
    edge_volume: Any = 0,
    state: dict[str, Any] | None = None,
) -> str:
    runtime_state = state or {}
    tts_class, error = load_rhvoice_tts_class(runtime_state)
    if tts_class is None:
        raise RuntimeError(error or "RHVoice-addon недоступен.")

    clean_text = str(text or "").strip()
    if not clean_text:
        raise RuntimeError("Пустой текст для RHVoice.")

    profile = str(voice_profile or "").strip()
    if not profile:
        raise RuntimeError("Не выбран голос RHVoice.")

    tts_obj: Any = None
    try:
        tts_obj = tts_class(threads=1)
        sets = {
            "absolute_rate": slider_to_rhvoice_absolute(edge_rate),
            "absolute_pitch": slider_to_rhvoice_absolute(edge_pitch),
            "absolute_volume": slider_to_rhvoice_absolute(edge_volume),
            "voice_profile": profile,
        }
        tts_obj.to_file(
            filename=str(out_path),
            text=clean_text,
            voice=profile,
            format_="wav",
            sets=sets,
        )
    except Exception as exc:
        raise RuntimeError(f"RHVoice synthesis failed: {type(exc).__name__}: {exc}") from exc
    finally:
        if tts_obj is not None:
            try:
                tts_obj.join()
            except Exception:
                pass
    return str(out_path)
