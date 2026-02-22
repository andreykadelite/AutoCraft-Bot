# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import abort, current_app, jsonify, render_template_string, request, send_file, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ...security import panel_has_access as has_access

try:
    import pyttsx3  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pyttsx3 = None

try:
    from gtts import gTTS  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    gTTS = None

_LOGGER = logging.getLogger("panel.plugins")
_TEMPLATE_ROOT: Path | None = None

_MAX_TEXT_LEN = 5000
_MIN_AUDIO_BYTES = 128
_MAX_AUDIO_AGE_SECONDS = 24 * 3600
_MAX_AUDIO_FILES = 300
_FILENAME_RE = re.compile(r"^tts_\d{10}_[a-f0-9]{12}\.(mp3|wav)$", re.IGNORECASE)
_EDGE_CHUNK_LIMITS = (2800, 1600, 1000, 700, 450)

_EDGE_FALLBACK_VOICES = (
    "ru-RU-SvetlanaNeural",
    "ru-RU-DmitryNeural",
    "ru-RU-DariyaNeural",
)

_FFMPEG_PATH: str | None = None

# Опции синтеза речи и состояние TTS.
ENGINE_OPTIONS: list[str] = []
VOICE_OPTIONS: dict[str, list[str]] = {}
PYTTSX3_VOICE_MAP: dict[str, str] = {}
EDGE_TTS_VOICE_MAP: dict[str, str] = {}
EDGE_TTS_MODULE: Any = None
TTS_INIT_DONE = False
TTS_IMPORT_ERRORS: list[str] = []
_DEPENDENCY_DIAGNOSTICS: dict[str, Any] = {}
_ONEFILE_DIR_PREFIXES = ("onefile_", "onefil", "_mei")


def _diag() -> dict[str, Any]:
    global _DEPENDENCY_DIAGNOSTICS
    if not isinstance(_DEPENDENCY_DIAGNOSTICS, dict):
        _DEPENDENCY_DIAGNOSTICS = {}
    _DEPENDENCY_DIAGNOSTICS.setdefault("runtime", {})
    _DEPENDENCY_DIAGNOSTICS.setdefault("paths", {})
    _DEPENDENCY_DIAGNOSTICS.setdefault("imports", {})
    _DEPENDENCY_DIAGNOSTICS.setdefault("ffmpeg", {})
    return _DEPENDENCY_DIAGNOSTICS


def _reset_dependency_diagnostics() -> None:
    global _DEPENDENCY_DIAGNOSTICS
    _DEPENDENCY_DIAGNOSTICS = {
        "runtime": {},
        "paths": {},
        "imports": {},
        "ffmpeg": {},
    }


def _short_text(value: Any, max_len: int = 300) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _module_origin(module_obj: Any) -> str:
    if module_obj is None:
        return ""
    try:
        path = getattr(module_obj, "__file__", None)
        if path:
            return str(path)
    except Exception:
        pass
    try:
        spec = getattr(module_obj, "__spec__", None)
        if spec and getattr(spec, "origin", None):
            return str(spec.origin)
    except Exception:
        pass
    return ""


def _is_csrf_valid() -> bool:
    payload = {}
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}
    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or payload.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _template_root() -> Path:
    if _TEMPLATE_ROOT:
        try:
            return Path(_TEMPLATE_ROOT)
        except Exception:
            pass
    return Path(__file__).resolve().parent


def _load_template(root: Path | None = None) -> str:
    template_path = (root or _template_root()) / "index.html"
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            data = template_path.read_text(encoding=encoding)
            return data.lstrip("\ufeff")
        except Exception:
            continue
    return ""


def _is_compiled_runtime() -> bool:
    try:
        if bool(globals().get("__compiled__", False)):
            return True
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        return True
    try:
        exe = str(getattr(sys, "executable", "") or "")
        exe_l = exe.lower()
        name = Path(exe_l).name
        if exe_l.endswith(".exe") and name not in ("python.exe", "pythonw.exe"):
            return True
    except Exception:
        pass
    return False


def _is_onefile_dir_name(name: str) -> bool:
    lower = str(name or "").lower()
    return any(lower.startswith(prefix) for prefix in _ONEFILE_DIR_PREFIXES)


def _pick_onefile_dir(path: Path) -> Path | None:
    for parent in [path] + list(path.parents):
        if _is_onefile_dir_name(parent.name):
            return parent
    return None


def _scan_temp_onefile_dirs(limit: int = 16) -> list[Path]:
    roots: list[Path] = []
    seen_roots: set[str] = set()
    for raw in (
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        os.environ.get("TMPDIR"),
        tempfile.gettempdir(),
    ):
        if not raw:
            continue
        try:
            root = Path(raw).expanduser().resolve()
        except Exception:
            root = Path(raw).expanduser()
        key = os.path.normcase(str(root))
        if key in seen_roots:
            continue
        seen_roots.add(key)
        roots.append(root)

    candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except Exception:
            continue
        for candidate in entries:
            if not candidate.is_dir():
                continue
            if not _is_onefile_dir_name(candidate.name):
                continue
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            key = os.path.normcase(str(resolved))
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidates.append(resolved)

    try:
        pid_prefix = f"onefile_{os.getpid()}_"
    except Exception:
        pid_prefix = ""

    def _sort_key(path: Path) -> tuple[int, float, str]:
        name = path.name.lower()
        pid_rank = 0 if (pid_prefix and name.startswith(pid_prefix)) else 1
        try:
            # Самые новые каталоги поднимаем выше.
            mtime = -float(path.stat().st_mtime)
        except Exception:
            mtime = 0.0
        return (pid_rank, mtime, name)

    candidates.sort(key=_sort_key)
    if limit > 0:
        return candidates[:limit]
    return candidates


def _guess_onefile_extract_dir() -> Path | None:

    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if not value:
            continue
        try:
            p = Path(value)
            if p.exists():
                return p if p.is_dir() else p.parent
        except Exception:
            continue

    for candidate in _scan_temp_onefile_dirs(limit=1):
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except Exception:
            continue

    try:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None)
        if main_file:
            resolved_main = Path(main_file).resolve()
            found = _pick_onefile_dir(resolved_main)
            if found:
                return found
            if resolved_main.parent.is_dir():
                # В некоторых сборках путь может не содержать onefile_ в имени,
                # но фактически указывать в temp-распаковку.
                return resolved_main.parent
    except Exception:
        pass

    try:
        found = _pick_onefile_dir(Path(sys.executable).resolve())
        if found:
            return found
    except Exception:
        pass

    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = Path(meipass)
            if p.exists():
                return p if p.is_dir() else p.parent
    except Exception:
        pass

    return None


def _path_from_env_or_parent(var_name: str) -> Path | None:
    value = os.environ.get(var_name, "").strip()
    if not value:
        return None
    # В Nuitka часть env может содержать PID/служебные значения, не путь.
    looks_like_path = ("\\" in value) or ("/" in value) or (":" in value)
    if not looks_like_path:
        return None
    try:
        path = Path(value).expanduser().resolve()
    except Exception:
        path = Path(value).expanduser()
    try:
        if path.exists():
            return path if path.is_dir() else path.parent
    except Exception:
        pass
    try:
        if path.parent and path.parent.is_dir():
            return path.parent
    except Exception:
        pass
    return None


def _runtime_probe_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except Exception:
            resolved = Path(str(path))
        key = os.path.normcase(str(resolved))
        if key in seen:
            return
        seen.add(key)
        dirs.append(resolved)

    # Явный base_dir панели (если задан).
    panel_base = os.environ.get("PANEL_BASE_DIR", "").strip()
    if panel_base:
        _add(Path(panel_base))

    # BASE_DIR из Flask-конфига (доступно в запросах и app context).
    try:
        app_base = current_app.config.get("BASE_DIR")
    except Exception:
        app_base = None
    if app_base:
        _add(Path(str(app_base)))

    onefile_env_enabled = any(
        str(os.environ.get(env_name, "") or "").strip()
        for env_name in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR")
    )
    onefile_runtime_enabled = _is_compiled_runtime() or onefile_env_enabled
    onefile_temp_candidates: list[Path] = []

    # Для onefile-режима важнее папка "оригинального" EXE и temp-распаковки.
    if onefile_runtime_enabled:
        _add(_path_from_env_or_parent("NUITKA_ONEFILE_PARENT"))
        _add(_path_from_env_or_parent("NUITKA_ONEFILE_TEMP"))
        _add(_path_from_env_or_parent("NUITKA_ONEFILE_TEMP_DIR"))
        _add(_guess_onefile_extract_dir())
        onefile_temp_candidates = _scan_temp_onefile_dirs()
        for onefile_dir in onefile_temp_candidates:
            _add(onefile_dir)

    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            _add(Path(meipass))
    except Exception:
        pass

    if _is_compiled_runtime():
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass
        try:
            _add(Path(sys.argv[0]).resolve().parent)
        except Exception:
            pass
        try:
            _add(Path.cwd().resolve())
        except Exception:
            pass
    else:
        try:
            _add(Path.cwd().resolve())
        except Exception:
            pass
        try:
            _add(Path(sys.argv[0]).resolve().parent)
        except Exception:
            pass
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass

    _add(_template_root())

    diag = _diag()
    runtime_info = diag.setdefault("runtime", {})
    runtime_info.update(
        {
            "compiled_runtime": _is_compiled_runtime(),
            "onefile_runtime_enabled": onefile_runtime_enabled,
            "probe_dirs": [str(path) for path in dirs],
            "base_dir": str(dirs[0]) if dirs else "",
            "python_executable": str(getattr(sys, "executable", "") or ""),
            "argv0": str(sys.argv[0]) if sys.argv else "",
            "onefile_temp_candidates": [str(path) for path in onefile_temp_candidates],
            "env_hints": {
                "NUITKA_ONEFILE_PARENT": str(os.environ.get("NUITKA_ONEFILE_PARENT", "") or ""),
                "NUITKA_ONEFILE_TEMP": str(os.environ.get("NUITKA_ONEFILE_TEMP", "") or ""),
                "NUITKA_ONEFILE_TEMP_DIR": str(os.environ.get("NUITKA_ONEFILE_TEMP_DIR", "") or ""),
                "TEMP": str(os.environ.get("TEMP", "") or ""),
            },
        },
    )
    return dirs


def _runtime_base_dir() -> Path:
    dirs = _runtime_probe_dirs()
    if dirs:
        return dirs[0]
    try:
        return Path.cwd().resolve()
    except Exception:
        return Path(".")


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except Exception:
        return False


def _is_onefile_extract_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = Path(str(path))

    if _pick_onefile_dir(resolved):
        return True

    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        env_path = _path_from_env_or_parent(env)
        if not env_path:
            continue
        try:
            env_resolved = env_path.resolve()
        except Exception:
            env_resolved = env_path
        if _path_is_within(resolved, env_resolved):
            return True
    return False


def _persistent_output_bases() -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except Exception:
            resolved = Path(str(path))
        if _is_onefile_extract_path(resolved):
            return
        key = os.path.normcase(str(resolved))
        if key in seen:
            return
        seen.add(key)
        candidates.append(resolved)

    panel_base = os.environ.get("PANEL_BASE_DIR", "").strip()
    if panel_base:
        _add(Path(panel_base))

    try:
        app_base = current_app.config.get("BASE_DIR")
    except Exception:
        app_base = None
    if app_base:
        _add(Path(str(app_base)))

    if _is_compiled_runtime():
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass
        try:
            _add(Path.cwd().resolve())
        except Exception:
            pass
        try:
            _add(Path(sys.argv[0]).resolve().parent)
        except Exception:
            pass
    else:
        try:
            _add(Path.cwd().resolve())
        except Exception:
            pass
        try:
            _add(Path(sys.argv[0]).resolve().parent)
        except Exception:
            pass

    return candidates


def _output_root() -> Path:
    # Сохраняем в постоянную папку sound, а не в temp onefile.
    mkdir_errors: list[str] = []
    output_bases = _persistent_output_bases()
    sound_candidates = [str(path / "sound") for path in output_bases]
    for base in output_bases:
        target = base / "sound"
        try:
            target.mkdir(parents=True, exist_ok=True)
            runtime_info = _diag().setdefault("runtime", {})
            runtime_info["sound_candidates"] = sound_candidates
            runtime_info["sound_selected"] = str(target)
            if mkdir_errors:
                runtime_info["sound_create_errors"] = mkdir_errors[:10]
            return target
        except Exception as exc:
            mkdir_errors.append(f"{target}: {type(exc).__name__}: {exc}")

    fallback = _runtime_base_dir() / "sound"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        fallback = Path.cwd() / "sound"
        fallback.mkdir(parents=True, exist_ok=True)

    runtime_info = _diag().setdefault("runtime", {})
    runtime_info["sound_candidates"] = sound_candidates
    runtime_info["sound_selected"] = str(fallback)
    if mkdir_errors:
        runtime_info["sound_create_errors"] = mkdir_errors[:10]
    return fallback


def _ensure_dependency_paths() -> None:
    major = sys.version_info.major
    minor = sys.version_info.minor
    py_tag = f"python{major}.{minor}"

    existing: set[str] = set()
    for entry in list(sys.path):
        if not entry:
            continue
        try:
            existing.add(os.path.normcase(str(Path(entry).resolve())))
        except Exception:
            existing.add(os.path.normcase(str(entry)))

    candidates: list[Path] = []
    for base in _runtime_probe_dirs():
        candidates.extend(
            [
                base,
                base / "Lib",
                base / "lib",
                base / "site-packages",
                base / "Lib" / "site-packages",
                base / "lib" / "site-packages",
                base / "lib" / py_tag / "site-packages",
                base / "python" / "Lib" / "site-packages",
                base / "python" / "lib" / py_tag / "site-packages",
            ]
        )

    checked: list[str] = []
    added: list[str] = []
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = os.path.normcase(str(resolved))
        checked.append(str(resolved))
        if key in existing:
            continue
        sys.path.insert(0, str(resolved))
        existing.add(key)
        added.append(str(resolved))

    diag = _diag()
    diag["paths"] = {
        "checked_count": len(checked),
        "checked": checked[:80],
        "added_count": len(added),
        "added": added,
    }


def _import_module_with_hints(module_name: str) -> tuple[Any | None, Exception | None]:
    spec_origin = ""
    spec_error = ""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec and getattr(spec, "origin", None):
            spec_origin = str(spec.origin)
    except Exception as exc:
        spec_error = f"{type(exc).__name__}: {exc}"

    attempts: list[dict[str, Any]] = []
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            module_obj = importlib.import_module(module_name)
            attempts.append(
                {
                    "step": attempt + 1,
                    "ok": True,
                    "origin": _module_origin(module_obj),
                }
            )
            _diag()["imports"][module_name] = {
                "available": True,
                "origin": _module_origin(module_obj),
                "spec_origin": spec_origin,
                "spec_error": spec_error,
                "attempts": attempts,
                "last_error": "",
            }
            return module_obj, None
        except Exception as exc:
            last_exc = exc
            attempts.append(
                {
                    "step": attempt + 1,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if attempt == 0:
                _ensure_dependency_paths()
                try:
                    sys.modules.pop(module_name, None)
                except Exception:
                    pass

    _diag()["imports"][module_name] = {
        "available": False,
        "origin": "",
        "spec_origin": spec_origin,
        "spec_error": spec_error,
        "attempts": attempts,
        "last_error": f"{type(last_exc).__name__}: {last_exc}" if last_exc else "",
    }
    return None, last_exc


def _detect_ffmpeg(force: bool = False) -> str | None:
    global _FFMPEG_PATH
    if _FFMPEG_PATH and not force:
        return _FFMPEG_PATH
    _FFMPEG_PATH = None

    root = _template_root()
    candidates = [
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
        str(root / "ffmpeg.exe"),
        str(root / "ffmpeg"),
    ]
    for base in _runtime_probe_dirs():
        candidates.extend(
            [
                str(base / "ffmpeg.exe"),
                str(base / "ffmpeg"),
                str(base / "ffmpeg-7.1" / "bin" / "ffmpeg.exe"),
                str(base / "ffmpeg-7.1" / "bin" / "ffmpeg"),
            ]
        )

    dedup: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(str(candidate))

    checked: list[dict[str, Any]] = []
    for candidate in dedup:
        if not candidate:
            continue
        exists = os.path.isfile(candidate)
        checked.append({"path": str(candidate), "exists": exists})
        if exists:
            _FFMPEG_PATH = candidate
            break

    _diag()["ffmpeg"] = {
        "selected": _FFMPEG_PATH or "",
        "available": bool(_FFMPEG_PATH),
        "candidates": checked,
    }
    return _FFMPEG_PATH


def _cleanup_generated_files() -> None:
    root = _output_root()
    now = time.time()
    files: list[Path] = []
    for item in root.glob("tts_*.*"):
        if not item.is_file():
            continue
        if item.suffix.lower() not in (".mp3", ".wav"):
            continue
        files.append(item)

    if not files:
        return

    # Удаляем слишком старые файлы.
    for path in files:
        try:
            age = now - path.stat().st_mtime
            if age > _MAX_AUDIO_AGE_SECONDS:
                path.unlink(missing_ok=True)
        except Exception:
            continue

    files = [p for p in root.glob("tts_*.*") if p.is_file() and p.suffix.lower() in (".mp3", ".wav")]
    if len(files) <= _MAX_AUDIO_FILES:
        return

    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for path in files[_MAX_AUDIO_FILES:]:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


def _resolve_audio_path(file_name: str) -> Path | None:
    if not file_name or not _FILENAME_RE.match(file_name):
        return None
    path = _output_root() / file_name
    if path.is_file():
        return path
    return None


def _audio_mimetype(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    return "application/octet-stream"


def _split_text_utf8(text: str, max_bytes: int) -> list[str]:
    """Режем текст на куски по лимиту UTF-8 байт."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\t ]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return []

    sentences = re.split(r"(?<=[\.\!\?…])\s+", cleaned)
    chunks: list[str] = []
    buf = ""

    def _fits(value: str) -> bool:
        return len(value.encode("utf-8")) <= max_bytes

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not buf:
            if _fits(sentence):
                buf = sentence
            else:
                words = sentence.split()
                wbuf = ""
                for word in words:
                    candidate = (wbuf + " " + word).strip()
                    if _fits(candidate):
                        wbuf = candidate
                    else:
                        if wbuf:
                            chunks.append(wbuf)
                        wbuf = word
                if wbuf:
                    chunks.append(wbuf)
                buf = ""
            continue

        candidate = (buf + " " + sentence).strip()
        if _fits(candidate):
            buf = candidate
        else:
            chunks.append(buf)
            if _fits(sentence):
                buf = sentence
            else:
                words = sentence.split()
                wbuf = ""
                for word in words:
                    candidate2 = (wbuf + " " + word).strip()
                    if _fits(candidate2):
                        wbuf = candidate2
                    else:
                        if wbuf:
                            chunks.append(wbuf)
                        wbuf = word
                if wbuf:
                    chunks.append(wbuf)
                buf = ""
    if buf:
        chunks.append(buf)
    return [chunk for chunk in chunks if chunk and chunk.strip()]


def _ffmpeg_concat_mp3(parts: list[str], out_path: str) -> bool:
    """Склеить mp3-части через ffmpeg concat demuxer."""
    if not parts:
        return False
    if len(parts) == 1:
        try:
            if os.path.abspath(parts[0]) != os.path.abspath(out_path):
                shutil.copyfile(parts[0], out_path)
            return True
        except Exception:
            return False

    ffmpeg_path = _detect_ffmpeg()
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False

    list_path = out_path + ".__concat__.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as file_handle:
            for part in parts:
                part_abs = os.path.abspath(part).replace("'", "\\'")
                file_handle.write(f"file '{part_abs}'\n")
        cmd = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES
    finally:
        try:
            if os.path.exists(list_path):
                os.remove(list_path)
        except Exception:
            pass


async def _edge_tts_save(communicate: Any, out_path: str) -> None:
    """Сохранить результат edge-tts."""
    if hasattr(communicate, "save"):
        await communicate.save(out_path)
        return
    with open(out_path, "wb") as file_handle:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                file_handle.write(chunk.get("data", b""))


def _edge_tts_create_communicate(text: str, voice_id: str) -> Any:
    """Создать edge_tts.Communicate с совместимостью по сигнатурам."""
    try:
        return EDGE_TTS_MODULE.Communicate(text=text, voice=voice_id)
    except TypeError:
        try:
            return EDGE_TTS_MODULE.Communicate(text, voice=voice_id)
        except TypeError:
            return EDGE_TTS_MODULE.Communicate(text, voice_id)


async def synthesize_edge_tts(text: str, voice_id: str, file_path: str) -> None:
    """Синтез через edge_tts в указанный файл."""
    if EDGE_TTS_MODULE is None:
        raise RuntimeError("edge-tts не инициализирован")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Пустой текст для синтеза")

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

    def _audio_ready(path: str) -> bool:
        return os.path.isfile(path) and os.path.getsize(path) > _MIN_AUDIO_BYTES

    def _safe_remove(path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _force_split_utf8(chunk_text: str, max_bytes: int) -> list[str]:
        result: list[str] = []
        buffer = ""
        for char in chunk_text:
            candidate = buffer + char
            if len(candidate.encode("utf-8")) <= max_bytes:
                buffer = candidate
            else:
                if buffer:
                    result.append(buffer)
                buffer = char
        if buffer:
            result.append(buffer)
        return [part for part in result if part] or [chunk_text]

    def _split_chunk(chunk_text: str, max_bytes: int) -> list[str]:
        parts = _split_text_utf8(chunk_text, max_bytes)
        if not parts:
            parts = [chunk_text]
        if len(parts) == 1 and len(parts[0].encode("utf-8")) > max_bytes:
            parts = _force_split_utf8(chunk_text, max_bytes)
        return parts

    async def _synth_one_chunk(chunk_text: str, out_path: str, try_voice: str, attempts: int = 3) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            _safe_remove(out_path)
            try:
                communicate = _edge_tts_create_communicate(chunk_text, try_voice)
                await _edge_tts_save(communicate, out_path)
                if _audio_ready(out_path):
                    return
                raise RuntimeError("edge-tts вернул пустой или слишком маленький аудиофайл")
            except Exception as exc:  # pragma: no cover - сетевые ошибки
                last_exc = exc
                await asyncio.sleep(0.25 * attempt)
        raise last_exc or RuntimeError("edge-tts synthesis failed")

    voice_fallbacks = [voice_id]
    for voice in _EDGE_FALLBACK_VOICES:
        if voice != voice_id:
            voice_fallbacks.append(voice)

    async def _synth_chunk_with_fallback(chunk_text: str, out_path: str) -> Exception | None:
        last_exc: Exception | None = None
        for try_voice in voice_fallbacks[:3]:
            try:
                await _synth_one_chunk(chunk_text, out_path, try_voice, attempts=3)
                return None
            except Exception as exc:
                last_exc = exc
        return last_exc

    top_level_chunks = _split_chunk(clean_text, _EDGE_CHUNK_LIMITS[0])
    parts: list[str] = []
    try:
        for index, chunk in enumerate(top_level_chunks):
            part_path = file_path + f".__part{index:03d}.mp3"
            last_error = await _synth_chunk_with_fallback(chunk, part_path)
            if _audio_ready(part_path):
                parts.append(part_path)
                continue

            recovered = False
            for split_limit in _EDGE_CHUNK_LIMITS[1:]:
                subchunks = _split_chunk(chunk, split_limit)
                if len(subchunks) <= 1:
                    continue

                subparts: list[str] = []
                split_error: Exception | None = None
                for sub_index, subchunk in enumerate(subchunks):
                    sub_path = file_path + f".__part{index:03d}_sub{sub_index:03d}.mp3"
                    subparts.append(sub_path)
                    split_error = await _synth_chunk_with_fallback(subchunk, sub_path)
                    if not _audio_ready(sub_path):
                        break

                if split_error is None and all(_audio_ready(path) for path in subparts):
                    _safe_remove(part_path)
                    if not _ffmpeg_concat_mp3(subparts, part_path):
                        with open(part_path, "wb") as out_handle:
                            for subpart in subparts:
                                with open(subpart, "rb") as in_handle:
                                    out_handle.write(in_handle.read())
                    if _audio_ready(part_path):
                        recovered = True
                        parts.append(part_path)
                for subpart in subparts:
                    _safe_remove(subpart)
                if recovered:
                    break
                if split_error is not None:
                    last_error = split_error

            if not recovered and not _audio_ready(part_path):
                _safe_remove(part_path)
                if last_error is not None:
                    raise RuntimeError(
                        f"Ошибка чанка edge-tts: {type(last_error).__name__}: {last_error}"
                    ) from last_error
                raise RuntimeError("edge-tts: аудиочасть не создана")

        if len(parts) == 1:
            shutil.move(parts[0], file_path)
            parts = []
            return

        if _ffmpeg_concat_mp3(parts, file_path):
            return

        with open(file_path, "wb") as out_handle:
            for part in parts:
                with open(part, "rb") as in_handle:
                    out_handle.write(in_handle.read())
    finally:
        for part in parts:
            _safe_remove(part)

def _run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "running event loop" not in str(exc).lower():
            raise
    result: dict[str, Any] = {}
    error: dict[str, Exception] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result["value"] = loop.run_until_complete(coro)
        except Exception as worker_exc:
            error["value"] = worker_exc
        finally:
            loop.close()

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _prepare_tts_module_imports() -> dict[str, str]:
    global pyttsx3, gTTS

    errors: dict[str, str] = {}
    _ensure_dependency_paths()

    if gTTS is None:
        gtts_module, gtts_exc = _import_module_with_hints("gtts")
        if gtts_module is not None:
            gtts_class = getattr(gtts_module, "gTTS", None)
            if gtts_class is not None:
                gTTS = gtts_class
            else:
                errors["gtts"] = "модуль gtts найден, но класс gTTS в нем отсутствует"
        elif gtts_exc is not None:
            errors["gtts"] = f"{type(gtts_exc).__name__}: {gtts_exc}"
    else:
        gtts_module = sys.modules.get("gtts")
        _diag()["imports"]["gtts"] = {
            "available": True,
            "origin": _module_origin(gtts_module),
            "spec_origin": "",
            "spec_error": "",
            "attempts": [],
            "last_error": "",
        }

    if pyttsx3 is None:
        pyttsx3_module, pyttsx3_exc = _import_module_with_hints("pyttsx3")
        if pyttsx3_module is not None:
            pyttsx3 = pyttsx3_module
        elif pyttsx3_exc is not None:
            errors["pyttsx3"] = f"{type(pyttsx3_exc).__name__}: {pyttsx3_exc}"
    else:
        _diag()["imports"]["pyttsx3"] = {
            "available": True,
            "origin": _module_origin(pyttsx3),
            "spec_origin": "",
            "spec_error": "",
            "attempts": [],
            "last_error": "",
        }

    return errors


def init_tts_engines(force: bool = False) -> None:
    """Инициализация доступных движков TTS."""
    global ENGINE_OPTIONS, VOICE_OPTIONS, PYTTSX3_VOICE_MAP, EDGE_TTS_VOICE_MAP
    global EDGE_TTS_MODULE, TTS_INIT_DONE, TTS_IMPORT_ERRORS

    if TTS_INIT_DONE and not force:
        return

    _reset_dependency_diagnostics()
    _detect_ffmpeg(force=force)
    module_import_errors = _prepare_tts_module_imports()

    ENGINE_OPTIONS = []
    VOICE_OPTIONS = {}
    PYTTSX3_VOICE_MAP = {}
    EDGE_TTS_VOICE_MAP = {}
    EDGE_TTS_MODULE = None
    TTS_IMPORT_ERRORS = []

    if not _FFMPEG_PATH:
        TTS_IMPORT_ERRORS.append(
            "FFmpeg не найден. Проверьте наличие ffmpeg.exe рядом с EXE, в PATH или в ffmpeg-7.1/bin."
        )

    # Google TTS.
    try:
        if gTTS is None:
            err = module_import_errors.get("gtts")
            if err:
                raise RuntimeError(f"модуль gTTS не импортирован ({err})")
            raise RuntimeError("модуль gTTS не импортирован")
        ENGINE_OPTIONS.append("Google")
        VOICE_OPTIONS["Google"] = ["Стандартный голос (ru-RU)"]
    except Exception as exc:
        TTS_IMPORT_ERRORS.append(f"Google TTS недоступен: {type(exc).__name__}: {exc}")

    # pyttsx3 (локальные голоса).
    try:
        if pyttsx3 is None:
            err = module_import_errors.get("pyttsx3")
            if err:
                raise RuntimeError(f"модуль pyttsx3 не импортирован ({err})")
            raise RuntimeError("модуль pyttsx3 не импортирован")
        tts_engine_tmp = pyttsx3.init()
        voices = tts_engine_tmp.getProperty("voices") or []
        labels: list[str] = []
        for index, voice in enumerate(voices, start=1):
            lang = None
            try:
                langs = getattr(voice, "languages", None)
                if langs:
                    raw_lang = langs[0]
                    if isinstance(raw_lang, bytes):
                        lang = raw_lang.decode(errors="ignore")
                    else:
                        lang = str(raw_lang)
            except Exception:
                lang = None
            if lang:
                label = f"{index}: {voice.name} ({lang})"
            else:
                label = f"{index}: {voice.name}"
            labels.append(label)
            PYTTSX3_VOICE_MAP[label] = voice.id
        if labels:
            ENGINE_OPTIONS.append("pyx3")
            VOICE_OPTIONS["pyx3"] = labels
        else:
            TTS_IMPORT_ERRORS.append("pyttsx3 не нашел доступных голосов.")
        try:
            tts_engine_tmp.stop()
        except Exception:
            pass
    except Exception as exc:
        TTS_IMPORT_ERRORS.append(f"pyttsx3 недоступен: {type(exc).__name__}: {exc}")

    # Edge TTS.
    try:
        _edge_tts, edge_exc = _import_module_with_hints("edge_tts")
        if _edge_tts is None:
            if edge_exc is None:
                raise RuntimeError("модуль edge_tts не импортирован")
            raise RuntimeError(f"{type(edge_exc).__name__}: {edge_exc}")
        EDGE_TTS_MODULE = _edge_tts

        try:
            from importlib import metadata as _metadata

            edge_ver = _metadata.version("edge-tts")
        except Exception:
            edge_ver = getattr(_edge_tts, "__version__", "") or ""

        def _ver_tuple(version_text: str) -> tuple[int, int, int]:
            parts = [int(x) for x in re.findall(r"\d+", version_text)[:3]]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)  # type: ignore[return-value]

        if edge_ver:
            try:
                if _ver_tuple(edge_ver) < (7, 2, 4):
                    TTS_IMPORT_ERRORS.append(
                        f"edge-tts версии {edge_ver} может работать нестабильно. Рекомендуется обновить до 7.2.4+."
                    )
            except Exception:
                pass

        edge_voices = {
            "ru-RU-SvetlanaNeural (женский)": "ru-RU-SvetlanaNeural",
            "ru-RU-DmitryNeural (мужской)": "ru-RU-DmitryNeural",
            "ru-RU-DariyaNeural (женский)": "ru-RU-DariyaNeural",
        }
        EDGE_TTS_VOICE_MAP.update(edge_voices)
        ENGINE_OPTIONS.append("Edge TTS")
        VOICE_OPTIONS["Edge TTS"] = list(edge_voices.keys())
    except Exception as exc:
        TTS_IMPORT_ERRORS.append(f"edge-tts недоступен: {type(exc).__name__}: {exc}")
        EDGE_TTS_MODULE = None

    if not ENGINE_OPTIONS:
        ENGINE_OPTIONS.append("Google")
        VOICE_OPTIONS["Google"] = ["Стандартный голос (ru-RU)"]
        TTS_IMPORT_ERRORS.append("Не удалось инициализировать ни один TTS-движок.")

    TTS_INIT_DONE = True


def _dependency_diagnostics_lines() -> list[str]:
    diag = _diag()
    lines: list[str] = []

    runtime = diag.get("runtime") or {}
    lines.append(
        f"Режим запуска: {'EXE/compiled' if runtime.get('compiled_runtime') else 'Python (.py)'}"
    )
    if runtime.get("base_dir"):
        lines.append(f"Базовая папка: {runtime.get('base_dir')}")
    if runtime.get("sound_selected"):
        lines.append(f"Папка сохранения sound: {runtime.get('sound_selected')}")
    if runtime.get("python_executable"):
        lines.append(f"Python executable: {runtime.get('python_executable')}")

    probe_dirs = runtime.get("probe_dirs") or []
    if probe_dirs:
        lines.append("Пути поиска зависимостей:")
        for path in probe_dirs[:12]:
            lines.append(f"- {path}")
        if len(probe_dirs) > 12:
            lines.append(f"- ... и еще {len(probe_dirs) - 12} путей")

    onefile_candidates = runtime.get("onefile_temp_candidates") or []
    if onefile_candidates:
        lines.append("Найденные onefile temp-каталоги:")
        for path in onefile_candidates[:8]:
            lines.append(f"- {path}")
        if len(onefile_candidates) > 8:
            lines.append(f"- ... и еще {len(onefile_candidates) - 8} путей")

    ffmpeg_info = diag.get("ffmpeg") or {}
    if ffmpeg_info.get("available"):
        lines.append(f"FFmpeg: найден ({ffmpeg_info.get('selected')})")
    else:
        lines.append("FFmpeg: не найден")

    ffmpeg_candidates = ffmpeg_info.get("candidates") or []
    if ffmpeg_candidates:
        lines.append("Проверенные пути FFmpeg:")
        for item in ffmpeg_candidates[:12]:
            status = "найден" if item.get("exists") else "нет"
            lines.append(f"- [{status}] {item.get('path')}")
        if len(ffmpeg_candidates) > 12:
            lines.append(f"- ... и еще {len(ffmpeg_candidates) - 12} путей")

    imports = diag.get("imports") or {}
    if imports:
        lines.append("Проверка Python-пакетов TTS:")
        for module_name in ("gtts", "pyttsx3", "edge_tts"):
            info = imports.get(module_name)
            if not info:
                lines.append(f"- {module_name}: данных нет")
                continue
            if info.get("available"):
                origin = info.get("origin") or info.get("spec_origin") or "origin неизвестен"
                lines.append(f"- {module_name}: найден ({origin})")
            else:
                lines.append(
                    f"- {module_name}: не найден ({_short_text(info.get('last_error') or 'без подробностей')})"
                )
                attempts = info.get("attempts") or []
                for attempt in attempts[:2]:
                    if attempt.get("ok"):
                        continue
                    lines.append(
                        f"  попытка {attempt.get('step')}: {_short_text(attempt.get('error') or 'ошибка не указана')}"
                    )

    paths = diag.get("paths") or {}
    added = paths.get("added") or []
    if added:
        lines.append("Добавленные пути поиска зависимостей:")
        for path in added[:10]:
            lines.append(f"- {path}")
        if len(added) > 10:
            lines.append(f"- ... и еще {len(added) - 10} путей")

    if TTS_IMPORT_ERRORS:
        lines.append("Предупреждения:")
        for warning in TTS_IMPORT_ERRORS:
            lines.append(f"- {warning}")

    sound_create_errors = runtime.get("sound_create_errors") or []
    if sound_create_errors:
        lines.append("Ошибки создания папки sound:")
        for item in sound_create_errors[:8]:
            lines.append(f"- {item}")

    return lines


def _dependency_diagnostics_payload() -> dict[str, Any]:
    return {
        "raw": _diag(),
        "lines": _dependency_diagnostics_lines(),
    }


def _synthesize_google(text: str, out_path: str) -> str:
    if gTTS is None:
        raise RuntimeError("Google TTS недоступен")
    tts = gTTS(text=text, lang="ru")
    tts.save(out_path)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= _MIN_AUDIO_BYTES:
        raise RuntimeError("Google TTS не сформировал аудиофайл")
    return out_path


def _synthesize_pyttsx3(text: str, voice_label: str, out_path: str) -> str:
    if pyttsx3 is None:
        raise RuntimeError("pyttsx3 недоступен")

    tts_engine = pyttsx3.init()
    voice_id = PYTTSX3_VOICE_MAP.get(voice_label)
    if voice_id:
        try:
            tts_engine.setProperty("voice", voice_id)
        except Exception:
            pass
    tts_engine.save_to_file(text, out_path)
    tts_engine.runAndWait()
    try:
        tts_engine.stop()
    except Exception:
        pass
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= _MIN_AUDIO_BYTES:
        raise RuntimeError("pyttsx3 не сформировал аудиофайл")
    return out_path


def _synthesize_edge(text: str, voice_label: str, out_path: str) -> str:
    voice_id = EDGE_TTS_VOICE_MAP.get(voice_label)
    if not voice_id:
        raise RuntimeError("Для Edge TTS не выбран голос")
    _run_async(synthesize_edge_tts(text=text, voice_id=voice_id, file_path=out_path))
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= _MIN_AUDIO_BYTES:
        raise RuntimeError("Edge TTS не сформировал аудиофайл")
    return out_path


def _synthesize_to_file(text: str, engine: str, voice: str, file_stem: Path) -> Path:
    if engine == "Google":
        out_path = str(file_stem.with_suffix(".mp3"))
        return Path(_synthesize_google(text=text, out_path=out_path))
    if engine == "pyx3":
        out_path = str(file_stem.with_suffix(".wav"))
        return Path(_synthesize_pyttsx3(text=text, voice_label=voice, out_path=out_path))
    if engine == "Edge TTS":
        out_path = str(file_stem.with_suffix(".mp3"))
        return Path(_synthesize_edge(text=text, voice_label=voice, out_path=out_path))
    raise RuntimeError("Неизвестный TTS-движок")


def _install_tts_dependencies() -> tuple[bool, str, list[str]]:
    if _is_compiled_runtime():
        return False, "Приложение запущено в режиме EXE/compiled; установка пакетов недоступна.", []

    commands = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", "gTTS", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
        ["pip", "install", "--upgrade", "--no-cache-dir", "gTTS", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
        ["python", "-m", "pip", "install", "--upgrade", "--no-cache-dir", "gTTS", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
    ]
    details: list[str] = []
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            details.append(f"$ {' '.join(cmd)} -> {proc.returncode}")
            if proc.stdout:
                details.append(proc.stdout.strip()[-1000:])
            if proc.stderr:
                details.append(proc.stderr.strip()[-1000:])
            if proc.returncode == 0:
                return True, "Установка зависимостей TTS завершена.", details
        except Exception as exc:
            details.append(f"$ {' '.join(cmd)} -> ошибка: {type(exc).__name__}: {exc}")
    return False, "Не удалось автоматически установить зависимости TTS.", details


class WinTTSView(BaseView):
    route_base = "/plugins/wintts"
    base_permissions = ["can_list"]

    def _render(self):
        template_source = _load_template()
        if not template_source:
            return "Шаблон расширения не найден.", 500

        return render_template_string(
            template_source,
            config_url=url_for(f"{self.__class__.__name__}.config"),
            synth_url=url_for(f"{self.__class__.__name__}.synthesize"),
            install_url=url_for(f"{self.__class__.__name__}.install"),
            static_url="/plugins/wintts/static",
            base_template=self.appbuilder.base_template,
            appbuilder=self.appbuilder,
            current_app=current_app,
        )

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        return self._render()

    @expose("/config", methods=["GET"])
    @has_access
    @permission_name("list")
    def config(self):
        try:
            init_tts_engines()
            _cleanup_generated_files()
            diagnostics = _dependency_diagnostics_payload()
            return jsonify(
                {
                    "ok": True,
                    "engines": ENGINE_OPTIONS,
                    "voices": VOICE_OPTIONS,
                    "warnings": TTS_IMPORT_ERRORS,
                    "ffmpeg_available": bool(_FFMPEG_PATH),
                    "ffmpeg_path": _FFMPEG_PATH or "",
                    "can_install": not _is_compiled_runtime(),
                    "max_text_len": _MAX_TEXT_LEN,
                    "diagnostics": diagnostics["raw"],
                    "diagnostics_lines": diagnostics["lines"],
                }
            )
        except Exception as exc:
            _LOGGER.exception("wintts config failed")
            try:
                diagnostics = _dependency_diagnostics_payload()
                lines = diagnostics.get("lines") or []
            except Exception:
                diagnostics = {"raw": {}, "lines": []}
                lines = []
            lines = list(lines)
            lines.append(f"Критическая ошибка инициализации: {type(exc).__name__}: {exc}")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Ошибка инициализации конфигурации: {type(exc).__name__}: {exc}",
                        "engines": ENGINE_OPTIONS,
                        "voices": VOICE_OPTIONS,
                        "warnings": TTS_IMPORT_ERRORS,
                        "ffmpeg_available": bool(_FFMPEG_PATH),
                        "ffmpeg_path": _FFMPEG_PATH or "",
                        "can_install": not _is_compiled_runtime(),
                        "max_text_len": _MAX_TEXT_LEN,
                        "diagnostics": diagnostics["raw"],
                        "diagnostics_lines": lines,
                    }
                ),
                500,
            )

    @expose("/install", methods=["POST"])
    @has_access
    @permission_name("list")
    def install(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        ok, message, details = _install_tts_dependencies()
        init_tts_engines(force=True)
        diagnostics = _dependency_diagnostics_payload()
        status = 200 if ok else 500
        payload = {
            "ok": ok,
            "message": message,
            "details": details[-20:],
            "engines": ENGINE_OPTIONS,
            "voices": VOICE_OPTIONS,
            "warnings": TTS_IMPORT_ERRORS,
            "diagnostics": diagnostics["raw"],
            "diagnostics_lines": diagnostics["lines"],
        }
        if not ok:
            payload["error"] = message
        return jsonify(payload), status

    @expose("/synthesize", methods=["POST"])
    @has_access
    @permission_name("list")
    def synthesize(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text") or request.form.get("text") or "").strip()
        engine = str(payload.get("engine") or request.form.get("engine") or "").strip()
        voice = str(payload.get("voice") or request.form.get("voice") or "").strip()

        if not text:
            return jsonify({"ok": False, "error": "Текст пустой."}), 400
        if len(text) > _MAX_TEXT_LEN:
            return jsonify({"ok": False, "error": f"Слишком длинный текст. Максимум {_MAX_TEXT_LEN} символов."}), 400

        init_tts_engines()
        if not engine:
            engine = ENGINE_OPTIONS[0] if ENGINE_OPTIONS else ""
        if engine not in ENGINE_OPTIONS:
            return jsonify({"ok": False, "error": "Выбранный движок недоступен."}), 400

        voices = VOICE_OPTIONS.get(engine) or []
        if not voice:
            voice = voices[0] if voices else ""
        if voices and voice not in voices:
            return jsonify({"ok": False, "error": "Выбранный голос недоступен для движка."}), 400

        _cleanup_generated_files()
        now = int(time.time())
        token = uuid.uuid4().hex[:12]
        file_stem = _output_root() / f"tts_{now}_{token}"

        try:
            out_path = _synthesize_to_file(text=text, engine=engine, voice=voice, file_stem=file_stem)
        except Exception as exc:
            _LOGGER.exception("wintts synthesis failed engine=%s voice=%s", engine, voice)
            diagnostics = _dependency_diagnostics_payload()
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Ошибка синтеза: {type(exc).__name__}: {exc}",
                        "warnings": TTS_IMPORT_ERRORS,
                        "diagnostics": diagnostics["raw"],
                        "diagnostics_lines": diagnostics["lines"],
                    }
                ),
                500,
            )

        if not out_path.exists() or out_path.stat().st_size <= _MIN_AUDIO_BYTES:
            return jsonify({"ok": False, "error": "Синтез завершился без аудиофайла."}), 500

        file_name = out_path.name
        audio_url = url_for(f"{self.__class__.__name__}.audio_file", file_name=file_name)
        download_url = url_for(f"{self.__class__.__name__}.download_file", file_name=file_name)

        user = getattr(current_user, "username", "web")
        try:
            _LOGGER.info(
                "wintts user=%s engine=%s voice=%s file=%s chars=%s",
                user,
                engine,
                voice,
                file_name,
                len(text),
            )
        except Exception:
            pass

        return jsonify(
            {
                "ok": True,
                "message": "Синтез завершен.",
                "engine": engine,
                "voice": voice,
                "filename": file_name,
                "audio_url": audio_url,
                "download_url": download_url,
                "size_bytes": out_path.stat().st_size,
                "warnings": TTS_IMPORT_ERRORS,
                "diagnostics_lines": _dependency_diagnostics_lines(),
            }
        )

    @expose("/audio/<file_name>", methods=["GET"])
    @has_access
    @permission_name("list")
    def audio_file(self, file_name: str):
        path = _resolve_audio_path(file_name)
        if not path:
            return abort(404)
        return send_file(
            path,
            mimetype=_audio_mimetype(path),
            as_attachment=False,
            conditional=True,
            max_age=0,
        )

    @expose("/download/<file_name>", methods=["GET"])
    @has_access
    @permission_name("list")
    def download_file(self, file_name: str):
        path = _resolve_audio_path(file_name)
        if not path:
            return abort(404)
        return send_file(
            path,
            mimetype=_audio_mimetype(path),
            as_attachment=True,
            download_name=path.name,
            conditional=True,
            max_age=0,
        )


def register(appbuilder, app, plugin):
    global _TEMPLATE_ROOT
    try:
        if plugin and getattr(plugin, "root", None):
            _TEMPLATE_ROOT = Path(plugin.root)
    except Exception:
        pass
    return WinTTSView

