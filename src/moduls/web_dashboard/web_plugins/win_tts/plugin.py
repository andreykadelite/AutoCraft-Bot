# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import audioop
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import importlib
import html as html_lib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import uuid
import wave
from pathlib import Path, PurePosixPath
from io import BytesIO
from typing import Any, Callable

from flask import abort, current_app, jsonify, render_template_string, request, send_file, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ...security import panel_has_access as has_access
from . import addon_runtime
from .dual_language_router import (
    build_dual_language_routing_plan,
    resolve_voice_language_code as resolve_dual_voice_language_code,
)
from .text_normalizer import (
    EdgeTextNormalizerSettings,
    analyze_edge_text_readability,
    edge_text_normalizer_config_payload,
    edge_text_normalizer_settings_payload,
    normalize_edge_text,
    parse_edge_text_normalizer_settings,
)

try:
    import pyttsx3  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pyttsx3 = None

try:
    from gtts import gTTS  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    gTTS = None

try:
    from gtts.lang import tts_langs as gtts_langs  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    gtts_langs = None

_LOGGER = logging.getLogger("panel.plugins")
_TEMPLATE_ROOT: Path | None = None

_DEFAULT_MAX_TEXT_LEN = 5000
_EDGE_UI_MAX_TEXT_LEN = 2_000_000
_ABSOLUTE_MAX_TEXT_LEN = _EDGE_UI_MAX_TEXT_LEN
_MIN_AUDIO_BYTES = 128
_MAX_AUDIO_AGE_SECONDS = 24 * 3600
_MAX_AUDIO_FILES = 300
_FILENAME_RE = re.compile(r"^tts_\d{10}_[a-f0-9]{12}\.(mp3|wav)$", re.IGNORECASE)
# Верхний предел для edge-tts: 4096 байт на текстовую часть запроса (по исходникам edge-tts).
_EDGE_CHUNK_LIMITS = (3900, 3000, 2200, 1600, 1100, 750)
_EDGE_REQUEST_TIMEOUT_SECONDS = 120
_EDGE_REQUEST_ATTEMPTS = 3
_EDGE_VOICE_LIST_TIMEOUT_SECONDS = 25
_EDGE_PARALLELISM_MIN = 1
_EDGE_PARALLELISM_DEFAULT = 4
_EDGE_PARALLELISM_MAX = 12
_EDGE_PARALLELISM_ENV = "AUTOCRAFT_EDGE_TTS_PARALLELISM"
_EDGE_HIGH_PARALLELISM_THRESHOLD = 8
_EDGE_START_SPACING_SECONDS = 0.09
_EDGE_START_SPACING_HIGH_SECONDS = 0.16
_EDGE_RETRY_BACKOFF_BASE_SECONDS = 0.45
_EDGE_RETRY_BACKOFF_MAX_SECONDS = 4.0
_EDGE_RATE_MIN = -100
_EDGE_RATE_MAX = 100
_EDGE_VOLUME_MIN = -100
_EDGE_VOLUME_MAX = 100
_EDGE_PITCH_MIN_HZ = -100
_EDGE_PITCH_MAX_HZ = 100
_EDGE_RATE_DEFAULT = "+0%"
_EDGE_VOLUME_DEFAULT = "+0%"
_EDGE_PITCH_DEFAULT = "+0Hz"
_EDGE_PERCENT_RE = re.compile(r"^[+-]\d+%$")
_EDGE_PITCH_RE = re.compile(r"^[+-]\d+Hz$")
_LANGUAGE_CODE_RE = re.compile(r"([a-z]{2,3})(?:[-_][a-z0-9]{2,8})?", re.IGNORECASE)
_DUAL_PAUSE_HARD_PUNCT_RE = re.compile(r"[.!?…]+[\"')\]\s]*$")
_DUAL_PAUSE_SOFT_PUNCT_RE = re.compile(r"[,;:]+[\"')\]\s]*$")
_DUAL_PAUSE_DASH_RE = re.compile(r"[-–—]+[\"')\]\s]*$")

_GOOGLE_DEFAULT_LANG = "ru"
_GOOGLE_DEFAULT_TLD = "com"
_GOOGLE_SLOW_THRESHOLD = -35
_GOOGLE_CHUNK_LIMITS = (8_000, 5_000, 3_400, 2_200, 1_200, 700)
_GOOGLE_REQUEST_CONNECT_TIMEOUT_SECONDS = 8.0
_GOOGLE_REQUEST_READ_TIMEOUT_SECONDS = 18.0
_GOOGLE_PARALLELISM_MIN = 1
_GOOGLE_PARALLELISM_DEFAULT = 4
_GOOGLE_PARALLELISM_MAX = 12
_GOOGLE_PARALLELISM_ENV = "AUTOCRAFT_GOOGLE_TTS_PARALLELISM"
_GOOGLE_SAFE_PARALLELISM_MEDIUM_THRESHOLD = 50_000
_GOOGLE_SAFE_PARALLELISM_LONG_THRESHOLD = 150_000
_GOOGLE_SAFE_PARALLELISM_MEDIUM = 2
_GOOGLE_SAFE_PARALLELISM_LONG = 1
_GOOGLE_RETRY_COUNT_MIN = 0
_GOOGLE_RETRY_COUNT_DEFAULT = 2
_GOOGLE_RETRY_COUNT_MAX = 8
_GOOGLE_RETRY_COUNT_ENV = "AUTOCRAFT_GOOGLE_TTS_RETRY_COUNT"
_GOOGLE_RETRY_BACKOFF_BASE_SECONDS = 0.65
_GOOGLE_RETRY_BACKOFF_MAX_SECONDS = 6.0
_GOOGLE_RETRY_BACKOFF_JITTER_SECONDS = 0.35
_GOOGLE_STAGE_COOLDOWN_SECONDS = 3.0
_GOOGLE_STAGE_COOLDOWN_TRANSIENT_SECONDS = 8.0
_GOOGLE_STAGE_COOLDOWN_RATE_LIMIT_SECONDS = 20.0
_PYTTSX3_RATE_MIN = 80
_PYTTSX3_RATE_MAX = 420
_PYTTSX3_RATE_DEFAULT = 200
_PYTTSX3_CHUNK_LIMITS = (14_000, 10_000, 7_000, 5_000, 3_500)

_UPLOAD_ACCEPT_EXTENSIONS = (".txt", ".fb2", ".epub", ".docx", ".md", ".html", ".htm")
_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
_HISTORY_INLINE_TEXT_LIMIT = 20_000

_DUAL_PAUSE_MODE_DEFAULT = "auto"
_DUAL_PAUSE_MODE_VALUES = ("auto", "manual", "off")
_DUAL_PAUSE_MS_MIN = 0
_DUAL_PAUSE_MS_MAX = 1_500
_DUAL_PAUSE_MS_DEFAULT = 90
_DUAL_PAUSE_AUTO_MIN_MS = 45
_DUAL_PAUSE_AUTO_MAX_MS = 320
_DUAL_PAUSE_AUTO_BASE_MS = 90
_DUAL_PAUSE_AUTO_SOFT_PUNCT_MS = 130
_DUAL_PAUSE_AUTO_HARD_PUNCT_MS = 200
_DUAL_PAUSE_AUTO_SWITCH_CAP_MS = 95
_DUAL_PAUSE_AUTO_SHORT_SEGMENT_MS = 65
_DUAL_PAUSE_TRIM_MAX_MS = 900
_DUAL_PAUSE_SILENCE_WINDOW_MS = 10
_DUAL_PAUSE_RMS_THRESHOLD_RATIO = 0.007


_EDGE_FALLBACK_VOICES = (
    "ru-RU-SvetlanaNeural",
    "ru-RU-DmitryNeural",
    "ru-RU-DariyaNeural",
)

_FFMPEG_PATH: str | None = None
_SYNTH_JOB_TTL_SECONDS = 3600
_SYNTH_JOB_DONE_TTL_SECONDS = 900
_SYNTH_JOB_MAX_LOGS = 240

# Опции синтеза речи и состояние TTS.
ENGINE_OPTIONS: list[str] = []
VOICE_OPTIONS: dict[str, list[str]] = {}
PYTTSX3_VOICE_MAP: dict[str, str] = {}
PYTTSX3_LANGUAGE_MAP: dict[str, list[str]] = {}
GOOGLE_TTS_VOICE_MAP: dict[str, dict[str, Any]] = {}
GOOGLE_TTS_LANGUAGE_MAP: dict[str, list[str]] = {}
EDGE_TTS_VOICE_MAP: dict[str, str] = {}
EDGE_TTS_VOICE_PRIORITY: list[str] = []
EDGE_TTS_LANGUAGE_MAP: dict[str, list[str]] = {}
EDGE_TTS_MODULE: Any = None
RHVOICE_TTS_VOICE_MAP: dict[str, str] = {}
RHVOICE_TTS_LANGUAGE_MAP: dict[str, list[str]] = {}
RHVOICE_ADDON_STATE: dict[str, Any] = {}
TTS_INIT_DONE = False
TTS_IMPORT_ERRORS: list[str] = []
_DEPENDENCY_DIAGNOSTICS: dict[str, Any] = {}
_ONEFILE_DIR_PREFIXES = ("onefile_", "onefil", "_mei")


@dataclass
class WinttsSynthesisTask:
    job_id: str
    user_id: int
    username: str
    engine: str
    voice: str
    primary_language: str
    edge_parallelism: int | None
    google_parallelism: int | None
    google_retry_count: int | None
    edge_rate: str | None
    edge_volume: str | None
    edge_pitch: str | None
    edge_text_normalizer: dict[str, Any] | None
    dual_language: dict[str, Any] | None
    text_length: int
    status: str = "queued"
    message: str = "Задача поставлена в очередь."
    percent: int = 0
    logs: list[dict[str, Any]] = field(default_factory=list)
    diagnostics_lines: list[str] = field(default_factory=list)
    done: bool = False
    error: str = ""
    filename: str = ""
    size_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started: bool = False


class WinttsSynthesisStore:
    def __init__(self, ttl_seconds: int = _SYNTH_JOB_TTL_SECONDS, done_ttl_seconds: int = _SYNTH_JOB_DONE_TTL_SECONDS) -> None:
        self._items: dict[str, WinttsSynthesisTask] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.done_ttl_seconds = done_ttl_seconds

    def create(
        self,
        user_id: int,
        username: str,
        engine: str,
        voice: str,
        primary_language: str,
        text_length: int,
        edge_parallelism: int | None,
        google_parallelism: int | None,
        google_retry_count: int | None,
        edge_rate: str | None,
        edge_volume: str | None,
        edge_pitch: str | None,
        edge_text_normalizer: dict[str, Any] | None,
        dual_language: dict[str, Any] | None,
    ) -> str:
        now = time.time()
        job_id = uuid.uuid4().hex
        item = WinttsSynthesisTask(
            job_id=job_id,
            user_id=user_id,
            username=username,
            engine=engine,
            voice=voice,
            primary_language=primary_language,
            edge_parallelism=edge_parallelism,
            google_parallelism=google_parallelism,
            google_retry_count=google_retry_count,
            edge_rate=edge_rate,
            edge_volume=edge_volume,
            edge_pitch=edge_pitch,
            edge_text_normalizer=dict(edge_text_normalizer) if isinstance(edge_text_normalizer, dict) else None,
            dual_language=dict(dual_language) if isinstance(dual_language, dict) else None,
            text_length=text_length,
            created_at=now,
            updated_at=now,
        )
        item.logs.append(
            {
                "ts": now,
                "percent": 0,
                "level": "info",
                "message": "Запрос принят. Задача поставлена в очередь.",
            }
        )
        with self._lock:
            self._cleanup_locked()
            self._items[job_id] = item
        return job_id

    def get(self, job_id: str) -> WinttsSynthesisTask | None:
        with self._lock:
            self._cleanup_locked()
            return self._items.get(job_id)

    def get_payload(self, job_id: str, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            self._cleanup_locked()
            item = self._items.get(job_id)
            if not item or item.user_id != user_id:
                return None
            return self._to_payload(item)

    def mark_started(self, job_id: str) -> bool:
        with self._lock:
            item = self._items.get(job_id)
            if not item or item.started:
                return False
            item.started = True
            item.status = "running"
            item.updated_at = time.time()
            return True

    def append_log(self, job_id: str, percent: int, message: str, level: str = "info", status: str | None = None) -> None:
        clean_message = str(message or "").strip()
        if not clean_message:
            return
        with self._lock:
            item = self._items.get(job_id)
            if not item or item.done:
                return
            normalized_percent = max(0, min(100, int(percent)))
            item.percent = max(item.percent, normalized_percent)
            item.message = clean_message
            if status:
                item.status = status
            item.updated_at = time.time()
            item.logs.append(
                {
                    "ts": item.updated_at,
                    "percent": item.percent,
                    "level": level,
                    "message": clean_message,
                }
            )
            if len(item.logs) > _SYNTH_JOB_MAX_LOGS:
                item.logs = item.logs[-_SYNTH_JOB_MAX_LOGS:]

    def finish(
        self,
        job_id: str,
        message: str,
        filename: str,
        size_bytes: int,
        diagnostics_lines: list[str],
        synthesis_result: dict[str, Any] | None = None,
    ) -> None:
        clean_message = str(message or "").strip() or "Синтез завершен."
        now = time.time()
        with self._lock:
            item = self._items.get(job_id)
            if not item:
                return
            item.done = True
            item.status = "done"
            item.message = clean_message
            item.percent = 100
            item.filename = filename
            item.size_bytes = max(0, int(size_bytes))
            item.error = ""
            item.diagnostics_lines = list(diagnostics_lines or [])
            if isinstance(synthesis_result, dict):
                item.primary_language = _normalize_language_code(
                    synthesis_result.get("primary_language"),
                    fallback=str(item.primary_language or "und"),
                )
                edge_parallelism_value = synthesis_result.get("edge_parallelism")
                if edge_parallelism_value is not None:
                    try:
                        item.edge_parallelism = int(edge_parallelism_value)
                    except Exception:
                        pass
                google_parallelism_value = synthesis_result.get("google_parallelism")
                if google_parallelism_value is not None:
                    try:
                        item.google_parallelism = int(google_parallelism_value)
                    except Exception:
                        pass
                google_retry_count_value = synthesis_result.get("google_retry_count")
                if google_retry_count_value is not None:
                    try:
                        item.google_retry_count = int(google_retry_count_value)
                    except Exception:
                        pass
                for field_name in ("edge_rate", "edge_volume", "edge_pitch"):
                    field_value = synthesis_result.get(field_name)
                    if field_value not in (None, ""):
                        setattr(item, field_name, str(field_value))
                if "edge_text_normalizer" in synthesis_result or "text_normalizer" in synthesis_result:
                    normalizer_payload = synthesis_result.get("text_normalizer")
                    if not isinstance(normalizer_payload, dict):
                        normalizer_payload = synthesis_result.get("edge_text_normalizer")
                    item.edge_text_normalizer = (
                        dict(normalizer_payload) if isinstance(normalizer_payload, dict) else None
                    )
                if "dual_language" in synthesis_result:
                    dual_payload = synthesis_result.get("dual_language")
                    item.dual_language = dict(dual_payload) if isinstance(dual_payload, dict) else None
            item.updated_at = now
            item.logs.append(
                {
                    "ts": now,
                    "percent": 100,
                    "level": "info",
                    "message": clean_message,
                }
            )
            if len(item.logs) > _SYNTH_JOB_MAX_LOGS:
                item.logs = item.logs[-_SYNTH_JOB_MAX_LOGS:]

    def fail(self, job_id: str, error: str, diagnostics_lines: list[str]) -> None:
        clean_error = str(error or "").strip() or "Синтез завершился ошибкой."
        now = time.time()
        with self._lock:
            item = self._items.get(job_id)
            if not item:
                return
            item.done = True
            item.status = "error"
            item.error = clean_error
            item.message = clean_error
            item.updated_at = now
            item.diagnostics_lines = list(diagnostics_lines or [])
            item.logs.append(
                {
                    "ts": now,
                    "percent": item.percent,
                    "level": "error",
                    "message": clean_error,
                }
            )
            if len(item.logs) > _SYNTH_JOB_MAX_LOGS:
                item.logs = item.logs[-_SYNTH_JOB_MAX_LOGS:]

    def _to_payload(self, item: WinttsSynthesisTask) -> dict[str, Any]:
        return {
            "job_id": item.job_id,
            "status": item.status,
            "message": item.message,
            "percent": item.percent,
            "engine": item.engine,
            "voice": item.voice,
            "primary_language": item.primary_language,
            "edge_language": item.primary_language,
            "edge_parallelism": item.edge_parallelism,
            "google_parallelism": item.google_parallelism,
            "google_retry_count": item.google_retry_count,
            "edge_rate": item.edge_rate,
            "edge_volume": item.edge_volume,
            "edge_pitch": item.edge_pitch,
            "edge_text_normalizer": dict(item.edge_text_normalizer) if isinstance(item.edge_text_normalizer, dict) else None,
            "text_normalizer": dict(item.edge_text_normalizer) if isinstance(item.edge_text_normalizer, dict) else None,
            "dual_language": dict(item.dual_language) if isinstance(item.dual_language, dict) else None,
            "text_length": item.text_length,
            "logs": [dict(entry) for entry in item.logs],
            "diagnostics_lines": list(item.diagnostics_lines or []),
            "done": item.done,
            "error": item.error,
            "filename": item.filename,
            "size_bytes": item.size_bytes,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired: list[str] = []
        for job_id, item in self._items.items():
            if item.done and now - item.updated_at > self.done_ttl_seconds:
                expired.append(job_id)
            elif not item.done and now - item.created_at > self.ttl_seconds:
                expired.append(job_id)
        for job_id in expired:
            self._items.pop(job_id, None)


_WINTTS_SYNTHESIS = WinttsSynthesisStore()
ProgressCallback = Callable[[int, str, str], None]


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
    if getattr(sys, "_MEIPASS", None):
        return True
    for env_name in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        if str(os.environ.get(env_name, "") or "").strip():
            return True
    try:
        exe = str(getattr(sys, "executable", "") or "")
        exe_l = exe.lower()
        name = Path(exe_l).name
        if exe_l.endswith(".exe") and name not in ("python.exe", "pythonw.exe", "py.exe"):
            return True
    except Exception:
        pass
    return False


def _can_install_tts_dependencies() -> bool:
    try:
        base_dir = _runtime_base_dir()
        state = addon_runtime.collect_addon_state(
            base_dir=str(base_dir),
            compiled_runtime=_is_compiled_runtime(),
        )
        return bool(state.get("can_install"))
    except Exception:
        return False


def _collect_addon_runtime_state() -> dict[str, Any]:
    global RHVOICE_ADDON_STATE
    try:
        base_dir = _runtime_base_dir()
        state = addon_runtime.collect_addon_state(
            base_dir=str(base_dir),
            compiled_runtime=_is_compiled_runtime(),
        )
    except Exception as exc:
        state = {
            "status": "error",
            "message": f"Ошибка определения состояния RHVoice-addon: {type(exc).__name__}: {exc}",
            "installed": False,
            "broken": False,
            "can_install": False,
            "addon_root": "",
            "venv_dir": "",
            "venv_python": "",
            "venv_pip": "",
            "venv_site_packages": "",
            "base_python": "",
            "path_candidates": [],
            "compiled_runtime": _is_compiled_runtime(),
        }
    RHVOICE_ADDON_STATE = dict(state or {})
    runtime_info = _diag().setdefault("runtime", {})
    runtime_info["rhvoice_addon"] = RHVOICE_ADDON_STATE
    return RHVOICE_ADDON_STATE


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


def _remove_file_safely(path: str | Path | None) -> None:
    if path is None:
        return
    try:
        target = Path(path)
    except Exception:
        return
    try:
        if target.exists() and target.is_file():
            target.unlink(missing_ok=True)
    except Exception:
        pass


def _cleanup_partial_output_files(file_stem: Path) -> None:
    base = file_stem.parent
    stem = file_stem.name
    patterns = (
        f"{stem}.mp3",
        f"{stem}.wav",
        f"{stem}.mp3.__part*.mp3",
        f"{stem}.mp3.__concat__.txt",
        f"{stem}.wav.__part*.wav",
        f"{stem}.wav.__concat__.txt",
    )
    for pattern in patterns:
        for candidate in base.glob(pattern):
            _remove_file_safely(candidate)


def _cleanup_generated_files() -> None:
    root = _output_root()
    now = time.time()
    files: list[Path] = []
    for item in root.iterdir():
        if not item.is_file():
            continue
        if not _FILENAME_RE.match(item.name):
            continue
        files.append(item)

    # Временные артефакты не отдаем пользователю; очищаем отдельно.
    temp_patterns = ("tts_*.__part*.mp3", "tts_*.__concat__.txt", "tts_*.__part*.wav")
    for pattern in temp_patterns:
        for temp_file in root.glob(pattern):
            try:
                age = now - temp_file.stat().st_mtime
            except Exception:
                age = _MAX_AUDIO_AGE_SECONDS + 1
            if age > 2 * 3600:
                _remove_file_safely(temp_file)

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

    files = [
        p
        for p in root.iterdir()
        if p.is_file() and _FILENAME_RE.match(p.name)
    ]
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


def _supported_import_extensions() -> list[str]:
    return list(_UPLOAD_ACCEPT_EXTENSIONS)


def _safe_source_name(value: str | None, fallback: str = "document") -> str:
    raw = str(value or "").strip().replace("\\", "/")
    name = Path(raw).name.strip()
    return name or fallback


def _human_size(num_bytes: int) -> str:
    size = float(max(0, int(num_bytes)))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024.0 or unit == "ГБ":
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(size)} ГБ"


def _read_uploaded_file(upload: Any, max_bytes: int = _UPLOAD_MAX_BYTES) -> bytes:
    stream = getattr(upload, "stream", upload)
    if hasattr(stream, "seek"):
        try:
            stream.seek(0)
        except Exception:
            pass

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"Файл слишком большой. Максимум {_human_size(max_bytes)}."
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    if hasattr(stream, "seek"):
        try:
            stream.seek(0)
        except Exception:
            pass
    return data


def _decode_text_bytes(data: bytes) -> str:
    if not data:
        return ""
    encodings = (
        "utf-8-sig",
        "utf-8",
        "cp1251",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
    )
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_imported_text(text: str) -> str:
    normalized = str(text or "").replace("\ufeff", "").replace("\xa0", " ")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    lines: list[str] = []
    empty_run = 0
    for raw_line in normalized.split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            empty_run += 1
            if empty_run <= 1:
                lines.append("")
            continue
        empty_run = 0
        lines.append(line)
    return "\n".join(lines).strip()


def _strip_html_markup(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|section|article|li|tr|h1|h2|h3|h4|h5|h6)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return _normalize_imported_text(value)


def _xml_local_name(tag: Any) -> str:
    value = str(tag or "")
    if "}" in value:
        return value.rsplit("}", 1)[-1]
    return value


def _resolve_posix_member(base_path: str, rel_path: str) -> str:
    rel_clean = str(rel_path or "").split("#", 1)[0].replace("\\", "/").strip()
    if not rel_clean:
        return ""
    if rel_clean.startswith("/"):
        return rel_clean.lstrip("/")
    base_parent = PurePosixPath(base_path).parent
    merged = (base_parent / rel_clean).as_posix()
    return str(PurePosixPath(merged))


def _extract_text_from_docx_bytes(data: bytes) -> tuple[str, str]:
    title = ""
    paragraphs: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        try:
            xml_bytes = archive.read("word/document.xml")
        except Exception as exc:
            raise ValueError(f"DOCX не содержит word/document.xml: {exc}") from exc

        try:
            import xml.etree.ElementTree as _ET

            root = _ET.fromstring(xml_bytes)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for paragraph in root.findall(".//w:p", namespace):
                parts: list[str] = []
                for node in paragraph.iter():
                    local = _xml_local_name(node.tag)
                    if local == "t" and node.text:
                        parts.append(node.text)
                    elif local == "tab":
                        parts.append("\t")
                    elif local in ("br", "cr"):
                        parts.append("\n")
                line = "".join(parts).strip()
                if line:
                    paragraphs.append(line)
        except Exception as exc:
            raise ValueError(f"Не удалось разобрать DOCX: {exc}") from exc

        try:
            core_xml = archive.read("docProps/core.xml")
            core_root = _ET.fromstring(core_xml)
            for node in core_root.iter():
                if _xml_local_name(node.tag) == "title" and (node.text or "").strip():
                    title = str(node.text).strip()
                    break
        except Exception:
            title = ""

    return _normalize_imported_text("\n".join(paragraphs)), title


def _extract_text_from_fb2_bytes(data: bytes) -> tuple[str, str]:
    title = ""
    chunks: list[str] = []
    try:
        import xml.etree.ElementTree as _ET

        root = _ET.fromstring(data)
    except Exception as exc:
        raise ValueError(f"Не удалось разобрать FB2: {exc}") from exc

    for node in root.iter():
        local = _xml_local_name(node.tag)
        if local == "book-title" and not title:
            raw = " ".join(str(part).strip() for part in node.itertext() if str(part).strip())
            title = re.sub(r"\s+", " ", raw).strip()
            continue
        if local == "body":
            for part in node.itertext():
                clean = re.sub(r"\s+", " ", str(part or "")).strip()
                if clean:
                    chunks.append(clean)

    return _normalize_imported_text("\n".join(chunks)), title


def _extract_text_from_epub_bytes(data: bytes) -> tuple[str, str, list[str]]:
    title = ""
    warnings: list[str] = []
    html_members: list[str] = []
    chunks: list[str] = []

    with zipfile.ZipFile(BytesIO(data)) as archive:
        members = archive.namelist()
        member_set = set(members)
        opf_path = ""

        try:
            if "META-INF/container.xml" in member_set:
                container_xml = archive.read("META-INF/container.xml")
                import xml.etree.ElementTree as _ET

                container_root = _ET.fromstring(container_xml)
                for node in container_root.iter():
                    if _xml_local_name(node.tag) == "rootfile":
                        opf_path = str(node.attrib.get("full-path") or "").strip()
                        if opf_path:
                            break
        except Exception as exc:
            warnings.append(f"Не удалось прочитать EPUB container.xml: {type(exc).__name__}: {exc}")

        if not opf_path:
            for member in members:
                if member.lower().endswith(".opf"):
                    opf_path = member
                    break

        manifest_by_id: dict[str, str] = {}
        if opf_path and opf_path in member_set:
            try:
                import xml.etree.ElementTree as _ET

                opf_root = _ET.fromstring(archive.read(opf_path))
                spine_ids: list[str] = []
                for node in opf_root.iter():
                    local = _xml_local_name(node.tag)
                    if local == "title" and not title and (node.text or "").strip():
                        title = str(node.text).strip()
                    elif local == "item":
                        item_id = str(node.attrib.get("id") or "").strip()
                        href = str(node.attrib.get("href") or "").strip()
                        if item_id and href:
                            manifest_by_id[item_id] = _resolve_posix_member(opf_path, href)
                    elif local == "itemref":
                        idref = str(node.attrib.get("idref") or "").strip()
                        if idref:
                            spine_ids.append(idref)

                for item_id in spine_ids:
                    member = manifest_by_id.get(item_id) or ""
                    if member and member in member_set:
                        html_members.append(member)
            except Exception as exc:
                warnings.append(f"Не удалось разобрать EPUB metadata/spine: {type(exc).__name__}: {exc}")

        if not html_members:
            fallback_members = [
                member for member in members
                if member.lower().endswith((".xhtml", ".html", ".htm"))
                and not member.lower().endswith(("toc.xhtml", "nav.xhtml"))
            ]
            html_members.extend(sorted(fallback_members))
            if html_members:
                warnings.append("Использован резервный порядок EPUB-файлов, потому что spine не найден.")

        seen_members: set[str] = set()
        for member in html_members:
            if member in seen_members or member not in member_set:
                continue
            seen_members.add(member)
            try:
                raw_html = _decode_text_bytes(archive.read(member))
            except Exception as exc:
                warnings.append(f"Не удалось прочитать EPUB-раздел {member}: {type(exc).__name__}: {exc}")
                continue
            clean = _strip_html_markup(raw_html)
            if clean:
                chunks.append(clean)

    return _normalize_imported_text("\n\n".join(chunks)), title, warnings


def _extract_text_from_uploaded_file(upload: Any) -> dict[str, Any]:
    file_name = _safe_source_name(getattr(upload, "filename", None), fallback="document")
    suffix = Path(file_name).suffix.lower()

    if suffix not in _UPLOAD_ACCEPT_EXTENSIONS:
        supported = ", ".join(_UPLOAD_ACCEPT_EXTENSIONS)
        raise ValueError(f"Неподдерживаемый формат файла: {suffix or 'без расширения'}. Поддерживаются: {supported}.")

    data = _read_uploaded_file(upload)
    if not data:
        raise ValueError("Файл пустой.")

    warnings: list[str] = []
    title = ""
    if suffix in (".txt", ".md"):
        text = _decode_text_bytes(data)
    elif suffix in (".html", ".htm"):
        text = _strip_html_markup(_decode_text_bytes(data))
    elif suffix == ".fb2":
        text, title = _extract_text_from_fb2_bytes(data)
    elif suffix == ".docx":
        text, title = _extract_text_from_docx_bytes(data)
    elif suffix == ".epub":
        text, title, warnings = _extract_text_from_epub_bytes(data)
    else:
        text = _decode_text_bytes(data)

    normalized = _normalize_imported_text(text)
    if not normalized:
        raise ValueError("Из файла не удалось извлечь текст.")

    char_count = len(normalized)
    if char_count > _ABSOLUTE_MAX_TEXT_LEN:
        raise ValueError(
            f"Извлечённый текст слишком большой: {char_count} символов. Лимит интерфейса {_ABSOLUTE_MAX_TEXT_LEN} символов."
        )

    if not title:
        title = Path(file_name).stem

    return {
        "text": normalized,
        "source_name": file_name,
        "source_ext": suffix,
        "title": title,
        "char_count": char_count,
        "size_bytes": len(data),
        "warnings": [line for line in warnings if str(line).strip()],
    }


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


def _get_engine_text_limit(engine: str) -> int | None:
    engine_name = str(engine or "").strip().lower()
    if engine_name == "edge tts":
        return None
    if engine_name in {"google", "pyx3", "rhvoice"}:
        return _ABSOLUTE_MAX_TEXT_LEN
    return _DEFAULT_MAX_TEXT_LEN


def _get_ui_max_text_len() -> int:
    return _EDGE_UI_MAX_TEXT_LEN


def _get_text_limits_payload() -> dict[str, int | None]:
    payload: dict[str, int | None] = {
        "Google": _ABSOLUTE_MAX_TEXT_LEN,
        "pyx3": _ABSOLUTE_MAX_TEXT_LEN,
        "Edge TTS": None,
        "RHVoice": _ABSOLUTE_MAX_TEXT_LEN,
    }
    for engine_name in ENGINE_OPTIONS:
        payload.setdefault(engine_name, _get_engine_text_limit(engine_name))
    return payload


def _normalize_edge_parallelism(value: Any | None = None) -> int:
    raw = value
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(_EDGE_PARALLELISM_ENV, "")

    if raw is None or str(raw).strip() == "":
        parsed = _EDGE_PARALLELISM_DEFAULT
    else:
        try:
            parsed = int(str(raw).strip())
        except Exception:
            parsed = _EDGE_PARALLELISM_DEFAULT

    if parsed < _EDGE_PARALLELISM_MIN:
        return _EDGE_PARALLELISM_MIN
    if parsed > _EDGE_PARALLELISM_MAX:
        return _EDGE_PARALLELISM_MAX
    return parsed


def _get_edge_parallelism() -> int:
    return _normalize_edge_parallelism()


def _get_edge_parallelism_payload() -> dict[str, int]:
    current = _get_edge_parallelism()
    return {
        "min": _EDGE_PARALLELISM_MIN,
        "max": _EDGE_PARALLELISM_MAX,
        "default": _EDGE_PARALLELISM_DEFAULT,
        "current": current,
    }


def _normalize_google_parallelism(value: Any | None = None) -> int:
    raw = value
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(_GOOGLE_PARALLELISM_ENV, "")

    if raw is None or str(raw).strip() == "":
        parsed = _GOOGLE_PARALLELISM_DEFAULT
    else:
        try:
            parsed = int(str(raw).strip())
        except Exception:
            parsed = _GOOGLE_PARALLELISM_DEFAULT

    if parsed < _GOOGLE_PARALLELISM_MIN:
        return _GOOGLE_PARALLELISM_MIN
    if parsed > _GOOGLE_PARALLELISM_MAX:
        return _GOOGLE_PARALLELISM_MAX
    return parsed


def _get_google_parallelism() -> int:
    return _normalize_google_parallelism()


def _get_google_parallelism_payload() -> dict[str, int]:
    current = _get_google_parallelism()
    return {
        "min": _GOOGLE_PARALLELISM_MIN,
        "max": _GOOGLE_PARALLELISM_MAX,
        "default": _GOOGLE_PARALLELISM_DEFAULT,
        "current": current,
    }


def _normalize_google_retry_count(value: Any | None = None) -> int:
    raw = value
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(_GOOGLE_RETRY_COUNT_ENV, "")

    if raw is None or str(raw).strip() == "":
        parsed = _GOOGLE_RETRY_COUNT_DEFAULT
    else:
        try:
            parsed = int(str(raw).strip())
        except Exception:
            parsed = _GOOGLE_RETRY_COUNT_DEFAULT

    if parsed < _GOOGLE_RETRY_COUNT_MIN:
        return _GOOGLE_RETRY_COUNT_MIN
    if parsed > _GOOGLE_RETRY_COUNT_MAX:
        return _GOOGLE_RETRY_COUNT_MAX
    return parsed


def _get_google_retry_count() -> int:
    return _normalize_google_retry_count()


def _get_google_retry_count_payload() -> dict[str, int]:
    current = _get_google_retry_count()
    return {
        "min": _GOOGLE_RETRY_COUNT_MIN,
        "max": _GOOGLE_RETRY_COUNT_MAX,
        "default": _GOOGLE_RETRY_COUNT_DEFAULT,
        "current": current,
    }


def _normalize_signed_int(value: Any | None, min_value: int, max_value: int, default_value: int) -> int:
    if value is None:
        return default_value
    text = str(value).strip()
    if not text:
        return default_value
    text = text.replace("%", "").replace("Hz", "").replace("hz", "").strip()
    try:
        parsed = int(float(text))
    except Exception:
        return default_value
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def _as_dict_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _engine_language_catalog(engine: str) -> dict[str, list[str]]:
    engine_name = str(engine or "").strip()
    if engine_name == "Google":
        return GOOGLE_TTS_LANGUAGE_MAP or {}
    if engine_name == "pyx3":
        return PYTTSX3_LANGUAGE_MAP or {}
    if engine_name == "Edge TTS":
        return EDGE_TTS_LANGUAGE_MAP or {}
    if engine_name == "RHVoice":
        return RHVOICE_TTS_LANGUAGE_MAP or {}
    return {}


def _voice_language_for_engine(engine: str, voice: str, fallback: str = "und") -> str:
    catalog = _engine_language_catalog(engine)
    resolved = resolve_dual_voice_language_code(voice, catalog, fallback=fallback)
    return _normalize_language_code(resolved, fallback=fallback)


def _first_voice_for_language(engine: str, language_code: str, fallback_voice: str = "") -> str:
    code = _normalize_language_code(language_code, fallback="")
    if not code:
        return str(fallback_voice or "").strip()
    catalog = _engine_language_catalog(engine)
    candidates = catalog.get(code) or []
    for candidate in candidates:
        label = str(candidate or "").strip()
        if label:
            return label
    return str(fallback_voice or "").strip()


def _normalize_edge_rate(value: Any | None) -> str:
    if isinstance(value, str) and _EDGE_PERCENT_RE.match(value.strip()):
        numeric = _normalize_signed_int(value.strip()[:-1], _EDGE_RATE_MIN, _EDGE_RATE_MAX, 0)
        return f"{numeric:+d}%"
    numeric = _normalize_signed_int(value, _EDGE_RATE_MIN, _EDGE_RATE_MAX, 0)
    return f"{numeric:+d}%"


def _normalize_edge_volume(value: Any | None) -> str:
    if isinstance(value, str) and _EDGE_PERCENT_RE.match(value.strip()):
        numeric = _normalize_signed_int(value.strip()[:-1], _EDGE_VOLUME_MIN, _EDGE_VOLUME_MAX, 0)
        return f"{numeric:+d}%"
    numeric = _normalize_signed_int(value, _EDGE_VOLUME_MIN, _EDGE_VOLUME_MAX, 0)
    return f"{numeric:+d}%"


def _normalize_edge_pitch(value: Any | None) -> str:
    if isinstance(value, str) and _EDGE_PITCH_RE.match(value.strip()):
        numeric = _normalize_signed_int(value.strip()[:-2], _EDGE_PITCH_MIN_HZ, _EDGE_PITCH_MAX_HZ, 0)
        return f"{numeric:+d}Hz"
    numeric = _normalize_signed_int(value, _EDGE_PITCH_MIN_HZ, _EDGE_PITCH_MAX_HZ, 0)
    return f"{numeric:+d}Hz"


def _edge_option_to_int(value: Any | None, kind: str = "rate") -> int:
    option = str(kind or "rate").strip().lower()
    if option == "pitch":
        return _normalize_signed_int(value, _EDGE_PITCH_MIN_HZ, _EDGE_PITCH_MAX_HZ, 0)
    if option == "volume":
        return _normalize_signed_int(value, _EDGE_VOLUME_MIN, _EDGE_VOLUME_MAX, 0)
    return _normalize_signed_int(value, _EDGE_RATE_MIN, _EDGE_RATE_MAX, 0)


def _normalize_dual_pause_mode(value: Any | None, default: str = _DUAL_PAUSE_MODE_DEFAULT) -> str:
    fallback = str(default or _DUAL_PAUSE_MODE_DEFAULT).strip().lower()
    if fallback not in _DUAL_PAUSE_MODE_VALUES:
        fallback = _DUAL_PAUSE_MODE_DEFAULT
    text = str(value or "").strip().lower()
    aliases = {
        "auto": "auto",
        "automatic": "auto",
        "smart": "auto",
        "manual": "manual",
        "custom": "manual",
        "fixed": "manual",
        "off": "off",
        "disabled": "off",
        "none": "off",
        "0": "off",
        "1": "manual",
    }
    if not text:
        return fallback
    normalized = aliases.get(text, text)
    if normalized not in _DUAL_PAUSE_MODE_VALUES:
        return fallback
    return normalized


def _normalize_dual_pause_ms(value: Any | None, default_value: int = _DUAL_PAUSE_MS_DEFAULT) -> int:
    normalized_default = _normalize_signed_int(
        default_value,
        _DUAL_PAUSE_MS_MIN,
        _DUAL_PAUSE_MS_MAX,
        _DUAL_PAUSE_MS_DEFAULT,
    )
    return _normalize_signed_int(value, _DUAL_PAUSE_MS_MIN, _DUAL_PAUSE_MS_MAX, normalized_default)


def _get_dual_pause_payload() -> dict[str, Any]:
    return {
        "available": True,
        "default_mode": _DUAL_PAUSE_MODE_DEFAULT,
        "modes": [
            {"id": "auto", "label": "Авто", "description": "Умная пауза по пунктуации и смене языка."},
            {"id": "manual", "label": "Ручная", "description": "Фиксированная пауза в миллисекундах."},
            {"id": "off", "label": "Без нормализации", "description": "Склейка без дополнительной обработки пауз."},
        ],
        "ms": {
            "min": _DUAL_PAUSE_MS_MIN,
            "max": _DUAL_PAUSE_MS_MAX,
            "default": _DUAL_PAUSE_MS_DEFAULT,
        },
        "auto": {
            "min": _DUAL_PAUSE_AUTO_MIN_MS,
            "max": _DUAL_PAUSE_AUTO_MAX_MS,
            "base": _DUAL_PAUSE_AUTO_BASE_MS,
            "hint": "Авто-режим уменьшает лишние паузы на стыках языков и учитывает пунктуацию.",
        },
    }


def _get_edge_options_payload() -> dict[str, dict[str, int | str]]:
    return {
        "rate": {
            "min": _EDGE_RATE_MIN,
            "max": _EDGE_RATE_MAX,
            "default": _EDGE_RATE_DEFAULT,
            "unit": "%",
        },
        "volume": {
            "min": _EDGE_VOLUME_MIN,
            "max": _EDGE_VOLUME_MAX,
            "default": _EDGE_VOLUME_DEFAULT,
            "unit": "%",
        },
        "pitch": {
            "min": _EDGE_PITCH_MIN_HZ,
            "max": _EDGE_PITCH_MAX_HZ,
            "default": _EDGE_PITCH_DEFAULT,
            "unit": "Hz",
        },
    }


def _get_edge_voice_catalog_payload() -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for language in sorted((EDGE_TTS_LANGUAGE_MAP or {}).keys(), key=_edge_language_sort_key):
        voices: list[str] = []
        seen: set[str] = set()
        for item in EDGE_TTS_LANGUAGE_MAP.get(language) or []:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            voices.append(label)
        if voices:
            payload[language] = voices
    return payload


def _get_google_voice_catalog_payload() -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for language in sorted((GOOGLE_TTS_LANGUAGE_MAP or {}).keys(), key=_edge_language_sort_key):
        voices: list[str] = []
        seen: set[str] = set()
        for item in GOOGLE_TTS_LANGUAGE_MAP.get(language) or []:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            voices.append(label)
        if voices:
            payload[language] = voices
    return payload


def _get_pyttsx3_voice_catalog_payload() -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for language in sorted((PYTTSX3_LANGUAGE_MAP or {}).keys(), key=_edge_language_sort_key):
        voices: list[str] = []
        seen: set[str] = set()
        for item in PYTTSX3_LANGUAGE_MAP.get(language) or []:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            voices.append(label)
        if voices:
            payload[language] = voices
    return payload


def _get_rhvoice_voice_catalog_payload() -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for language in sorted((RHVOICE_TTS_LANGUAGE_MAP or {}).keys(), key=_edge_language_sort_key):
        voices: list[str] = []
        seen: set[str] = set()
        for item in RHVOICE_TTS_LANGUAGE_MAP.get(language) or []:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            voices.append(label)
        if voices:
            payload[language] = voices
    return payload


def _get_voice_catalog_payload() -> dict[str, dict[str, list[str]]]:
    return {
        "Google": _get_google_voice_catalog_payload(),
        "pyx3": _get_pyttsx3_voice_catalog_payload(),
        "Edge TTS": _get_edge_voice_catalog_payload(),
        "RHVoice": _get_rhvoice_voice_catalog_payload(),
    }


def _get_engine_options_payload() -> dict[str, dict[str, Any]]:
    return {
        "Google": {
            "show_language": True,
            "show_parallelism": True,
            "show_retry_count": True,
            "show_text_normalizer": True,
            "hint": (
                "Google TTS поддерживает выбор языка и базовую скорость "
                "(обычная или замедленная). Длинный текст автоматически режется на части и склеивается обратно. "
                "Тон и громкость сервис напрямую не меняет. "
                "Перед синтезом доступен общий нормализатор текста и анализатор символов."
            ),
            "rate": {
                "enabled": True,
                "min": -100,
                "max": 0,
                "default": 0,
                "unit": "%",
            },
            "pitch": {
                "enabled": False,
                "min": 0,
                "max": 0,
                "default": 0,
                "unit": "Hz",
            },
            "volume": {
                "enabled": False,
                "min": 0,
                "max": 0,
                "default": 0,
                "unit": "%",
            },
        },
        "pyx3": {
            "show_language": True,
            "show_parallelism": False,
            "show_retry_count": False,
            "show_text_normalizer": True,
            "hint": (
                "pyttsx3 использует локальные Windows-голоса. "
                "Поддерживаются скорость и громкость, тон зависит от драйвера и обычно недоступен. "
                "Длинный текст автоматически режется на части с последующей склейкой WAV. "
                "Перед синтезом доступен общий нормализатор текста и анализатор символов."
            ),
            "rate": {
                "enabled": True,
                "min": _EDGE_RATE_MIN,
                "max": _EDGE_RATE_MAX,
                "default": 0,
                "unit": "%",
            },
            "pitch": {
                "enabled": False,
                "min": 0,
                "max": 0,
                "default": 0,
                "unit": "Hz",
            },
            "volume": {
                "enabled": True,
                "min": _EDGE_VOLUME_MIN,
                "max": _EDGE_VOLUME_MAX,
                "default": 0,
                "unit": "%",
            },
        },
        "Edge TTS": {
            "show_language": True,
            "show_parallelism": True,
            "show_retry_count": False,
            "show_text_normalizer": True,
            "hint": (
                "Edge TTS поддерживает выбор языка, голоса и полные параметры тембра. "
                "Для длинных текстов это наиболее стабильный режим в текущей реализации, "
                "дополнительно доступен общий нормализатор текста и анализатор символов."
            ),
            "rate": {
                "enabled": True,
                "min": _EDGE_RATE_MIN,
                "max": _EDGE_RATE_MAX,
                "default": _edge_option_to_int(_EDGE_RATE_DEFAULT, "rate"),
                "unit": "%",
            },
            "pitch": {
                "enabled": True,
                "min": _EDGE_PITCH_MIN_HZ,
                "max": _EDGE_PITCH_MAX_HZ,
                "default": _edge_option_to_int(_EDGE_PITCH_DEFAULT, "pitch"),
                "unit": "Hz",
            },
            "volume": {
                "enabled": True,
                "min": _EDGE_VOLUME_MIN,
                "max": _EDGE_VOLUME_MAX,
                "default": _edge_option_to_int(_EDGE_VOLUME_DEFAULT, "volume"),
                "unit": "%",
            },
        },
        "RHVoice": {
            "show_language": True,
            "show_parallelism": False,
            "show_retry_count": False,
            "show_text_normalizer": True,
            "hint": (
                "RHVoice работает через изолированное addon-окружение и использует локальные голоса. "
                "Поддерживаются язык, голос и параметры скорости/тона/громкости. "
                "Перед синтезом доступен общий нормализатор текста и анализатор символов."
            ),
            "rate": {
                "enabled": True,
                "min": _EDGE_RATE_MIN,
                "max": _EDGE_RATE_MAX,
                "default": 0,
                "unit": "%",
            },
            "pitch": {
                "enabled": True,
                "min": _EDGE_PITCH_MIN_HZ,
                "max": _EDGE_PITCH_MAX_HZ,
                "default": 0,
                "unit": "Hz",
            },
            "volume": {
                "enabled": True,
                "min": _EDGE_VOLUME_MIN,
                "max": _EDGE_VOLUME_MAX,
                "default": 0,
                "unit": "%",
            },
        },
    }


def _get_edge_text_normalizer_payload() -> dict[str, Any]:
    try:
        return edge_text_normalizer_config_payload()
    except Exception:
        default_payload = edge_text_normalizer_settings_payload(EdgeTextNormalizerSettings())
        return {
            "available": True,
            "default": default_payload,
            "profiles": {
                "soft": dict(default_payload, preset="soft"),
                "balanced": dict(default_payload, preset="balanced"),
                "aggressive": dict(default_payload, preset="aggressive"),
            },
            "presets": [
                {"id": "soft", "label": "Мягкий", "description": ""},
                {"id": "balanced", "label": "Сбалансированный", "description": ""},
                {"id": "aggressive", "label": "Агрессивный", "description": ""},
            ],
            "auto_tune": {
                "enabled": True,
                "balanced_threshold": 12_000,
                "aggressive_threshold": 60_000,
                "hint": "Автонастройка недоступна из-за ошибки конфигурации.",
            },
        }


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


def _concat_binary_parts(parts: list[str], out_path: str) -> bool:
    if not parts:
        return False
    try:
        with open(out_path, "wb") as out_handle:
            for part in parts:
                with open(part, "rb") as in_handle:
                    shutil.copyfileobj(in_handle, out_handle, length=1024 * 1024)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES
    except Exception:
        _remove_file_safely(out_path)
        return False


def _ffmpeg_concat_wav(parts: list[str], out_path: str) -> bool:
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
            "-c:a",
            "pcm_s16le",
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


def _concat_wav_parts(parts: list[str], out_path: str) -> bool:
    if not parts:
        return False
    if len(parts) == 1:
        try:
            if os.path.abspath(parts[0]) != os.path.abspath(out_path):
                shutil.copyfile(parts[0], out_path)
            return True
        except Exception:
            return False

    try:
        with wave.open(parts[0], "rb") as first_handle:
            nchannels = first_handle.getnchannels()
            sampwidth = first_handle.getsampwidth()
            framerate = first_handle.getframerate()
            comptype = first_handle.getcomptype()
            compname = first_handle.getcompname()

            with wave.open(out_path, "wb") as out_handle:
                out_handle.setparams(first_handle.getparams())
                out_handle.writeframes(first_handle.readframes(first_handle.getnframes()))
                for part in parts[1:]:
                    with wave.open(part, "rb") as in_handle:
                        if (
                            in_handle.getnchannels() != nchannels
                            or in_handle.getsampwidth() != sampwidth
                            or in_handle.getframerate() != framerate
                            or in_handle.getcomptype() != comptype
                            or in_handle.getcompname() != compname
                        ):
                            raise RuntimeError("WAV параметры частей не совпадают")
                        out_handle.writeframes(in_handle.readframes(in_handle.getnframes()))
        if os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES:
            return True
    except Exception:
        _remove_file_safely(out_path)

    return _ffmpeg_concat_wav(parts, out_path)


def _wav_rms_threshold(sampwidth: int) -> int:
    width = max(1, min(4, int(sampwidth or 2)))
    max_amplitudes = {
        1: 127,
        2: 32767,
        3: 8388607,
        4: 2147483647,
    }
    peak = max_amplitudes.get(width, 32767)
    return max(24, int(peak * _DUAL_PAUSE_RMS_THRESHOLD_RATIO))


def _wav_silence_bytes(nchannels: int, sampwidth: int, frames: int) -> bytes:
    frame_count = max(0, int(frames or 0))
    if frame_count <= 0:
        return b""
    channels = max(1, int(nchannels or 1))
    width = max(1, int(sampwidth or 2))
    if width == 1:
        one_frame = b"\x80" * channels
    else:
        one_frame = b"\x00" * (channels * width)
    return one_frame * frame_count


def _count_wav_boundary_silence_frames(
    pcm_data: bytes,
    nchannels: int,
    sampwidth: int,
    framerate: int,
) -> tuple[int, int]:
    frame_size = max(1, int(nchannels or 1) * max(1, int(sampwidth or 2)))
    total_frames = len(pcm_data) // frame_size
    if total_frames <= 0:
        return 0, 0

    rate = max(1, int(framerate or 24_000))
    window_frames = max(1, int(round(rate * (_DUAL_PAUSE_SILENCE_WINDOW_MS / 1000.0))))
    max_scan_frames = min(total_frames, int(round(rate * (_DUAL_PAUSE_TRIM_MAX_MS / 1000.0))))
    threshold = _wav_rms_threshold(sampwidth)

    def _chunk_rms(offset_frames: int, frames_count: int) -> int:
        start = offset_frames * frame_size
        end = start + max(0, frames_count) * frame_size
        if end <= start:
            return 0
        chunk = pcm_data[start:end]
        if not chunk:
            return 0
        try:
            return int(audioop.rms(chunk, max(1, int(sampwidth or 2))))
        except Exception:
            return 0

    leading = 0
    while leading < max_scan_frames:
        step = min(window_frames, max_scan_frames - leading, total_frames - leading)
        if step <= 0:
            break
        if _chunk_rms(leading, step) > threshold:
            break
        leading += step

    trailing = 0
    while trailing < max_scan_frames:
        step = min(window_frames, max_scan_frames - trailing, total_frames - trailing)
        if step <= 0:
            break
        start_frame = total_frames - trailing - step
        if start_frame < 0:
            break
        if _chunk_rms(start_frame, step) > threshold:
            break
        trailing += step

    return max(0, leading), max(0, trailing)


def _trim_wav_boundary_silence(
    pcm_data: bytes,
    nchannels: int,
    sampwidth: int,
    framerate: int,
    trim_leading: bool,
    trim_trailing: bool,
) -> tuple[bytes, int, int]:
    frame_size = max(1, int(nchannels or 1) * max(1, int(sampwidth or 2)))
    total_frames = len(pcm_data) // frame_size
    if total_frames <= 0:
        return b"", 0, 0

    leading_detected, trailing_detected = _count_wav_boundary_silence_frames(
        pcm_data=pcm_data,
        nchannels=nchannels,
        sampwidth=sampwidth,
        framerate=framerate,
    )
    leading_trim = leading_detected if trim_leading else 0
    trailing_trim = trailing_detected if trim_trailing else 0

    if leading_trim + trailing_trim >= total_frames:
        keep_frames = max(1, total_frames // 4)
        if leading_trim >= total_frames:
            leading_trim = max(0, total_frames - keep_frames)
            trailing_trim = 0
        elif trailing_trim >= total_frames:
            trailing_trim = max(0, total_frames - keep_frames)
            leading_trim = 0
        else:
            overflow = (leading_trim + trailing_trim) - (total_frames - keep_frames)
            if overflow > 0:
                reduce_leading = min(overflow // 2, leading_trim)
                reduce_trailing = min(overflow - reduce_leading, trailing_trim)
                leading_trim -= reduce_leading
                trailing_trim -= reduce_trailing
                rest = (leading_trim + trailing_trim) - (total_frames - keep_frames)
                if rest > 0:
                    trailing_trim = max(0, trailing_trim - rest)

    start = max(0, leading_trim) * frame_size
    end = (total_frames - max(0, trailing_trim)) * frame_size
    if end <= start:
        end = min(len(pcm_data), start + frame_size)
    return pcm_data[start:end], max(0, leading_trim), max(0, trailing_trim)


def _estimate_auto_dual_pause_ms(left_segment: dict[str, Any], right_segment: dict[str, Any]) -> int:
    left_text = str(left_segment.get("text") or "").rstrip()
    right_text = str(right_segment.get("text") or "").lstrip()
    left_role = str(left_segment.get("role") or "primary").strip().lower()
    right_role = str(right_segment.get("role") or "primary").strip().lower()
    left_lang = _normalize_language_code(left_segment.get("language"), fallback="und")
    right_lang = _normalize_language_code(right_segment.get("language"), fallback="und")

    pause_ms = _DUAL_PAUSE_AUTO_BASE_MS
    if _DUAL_PAUSE_HARD_PUNCT_RE.search(left_text):
        pause_ms = _DUAL_PAUSE_AUTO_HARD_PUNCT_MS
    elif _DUAL_PAUSE_SOFT_PUNCT_RE.search(left_text):
        pause_ms = _DUAL_PAUSE_AUTO_SOFT_PUNCT_MS
    elif _DUAL_PAUSE_DASH_RE.search(left_text):
        pause_ms = max(pause_ms, 110)

    language_or_voice_switch = (left_role != right_role) or (left_lang != right_lang)
    if language_or_voice_switch:
        pause_ms = min(pause_ms, _DUAL_PAUSE_AUTO_SWITCH_CAP_MS)

    left_compact = re.sub(r"\W+", "", left_text, flags=re.UNICODE)
    right_compact = re.sub(r"\W+", "", right_text, flags=re.UNICODE)
    if len(left_compact) <= 3 or len(right_compact) <= 3:
        pause_ms = min(pause_ms, _DUAL_PAUSE_AUTO_SHORT_SEGMENT_MS)

    return max(_DUAL_PAUSE_AUTO_MIN_MS, min(_DUAL_PAUSE_AUTO_MAX_MS, int(pause_ms)))


def _build_dual_pause_targets(
    rendered_segments: list[dict[str, Any]],
    pause_mode: str,
    pause_ms: int,
) -> list[int]:
    if len(rendered_segments) <= 1:
        return []
    if pause_mode == "manual":
        return [_normalize_dual_pause_ms(pause_ms)] * (len(rendered_segments) - 1)
    if pause_mode != "auto":
        return []

    targets: list[int] = []
    for index in range(len(rendered_segments) - 1):
        left = rendered_segments[index]
        right = rendered_segments[index + 1]
        targets.append(_estimate_auto_dual_pause_ms(left, right))
    return targets


def _merge_wav_parts_with_pause(
    parts: list[str],
    out_path: str,
    target_pauses_ms: list[int],
) -> tuple[bool, dict[str, Any]]:
    if not parts:
        return False, {"reason": "no_parts"}

    wav_chunks: list[bytes] = []
    leading_trim_ms: list[int] = []
    trailing_trim_ms: list[int] = []

    nchannels = 0
    sampwidth = 0
    framerate = 0
    comptype = "NONE"
    compname = "not compressed"

    try:
        for index, part in enumerate(parts):
            with wave.open(part, "rb") as handle:
                part_channels = handle.getnchannels()
                part_width = handle.getsampwidth()
                part_rate = handle.getframerate()
                part_comptype = handle.getcomptype()
                part_compname = handle.getcompname()
                pcm_data = handle.readframes(handle.getnframes())

            if index == 0:
                nchannels = part_channels
                sampwidth = part_width
                framerate = part_rate
                comptype = part_comptype
                compname = part_compname
            else:
                if (
                    part_channels != nchannels
                    or part_width != sampwidth
                    or part_rate != framerate
                    or part_comptype != comptype
                ):
                    return False, {"reason": "wav_params_mismatch"}

            trimmed, leading_trim_frames, trailing_trim_frames = _trim_wav_boundary_silence(
                pcm_data=pcm_data,
                nchannels=nchannels,
                sampwidth=sampwidth,
                framerate=framerate,
                trim_leading=index > 0,
                trim_trailing=index < (len(parts) - 1),
            )
            wav_chunks.append(trimmed)
            leading_trim_ms.append(int(round((leading_trim_frames / max(1, framerate)) * 1000.0)))
            trailing_trim_ms.append(int(round((trailing_trim_frames / max(1, framerate)) * 1000.0)))
    except Exception:
        _remove_file_safely(out_path)
        return False, {"reason": "wav_read_failed"}

    if len(target_pauses_ms) < max(0, len(parts) - 1):
        fill_value = target_pauses_ms[-1] if target_pauses_ms else _DUAL_PAUSE_MS_DEFAULT
        target_pauses_ms = list(target_pauses_ms) + [fill_value] * (len(parts) - 1 - len(target_pauses_ms))

    try:
        with wave.open(out_path, "wb") as out_handle:
            out_handle.setnchannels(nchannels)
            out_handle.setsampwidth(sampwidth)
            out_handle.setframerate(framerate)
            out_handle.setcomptype(comptype, compname)

            for index, pcm_chunk in enumerate(wav_chunks):
                if pcm_chunk:
                    out_handle.writeframes(pcm_chunk)
                if index < len(wav_chunks) - 1:
                    pause_ms = _normalize_dual_pause_ms(target_pauses_ms[index] if index < len(target_pauses_ms) else None)
                    pause_frames = int(round((pause_ms / 1000.0) * framerate))
                    silence_chunk = _wav_silence_bytes(nchannels, sampwidth, pause_frames)
                    if silence_chunk:
                        out_handle.writeframes(silence_chunk)
    except Exception:
        _remove_file_safely(out_path)
        return False, {"reason": "wav_write_failed"}

    if not (os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES):
        _remove_file_safely(out_path)
        return False, {"reason": "wav_empty"}

    return True, {
        "trimmed_leading_ms_preview": leading_trim_ms[:12],
        "trimmed_trailing_ms_preview": trailing_trim_ms[:12],
    }


def _ffmpeg_decode_to_wav(in_path: str, out_path: str) -> bool:
    ffmpeg_path = _detect_ffmpeg()
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        in_path,
        "-ac",
        "1",
        "-ar",
        "24000",
        "-c:a",
        "pcm_s16le",
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return False
    return proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES


def _ffmpeg_encode_wav_to_mp3(in_path: str, out_path: str) -> bool:
    ffmpeg_path = _detect_ffmpeg()
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False

    attempts = (
        [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            in_path,
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "4",
            out_path,
        ],
        [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            in_path,
            "-b:a",
            "128k",
            out_path,
        ],
    )
    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except Exception:
            continue
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > _MIN_AUDIO_BYTES:
            return True
    return False


def _merge_mp3_parts_with_pause(
    parts: list[str],
    out_path: str,
    target_pauses_ms: list[int],
) -> tuple[bool, dict[str, Any]]:
    ffmpeg_path = _detect_ffmpeg()
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False, {"reason": "ffmpeg_missing"}

    with tempfile.TemporaryDirectory(prefix="wintts_dual_pause_") as temp_dir:
        decoded_parts: list[str] = []
        for index, part in enumerate(parts, start=1):
            wav_path = str(Path(temp_dir) / f"segment_{index:03d}.wav")
            if not _ffmpeg_decode_to_wav(part, wav_path):
                return False, {"reason": "decode_failed", "failed_part": index}
            decoded_parts.append(wav_path)

        merged_wav = str(Path(temp_dir) / "merged.wav")
        merged_ok, merged_meta = _merge_wav_parts_with_pause(
            parts=decoded_parts,
            out_path=merged_wav,
            target_pauses_ms=target_pauses_ms,
        )
        if not merged_ok:
            return False, dict(merged_meta or {}, reason=(merged_meta or {}).get("reason") or "wav_merge_failed")

        if not _ffmpeg_encode_wav_to_mp3(merged_wav, out_path):
            _remove_file_safely(out_path)
            return False, {"reason": "encode_failed"}

    return True, dict(merged_meta or {})


def _concat_dual_parts_with_pause(
    parts: list[str],
    out_path: str,
    out_suffix: str,
    rendered_segments: list[dict[str, Any]],
    pause_mode: str,
    pause_ms: int,
) -> tuple[bool, dict[str, Any]]:
    normalized_mode = _normalize_dual_pause_mode(pause_mode)
    normalized_pause_ms = _normalize_dual_pause_ms(pause_ms)

    if normalized_mode == "off":
        return False, {
            "mode": normalized_mode,
            "requested_ms": normalized_pause_ms,
            "applied": False,
            "reason": "disabled",
        }
    if len(parts) <= 1:
        return False, {
            "mode": normalized_mode,
            "requested_ms": normalized_pause_ms,
            "applied": False,
            "reason": "single_part",
        }

    targets = _build_dual_pause_targets(rendered_segments, normalized_mode, normalized_pause_ms)
    if len(targets) < len(parts) - 1:
        fill_value = normalized_pause_ms if normalized_mode == "manual" else _DUAL_PAUSE_AUTO_BASE_MS
        targets = list(targets) + [fill_value] * (len(parts) - 1 - len(targets))

    meta: dict[str, Any] = {
        "mode": normalized_mode,
        "requested_ms": normalized_pause_ms,
        "applied": False,
        "boundaries_count": max(0, len(parts) - 1),
        "target_pause_ms_preview": targets[:12],
        "target_pause_ms_avg": round(sum(targets) / len(targets), 1) if targets else 0.0,
    }

    if out_suffix == ".wav":
        ok, details = _merge_wav_parts_with_pause(parts=parts, out_path=out_path, target_pauses_ms=targets)
    elif out_suffix == ".mp3":
        ok, details = _merge_mp3_parts_with_pause(parts=parts, out_path=out_path, target_pauses_ms=targets)
    else:
        return False, dict(meta, reason=f"unsupported_format:{out_suffix}")

    details_payload = dict(details or {})
    meta.update(details_payload)
    meta["applied"] = bool(ok)
    if not ok and "reason" not in meta:
        meta["reason"] = "merge_failed"
    return bool(ok), meta


async def _edge_tts_save(communicate: Any, out_path: str) -> None:
    """Сохранить результат edge-tts."""
    if hasattr(communicate, "save"):
        await communicate.save(out_path)
        return
    with open(out_path, "wb") as file_handle:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                file_handle.write(chunk.get("data", b""))


def _edge_tts_create_communicate(
    text: str,
    voice_id: str,
    rate: str = _EDGE_RATE_DEFAULT,
    volume: str = _EDGE_VOLUME_DEFAULT,
    pitch: str = _EDGE_PITCH_DEFAULT,
) -> Any:
    """Создать edge_tts.Communicate с совместимостью по сигнатурам."""
    attempts = (
        lambda: EDGE_TTS_MODULE.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
            volume=volume,
            pitch=pitch,
        ),
        lambda: EDGE_TTS_MODULE.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
            pitch=pitch,
        ),
        lambda: EDGE_TTS_MODULE.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
        ),
        lambda: EDGE_TTS_MODULE.Communicate(text=text, voice=voice_id),
        lambda: EDGE_TTS_MODULE.Communicate(text, voice=voice_id),
        lambda: EDGE_TTS_MODULE.Communicate(text, voice_id),
    )
    last_exc: Exception | None = None
    for builder in attempts:
        try:
            return builder()
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    try:
        return EDGE_TTS_MODULE.Communicate(text=text, voice=voice_id)
    except Exception:
        return EDGE_TTS_MODULE.Communicate(text, voice_id)


def _edge_voice_fallback_ids(primary_voice: str) -> list[str]:
    voices: list[str] = []
    for item in [primary_voice] + list(EDGE_TTS_VOICE_PRIORITY or []) + list(_EDGE_FALLBACK_VOICES):
        candidate = str(item or "").strip()
        if not candidate or candidate in voices:
            continue
        voices.append(candidate)
    return voices[:6]


def _edge_error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".strip().lower()


def _is_transient_edge_error(exc: Exception) -> bool:
    text = _edge_error_text(exc)
    markers = (
        "timeout",
        "timed out",
        "connection",
        "connect",
        "socket",
        "network",
        "temporary",
        "temporarily",
        "thrott",
        "too many requests",
        "429",
        "503",
        "502",
        "504",
        "noaudioreceived",
        "cancelled",
        "reset by peer",
    )
    return any(marker in text for marker in markers)


def _is_voice_related_edge_error(exc: Exception) -> bool:
    text = _edge_error_text(exc)
    markers = (
        "voice",
        "locale",
        "lang",
        "invalidargument",
        "invalid voice",
        "unsupported",
        "bad request",
    )
    return any(marker in text for marker in markers)


async def synthesize_edge_tts(
    text: str,
    voice_id: str,
    file_path: str,
    parallelism: int | None = None,
    edge_rate: str = _EDGE_RATE_DEFAULT,
    edge_volume: str = _EDGE_VOLUME_DEFAULT,
    edge_pitch: str = _EDGE_PITCH_DEFAULT,
    progress: ProgressCallback | None = None,
) -> None:
    """Синтез через edge_tts в указанный файл."""
    if EDGE_TTS_MODULE is None:
        raise RuntimeError("edge-tts не инициализирован")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Пустой текст для синтеза")
    edge_rate = _normalize_edge_rate(edge_rate)
    edge_volume = _normalize_edge_volume(edge_volume)
    edge_pitch = _normalize_edge_pitch(edge_pitch)

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    _report_progress(progress, 18, "Инициализирую Edge TTS.")
    _report_progress(
        progress,
        19,
        f"Параметры Edge TTS: скорость {edge_rate}, тон {edge_pitch}, громкость {edge_volume}.",
    )

    def _audio_ready(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > _MIN_AUDIO_BYTES
        except Exception:
            return False

    def _safe_remove(path: str) -> None:
        _remove_file_safely(path)

    temp_parts: set[str] = set()

    def _track_temp(path: str) -> str:
        temp_parts.add(os.path.abspath(path))
        return path

    def _untrack_temp(path: str) -> None:
        temp_parts.discard(os.path.abspath(path))

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

    async def _concat_parts_async(parts: list[str], out_path: str) -> bool:
        return await asyncio.to_thread(_ffmpeg_concat_mp3, parts, out_path)

    top_level_chunks = _split_chunk(clean_text, _EDGE_CHUNK_LIMITS[0])
    requested_parallelism = _normalize_edge_parallelism(parallelism)
    effective_parallelism = max(1, min(requested_parallelism, len(top_level_chunks)))
    start_spacing = (
        _EDGE_START_SPACING_HIGH_SECONDS
        if effective_parallelism >= _EDGE_HIGH_PARALLELISM_THRESHOLD
        else _EDGE_START_SPACING_SECONDS
    )
    semaphore = asyncio.Semaphore(effective_parallelism)
    progress_lock = asyncio.Lock()
    start_lock = asyncio.Lock()
    completed_chunks = 0
    next_start_at = time.monotonic()

    async def _acquire_request_slot() -> None:
        nonlocal next_start_at
        delay = 0.0
        async with start_lock:
            now = time.monotonic()
            if now < next_start_at:
                delay = next_start_at - now
            next_start_at = max(now, next_start_at) + start_spacing
        if delay > 0:
            await asyncio.sleep(delay)

    _LOGGER.info(
        "wintts edge long synth start voice=%s chars=%s chunks=%s parallel=%s requested_parallel=%s start_spacing=%.2f",
        voice_id,
        len(clean_text),
        len(top_level_chunks),
        effective_parallelism,
        requested_parallelism,
        start_spacing,
    )
    progress_message = (
        f"Текст разбит на {len(top_level_chunks)} частей. "
        f"Параллельность Edge TTS: {effective_parallelism}."
    )
    if effective_parallelism != requested_parallelism:
        progress_message += (
            f" Запрошено: {requested_parallelism}, применено: {effective_parallelism} "
            f"(частей меньше, чем потоков)."
        )
    _report_progress(progress, 22, progress_message)

    async def _synth_one_chunk(
        chunk_text: str,
        out_path: str,
        try_voice: str,
        chunk_label: str,
        attempts: int = _EDGE_REQUEST_ATTEMPTS,
    ) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            _safe_remove(out_path)
            try:
                if attempt == 1:
                    _report_progress(progress, 24, f"{chunk_label}: отправляю текст в Edge TTS.")
                await _acquire_request_slot()
                communicate = _edge_tts_create_communicate(
                    chunk_text,
                    try_voice,
                    rate=edge_rate,
                    volume=edge_volume,
                    pitch=edge_pitch,
                )
                await asyncio.wait_for(_edge_tts_save(communicate, out_path), timeout=_EDGE_REQUEST_TIMEOUT_SECONDS)
                if _audio_ready(out_path):
                    return
                raise RuntimeError("edge-tts вернул пустой или слишком маленький аудиофайл")
            except Exception as exc:  # pragma: no cover - сетевые ошибки
                last_exc = exc
                _safe_remove(out_path)
                if _is_voice_related_edge_error(exc):
                    raise
                if not _is_transient_edge_error(exc):
                    raise
                sleep_seconds = min(
                    _EDGE_RETRY_BACKOFF_BASE_SECONDS * attempt + random.uniform(0.0, 0.35),
                    _EDGE_RETRY_BACKOFF_MAX_SECONDS,
                )
                _report_progress(
                    progress,
                    24,
                    (
                        f"{chunk_label}: временная ошибка, попытка {attempt}/{attempts} "
                        f"({type(exc).__name__}: {exc}). Повторяю..."
                    ),
                    "warning",
                )
                _LOGGER.warning(
                    "wintts edge chunk retry voice=%s attempt=%s/%s chars=%s error=%s: %s",
                    try_voice,
                    attempt,
                    attempts,
                    len(chunk_text),
                    type(exc).__name__,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(sleep_seconds)
        raise last_exc or RuntimeError("edge-tts: синтез завершился ошибкой")

    voice_fallbacks = _edge_voice_fallback_ids(voice_id)

    async def _synth_chunk_with_fallback(chunk_text: str, out_path: str, chunk_label: str) -> Exception | None:
        last_exc: Exception | None = None
        for index, try_voice in enumerate(voice_fallbacks[:3]):
            try:
                if try_voice != voice_id:
                    if last_exc is not None and not _is_voice_related_edge_error(last_exc):
                        break
                    _report_progress(progress, 26, f"{chunk_label}: пробую резервный голос {try_voice}.", "warning")
                attempt_budget = _EDGE_REQUEST_ATTEMPTS if index == 0 else max(1, _EDGE_REQUEST_ATTEMPTS - 1)
                await _synth_one_chunk(chunk_text, out_path, try_voice, chunk_label, attempts=attempt_budget)
                return None
            except Exception as exc:
                last_exc = exc
                if index == 0 and not _is_voice_related_edge_error(exc):
                    break
        return last_exc

    async def _build_part(index: int, chunk: str) -> str:
        part_path = _track_temp(file_path + f".__part{index:03d}.mp3")
        chunk_label = f"Часть {index + 1}/{len(top_level_chunks)}"
        last_error = await _synth_chunk_with_fallback(chunk, part_path, chunk_label)
        if _audio_ready(part_path):
            return part_path

        for split_limit in _EDGE_CHUNK_LIMITS[1:]:
            subchunks = _split_chunk(chunk, split_limit)
            if len(subchunks) <= 1:
                continue

            _report_progress(
                progress,
                30,
                f"{chunk_label}: делю на {len(subchunks)} подпакетов, чтобы обойти ошибку сервиса.",
                "warning",
            )
            subparts: list[str] = []
            split_error: Exception | None = None
            try:
                for sub_index, subchunk in enumerate(subchunks):
                    sub_path = _track_temp(file_path + f".__part{index:03d}_sub{sub_index:03d}.mp3")
                    subparts.append(sub_path)
                    split_label = f"{chunk_label}, подпакет {sub_index + 1}/{len(subchunks)}"
                    split_error = await _synth_chunk_with_fallback(subchunk, sub_path, split_label)
                    if not _audio_ready(sub_path):
                        break

                if split_error is None and all(_audio_ready(path) for path in subparts):
                    _safe_remove(part_path)
                    if not await _concat_parts_async(subparts, part_path):
                        with open(part_path, "wb") as out_handle:
                            for subpart in subparts:
                                with open(subpart, "rb") as in_handle:
                                    out_handle.write(in_handle.read())
                    if _audio_ready(part_path):
                        return part_path
                if split_error is not None:
                    last_error = split_error
            finally:
                for subpart in subparts:
                    _safe_remove(subpart)
                    _untrack_temp(subpart)

        _safe_remove(part_path)
        _untrack_temp(part_path)
        if last_error is not None:
            raise RuntimeError(
                f"Ошибка чанка edge-tts: {type(last_error).__name__}: {last_error}"
            ) from last_error
        raise RuntimeError("edge-tts: аудиочасть не создана")

    tasks: list[asyncio.Task[str]] = []
    parts: list[str] = []

    async def _worker(index: int, chunk: str) -> str:
        async with semaphore:
            part_path = await _build_part(index, chunk)
            nonlocal completed_chunks
            async with progress_lock:
                completed_chunks += 1
                done_count = completed_chunks
            percent = 30 + int((done_count / max(1, len(top_level_chunks))) * 52)
            _report_progress(progress, percent, f"Часть {index + 1}/{len(top_level_chunks)} готова.")
            return part_path

    try:
        if len(top_level_chunks) == 1:
            part_path = await _worker(0, top_level_chunks[0])
            _report_progress(progress, 88, "Финализирую итоговый MP3.")
            _safe_remove(file_path)
            shutil.move(part_path, file_path)
            _untrack_temp(part_path)
            if not _audio_ready(file_path):
                _safe_remove(file_path)
                raise RuntimeError("Edge TTS сформировал пустой итоговый MP3.")
            return

        tasks = [asyncio.create_task(_worker(index, chunk)) for index, chunk in enumerate(top_level_chunks)]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        parts = list(results)

        _report_progress(progress, 86, "Склеиваю все части в итоговый MP3.")
        _safe_remove(file_path)
        if await _concat_parts_async(parts, file_path):
            if not _audio_ready(file_path):
                _safe_remove(file_path)
                raise RuntimeError("Edge TTS сформировал пустой MP3 после склейки.")
            return

        _report_progress(progress, 90, "FFmpeg недоступен или не справился. Склеиваю части напрямую.")
        _safe_remove(file_path)
        with open(file_path, "wb") as out_handle:
            for part in parts:
                with open(part, "rb") as in_handle:
                    out_handle.write(in_handle.read())
        if not _audio_ready(file_path):
            _safe_remove(file_path)
            raise RuntimeError("Edge TTS сформировал пустой MP3 после прямой склейки.")
    except Exception:
        _safe_remove(file_path)
        raise
    finally:
        for part in parts:
            _safe_remove(part)
            _untrack_temp(part)
        for part_path in list(temp_parts):
            _remove_file_safely(part_path)
            temp_parts.discard(part_path)

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


def _normalize_language_code(value: Any, fallback: str = "und") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    text = "".join(char for char in text if char.isprintable())
    match = _LANGUAGE_CODE_RE.search(text)
    if match:
        return str(match.group(1) or "").strip().lower() or fallback
    return fallback


def _decode_language_tag(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8", errors="ignore")
        except Exception:
            decoded = value.decode(errors="ignore")
        return "".join(ch for ch in decoded if ch.isprintable()).strip()
    return "".join(ch for ch in str(value or "") if ch.isprintable()).strip()


def _extract_pyttsx3_languages(voice_obj: Any) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    try:
        for raw_item in list(getattr(voice_obj, "languages", None) or []):
            code = _normalize_language_code(_decode_language_tag(raw_item))
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
    except Exception:
        pass

    if not codes:
        fallback_source = f"{getattr(voice_obj, 'id', '')} {getattr(voice_obj, 'name', '')}"
        code = _normalize_language_code(fallback_source)
        if code and code != "und":
            codes.append(code)
    return codes or ["und"]


def _build_google_voice_catalog() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
    warnings: list[str] = []
    voice_map: dict[str, dict[str, Any]] = {}
    language_map: dict[str, list[str]] = {}

    raw_languages: dict[str, str] = {}
    if callable(gtts_langs):
        try:
            result = gtts_langs() or {}
            if isinstance(result, dict):
                raw_languages = {str(key): str(value or "") for key, value in result.items()}
        except Exception as exc:
            warnings.append(f"Не удалось получить список языков gTTS: {type(exc).__name__}: {exc}")

    if not raw_languages:
        raw_languages = {_GOOGLE_DEFAULT_LANG: "Русский"}
        warnings.append("Использован встроенный список языков Google TTS (динамический список недоступен).")

    normalized_languages: dict[str, str] = {}
    for raw_code, raw_name in raw_languages.items():
        code = _normalize_language_code(raw_code)
        if code == "und":
            continue
        if code not in normalized_languages:
            normalized_languages[code] = raw_name

    if _GOOGLE_DEFAULT_LANG not in normalized_languages:
        normalized_languages[_GOOGLE_DEFAULT_LANG] = "Русский"

    for language_code in sorted(normalized_languages.keys(), key=_edge_language_sort_key):
        language_name = str(normalized_languages.get(language_code) or "").strip()
        if language_name:
            label = f"Google TTS: {language_name} ({language_code})"
        else:
            label = f"Google TTS: {language_code}"
        voice_map[label] = {
            "lang": language_code,
            "tld": _GOOGLE_DEFAULT_TLD,
        }
        language_map.setdefault(language_code, []).append(label)

    return voice_map, language_map, warnings


def _edge_gender_label(value: Any) -> str:
    gender = str(value or "").strip().lower()
    if gender == "female":
        return "женский"
    if gender == "male":
        return "мужской"
    return str(value or "").strip()


def _edge_voice_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    locale = str(item.get("locale") or "").strip().lower()
    short_name = str(item.get("short_name") or "").strip().lower()
    label = str(item.get("label") or "").strip().lower()
    if locale.startswith("ru-"):
        group = 0
    elif locale.startswith("uk-"):
        group = 1
    else:
        group = 2
    return (group, locale, short_name or label)


def _edge_language_from_locale(locale: str) -> str:
    clean_locale = str(locale or "").strip().lower()
    if not clean_locale:
        return "und"
    language = clean_locale.split("-", 1)[0].strip()
    return language or "und"


def _edge_language_sort_key(language: str) -> tuple[int, str]:
    clean_language = str(language or "").strip().lower()
    if clean_language == "ru":
        group = 0
    elif clean_language == "uk":
        group = 1
    else:
        group = 2
    return (group, clean_language)


def _build_edge_voice_catalog(edge_tts_module: Any) -> tuple[dict[str, str], list[str], dict[str, list[str]], list[str]]:
    warnings: list[str] = []
    voices_data: list[dict[str, Any]] = []

    try:
        list_voices_fn = getattr(edge_tts_module, "list_voices", None)
        if callable(list_voices_fn):
            raw_data = _run_async(asyncio.wait_for(list_voices_fn(), timeout=_EDGE_VOICE_LIST_TIMEOUT_SECONDS))
            if isinstance(raw_data, list):
                voices_data = [item for item in raw_data if isinstance(item, dict)]
    except Exception as exc:
        warnings.append(f"Не удалось получить список голосов Edge TTS: {type(exc).__name__}: {exc}")

    all_voices: list[dict[str, Any]] = []
    for item in voices_data:
        short_name = str(item.get("ShortName") or "").strip()
        if not short_name:
            continue
        locale = str(item.get("Locale") or "").strip()
        gender = _edge_gender_label(item.get("Gender"))
        details: list[str] = []
        if locale:
            details.append(locale)
        if gender:
            details.append(gender)
        label = short_name if not details else f"{short_name} ({', '.join(details)})"
        all_voices.append(
            {
                "label": label,
                "short_name": short_name,
                "locale": locale,
            }
        )

    selected_voices = list(all_voices)
    selected_voices.sort(key=_edge_voice_sort_key)

    voice_map: dict[str, str] = {}
    voice_priority: list[str] = []
    language_map: dict[str, list[str]] = {}
    duplicate_guard: dict[str, int] = {}
    for item in selected_voices:
        label = str(item.get("label") or "").strip()
        short_name = str(item.get("short_name") or "").strip()
        if not label or not short_name:
            continue
        count = duplicate_guard.get(label, 0)
        duplicate_guard[label] = count + 1
        final_label = f"{label} [{count + 1}]" if count else label
        voice_map[final_label] = short_name
        if short_name not in voice_priority:
            voice_priority.append(short_name)
        language = _edge_language_from_locale(str(item.get("locale") or ""))
        language_voices = language_map.setdefault(language, [])
        if final_label not in language_voices:
            language_voices.append(final_label)

    if not voice_map:
        voice_map = {
            "ru-RU-SvetlanaNeural (женский)": "ru-RU-SvetlanaNeural",
            "ru-RU-DmitryNeural (мужской)": "ru-RU-DmitryNeural",
            "ru-RU-DariyaNeural (женский)": "ru-RU-DariyaNeural",
        }
        for voice_id in voice_map.values():
            if voice_id not in voice_priority:
                voice_priority.append(voice_id)
        language_map["ru"] = list(voice_map.keys())
        warnings.append("Использован встроенный список голосов Edge TTS (динамический список недоступен).")

    if not language_map and voice_map:
        language_map["und"] = list(voice_map.keys())

    return voice_map, voice_priority, language_map, warnings


def init_tts_engines(force: bool = False) -> None:
    """Инициализация доступных движков TTS."""
    global ENGINE_OPTIONS, VOICE_OPTIONS, PYTTSX3_VOICE_MAP, PYTTSX3_LANGUAGE_MAP
    global GOOGLE_TTS_VOICE_MAP, GOOGLE_TTS_LANGUAGE_MAP
    global EDGE_TTS_VOICE_MAP, EDGE_TTS_VOICE_PRIORITY, EDGE_TTS_LANGUAGE_MAP
    global EDGE_TTS_MODULE
    global RHVOICE_TTS_VOICE_MAP, RHVOICE_TTS_LANGUAGE_MAP, RHVOICE_ADDON_STATE
    global TTS_INIT_DONE, TTS_IMPORT_ERRORS

    if TTS_INIT_DONE and not force:
        return

    _reset_dependency_diagnostics()
    _detect_ffmpeg(force=force)
    module_import_errors = _prepare_tts_module_imports()

    ENGINE_OPTIONS = []
    VOICE_OPTIONS = {}
    PYTTSX3_VOICE_MAP = {}
    PYTTSX3_LANGUAGE_MAP = {}
    GOOGLE_TTS_VOICE_MAP = {}
    GOOGLE_TTS_LANGUAGE_MAP = {}
    EDGE_TTS_VOICE_MAP = {}
    EDGE_TTS_VOICE_PRIORITY = []
    EDGE_TTS_LANGUAGE_MAP = {}
    EDGE_TTS_MODULE = None
    RHVOICE_TTS_VOICE_MAP = {}
    RHVOICE_TTS_LANGUAGE_MAP = {}
    RHVOICE_ADDON_STATE = {}
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
        google_voice_map, google_language_map, google_warnings = _build_google_voice_catalog()
        GOOGLE_TTS_VOICE_MAP = dict(google_voice_map)
        GOOGLE_TTS_LANGUAGE_MAP = {key: list(values) for key, values in (google_language_map or {}).items()}
        if not GOOGLE_TTS_VOICE_MAP:
            fallback_label = "Google TTS: Русский (ru)"
            GOOGLE_TTS_VOICE_MAP = {
                fallback_label: {
                    "lang": _GOOGLE_DEFAULT_LANG,
                    "tld": _GOOGLE_DEFAULT_TLD,
                }
            }
            GOOGLE_TTS_LANGUAGE_MAP = {_GOOGLE_DEFAULT_LANG: [fallback_label]}
        if google_warnings:
            TTS_IMPORT_ERRORS.extend(google_warnings)
        ENGINE_OPTIONS.append("Google")
        VOICE_OPTIONS["Google"] = list(GOOGLE_TTS_VOICE_MAP.keys())
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
        language_map: dict[str, list[str]] = {}
        for index, voice in enumerate(voices, start=1):
            language_codes = _extract_pyttsx3_languages(voice)
            primary_language = language_codes[0] if language_codes else "und"
            if primary_language and primary_language != "und":
                label = f"{index}: {voice.name} ({primary_language})"
            else:
                label = f"{index}: {voice.name}"
            labels.append(label)
            PYTTSX3_VOICE_MAP[label] = voice.id
            for code in language_codes:
                language_map.setdefault(code, []).append(label)
        ordered_language_map: dict[str, list[str]] = {}
        for code in sorted(language_map.keys(), key=_edge_language_sort_key):
            voices_for_code = language_map.get(code) or []
            if voices_for_code:
                ordered_language_map[code] = voices_for_code
        PYTTSX3_LANGUAGE_MAP = ordered_language_map
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
                if _ver_tuple(edge_ver) < (7, 2, 7):
                    TTS_IMPORT_ERRORS.append(
                        f"edge-tts версии {edge_ver} может работать нестабильно. Рекомендуется обновить до 7.2.7+."
                    )
            except Exception:
                pass

        edge_voices, voice_priority, language_map, voice_warnings = _build_edge_voice_catalog(_edge_tts)
        EDGE_TTS_VOICE_MAP.update(edge_voices)
        EDGE_TTS_VOICE_PRIORITY = list(voice_priority or [])
        EDGE_TTS_LANGUAGE_MAP = {key: list(values) for key, values in (language_map or {}).items()}
        if voice_warnings:
            TTS_IMPORT_ERRORS.extend(voice_warnings)
        ENGINE_OPTIONS.append("Edge TTS")
        VOICE_OPTIONS["Edge TTS"] = list(edge_voices.keys())
    except Exception as exc:
        TTS_IMPORT_ERRORS.append(f"edge-tts недоступен: {type(exc).__name__}: {exc}")
        EDGE_TTS_MODULE = None

    # RHVoice addon (динамически через локальное venv-окружение).
    try:
        addon_state = _collect_addon_runtime_state()
        RHVOICE_ADDON_STATE = dict(addon_state or {})

        if bool(addon_state.get("broken")):
            TTS_IMPORT_ERRORS.append(
                "RHVoice-addon поврежден: окружение venv создано не полностью. Нажмите «Установить RHVoice-addon»."
            )

        if bool(addon_state.get("installed")):
            rh_tts_class, load_error = addon_runtime.load_rhvoice_tts_class(addon_state)
            if rh_tts_class is None:
                raise RuntimeError(load_error or "класс TTS не найден")

            rh_voice_map, rh_language_map, rh_warnings = addon_runtime.build_rhvoice_voice_catalog(rh_tts_class)
            RHVOICE_TTS_VOICE_MAP = dict(rh_voice_map)
            RHVOICE_TTS_LANGUAGE_MAP = {key: list(values) for key, values in (rh_language_map or {}).items()}
            if rh_warnings:
                TTS_IMPORT_ERRORS.extend(rh_warnings)

            if RHVOICE_TTS_VOICE_MAP:
                ENGINE_OPTIONS.append("RHVoice")
                VOICE_OPTIONS["RHVoice"] = list(RHVOICE_TTS_VOICE_MAP.keys())
            else:
                TTS_IMPORT_ERRORS.append("RHVoice не вернул доступные голоса.")
    except Exception as exc:
        TTS_IMPORT_ERRORS.append(f"RHVoice недоступен: {type(exc).__name__}: {exc}")

    if not ENGINE_OPTIONS:
        fallback_label = "Google TTS: Русский (ru)"
        ENGINE_OPTIONS.append("Google")
        VOICE_OPTIONS["Google"] = [fallback_label]
        GOOGLE_TTS_VOICE_MAP = {
            fallback_label: {
                "lang": _GOOGLE_DEFAULT_LANG,
                "tld": _GOOGLE_DEFAULT_TLD,
            }
        }
        GOOGLE_TTS_LANGUAGE_MAP = {_GOOGLE_DEFAULT_LANG: [fallback_label]}
        TTS_IMPORT_ERRORS.append("Не удалось инициализировать ни один TTS-движок.")

    TTS_INIT_DONE = True


def _default_engine_name(available: list[str] | None = None) -> str:
    engines = [str(item).strip() for item in (available if available is not None else ENGINE_OPTIONS) if str(item).strip()]
    for candidate in ("Edge TTS", "Google", "RHVoice", "pyx3"):
        if candidate in engines:
            return candidate
    return engines[0] if engines else ""


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

    addon_state = RHVOICE_ADDON_STATE if RHVOICE_ADDON_STATE else _collect_addon_runtime_state()
    if addon_state:
        lines.append(
            "RHVoice-addon: "
            f"{'установлен' if addon_state.get('installed') else ('поврежден' if addon_state.get('broken') else 'не установлен')}"
        )
        if addon_state.get("addon_root"):
            lines.append(f"- Папка addon: {addon_state.get('addon_root')}")
        if addon_state.get("venv_python"):
            lines.append(f"- Python addon-venv: {addon_state.get('venv_python')}")
        if addon_state.get("base_python"):
            source_label = str(addon_state.get("base_python_source_label") or "").strip()
            if source_label:
                lines.append(f"- Базовый Python: {addon_state.get('base_python')} ({source_label})")
            else:
                lines.append(f"- Базовый Python: {addon_state.get('base_python')}")

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

    lines.append(f"Импорт документов: {', '.join(_UPLOAD_ACCEPT_EXTENSIONS)}")
    lines.append(f"Максимальный размер файла для импорта: {_UPLOAD_MAX_BYTES // (1024 * 1024)} МБ")

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


def _report_progress(progress: ProgressCallback | None, percent: int, message: str, level: str = "info") -> None:
    if progress is None:
        return
    try:
        progress(max(0, min(100, int(percent))), str(message or "").strip(), str(level or "info"))
    except Exception:
        pass


def _parse_synthesis_request(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, tuple[dict[str, Any], int] | None]:
    def _pick_value(*keys: str) -> Any:
        for key in keys:
            if not key:
                continue
            value = payload.get(key)
            if value not in (None, ""):
                return value
            form_value = request.form.get(key)
            if form_value not in (None, ""):
                return form_value
        return None

    text = str(payload.get("text") or request.form.get("text") or "").strip()
    engine = str(payload.get("engine") or request.form.get("engine") or "").strip()
    voice = str(payload.get("voice") or request.form.get("voice") or "").strip()
    primary_language_raw = _pick_value("primary_language", "edge_language", "language")

    dual_payload = _as_dict_payload(payload.get("dual_language"))
    dual_enabled_raw = dual_payload.get("enabled")
    if dual_enabled_raw in (None, ""):
        dual_enabled_raw = _pick_value("dual_language_enabled", "use_secondary_language", "secondary_language_enabled")
    secondary_language_raw = dual_payload.get("secondary_language")
    if secondary_language_raw in (None, ""):
        secondary_language_raw = _pick_value("secondary_language", "second_language")
    secondary_voice_raw = dual_payload.get("secondary_voice")
    if secondary_voice_raw in (None, ""):
        secondary_voice_raw = _pick_value("secondary_voice", "second_voice")
    secondary_rate_raw = dual_payload.get("secondary_edge_rate")
    if secondary_rate_raw in (None, ""):
        secondary_rate_raw = _pick_value("secondary_edge_rate", "second_edge_rate", "secondary_rate")
    secondary_pitch_raw = dual_payload.get("secondary_edge_pitch")
    if secondary_pitch_raw in (None, ""):
        secondary_pitch_raw = _pick_value("secondary_edge_pitch", "second_edge_pitch", "secondary_pitch")
    secondary_volume_raw = dual_payload.get("secondary_edge_volume")
    if secondary_volume_raw in (None, ""):
        secondary_volume_raw = _pick_value("secondary_edge_volume", "second_edge_volume", "secondary_volume")
    dual_pause_mode_raw = dual_payload.get("pause_mode")
    if dual_pause_mode_raw in (None, ""):
        dual_pause_mode_raw = dual_payload.get("dual_pause_mode")
    if dual_pause_mode_raw in (None, ""):
        dual_pause_mode_raw = _pick_value(
            "dual_pause_mode",
            "dual_language_pause_mode",
            "cross_language_pause_mode",
            "pause_mode",
        )
    dual_pause_ms_raw = dual_payload.get("pause_ms")
    if dual_pause_ms_raw in (None, ""):
        dual_pause_ms_raw = dual_payload.get("dual_pause_ms")
    if dual_pause_ms_raw in (None, ""):
        dual_pause_ms_raw = _pick_value(
            "dual_pause_ms",
            "dual_language_pause_ms",
            "cross_language_pause_ms",
            "pause_ms",
        )

    edge_parallelism_raw = payload.get("edge_parallelism")
    if edge_parallelism_raw in (None, ""):
        edge_parallelism_raw = request.form.get("edge_parallelism")
    edge_parallelism = _normalize_edge_parallelism(edge_parallelism_raw)

    google_parallelism_raw = payload.get("google_parallelism")
    if google_parallelism_raw in (None, ""):
        google_parallelism_raw = payload.get("gtts_parallelism")
    if google_parallelism_raw in (None, ""):
        google_parallelism_raw = request.form.get("google_parallelism")
    if google_parallelism_raw in (None, ""):
        google_parallelism_raw = request.form.get("gtts_parallelism")
    if google_parallelism_raw in (None, ""):
        # Backward compatibility with older UI that reused edge_parallelism for all engines.
        google_parallelism_raw = edge_parallelism_raw
    google_parallelism = _normalize_google_parallelism(google_parallelism_raw)

    google_retry_count_raw = payload.get("google_retry_count")
    if google_retry_count_raw in (None, ""):
        google_retry_count_raw = payload.get("gtts_retry_count")
    if google_retry_count_raw in (None, ""):
        google_retry_count_raw = payload.get("google_retries")
    if google_retry_count_raw in (None, ""):
        google_retry_count_raw = request.form.get("google_retry_count")
    if google_retry_count_raw in (None, ""):
        google_retry_count_raw = request.form.get("gtts_retry_count")
    if google_retry_count_raw in (None, ""):
        google_retry_count_raw = request.form.get("google_retries")
    google_retry_count = _normalize_google_retry_count(google_retry_count_raw)

    edge_rate_raw = payload.get("edge_rate")
    if edge_rate_raw in (None, ""):
        edge_rate_raw = payload.get("speech_rate")
    if edge_rate_raw in (None, ""):
        edge_rate_raw = request.form.get("edge_rate")
    if edge_rate_raw in (None, ""):
        edge_rate_raw = request.form.get("speech_rate")

    edge_volume_raw = payload.get("edge_volume")
    if edge_volume_raw in (None, ""):
        edge_volume_raw = payload.get("speech_volume")
    if edge_volume_raw in (None, ""):
        edge_volume_raw = request.form.get("edge_volume")
    if edge_volume_raw in (None, ""):
        edge_volume_raw = request.form.get("speech_volume")

    edge_pitch_raw = payload.get("edge_pitch")
    if edge_pitch_raw in (None, ""):
        edge_pitch_raw = payload.get("speech_pitch")
    if edge_pitch_raw in (None, ""):
        edge_pitch_raw = request.form.get("edge_pitch")
    if edge_pitch_raw in (None, ""):
        edge_pitch_raw = request.form.get("speech_pitch")

    edge_text_normalizer_raw = payload.get("edge_text_normalizer")
    if edge_text_normalizer_raw in (None, ""):
        edge_text_normalizer_raw = payload.get("text_normalizer")
    if edge_text_normalizer_raw in (None, ""):
        edge_text_normalizer_raw = request.form.get("edge_text_normalizer")
    if edge_text_normalizer_raw in (None, ""):
        edge_text_normalizer_raw = request.form.get("text_normalizer")

    edge_rate = _normalize_edge_rate(edge_rate_raw)
    edge_volume = _normalize_edge_volume(edge_volume_raw)
    edge_pitch = _normalize_edge_pitch(edge_pitch_raw)
    edge_text_normalizer_settings = parse_edge_text_normalizer_settings(edge_text_normalizer_raw)

    if not text:
        return None, ({"ok": False, "error": "Текст пуст. Введите текст для синтеза."}, 400)
    if len(text) > _ABSOLUTE_MAX_TEXT_LEN:
        return None, (
            {
                "ok": False,
                "error": f"Текст слишком длинный. Практический лимит: {_ABSOLUTE_MAX_TEXT_LEN} символов.",
            },
            400,
        )

    try:
        init_tts_engines()
    except Exception as exc:
        diagnostics = _dependency_diagnostics_payload()
        return None, (
            {
                "ok": False,
                "error": f"Ошибка инициализации TTS: {type(exc).__name__}: {exc}",
                "warnings": TTS_IMPORT_ERRORS,
                "diagnostics": diagnostics["raw"],
                "diagnostics_lines": diagnostics["lines"],
            },
            500,
        )

    if not engine:
        engine = _default_engine_name()
    if engine not in ENGINE_OPTIONS:
        return None, ({"ok": False, "error": "Выбранный движок недоступен."}, 400)

    if engine == "Google":
        edge_rate = _normalize_edge_rate(max(-100, min(0, _edge_option_to_int(edge_rate, "rate"))))
        edge_pitch = _normalize_edge_pitch(0)
        edge_volume = _normalize_edge_volume(0)

    text_limit = _get_engine_text_limit(engine)
    if text_limit is not None and len(text) > text_limit:
        return None, ({"ok": False, "error": f"Для движка {engine} максимальная длина текста: {text_limit} символов."}, 400)

    voices = VOICE_OPTIONS.get(engine) or []
    if not voice:
        voice = voices[0] if voices else ""
    if voices and voice not in voices:
        return None, ({"ok": False, "error": "Выбранный голос недоступен для текущего движка."}, 400)

    primary_language = _normalize_language_code(
        primary_language_raw or _voice_language_for_engine(engine, voice, fallback="und"),
        fallback="und",
    )

    dual_enabled = _coerce_bool(dual_enabled_raw, False)
    secondary_language = _normalize_language_code(secondary_language_raw, fallback="und")
    secondary_voice = str(secondary_voice_raw or "").strip()
    secondary_edge_rate = _normalize_edge_rate(secondary_rate_raw if secondary_rate_raw not in (None, "") else edge_rate)
    secondary_edge_pitch = _normalize_edge_pitch(secondary_pitch_raw if secondary_pitch_raw not in (None, "") else edge_pitch)
    secondary_edge_volume = _normalize_edge_volume(secondary_volume_raw if secondary_volume_raw not in (None, "") else edge_volume)
    dual_pause_mode = _normalize_dual_pause_mode(dual_pause_mode_raw)
    dual_pause_ms = _normalize_dual_pause_ms(dual_pause_ms_raw)

    if engine == "Google":
        secondary_edge_rate = _normalize_edge_rate(max(-100, min(0, _edge_option_to_int(secondary_edge_rate, "rate"))))
        secondary_edge_pitch = _normalize_edge_pitch(0)
        secondary_edge_volume = _normalize_edge_volume(0)

    dual_language: dict[str, Any] | None = None
    if dual_enabled:
        if len(voices) < 2:
            return None, ({"ok": False, "error": "Для режима двух языков требуется минимум два голоса."}, 400)

        if not secondary_voice:
            secondary_voice = _first_voice_for_language(engine, secondary_language, fallback_voice="")
        if not secondary_voice:
            for candidate in voices:
                candidate_voice = str(candidate or "").strip()
                if candidate_voice and candidate_voice != voice:
                    secondary_voice = candidate_voice
                    break
        if not secondary_voice:
            return None, ({"ok": False, "error": "Не выбран второй голос."}, 400)
        if secondary_voice not in voices:
            return None, ({"ok": False, "error": "Выбранный второй голос недоступен для текущего движка."}, 400)
        if secondary_voice == voice:
            return None, ({"ok": False, "error": "Основной и второй голос должны отличаться."}, 400)

        if secondary_language == "und":
            secondary_language = _voice_language_for_engine(engine, secondary_voice, fallback="und")
        if primary_language == "und":
            primary_language = _voice_language_for_engine(engine, voice, fallback="und")
        if secondary_language == "und":
            return None, ({"ok": False, "error": "Не удалось определить второй язык. Выберите его явно."}, 400)
        if primary_language == secondary_language:
            return None, ({"ok": False, "error": "Основной и второй язык должны отличаться."}, 400)

        dual_language = {
            "enabled": True,
            "primary_language": primary_language,
            "secondary_language": secondary_language,
            "secondary_voice": secondary_voice,
            "secondary_edge_rate": secondary_edge_rate,
            "secondary_edge_pitch": secondary_edge_pitch,
            "secondary_edge_volume": secondary_edge_volume,
            "pause_mode": dual_pause_mode,
            "pause_ms": dual_pause_ms,
        }

    return (
        {
            "text": text,
            "engine": engine,
            "voice": voice,
            "primary_language": primary_language,
            "edge_parallelism": edge_parallelism if engine == "Edge TTS" else None,
            "google_parallelism": google_parallelism if engine == "Google" else None,
            "google_retry_count": google_retry_count if engine == "Google" else None,
            "edge_rate": edge_rate,
            "edge_volume": edge_volume,
            "edge_pitch": edge_pitch,
            "dual_language": dual_language,
            "edge_text_normalizer": edge_text_normalizer_settings_payload(edge_text_normalizer_settings),
            "text_normalizer": edge_text_normalizer_settings_payload(edge_text_normalizer_settings),
        },
        None,
    )

def _perform_synthesis(
    text: str,
    engine: str,
    voice: str,
    edge_parallelism: int | None = None,
    google_parallelism: int | None = None,
    google_retry_count: int | None = None,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    edge_text_normalizer: dict[str, Any] | EdgeTextNormalizerSettings | None = None,
    primary_language: str = "und",
    dual_language: dict[str, Any] | None = None,
    user: str = "web",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    _report_progress(progress, 5, "Готовлю рабочую область синтеза.")
    _cleanup_generated_files()
    now = int(time.time())
    token = uuid.uuid4().hex[:12]
    file_stem = _output_root() / f"tts_{now}_{token}"
    _cleanup_partial_output_files(file_stem)

    _report_progress(progress, 9, f"Подготовлен выходной файл: {file_stem.name}")
    _report_progress(progress, 12, f"Движок: {engine}. Голос: {voice or 'по умолчанию'}.")
    if engine == "Edge TTS" and edge_parallelism is not None:
        _report_progress(progress, 14, f"Параллельность Edge TTS: {edge_parallelism}.")
    if engine == "Google":
        google_parallelism_value = _normalize_google_parallelism(google_parallelism)
        google_retry_count_value = _normalize_google_retry_count(google_retry_count)
        _report_progress(
            progress,
            14,
            f"Параллельность Google TTS: {google_parallelism_value}. Повторы: {google_retry_count_value}.",
        )
    _report_progress(
        progress,
        15,
        (
            f"Параметры голоса ({engine}): скорость {_normalize_edge_rate(edge_rate)}, "
            f"тон {_normalize_edge_pitch(edge_pitch)}, громкость {_normalize_edge_volume(edge_volume)}."
        ),
    )

    input_text = str(text or "")
    synth_text = input_text
    normalizer_result_payload: dict[str, Any] | None = None
    normalizer_settings = parse_edge_text_normalizer_settings(edge_text_normalizer)
    normalizer_result = normalize_edge_text(input_text, normalizer_settings)
    synth_text = str(normalizer_result.text or "").strip()
    normalizer_result_payload = normalizer_result.as_payload()
    _report_progress(progress, 16, normalizer_result.summary)
    if not synth_text:
        raise RuntimeError("Текст стал пустым после нормализации.")

    dual_language_payload: dict[str, Any] | None = None
    dual_enabled = bool(isinstance(dual_language, dict) and dual_language.get("enabled"))
    if dual_enabled:
        _report_progress(progress, 17, "Режим двух языков включен.")

    try:
        if dual_enabled:
            out_path, dual_language_payload = _synthesize_to_file_dual_language(
                text=synth_text,
                engine=engine,
                voice=voice,
                file_stem=file_stem,
                primary_language=primary_language,
                dual_language=dict(dual_language or {}),
                edge_parallelism=edge_parallelism,
                google_parallelism=google_parallelism,
                google_retry_count=google_retry_count,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        else:
            out_path = _synthesize_to_file(
                text=synth_text,
                engine=engine,
                voice=voice,
                file_stem=file_stem,
                edge_parallelism=edge_parallelism,
                google_parallelism=google_parallelism,
                google_retry_count=google_retry_count,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        _report_progress(progress, 97, "Проверяю итоговый аудиофайл.")
        if not out_path.exists() or out_path.stat().st_size <= _MIN_AUDIO_BYTES:
            raise RuntimeError("Синтез завершился без валидного аудиофайла.")
    except Exception:
        _cleanup_partial_output_files(file_stem)
        raise

    file_name = out_path.name
    size_bytes = out_path.stat().st_size

    try:
        _LOGGER.info(
            "wintts user=%s engine=%s voice=%s file=%s chars_input=%s chars_synth=%s edge_parallelism=%s google_parallelism=%s google_retry_count=%s edge_rate=%s edge_pitch=%s edge_volume=%s normalizer=%s dual=%s",
            user,
            engine,
            voice,
            file_name,
            len(input_text),
            len(synth_text),
            edge_parallelism if engine == "Edge TTS" else "-",
            google_parallelism if engine == "Google" else "-",
            google_retry_count if engine == "Google" else "-",
            _normalize_edge_rate(edge_rate),
            _normalize_edge_pitch(edge_pitch),
            _normalize_edge_volume(edge_volume),
            normalizer_result_payload.get("preset") if isinstance(normalizer_result_payload, dict) else "-",
            "on" if dual_enabled else "off",
        )
    except Exception:
        pass

    edge_text_normalizer_payload = (
        normalizer_result_payload.get("settings")
        if isinstance(normalizer_result_payload, dict)
        else edge_text_normalizer_settings_payload(parse_edge_text_normalizer_settings(edge_text_normalizer))
    )

    return {
        "engine": engine,
        "voice": voice,
        "primary_language": _normalize_language_code(primary_language, fallback="und"),
        "filename": file_name,
        "path": out_path,
        "size_bytes": size_bytes,
        "text_input_length": len(input_text),
        "text_synth_length": len(synth_text),
        "edge_parallelism": edge_parallelism if engine == "Edge TTS" else None,
        "google_parallelism": google_parallelism if engine == "Google" else None,
        "google_retry_count": google_retry_count if engine == "Google" else None,
        "edge_rate": _normalize_edge_rate(edge_rate),
        "edge_volume": _normalize_edge_volume(edge_volume),
        "edge_pitch": _normalize_edge_pitch(edge_pitch),
        "edge_text_normalizer": edge_text_normalizer_payload,
        "edge_text_normalizer_result": normalizer_result_payload,
        "text_normalizer": edge_text_normalizer_payload,
        "text_normalizer_result": normalizer_result_payload,
        "dual_language": dual_language_payload,
    }

def _synthesis_task_payload(job_id: str, user_id: int) -> dict[str, Any] | None:
    payload = _WINTTS_SYNTHESIS.get_payload(job_id, user_id)
    if not payload:
        return None

    file_name = str(payload.get("filename") or "").strip()
    if file_name and _resolve_audio_path(file_name):
        payload["audio_url"] = url_for(f"{WinTTSView.__name__}.audio_file", file_name=file_name)
        payload["download_url"] = url_for(f"{WinTTSView.__name__}.download_file", file_name=file_name)
    else:
        payload["audio_url"] = ""
        payload["download_url"] = ""

    payload["ok"] = payload.get("status") != "error"
    payload["warnings"] = list(TTS_IMPORT_ERRORS)
    return payload


def _make_task_progress_callback(job_id: str) -> ProgressCallback:
    def _callback(percent: int, message: str, level: str = "info") -> None:
        _WINTTS_SYNTHESIS.append_log(job_id, percent, message, level=level, status="running")

    return _callback


def _run_synthesis_task(app: Any, job_id: str, text: str) -> None:
    try:
        with app.app_context():
            task = _WINTTS_SYNTHESIS.get(job_id)
            if not task:
                return

            try:
                init_tts_engines()
            except Exception as exc:
                diagnostics = _dependency_diagnostics_payload()
                _WINTTS_SYNTHESIS.fail(
                    job_id,
                    f"Ошибка инициализации синтеза: {type(exc).__name__}: {exc}",
                    diagnostics["lines"],
                )
                return

            progress = _make_task_progress_callback(job_id)
            _report_progress(progress, 2, "Фоновая задача запущена.")
            result = _perform_synthesis(
                text=text,
                engine=task.engine,
                voice=task.voice,
                edge_parallelism=task.edge_parallelism,
                google_parallelism=task.google_parallelism,
                google_retry_count=task.google_retry_count,
                edge_rate=task.edge_rate,
                edge_volume=task.edge_volume,
                edge_pitch=task.edge_pitch,
                edge_text_normalizer=task.edge_text_normalizer,
                primary_language=task.primary_language,
                dual_language=task.dual_language,
                user=task.username or "web",
                progress=progress,
            )
            _WINTTS_SYNTHESIS.finish(
                job_id,
                f"Синтез завершен. Файл {result['filename']} готов к прослушиванию.",
                str(result["filename"]),
                int(result["size_bytes"]),
                _dependency_diagnostics_lines(),
                synthesis_result=result,
            )
    except Exception as exc:
        task = _WINTTS_SYNTHESIS.get(job_id)
        engine = getattr(task, "engine", "")
        voice = getattr(task, "voice", "")
        _LOGGER.exception("wintts async synthesis failed job=%s engine=%s voice=%s", job_id, engine, voice)
        diagnostics = _dependency_diagnostics_payload()
        error_message = f"Ошибка синтеза: {type(exc).__name__}: {exc}"
        _WINTTS_SYNTHESIS.fail(job_id, error_message, diagnostics["lines"])


def _start_synthesis_task(app: Any, job_id: str, text: str) -> None:
    if not _WINTTS_SYNTHESIS.mark_started(job_id):
        return
    thread = threading.Thread(target=_run_synthesis_task, args=(app, job_id, text), daemon=True)
    thread.start()


def _synthesize_google(
    text: str,
    voice_label: str,
    out_path: str,
    parallelism: int | None = None,
    retry_count: int | None = None,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    if gTTS is None:
        raise RuntimeError("Google TTS недоступен")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Пустой текст для синтеза")

    voice_settings = GOOGLE_TTS_VOICE_MAP.get(voice_label) or {}
    language_code = _normalize_language_code(voice_settings.get("lang"), fallback=_GOOGLE_DEFAULT_LANG)
    tld = str(voice_settings.get("tld") or _GOOGLE_DEFAULT_TLD).strip().lower() or _GOOGLE_DEFAULT_TLD
    effective_parallelism = _normalize_google_parallelism(parallelism)
    effective_retry_count = _normalize_google_retry_count(retry_count)
    request_timeout = (_GOOGLE_REQUEST_CONNECT_TIMEOUT_SECONDS, _GOOGLE_REQUEST_READ_TIMEOUT_SECONDS)
    requested_parallelism = effective_parallelism

    if len(clean_text) >= _GOOGLE_SAFE_PARALLELISM_LONG_THRESHOLD:
        effective_parallelism = min(effective_parallelism, _GOOGLE_SAFE_PARALLELISM_LONG)
    elif len(clean_text) >= _GOOGLE_SAFE_PARALLELISM_MEDIUM_THRESHOLD:
        effective_parallelism = min(effective_parallelism, _GOOGLE_SAFE_PARALLELISM_MEDIUM)

    rate_percent = max(-100, min(0, _edge_option_to_int(edge_rate, "rate")))
    use_slow_mode = rate_percent <= _GOOGLE_SLOW_THRESHOLD
    pitch_percent = _edge_option_to_int(edge_pitch, "pitch")
    volume_percent = _edge_option_to_int(edge_volume, "volume")

    _report_progress(
        progress,
        20,
        f"Подключаю Google TTS: язык {language_code}, tld={tld}, "
        f"скорость {'медленно' if use_slow_mode else 'обычно'}.",
    )
    _report_progress(
        progress,
        21,
        f"Google TTS: потоки {effective_parallelism}, повторы при ошибках {effective_retry_count}, "
        f"timeout={request_timeout[0]:.0f}/{request_timeout[1]:.0f} c.",
    )
    if effective_parallelism != requested_parallelism:
        _report_progress(
            progress,
            21,
            f"Для стабильности на длинном тексте уменьшаю потоки Google TTS: {requested_parallelism} -> {effective_parallelism}.",
            "warning",
        )
    if pitch_percent or volume_percent:
        _report_progress(
            progress,
            22,
            "Google TTS не поддерживает прямую настройку тона и громкости. Эти параметры оставлены без изменения.",
            "warning",
        )

    def _audio_ready(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > _MIN_AUDIO_BYTES
        except Exception:
            return False

    def _has_speakable_content(chunk_text: str) -> bool:
        return bool(re.search(r"\w", str(chunk_text or ""), re.UNICODE))

    def _error_text(exc: Exception, max_len: int = 240) -> str:
        parts: list[str] = []
        seen: set[int] = set()
        current: Exception | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chunk = f"{type(current).__name__}: {current}"
            chunk = chunk.strip()
            if chunk and chunk not in parts:
                parts.append(chunk)
            next_exc = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
            if isinstance(next_exc, Exception):
                current = next_exc
            else:
                break
        return _short_text(" | ".join(parts), max_len=max_len)

    def _is_transient_google_error(exc: Exception) -> bool:
        text = _error_text(exc, max_len=480).lower()
        keywords = (
            "timeout",
            "timed out",
            "too many requests",
            "429",
            "503",
            "service unavailable",
            "connection",
            "failed to connect",
            "status code",
            "remote end closed",
            "reset by peer",
            "ssl",
            "proxy",
            "temporar",
            "network",
        )
        return any(item in text for item in keywords)

    def _is_google_rate_limited(exc: Exception) -> bool:
        text = _error_text(exc, max_len=480).lower()
        keywords = (
            "429",
            "too many requests",
            "rate limit",
            "quota",
            "sorry/index",
        )
        return any(item in text for item in keywords)

    def _google_rate_limit_hint() -> str:
        return (
            "Google TTS ограничил запросы (429 Too Many Requests). "
            "Обычно помогает пауза 15-60 минут, смена IP/сети или переход на Edge TTS."
        )

    def _is_retryable_google_error(exc: Exception) -> bool:
        text = _error_text(exc, max_len=360).lower()
        non_retryable = (
            "не поддерживает timeout",
            "not support timeout",
            "unsupported language",
            "language not supported",
            "invalid tld",
            "invalid language",
        )
        return not any(item in text for item in non_retryable)

    def _is_split_likely_helpful(exc: Exception) -> bool:
        if _is_transient_google_error(exc):
            return False
        text = _error_text(exc, max_len=360).lower()
        keywords = (
            "no text to send",
            "пуст",
            "empty",
            "too long",
            "request too large",
            "payload too large",
            "413",
        )
        return any(item in text for item in keywords)

    def _split_chunk(chunk_text: str, max_bytes: int) -> list[str]:
        parts = _split_text_utf8(chunk_text, max_bytes)
        if not parts:
            parts = [chunk_text]
        fixed: list[str] = []
        for part in parts:
            if len(part.encode("utf-8")) <= max_bytes:
                fixed.append(part)
                continue
            buf = ""
            for char in part:
                candidate = buf + char
                if len(candidate.encode("utf-8")) <= max_bytes:
                    buf = candidate
                else:
                    if buf:
                        fixed.append(buf)
                    buf = char
            if buf:
                fixed.append(buf)
        filtered = [item for item in fixed if item and item.strip()]
        if not filtered:
            return []
        normalized: list[str] = []
        pending_prefix = ""
        for item in filtered:
            current = f"{pending_prefix}{item}" if pending_prefix else item
            pending_prefix = ""
            if _has_speakable_content(current):
                normalized.append(current)
                continue
            if normalized:
                candidate = normalized[-1] + current
                if len(candidate.encode("utf-8")) <= max_bytes:
                    normalized[-1] = candidate
                else:
                    normalized[-1] = f"{normalized[-1]} {current}"
            else:
                pending_prefix = current

        if pending_prefix:
            if normalized:
                candidate = normalized[-1] + pending_prefix
                if len(candidate.encode("utf-8")) <= max_bytes:
                    normalized[-1] = candidate
                else:
                    normalized[-1] = f"{normalized[-1]} {pending_prefix}"
            else:
                normalized.append(pending_prefix)

        return [item for item in normalized if item and item.strip()]

    _report_progress(progress, 55, "Google TTS формирует MP3-файл.")

    tld_attempts: list[str] = []
    for candidate in (tld, _GOOGLE_DEFAULT_TLD):
        candidate_clean = str(candidate or "").strip().lower() or _GOOGLE_DEFAULT_TLD
        if candidate_clean not in tld_attempts:
            tld_attempts.append(candidate_clean)

    def _new_gtts_instance(chunk_text: str, candidate_tld: str) -> Any:
        base_kwargs = {
            "text": chunk_text,
            "lang": language_code,
            "slow": use_slow_mode,
            # Language is validated when building the catalog; skip extra checks per chunk.
            "lang_check": False,
        }
        constructor_options = (
            {"tld": candidate_tld, "timeout": request_timeout},
            {"timeout": request_timeout},
        )
        last_type_error: Exception | None = None
        for option in constructor_options:
            kwargs = dict(base_kwargs)
            kwargs.update(option)
            try:
                return gTTS(**kwargs)
            except TypeError as exc:
                last_type_error = exc
                continue
        if last_type_error is not None:
            raise RuntimeError(
                f"Текущая версия gTTS не поддерживает timeout (нужен для защиты от зависаний): "
                f"{type(last_type_error).__name__}: {last_type_error}"
            ) from last_type_error
        raise RuntimeError("Не удалось подготовить экземпляр gTTS.")

    def _synthesize_google_chunk_once(chunk_text: str, target_path: str, chunk_label: str) -> None:
        last_exc: Exception | None = None
        for index, candidate_tld in enumerate(tld_attempts):
            try:
                _remove_file_safely(target_path)
                tts = _new_gtts_instance(chunk_text, candidate_tld)
                tts.save(target_path)
                if _audio_ready(target_path):
                    return
                raise RuntimeError("Google TTS вернул пустой или слишком маленький MP3")
            except Exception as exc:
                last_exc = exc
                _remove_file_safely(target_path)
                if index + 1 < len(tld_attempts):
                    _report_progress(
                        progress,
                        58,
                        f"{chunk_label}: Google TTS отклонил tld={candidate_tld}. Пробую запасной вариант.",
                        "warning",
                    )
        if last_exc is not None:
            raise RuntimeError(
                f"Google TTS не смог обработать запрос: {type(last_exc).__name__}: {last_exc}"
            ) from last_exc
        raise RuntimeError("Google TTS не смог обработать запрос")

    def _synthesize_google_chunk(chunk_text: str, target_path: str, chunk_label: str) -> None:
        total_attempts = max(1, effective_retry_count + 1)
        last_error: Exception | None = None
        attempt = 1
        while attempt <= total_attempts:
            try:
                _synthesize_google_chunk_once(chunk_text, target_path, chunk_label)
                return
            except Exception as exc:
                last_error = exc
                is_rate_limited = _is_google_rate_limited(exc)
                if not _is_retryable_google_error(exc):
                    break
                if attempt >= total_attempts:
                    break
                delay = min(
                    _GOOGLE_RETRY_BACKOFF_MAX_SECONDS,
                    _GOOGLE_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                )
                if is_rate_limited:
                    delay = max(delay, min(20.0, 4.5 * attempt))
                elif _is_transient_google_error(exc):
                    delay = min(_GOOGLE_RETRY_BACKOFF_MAX_SECONDS, (delay * 1.8) + 0.35)
                delay += random.uniform(0.0, _GOOGLE_RETRY_BACKOFF_JITTER_SECONDS)
                reason_text = _error_text(exc, max_len=150)
                _report_progress(
                    progress,
                    60,
                    f"{chunk_label}: ошибка ({reason_text}). Повтор {attempt}/{total_attempts - 1} через {delay:.1f} c.",
                    "warning",
                )
                time.sleep(delay)
                attempt += 1
                continue
            attempt += 1
        if last_error is not None:
            details = _error_text(last_error, max_len=180)
            if _is_google_rate_limited(last_error):
                details = f"{details}. {_google_rate_limit_hint()}"
            raise RuntimeError(
                f"{chunk_label}: ошибка синтеза Google TTS ({details})"
            ) from last_error
        raise RuntimeError(f"{chunk_label}: ошибка синтеза Google TTS")

    top_level_chunks = _split_chunk(clean_text, _GOOGLE_CHUNK_LIMITS[0])
    if not top_level_chunks:
        top_level_chunks = [clean_text]
    if not any(_has_speakable_content(item) for item in top_level_chunks):
        raise ValueError("Google TTS: в тексте не найдено озвучиваемых символов.")

    if len(top_level_chunks) > 1:
        _report_progress(progress, 56, f"Google TTS: текст разбит на {len(top_level_chunks)} частей.")

    effective_parallelism = max(1, min(effective_parallelism, len(top_level_chunks)))
    if len(top_level_chunks) > 1:
        _report_progress(progress, 57, f"Google TTS: запускаю до {effective_parallelism} потоков.")

    def _build_part(index: int, chunk_text: str, total_chunks: int) -> str:
        part_path = f"{out_path}.__part{index:03d}.mp3"
        _remove_file_safely(part_path)
        chunk_label = f"Часть {index + 1}/{total_chunks}"
        if not _has_speakable_content(chunk_text):
            _report_progress(progress, 58, f"{chunk_label}: пропускаю непроизносимый фрагмент.", "warning")
            return ""

        try:
            _synthesize_google_chunk(chunk_text, part_path, chunk_label)
        except Exception as base_exc:
            last_error: Exception = base_exc
            recovered = False
            if not _is_split_likely_helpful(base_exc):
                _remove_file_safely(part_path)
                raise
            for split_limit in _GOOGLE_CHUNK_LIMITS[1:]:
                subchunks = _split_chunk(chunk_text, split_limit)
                if len(subchunks) <= 1:
                    continue
                _report_progress(
                    progress,
                    60,
                    f"{chunk_label}: делю на {len(subchunks)} подпакетов, чтобы обойти ошибку Google TTS.",
                    "warning",
                )
                subparts: list[str] = []
                try:
                    for sub_index, subchunk in enumerate(subchunks):
                        sub_path = f"{part_path}.__sub{sub_index:03d}.mp3"
                        _remove_file_safely(sub_path)
                        subparts.append(sub_path)
                        sub_label = f"{chunk_label}, подпакет {sub_index + 1}/{len(subchunks)}"
                        _synthesize_google_chunk(subchunk, sub_path, sub_label)

                    _remove_file_safely(part_path)
                    if not _ffmpeg_concat_mp3(subparts, part_path):
                        if not _concat_binary_parts(subparts, part_path):
                            raise RuntimeError("Не удалось склеить подпакеты Google TTS")
                    if _audio_ready(part_path):
                        recovered = True
                        break
                    raise RuntimeError("Google TTS сформировал пустой MP3 после склейки подпакетов")
                except Exception as split_exc:
                    last_error = split_exc
                finally:
                    for subpart in subparts:
                        _remove_file_safely(subpart)
            if not recovered:
                _remove_file_safely(part_path)
                details = _error_text(last_error, max_len=180)
                if _is_google_rate_limited(last_error):
                    details = f"{details}. {_google_rate_limit_hint()}"
                raise RuntimeError(
                    f"{chunk_label}: ошибка синтеза Google TTS ({details})"
                ) from last_error

        if not _audio_ready(part_path):
            _remove_file_safely(part_path)
            raise RuntimeError(f"{chunk_label}: Google TTS не сформировал аудиофайл")

        return part_path

    parts: list[str] = []
    results: list[str | None] = [None] * len(top_level_chunks)
    try:
        total_chunks = len(top_level_chunks)
        pending_indices = list(range(total_chunks))
        stage_parallelism_values: list[int] = []
        for candidate in (effective_parallelism, max(1, min(2, effective_parallelism)), 1):
            if candidate not in stage_parallelism_values:
                stage_parallelism_values.append(candidate)

        for stage_index, stage_parallelism in enumerate(stage_parallelism_values, start=1):
            if not pending_indices:
                break

            if stage_index > 1:
                _report_progress(
                    progress,
                    66,
                    f"Google TTS: перезапуск оставшихся частей на этапе {stage_index} с параллельностью x{stage_parallelism}.",
                    "warning",
                )

            stage_failures: dict[int, Exception] = {}
            if stage_parallelism <= 1:
                for index in pending_indices:
                    try:
                        part_path = _build_part(index, top_level_chunks[index], total_chunks)
                    except Exception as exc:
                        stage_failures[index] = exc
                        continue
                    results[index] = part_path
                    resolved_count = sum(1 for item in results if item is not None)
                    percent = 58 + int((resolved_count / max(1, total_chunks)) * 27)
                    if part_path:
                        _report_progress(progress, percent, f"Часть {index + 1}/{total_chunks} готова.")
                    else:
                        _report_progress(progress, percent, f"Часть {index + 1}/{total_chunks} пропущена (пустой фрагмент).", "warning")
            else:
                with ThreadPoolExecutor(max_workers=stage_parallelism, thread_name_prefix="wintts-gtts") as executor:
                    future_to_index = {
                        executor.submit(_build_part, index, top_level_chunks[index], total_chunks): index
                        for index in pending_indices
                    }
                    for future in as_completed(future_to_index):
                        index = future_to_index[future]
                        try:
                            part_path = future.result()
                        except Exception as exc:
                            stage_failures[index] = exc
                            continue
                        results[index] = part_path
                        resolved_count = sum(1 for item in results if item is not None)
                        percent = 58 + int((resolved_count / max(1, total_chunks)) * 27)
                        if part_path:
                            _report_progress(progress, percent, f"Часть {index + 1}/{total_chunks} готова.")
                        else:
                            _report_progress(progress, percent, f"Часть {index + 1}/{total_chunks} пропущена (пустой фрагмент).", "warning")

            pending_indices = sorted(stage_failures.keys())
            if pending_indices and stage_index < len(stage_parallelism_values):
                first_error = stage_failures[pending_indices[0]]
                cooldown = _GOOGLE_STAGE_COOLDOWN_SECONDS
                if _is_google_rate_limited(first_error):
                    cooldown = max(cooldown, _GOOGLE_STAGE_COOLDOWN_RATE_LIMIT_SECONDS)
                elif _is_transient_google_error(first_error):
                    cooldown = max(cooldown, _GOOGLE_STAGE_COOLDOWN_TRANSIENT_SECONDS)
                _report_progress(
                    progress,
                    66,
                    (
                        f"Google TTS: {len(pending_indices)} частей завершились ошибкой на этапе {stage_index} "
                        f"(x{stage_parallelism}). Снижаю параллельность, пауза {cooldown:.0f} c. "
                        f"Причина: {_error_text(first_error, max_len=140)}."
                    ),
                    "warning",
                )
                time.sleep(cooldown)
                continue
            if pending_indices:
                first_error = stage_failures[pending_indices[0]]
                details = _error_text(first_error, max_len=180)
                if _is_google_rate_limited(first_error):
                    details = f"{details}. {_google_rate_limit_hint()}"
                raise RuntimeError(
                    f"Google TTS: не удалось синтезировать {len(pending_indices)} частей. "
                    f"Последняя ошибка: {details}"
                ) from first_error

        parts = [part for part in results if part]
        if not parts:
            raise RuntimeError("Google TTS не смог сформировать ни одной валидной части.")

        _report_progress(progress, 88, "Google TTS завершил генерацию частей. Собираю итоговый MP3.")
        _remove_file_safely(out_path)
        if len(parts) == 1:
            shutil.move(parts[0], out_path)
        else:
            if not _ffmpeg_concat_mp3(parts, out_path):
                if not _concat_binary_parts(parts, out_path):
                    raise RuntimeError("Не удалось собрать итоговый MP3 из частей Google TTS")
    finally:
        for part in parts:
            _remove_file_safely(part)
        for index in range(len(top_level_chunks)):
            _remove_file_safely(f"{out_path}.__part{index:03d}.mp3")

    _report_progress(progress, 90, "Google TTS завершил выгрузку. Проверяю файл.")
    if not _audio_ready(out_path):
        _remove_file_safely(out_path)
        raise RuntimeError("Google TTS не сформировал аудиофайл")
    return out_path

def _synthesize_pyttsx3(
    text: str,
    voice_label: str,
    out_path: str,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    if pyttsx3 is None:
        raise RuntimeError("pyttsx3 недоступен")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Пустой текст для синтеза")

    def _audio_ready(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > _MIN_AUDIO_BYTES
        except Exception:
            return False

    def _split_chunk(chunk_text: str, max_bytes: int) -> list[str]:
        parts = _split_text_utf8(chunk_text, max_bytes)
        if not parts:
            parts = [chunk_text]
        fixed: list[str] = []
        for part in parts:
            if len(part.encode("utf-8")) <= max_bytes:
                fixed.append(part)
                continue
            buf = ""
            for char in part:
                candidate = buf + char
                if len(candidate.encode("utf-8")) <= max_bytes:
                    buf = candidate
                else:
                    if buf:
                        fixed.append(buf)
                    buf = char
            if buf:
                fixed.append(buf)
        return [item for item in fixed if item and item.strip()]

    _report_progress(progress, 18, "Инициализирую локальный движок pyttsx3.")
    tts_engine = pyttsx3.init()
    try:
        voice_id = PYTTSX3_VOICE_MAP.get(voice_label)
        if voice_id:
            try:
                tts_engine.setProperty("voice", voice_id)
            except Exception:
                pass

        rate_percent = _edge_option_to_int(edge_rate, "rate")
        volume_percent = _edge_option_to_int(edge_volume, "volume")
        pitch_percent = _edge_option_to_int(edge_pitch, "pitch")

        base_rate = _PYTTSX3_RATE_DEFAULT
        try:
            base_rate = int(float(tts_engine.getProperty("rate") or _PYTTSX3_RATE_DEFAULT))
        except Exception:
            base_rate = _PYTTSX3_RATE_DEFAULT
        target_rate = int(round(base_rate * (1.0 + rate_percent / 100.0)))
        target_rate = max(_PYTTSX3_RATE_MIN, min(_PYTTSX3_RATE_MAX, target_rate))
        try:
            tts_engine.setProperty("rate", target_rate)
        except Exception:
            pass

        base_volume = 1.0
        try:
            base_volume = float(tts_engine.getProperty("volume") or 1.0)
        except Exception:
            base_volume = 1.0
        base_volume = max(0.0, min(1.0, base_volume))
        if volume_percent < 0:
            target_volume = base_volume * max(0.0, 1.0 + (volume_percent / 100.0))
        elif volume_percent > 0:
            target_volume = base_volume + (1.0 - base_volume) * (volume_percent / 100.0)
        else:
            target_volume = base_volume
        target_volume = max(0.0, min(1.0, float(target_volume)))
        try:
            tts_engine.setProperty("volume", target_volume)
        except Exception:
            pass

        if pitch_percent:
            _report_progress(
                progress,
                26,
                "pyttsx3 обычно не поддерживает настройку тона на Windows (SAPI5). Параметр тона пропущен.",
                "warning",
            )
        _report_progress(
            progress,
            30,
            f"Параметры pyttsx3: скорость {rate_percent:+d}% (rate={target_rate}), "
            f"громкость {volume_percent:+d}% (volume={target_volume:.2f}).",
        )

        def _configure_pyttsx3_chunk_engine(engine_obj: Any) -> None:
            if voice_id:
                try:
                    engine_obj.setProperty("voice", voice_id)
                except Exception:
                    pass
            try:
                engine_obj.setProperty("rate", target_rate)
            except Exception:
                pass
            try:
                engine_obj.setProperty("volume", target_volume)
            except Exception:
                pass

        def _synthesize_pyttsx3_chunk(chunk_text: str, target_path: str) -> None:
            chunk_engine = pyttsx3.init()
            try:
                _configure_pyttsx3_chunk_engine(chunk_engine)
                _remove_file_safely(target_path)
                chunk_engine.save_to_file(chunk_text, target_path)
                chunk_engine.runAndWait()
            finally:
                try:
                    chunk_engine.stop()
                except Exception:
                    pass
            if _audio_ready(target_path):
                return
            _remove_file_safely(target_path)
            raise RuntimeError("pyttsx3 не сформировал WAV-часть")

        top_level_chunks = _split_chunk(clean_text, _PYTTSX3_CHUNK_LIMITS[0])
        if not top_level_chunks:
            top_level_chunks = [clean_text]

        if len(top_level_chunks) > 1:
            _report_progress(progress, 42, f"pyttsx3: текст разбит на {len(top_level_chunks)} частей.")
        else:
            _report_progress(progress, 42, "Формирую аудиофайл через pyttsx3.")

        parts: list[str] = []
        try:
            for index, chunk_text in enumerate(top_level_chunks):
                part_path = f"{out_path}.__part{index:03d}.wav"
                _remove_file_safely(part_path)
                chunk_label = f"Часть {index + 1}/{len(top_level_chunks)}"
                try:
                    _synthesize_pyttsx3_chunk(chunk_text, part_path)
                except Exception as base_exc:
                    last_error: Exception = base_exc
                    recovered = False
                    for split_limit in _PYTTSX3_CHUNK_LIMITS[1:]:
                        subchunks = _split_chunk(chunk_text, split_limit)
                        if len(subchunks) <= 1:
                            continue
                        _report_progress(
                            progress,
                            46,
                            f"{chunk_label}: делю на {len(subchunks)} подпакетов, чтобы обойти ошибку pyttsx3.",
                            "warning",
                        )
                        subparts: list[str] = []
                        try:
                            for sub_index, subchunk in enumerate(subchunks):
                                sub_path = f"{part_path}.__sub{sub_index:03d}.wav"
                                _remove_file_safely(sub_path)
                                subparts.append(sub_path)
                                _synthesize_pyttsx3_chunk(subchunk, sub_path)
                            _remove_file_safely(part_path)
                            if not _concat_wav_parts(subparts, part_path):
                                raise RuntimeError("Не удалось склеить подпакеты pyttsx3")
                            if _audio_ready(part_path):
                                recovered = True
                                break
                            raise RuntimeError("pyttsx3 сформировал пустой WAV после склейки подпакетов")
                        except Exception as split_exc:
                            last_error = split_exc
                        finally:
                            for subpart in subparts:
                                _remove_file_safely(subpart)
                    if not recovered:
                        _remove_file_safely(part_path)
                        raise RuntimeError(
                            f"{chunk_label}: ошибка синтеза pyttsx3 ({type(last_error).__name__}: {last_error})"
                        ) from last_error

                if not _audio_ready(part_path):
                    _remove_file_safely(part_path)
                    raise RuntimeError(f"{chunk_label}: pyttsx3 не сформировал аудиофайл")

                parts.append(part_path)
                percent = 48 + int(((index + 1) / max(1, len(top_level_chunks))) * 38)
                _report_progress(progress, percent, f"{chunk_label} готова.")

            _report_progress(progress, 88, "pyttsx3 завершил генерацию частей. Собираю итоговый WAV.")
            _remove_file_safely(out_path)
            if len(parts) == 1:
                shutil.move(parts[0], out_path)
            else:
                if not _concat_wav_parts(parts, out_path):
                    raise RuntimeError("Не удалось собрать итоговый WAV из частей pyttsx3")
        finally:
            for part in parts:
                _remove_file_safely(part)
    finally:
        try:
            tts_engine.stop()
        except Exception:
            pass

    _report_progress(progress, 90, "pyttsx3 завершил синтез. Проверяю WAV-файл.")
    if not _audio_ready(out_path):
        _remove_file_safely(out_path)
        raise RuntimeError("pyttsx3 не сформировал аудиофайл")
    return out_path


def _synthesize_edge(
    text: str,
    voice_label: str,
    out_path: str,
    parallelism: int | None = None,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    voice_id = EDGE_TTS_VOICE_MAP.get(voice_label)
    if not voice_id:
        raise RuntimeError("Для Edge TTS не выбран голос")
    _run_async(
        synthesize_edge_tts(
            text=text,
            voice_id=voice_id,
            file_path=out_path,
            parallelism=parallelism,
            edge_rate=_normalize_edge_rate(edge_rate),
            edge_volume=_normalize_edge_volume(edge_volume),
            edge_pitch=_normalize_edge_pitch(edge_pitch),
            progress=progress,
        )
    )
    _report_progress(progress, 95, "Edge TTS завершил обработку. Проверяю итоговый MP3.")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= _MIN_AUDIO_BYTES:
        _remove_file_safely(out_path)
        raise RuntimeError("Edge TTS не сформировал аудиофайл")
    return out_path


def _synthesize_rhvoice(
    text: str,
    voice_label: str,
    out_path: str,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    voice_profile = str(RHVOICE_TTS_VOICE_MAP.get(voice_label) or voice_label or "").strip()
    if not voice_profile:
        raise RuntimeError("Для RHVoice не выбран голос.")

    addon_state = RHVOICE_ADDON_STATE if RHVOICE_ADDON_STATE else _collect_addon_runtime_state()
    if not bool(addon_state.get("installed")):
        raise RuntimeError("RHVoice-addon не установлен. Нажмите «Установить RHVoice-addon».")

    rate_value = _edge_option_to_int(edge_rate, "rate")
    pitch_value = _edge_option_to_int(edge_pitch, "pitch")
    volume_value = _edge_option_to_int(edge_volume, "volume")

    _report_progress(progress, 22, "Инициализирую RHVoice.")
    _report_progress(
        progress,
        28,
        f"Параметры RHVoice: скорость {rate_value:+d}%, тон {pitch_value:+d}Hz, громкость {volume_value:+d}%.",
    )

    addon_runtime.synthesize_rhvoice_to_file(
        text=text,
        voice_profile=voice_profile,
        out_path=out_path,
        edge_rate=rate_value,
        edge_pitch=pitch_value,
        edge_volume=volume_value,
        state=addon_state,
    )

    _report_progress(progress, 95, "RHVoice завершил обработку. Проверяю итоговый WAV.")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) <= _MIN_AUDIO_BYTES:
        _remove_file_safely(out_path)
        raise RuntimeError("RHVoice не сформировал аудиофайл.")
    return out_path


def _synthesize_to_file(
    text: str,
    engine: str,
    voice: str,
    file_stem: Path,
    edge_parallelism: int | None = None,
    google_parallelism: int | None = None,
    google_retry_count: int | None = None,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    if engine == "Google":
        out_path = str(file_stem.with_suffix(".mp3"))
        return Path(
            _synthesize_google(
                text=text,
                voice_label=voice,
                out_path=out_path,
                parallelism=google_parallelism,
                retry_count=google_retry_count,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        )
    if engine == "pyx3":
        out_path = str(file_stem.with_suffix(".wav"))
        return Path(
            _synthesize_pyttsx3(
                text=text,
                voice_label=voice,
                out_path=out_path,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        )
    if engine == "Edge TTS":
        out_path = str(file_stem.with_suffix(".mp3"))
        return Path(
            _synthesize_edge(
                text=text,
                voice_label=voice,
                out_path=out_path,
                parallelism=edge_parallelism,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        )
    if engine == "RHVoice":
        out_path = str(file_stem.with_suffix(".wav"))
        return Path(
            _synthesize_rhvoice(
                text=text,
                voice_label=voice,
                out_path=out_path,
                edge_rate=edge_rate,
                edge_volume=edge_volume,
                edge_pitch=edge_pitch,
                progress=progress,
            )
        )
    raise RuntimeError("Неизвестный TTS-движок")


def _synthesize_to_file_dual_language(
    text: str,
    engine: str,
    voice: str,
    file_stem: Path,
    primary_language: str,
    dual_language: dict[str, Any],
    edge_parallelism: int | None = None,
    google_parallelism: int | None = None,
    google_retry_count: int | None = None,
    edge_rate: str | None = None,
    edge_volume: str | None = None,
    edge_pitch: str | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[Path, dict[str, Any]]:
    dual_payload = dict(dual_language or {})
    secondary_voice = str(dual_payload.get("secondary_voice") or "").strip()
    secondary_language = _normalize_language_code(dual_payload.get("secondary_language"), fallback="und")
    secondary_rate = _normalize_edge_rate(dual_payload.get("secondary_edge_rate"))
    secondary_pitch = _normalize_edge_pitch(dual_payload.get("secondary_edge_pitch"))
    secondary_volume = _normalize_edge_volume(dual_payload.get("secondary_edge_volume"))
    pause_mode = _normalize_dual_pause_mode(dual_payload.get("pause_mode"))
    pause_ms = _normalize_dual_pause_ms(dual_payload.get("pause_ms"))

    if not secondary_voice:
        raise RuntimeError("Для режима двух языков требуется второй голос.")

    if engine == "Google":
        secondary_rate = _normalize_edge_rate(max(-100, min(0, _edge_option_to_int(secondary_rate, "rate"))))
        secondary_pitch = _normalize_edge_pitch(0)
        secondary_volume = _normalize_edge_volume(0)

    routing_plan = build_dual_language_routing_plan(
        text=text,
        enabled=True,
        primary_language=primary_language,
        secondary_language=secondary_language,
    )
    routing_payload = routing_plan.as_payload()
    _report_progress(progress, 16, routing_plan.summary)

    result_payload: dict[str, Any] = {
        "enabled": True,
        "active": bool(routing_plan.active),
        "primary_language": routing_plan.primary_language,
        "secondary_language": routing_plan.secondary_language,
        "secondary_voice": secondary_voice,
        "secondary_edge_rate": secondary_rate,
        "secondary_edge_pitch": secondary_pitch,
        "secondary_edge_volume": secondary_volume,
        "pause_mode": pause_mode,
        "pause_ms": pause_ms,
        "pause_normalization": {
            "mode": pause_mode,
            "requested_ms": pause_ms,
            "applied": False,
            "reason": "not_started",
        },
        "routing": routing_payload,
    }

    if not routing_plan.active:
        result_payload["pause_normalization"] = {
            "mode": pause_mode,
            "requested_ms": pause_ms,
            "applied": False,
            "reason": "routing_inactive",
        }
        path = _synthesize_to_file(
            text=text,
            engine=engine,
            voice=voice,
            file_stem=file_stem,
            edge_parallelism=edge_parallelism,
            google_parallelism=google_parallelism,
            google_retry_count=google_retry_count,
            edge_rate=edge_rate,
            edge_volume=edge_volume,
            edge_pitch=edge_pitch,
            progress=progress,
        )
        return path, result_payload

    out_suffix = ".wav" if engine in {"pyx3", "RHVoice"} else ".mp3"
    out_path = str(file_stem.with_suffix(out_suffix))
    parts: list[str] = []
    rendered_segments: list[dict[str, Any]] = []

    def _audio_ready(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) > _MIN_AUDIO_BYTES
        except Exception:
            return False

    def _scaled_progress(segment_index: int, total: int) -> ProgressCallback:
        start = 18 + int(((segment_index - 1) / max(1, total)) * 66)
        end = 18 + int((segment_index / max(1, total)) * 66)
        start = max(18, min(92, start))
        end = max(start, min(95, end))

        def _callback(percent: int, message: str, level: str = "info") -> None:
            clamped = max(0, min(100, int(percent)))
            scaled = start + int((clamped / 100.0) * (end - start))
            _report_progress(progress, scaled, f"Сегмент {segment_index}/{total}: {message}", level)

        return _callback

    total_segments = len(routing_plan.segments)
    try:
        for index, segment in enumerate(routing_plan.segments, start=1):
            segment_text_raw = str(segment.text or "")
            if not segment_text_raw.strip():
                continue
            segment_text = segment_text_raw
            role = str(segment.role or "primary").strip().lower()
            if role == "secondary":
                segment_voice = secondary_voice
                segment_rate = secondary_rate
                segment_pitch = secondary_pitch
                segment_volume = secondary_volume
            else:
                segment_voice = voice
                segment_rate = _normalize_edge_rate(edge_rate)
                segment_pitch = _normalize_edge_pitch(edge_pitch)
                segment_volume = _normalize_edge_volume(edge_volume)

            part_path = f"{out_path}.__segment{index:03d}{out_suffix}"
            _remove_file_safely(part_path)
            segment_progress = _scaled_progress(index, total_segments)
            _report_progress(
                progress,
                max(18, min(92, 18 + int(((index - 1) / max(1, total_segments)) * 66))),
                f"Подготовка сегмента {index}/{total_segments}: {role}, язык {segment.language}.",
            )

            if engine == "Google":
                _synthesize_google(
                    text=segment_text,
                    voice_label=segment_voice,
                    out_path=part_path,
                    parallelism=google_parallelism,
                    retry_count=google_retry_count,
                    edge_rate=segment_rate,
                    edge_volume=segment_volume,
                    edge_pitch=segment_pitch,
                    progress=segment_progress,
                )
            elif engine == "pyx3":
                _synthesize_pyttsx3(
                    text=segment_text,
                    voice_label=segment_voice,
                    out_path=part_path,
                    edge_rate=segment_rate,
                    edge_volume=segment_volume,
                    edge_pitch=segment_pitch,
                    progress=segment_progress,
                )
            elif engine == "Edge TTS":
                _synthesize_edge(
                    text=segment_text,
                    voice_label=segment_voice,
                    out_path=part_path,
                    parallelism=edge_parallelism,
                    edge_rate=segment_rate,
                    edge_volume=segment_volume,
                    edge_pitch=segment_pitch,
                    progress=segment_progress,
                )
            elif engine == "RHVoice":
                _synthesize_rhvoice(
                    text=segment_text,
                    voice_label=segment_voice,
                    out_path=part_path,
                    edge_rate=segment_rate,
                    edge_volume=segment_volume,
                    edge_pitch=segment_pitch,
                    progress=segment_progress,
                )
            else:
                raise RuntimeError("Неизвестный TTS-движок в режиме двух языков.")

            if not _audio_ready(part_path):
                raise RuntimeError(f"Сегмент {index}/{total_segments} в режиме двух языков не сформировал аудио.")
            parts.append(part_path)
            rendered_segments.append(
                {
                    "text": segment_text,
                    "role": role,
                    "language": _normalize_language_code(segment.language, fallback="und"),
                }
            )

        if not parts:
            raise RuntimeError("Режим двух языков не сформировал ни одной аудио-части.")

        _report_progress(progress, 93, "Собираю итоговый файл из сегментов двух языков.")
        _remove_file_safely(out_path)
        pause_payload: dict[str, Any] = {
            "mode": pause_mode,
            "requested_ms": pause_ms,
            "applied": False,
            "reason": "not_applied",
        }
        if len(parts) == 1:
            shutil.move(parts[0], out_path)
            pause_payload["reason"] = "single_segment"
        else:
            normalized_ok = False
            if pause_mode != "off":
                normalized_ok, pause_payload = _concat_dual_parts_with_pause(
                    parts=parts,
                    out_path=out_path,
                    out_suffix=out_suffix,
                    rendered_segments=rendered_segments,
                    pause_mode=pause_mode,
                    pause_ms=pause_ms,
                )
                if normalized_ok:
                    _report_progress(progress, 94, "Нормализация пауз между сегментами выполнена.")
                else:
                    reason = str(pause_payload.get("reason") or "merge_failed")
                    _report_progress(
                        progress,
                        94,
                        f"Нормализация пауз не применена ({reason}). Использую стандартную склейку.",
                        "warning",
                    )
            else:
                pause_payload = {
                    "mode": pause_mode,
                    "requested_ms": pause_ms,
                    "applied": False,
                    "reason": "disabled",
                }

            if not normalized_ok:
                if out_suffix == ".wav":
                    if not _concat_wav_parts(parts, out_path):
                        raise RuntimeError("Не удалось объединить WAV-части в режиме двух языков.")
                else:
                    if not _ffmpeg_concat_mp3(parts, out_path):
                        if not _concat_binary_parts(parts, out_path):
                            raise RuntimeError("Не удалось объединить MP3-части в режиме двух языков.")
        result_payload["pause_normalization"] = dict(pause_payload)
    finally:
        for part in parts:
            _remove_file_safely(part)
        for index in range(1, total_segments + 1):
            _remove_file_safely(f"{out_path}.__segment{index:03d}{out_suffix}")

    if not _audio_ready(out_path):
        _remove_file_safely(out_path)
        raise RuntimeError("Синтез в режиме двух языков сформировал пустой аудиофайл.")
    return Path(out_path), result_payload


def _install_tts_dependencies() -> tuple[bool, str, list[str], dict[str, Any]]:
    if not _can_install_tts_dependencies():
        state = _collect_addon_runtime_state()
        return False, "Не найден доступный Python или путь записи для RHVoice-addon.", [], state

    result = addon_runtime.ensure_addon_environment(
        base_dir=str(_runtime_base_dir()),
        compiled_runtime=_is_compiled_runtime(),
    )
    state = dict(result.get("state") or _collect_addon_runtime_state())
    ok = bool(result.get("ok"))
    message = str(result.get("message") or ("RHVoice-addon установлен." if ok else "Не удалось установить RHVoice-addon."))
    details = [str(item or "") for item in (result.get("details") or []) if str(item or "").strip()]
    return ok, message, details, state


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
            synth_start_url=url_for(f"{self.__class__.__name__}.synthesize_start"),
            synth_status_url_template=url_for(f"{self.__class__.__name__}.synthesize_status", job_id="__JOB_ID__"),
            import_url=url_for(f"{self.__class__.__name__}.import_text"),
            normalize_preview_url=url_for(f"{self.__class__.__name__}.normalize_preview"),
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
            init_tts_engines(force=True)
            _cleanup_generated_files()
            diagnostics = _dependency_diagnostics_payload()
            addon_state = _collect_addon_runtime_state()
            return jsonify(
                {
                    "ok": True,
                    "engines": ENGINE_OPTIONS,
                    "default_engine": _default_engine_name(),
                    "voices": VOICE_OPTIONS,
                    "warnings": TTS_IMPORT_ERRORS,
                    "ffmpeg_available": bool(_FFMPEG_PATH),
                    "ffmpeg_path": _FFMPEG_PATH or "",
                    "can_install": bool(addon_state.get("can_install")),
                    "runtime_compiled": _is_compiled_runtime(),
                    "addon_runtime": addon_state,
                    "max_text_len": _get_ui_max_text_len(),
                    "max_text_len_by_engine": _get_text_limits_payload(),
                    "edge_parallelism": _get_edge_parallelism_payload(),
                    "google_parallelism": _get_google_parallelism_payload(),
                    "google_retry_count": _get_google_retry_count_payload(),
                    "edge_options": _get_edge_options_payload(),
                    "dual_pause": _get_dual_pause_payload(),
                    "edge_text_normalizer": _get_edge_text_normalizer_payload(),
                    "text_normalizer": _get_edge_text_normalizer_payload(),
                    "edge_voice_catalog": _get_edge_voice_catalog_payload(),
                    "engine_options": _get_engine_options_payload(),
                    "voice_catalog": _get_voice_catalog_payload(),
                    "supported_import_extensions": _supported_import_extensions(),
                    "max_upload_bytes": _UPLOAD_MAX_BYTES,
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
            addon_state = _collect_addon_runtime_state()
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Ошибка инициализации конфигурации: {type(exc).__name__}: {exc}",
                        "engines": ENGINE_OPTIONS,
                        "default_engine": _default_engine_name(),
                        "voices": VOICE_OPTIONS,
                        "warnings": TTS_IMPORT_ERRORS,
                        "ffmpeg_available": bool(_FFMPEG_PATH),
                        "ffmpeg_path": _FFMPEG_PATH or "",
                        "can_install": bool(addon_state.get("can_install")),
                        "runtime_compiled": _is_compiled_runtime(),
                        "addon_runtime": addon_state,
                        "max_text_len": _get_ui_max_text_len(),
                        "max_text_len_by_engine": _get_text_limits_payload(),
                        "edge_parallelism": _get_edge_parallelism_payload(),
                        "google_parallelism": _get_google_parallelism_payload(),
                        "google_retry_count": _get_google_retry_count_payload(),
                        "edge_options": _get_edge_options_payload(),
                        "dual_pause": _get_dual_pause_payload(),
                        "edge_text_normalizer": _get_edge_text_normalizer_payload(),
                        "text_normalizer": _get_edge_text_normalizer_payload(),
                        "edge_voice_catalog": _get_edge_voice_catalog_payload(),
                        "engine_options": _get_engine_options_payload(),
                        "voice_catalog": _get_voice_catalog_payload(),
                        "supported_import_extensions": _supported_import_extensions(),
                        "max_upload_bytes": _UPLOAD_MAX_BYTES,
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

        ok, message, details, addon_state = _install_tts_dependencies()
        init_tts_engines(force=True)
        diagnostics = _dependency_diagnostics_payload()
        addon_state = _collect_addon_runtime_state() if ok else dict(addon_state or _collect_addon_runtime_state())
        status = 200 if ok else 500
        payload = {
            "ok": ok,
            "message": message,
            "details": details[-20:],
            "engines": ENGINE_OPTIONS,
            "voices": VOICE_OPTIONS,
            "warnings": TTS_IMPORT_ERRORS,
            "addon_runtime": addon_state,
            "diagnostics": diagnostics["raw"],
            "diagnostics_lines": diagnostics["lines"],
        }
        if not ok:
            payload["error"] = message
        return jsonify(payload), status

    @expose("/import", methods=["POST"])
    @has_access
    @permission_name("list")
    def import_text(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        upload = request.files.get("file") or request.files.get("book_file")
        if upload is None:
            return jsonify({"ok": False, "error": "Файл не передан."}), 400

        try:
            payload = _extract_text_from_uploaded_file(upload)
        except ValueError as exc:
            diagnostics = _dependency_diagnostics_payload()
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "supported_import_extensions": _supported_import_extensions(),
                        "max_upload_bytes": _UPLOAD_MAX_BYTES,
                        "diagnostics": diagnostics["raw"],
                        "diagnostics_lines": diagnostics["lines"],
                    }
                ),
                400,
            )
        except Exception as exc:
            _LOGGER.exception("wintts import failed")
            diagnostics = _dependency_diagnostics_payload()
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Ошибка импорта файла: {type(exc).__name__}: {exc}",
                        "supported_import_extensions": _supported_import_extensions(),
                        "max_upload_bytes": _UPLOAD_MAX_BYTES,
                        "diagnostics": diagnostics["raw"],
                        "diagnostics_lines": diagnostics["lines"],
                    }
                ),
                500,
            )

        diagnostics = _dependency_diagnostics_payload()
        return jsonify(
            {
                "ok": True,
                "message": "Текст из файла загружен.",
                "text": payload["text"],
                "source_name": payload["source_name"],
                "source_ext": payload["source_ext"],
                "title": payload["title"],
                "char_count": payload["char_count"],
                "size_bytes": payload["size_bytes"],
                "warnings": payload["warnings"],
                "supported_import_extensions": _supported_import_extensions(),
                "max_upload_bytes": _UPLOAD_MAX_BYTES,
                "diagnostics": diagnostics["raw"],
                "diagnostics_lines": diagnostics["lines"],
            }
        )

    @expose("/normalize-preview", methods=["POST"])
    @has_access
    @permission_name("list")
    def normalize_preview(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Проверка CSRF не пройдена. Обновите страницу и попробуйте снова."}), 403

        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text") or request.form.get("text") or "")
        if len(text) > _EDGE_UI_MAX_TEXT_LEN:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Текст слишком длинный для предпросмотра анализатора ({len(text)} > {_EDGE_UI_MAX_TEXT_LEN}).",
                        "analysis": None,
                        "diagnostics_lines": _dependency_diagnostics_lines(),
                    }
                ),
                400,
            )

        normalizer_raw = payload.get("edge_text_normalizer")
        if normalizer_raw in (None, ""):
            normalizer_raw = payload.get("text_normalizer")
        if normalizer_raw in (None, ""):
            normalizer_raw = request.form.get("edge_text_normalizer")
        if normalizer_raw in (None, ""):
            normalizer_raw = request.form.get("text_normalizer")

        try:
            settings = parse_edge_text_normalizer_settings(normalizer_raw)
            analysis = analyze_edge_text_readability(text, settings)
            return jsonify(
                {
                    "ok": True,
                    "analysis": analysis,
                    "diagnostics_lines": _dependency_diagnostics_lines(),
                }
            )
        except Exception as exc:
            _LOGGER.exception("wintts normalize preview failed")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Ошибка анализа нормализатора: {type(exc).__name__}: {exc}",
                        "analysis": None,
                        "diagnostics_lines": _dependency_diagnostics_lines(),
                    }
                ),
                500,
            )

    @expose("/synthesize", methods=["POST"])
    @has_access
    @permission_name("list")
    def synthesize(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        request_data, error_response = _parse_synthesis_request(payload)
        if error_response is not None:
            body, status = error_response
            return jsonify(body), status

        try:
            result = _perform_synthesis(
                text=str(request_data["text"]),
                engine=str(request_data["engine"]),
                voice=str(request_data["voice"]),
                edge_parallelism=request_data.get("edge_parallelism"),
                google_parallelism=request_data.get("google_parallelism"),
                google_retry_count=request_data.get("google_retry_count"),
                edge_rate=request_data.get("edge_rate"),
                edge_volume=request_data.get("edge_volume"),
                edge_pitch=request_data.get("edge_pitch"),
                edge_text_normalizer=request_data.get("edge_text_normalizer"),
                primary_language=str(request_data.get("primary_language") or "und"),
                dual_language=request_data.get("dual_language"),
                user=str(getattr(current_user, "username", "web") or "web"),
            )
        except Exception as exc:
            _LOGGER.exception(
                "wintts synthesis failed engine=%s voice=%s",
                request_data.get("engine"),
                request_data.get("voice"),
            )
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

        file_name = str(result["filename"])
        audio_url = url_for(f"{self.__class__.__name__}.audio_file", file_name=file_name)
        download_url = url_for(f"{self.__class__.__name__}.download_file", file_name=file_name)

        return jsonify(
            {
                "ok": True,
                "message": "Синтез завершен.",
                "engine": result["engine"],
                "voice": result["voice"],
                "primary_language": result.get("primary_language"),
                "edge_language": result.get("primary_language"),
                "filename": file_name,
                "audio_url": audio_url,
                "download_url": download_url,
                "edge_parallelism": result.get("edge_parallelism"),
                "google_parallelism": result.get("google_parallelism"),
                "google_retry_count": result.get("google_retry_count"),
                "edge_rate": result.get("edge_rate"),
                "edge_volume": result.get("edge_volume"),
                "edge_pitch": result.get("edge_pitch"),
                "text_input_length": result.get("text_input_length"),
                "text_synth_length": result.get("text_synth_length"),
                "edge_text_normalizer": result.get("edge_text_normalizer"),
                "edge_text_normalizer_result": result.get("edge_text_normalizer_result"),
                "text_normalizer": result.get("text_normalizer"),
                "text_normalizer_result": result.get("text_normalizer_result"),
                "dual_language": result.get("dual_language"),
                "size_bytes": result["size_bytes"],
                "warnings": TTS_IMPORT_ERRORS,
                "diagnostics_lines": _dependency_diagnostics_lines(),
            }
        )

    @expose("/synthesize/start", methods=["POST"])
    @has_access
    @permission_name("list")
    def synthesize_start(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        request_data, error_response = _parse_synthesis_request(payload)
        if error_response is not None:
            body, status = error_response
            return jsonify(body), status

        user_id = int(getattr(current_user, "id", 0) or 0)
        username = str(getattr(current_user, "username", "web") or "web")
        job_id = _WINTTS_SYNTHESIS.create(
            user_id=user_id,
            username=username,
            engine=str(request_data["engine"]),
            voice=str(request_data["voice"]),
            primary_language=str(request_data.get("primary_language") or "und"),
            text_length=len(str(request_data["text"])),
            edge_parallelism=request_data.get("edge_parallelism"),
            google_parallelism=request_data.get("google_parallelism"),
            google_retry_count=request_data.get("google_retry_count"),
            edge_rate=request_data.get("edge_rate"),
            edge_volume=request_data.get("edge_volume"),
            edge_pitch=request_data.get("edge_pitch"),
            edge_text_normalizer=request_data.get("edge_text_normalizer"),
            dual_language=request_data.get("dual_language"),
        )
        _WINTTS_SYNTHESIS.append_log(job_id, 1, f"Движок: {request_data['engine']}. Голос: {request_data['voice'] or 'по умолчанию'}.", status="queued")
        _WINTTS_SYNTHESIS.append_log(job_id, 1, f"Объём текста: {len(str(request_data['text']))} символов.", status="queued")
        dual_settings = request_data.get("dual_language") or {}
        if bool(dual_settings.get("enabled")):
            pause_mode = _normalize_dual_pause_mode(dual_settings.get("pause_mode"))
            pause_ms = _normalize_dual_pause_ms(dual_settings.get("pause_ms"))
            if pause_mode == "manual":
                pause_label = f"ручная {pause_ms} мс"
            elif pause_mode == "off":
                pause_label = "выключена"
            else:
                pause_label = "авто"
            _WINTTS_SYNTHESIS.append_log(
                job_id,
                1,
                (
                    "Режим двух языков: "
                    f"{request_data.get('primary_language') or 'und'} -> {dual_settings.get('secondary_language') or 'und'}, "
                    f"второй голос {dual_settings.get('secondary_voice') or '-'}, пауза {pause_label}."
                ),
                status="queued",
            )

        normalizer = request_data.get("edge_text_normalizer") or {}
        if bool(normalizer.get("enabled", True)):
            _WINTTS_SYNTHESIS.append_log(
                job_id,
                1,
                (
                    "Нормализатор текста: "
                    f"профиль {normalizer.get('preset') or 'balanced'}, "
                    f"автонастройка {'вкл' if normalizer.get('auto_tune', True) else 'выкл'}."
                ),
                status="queued",
            )
        else:
            _WINTTS_SYNTHESIS.append_log(job_id, 1, "Нормализатор текста отключен.", status="queued")
        if request_data.get("edge_parallelism") is not None:
            _WINTTS_SYNTHESIS.append_log(job_id, 1, f"Параллельность Edge TTS: {request_data['edge_parallelism']}.", status="queued")
        if request_data.get("google_parallelism") is not None:
            _WINTTS_SYNTHESIS.append_log(job_id, 1, f"Параллельность Google TTS: {request_data['google_parallelism']}.", status="queued")
        if request_data.get("google_retry_count") is not None:
            _WINTTS_SYNTHESIS.append_log(job_id, 1, f"Повторы Google TTS при ошибке: {request_data['google_retry_count']}.", status="queued")
        _WINTTS_SYNTHESIS.append_log(
            job_id,
            1,
            f"Параметры {request_data.get('engine')}: "
            f"скорость {request_data.get('edge_rate')}, "
            f"тон {request_data.get('edge_pitch')}, "
            f"громкость {request_data.get('edge_volume')}.",
            status="queued",
        )

        _start_synthesis_task(current_app._get_current_object(), job_id, str(request_data["text"]))

        task_payload = _synthesis_task_payload(job_id, user_id)
        if not task_payload:
            return jsonify({"ok": False, "error": "Не удалось создать задачу синтеза."}), 500
        return jsonify(task_payload), 202

    @expose("/synthesize/status/<job_id>", methods=["GET"])
    @has_access
    @permission_name("list")
    def synthesize_status(self, job_id: str):
        user_id = int(getattr(current_user, "id", 0) or 0)
        payload = _synthesis_task_payload(job_id, user_id)
        if not payload:
            return jsonify({"ok": False, "error": "Задача синтеза не найдена или уже очищена."}), 404
        return jsonify(payload)

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
