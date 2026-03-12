from __future__ import annotations

import io
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from flask import Response, current_app, jsonify, request, send_file, session
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..db import db
from ..models.audit import AuditLog
from ..utils import ensure_dir

try:
    import mss  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    mss = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None

try:
    import pyautogui  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pyautogui = None

try:
    import ctypes
    from ctypes import wintypes
except Exception:  # pragma: no cover - optional dependency
    ctypes = None
    wintypes = None


_MAX_FPS = 30
_MIN_FPS = 1
_DEFAULT_FPS = 15
_DEFAULT_QUALITY = 70
_MIN_QUALITY = 30
_MAX_QUALITY = 95
_MAX_TEXT_LEN = 2000
_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)
_CAPTURE_ERROR_LOG_THROTTLE = 10.0
_CAPTURE_STATE_MAX_ENTRIES = 256
_CAPTURE_STATE_LOCK = threading.Lock()
_CAPTURE_STATES: dict[str, dict[str, Any]] = {}
_STREAM_STATE_MAX_ENTRIES = 256
_STREAM_STATE_LOCK = threading.Lock()
_STREAM_STATES: dict[str, dict[str, Any]] = {}
_RD_LOG_FIELD_MAX_LEN = 1200
_MJPEG_BOUNDARY_PREFIX = "rdframe"
_WINDOWS_INPUT_KEYBOARD = 1
_WINDOWS_KEYEVENTF_KEYUP = 0x0002
_WINDOWS_KEYEVENTF_UNICODE = 0x0004
_KEYBOARD_RELEASE_KEYS = ("shift", "ctrl", "alt", "winleft", "winright")

_KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "escape": "esc",
    "esc": "esc",
    "return": "enter",
    "enter": "enter",
    "del": "delete",
    "delete": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "prtsc": "printscreen",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "cmd": "winleft",
    "command": "winleft",
    "meta": "winleft",
    "win": "winleft",
    "windows": "winleft",
    "super": "winleft",
    "option": "alt",
}

_WIN_VK_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "winleft": 0x5B,
    "winright": 0x5C,
    "apps": 0x5D,
    "numlock": 0x90,
    "scrolllock": 0x91,
}

_WIN_CHAR_KEYMAP: dict[str, tuple[int, tuple[str, ...]]] = {
    ",": (0xBC, ()),
    "<": (0xBC, ("shift",)),
    "-": (0xBD, ()),
    "_": (0xBD, ("shift",)),
    ".": (0xBE, ()),
    ">": (0xBE, ("shift",)),
    "/": (0xBF, ()),
    "?": (0xBF, ("shift",)),
    "`": (0xC0, ()),
    "~": (0xC0, ("shift",)),
    ";": (0xBA, ()),
    ":": (0xBA, ("shift",)),
    "=": (0xBB, ()),
    "+": (0xBB, ("shift",)),
    "[": (0xDB, ()),
    "{": (0xDB, ("shift",)),
    "\\": (0xDC, ()),
    "|": (0xDC, ("shift",)),
    "]": (0xDD, ()),
    "}": (0xDD, ("shift",)),
    "'": (0xDE, ()),
    '"': (0xDE, ("shift",)),
    "!": (0x31, ("shift",)),
    "@": (0x32, ("shift",)),
    "#": (0x33, ("shift",)),
    "$": (0x34, ("shift",)),
    "%": (0x35, ("shift",)),
    "^": (0x36, ("shift",)),
    "&": (0x37, ("shift",)),
    "*": (0x38, ("shift",)),
    "(": (0x39, ("shift",)),
    ")": (0x30, ("shift",)),
}

_WINDOWS_USER32 = None
_WINDOWS_VK_KEYSCAN = None
_WINDOWS_SENDINPUT = None
_WINDOWS_SENDSAS = None
_WINDOWS_KERNEL32 = None
_WINDOWS_PROCESSIDTOSESSIONID = None
_WINDOWS_WTSGETACTIVECONSOLESESSIONID = None
_WINDOWS_OPENINPUTDESKTOP = None
_WINDOWS_GETUSEROBJECTINFORMATION = None
_WINDOWS_CLOSEDESKTOP = None
_WINDOWS_LOCKWORKSTATION = None
_WINDOWS_INPUT_STRUCT = None
_WINDOWS_KEYBDINPUT_STRUCT = None
_WINDOWS_UOI_NAME = 2
_WINDOWS_DESKTOP_READOBJECTS = 0x0001
_SECURE_ATTENTION_CONFIRM_TIMEOUT_SEC = 1.5

_RD_LOGGER = logging.getLogger("panel.remote_desktop")
_RD_LOGGER.addHandler(logging.NullHandler())
_RD_LOGGER.propagate = False


def _rd_log(level: int, message: str, **fields: Any) -> None:
    if not _RD_LOGGER.isEnabledFor(level):
        return
    parts = [f"event={message}"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > _RD_LOG_FIELD_MAX_LEN:
            text = text[:_RD_LOG_FIELD_MAX_LEN] + "...(truncated)"
        parts.append(f"{key}={text}")
    _RD_LOGGER.log(level, "remote_desktop %s", " ".join(parts))


if pyautogui:
    try:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
    except Exception:
        pass

if os.name == "nt" and ctypes and wintypes:
    try:
        _WINDOWS_USER32 = ctypes.WinDLL("user32", use_last_error=True)
        _WINDOWS_VK_KEYSCAN = _WINDOWS_USER32.VkKeyScanW
        _WINDOWS_VK_KEYSCAN.argtypes = [wintypes.WCHAR]
        _WINDOWS_VK_KEYSCAN.restype = ctypes.c_short

        _WINDOWS_SENDINPUT = _WINDOWS_USER32.SendInput
        _WINDOWS_SENDINPUT.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        _WINDOWS_SENDINPUT.restype = wintypes.UINT

        try:
            _windows_sas = ctypes.WinDLL("sas", use_last_error=True)
            _WINDOWS_SENDSAS = _windows_sas.SendSAS
            _WINDOWS_SENDSAS.argtypes = [wintypes.BOOL]
            _WINDOWS_SENDSAS.restype = None
        except Exception:
            _WINDOWS_SENDSAS = None

        try:
            _WINDOWS_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _WINDOWS_PROCESSIDTOSESSIONID = _WINDOWS_KERNEL32.ProcessIdToSessionId
            _WINDOWS_PROCESSIDTOSESSIONID.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            _WINDOWS_PROCESSIDTOSESSIONID.restype = wintypes.BOOL
            _WINDOWS_WTSGETACTIVECONSOLESESSIONID = _WINDOWS_KERNEL32.WTSGetActiveConsoleSessionId
            _WINDOWS_WTSGETACTIVECONSOLESESSIONID.argtypes = []
            _WINDOWS_WTSGETACTIVECONSOLESESSIONID.restype = wintypes.DWORD
        except Exception:
            _WINDOWS_KERNEL32 = None
            _WINDOWS_PROCESSIDTOSESSIONID = None
            _WINDOWS_WTSGETACTIVECONSOLESESSIONID = None

        try:
            _WINDOWS_OPENINPUTDESKTOP = _WINDOWS_USER32.OpenInputDesktop
            _WINDOWS_OPENINPUTDESKTOP.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            _WINDOWS_OPENINPUTDESKTOP.restype = wintypes.HANDLE

            _WINDOWS_GETUSEROBJECTINFORMATION = _WINDOWS_USER32.GetUserObjectInformationW
            _WINDOWS_GETUSEROBJECTINFORMATION.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            _WINDOWS_GETUSEROBJECTINFORMATION.restype = wintypes.BOOL

            _WINDOWS_CLOSEDESKTOP = _WINDOWS_USER32.CloseDesktop
            _WINDOWS_CLOSEDESKTOP.argtypes = [wintypes.HANDLE]
            _WINDOWS_CLOSEDESKTOP.restype = wintypes.BOOL
        except Exception:
            _WINDOWS_OPENINPUTDESKTOP = None
            _WINDOWS_GETUSEROBJECTINFORMATION = None
            _WINDOWS_CLOSEDESKTOP = None

        try:
            _WINDOWS_LOCKWORKSTATION = _WINDOWS_USER32.LockWorkStation
            _WINDOWS_LOCKWORKSTATION.argtypes = []
            _WINDOWS_LOCKWORKSTATION.restype = wintypes.BOOL
        except Exception:
            _WINDOWS_LOCKWORKSTATION = None

        _ULONG_PTR = getattr(
            wintypes,
            "ULONG_PTR",
            ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong,
        )

        class _WinMouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR),
            ]

        class _WinKeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR),
            ]

        class _WinHardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _WinInputUnion(ctypes.Union):
            _fields_ = [
                ("mi", _WinMouseInput),
                ("ki", _WinKeyboardInput),
                ("hi", _WinHardwareInput),
            ]

        class _WinInput(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", _WinInputUnion),
            ]

        _WINDOWS_INPUT_STRUCT = _WinInput
        _WINDOWS_KEYBDINPUT_STRUCT = _WinKeyboardInput
    except Exception:
        _WINDOWS_USER32 = None
        _WINDOWS_VK_KEYSCAN = None
        _WINDOWS_SENDINPUT = None
        _WINDOWS_SENDSAS = None
        _WINDOWS_KERNEL32 = None
        _WINDOWS_PROCESSIDTOSESSIONID = None
        _WINDOWS_WTSGETACTIVECONSOLESESSIONID = None
        _WINDOWS_OPENINPUTDESKTOP = None
        _WINDOWS_GETUSEROBJECTINFORMATION = None
        _WINDOWS_CLOSEDESKTOP = None
        _WINDOWS_LOCKWORKSTATION = None
        _WINDOWS_INPUT_STRUCT = None
        _WINDOWS_KEYBDINPUT_STRUCT = None


def _audit(action: str, target: str = "", result: str = "ok", details: str = "") -> None:
    try:
        user = getattr(current_user, "username", None) or str(
            getattr(current_user, "id", "unknown")
        )
        ip = request.headers.get("X-Forwarded-For") or request.remote_addr or ""
        entry = AuditLog(
            user=user,
            action=action,
            target=target,
            result=result,
            source="web",
            ip=ip,
            details=details or "",
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _is_csrf_valid() -> bool:
    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or request.args.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _get_session_token() -> str:
    token = session.get("rdp_token")
    if not token:
        token = secrets.token_urlsafe(16)
        session["rdp_token"] = token
    return token


def _capture_state_key() -> str:
    return str(_get_session_token())


def _new_capture_state() -> dict[str, Any]:
    now = time.time()
    return {
        "error": "",
        "blank": False,
        "last_error_at": 0.0,
        "last_ok_at": 0.0,
        "last_logged_error": "",
        "last_logged_at": 0.0,
        "touched_at": now,
    }


def _get_or_create_capture_state_locked(state_key: str, now: float) -> dict[str, Any]:
    state = _CAPTURE_STATES.get(state_key)
    if state is None:
        if len(_CAPTURE_STATES) >= _CAPTURE_STATE_MAX_ENTRIES and _CAPTURE_STATES:
            oldest_key = min(
                _CAPTURE_STATES.items(),
                key=lambda item: float(item[1].get("touched_at", 0.0)),
            )[0]
            _CAPTURE_STATES.pop(oldest_key, None)
        state = _new_capture_state()
        _CAPTURE_STATES[state_key] = state
    state["touched_at"] = now
    return state


def _get_capture_snapshot(state_key: str | None = None) -> dict[str, Any]:
    key = state_key or _capture_state_key()
    now = time.time()
    with _CAPTURE_STATE_LOCK:
        state = _get_or_create_capture_state_locked(key, now).copy()
    return state


def _is_token_valid(token: str | None) -> bool:
    if not token:
        return False
    return token == session.get("rdp_token")


def _json_error(message: str, code: int = 400) -> Response:
    return Response(
        jsonify({"ok": False, "error": message}).data,
        status=code,
        mimetype="application/json",
    )


def _set_capture_error(message: str, blank: bool = False, state_key: str | None = None) -> None:
    key = state_key or _capture_state_key()
    now = time.time()
    should_log = False
    with _CAPTURE_STATE_LOCK:
        state = _get_or_create_capture_state_locked(key, now)
        state["error"] = message
        state["blank"] = bool(blank)
        state["last_error_at"] = now
        if message:
            last_logged_error = str(state.get("last_logged_error") or "")
            last_logged_at = float(state.get("last_logged_at", 0.0) or 0.0)
            if message != last_logged_error or (now - last_logged_at) >= _CAPTURE_ERROR_LOG_THROTTLE:
                state["last_logged_error"] = message
                state["last_logged_at"] = now
                should_log = True
    if should_log:
        _rd_log(logging.WARNING, "capture_error", detail=message)


def _clear_capture_error(state_key: str | None = None) -> None:
    key = state_key or _capture_state_key()
    now = time.time()
    had_error = False
    with _CAPTURE_STATE_LOCK:
        state = _get_or_create_capture_state_locked(key, now)
        had_error = bool(state.get("error"))
        state["error"] = ""
        state["blank"] = False
        state["last_logged_error"] = ""
        state["last_ok_at"] = now
    if had_error:
        _rd_log(logging.INFO, "capture_recovered")


def _new_stream_state(now: float) -> dict[str, Any]:
    return {
        "stream_id": "",
        "session_active": False,
        "updated_at": now,
    }


def _get_or_create_stream_state_locked(state_key: str, now: float) -> dict[str, Any]:
    state = _STREAM_STATES.get(state_key)
    if state is None:
        if len(_STREAM_STATES) >= _STREAM_STATE_MAX_ENTRIES and _STREAM_STATES:
            oldest_key = min(
                _STREAM_STATES.items(),
                key=lambda item: float(item[1].get("updated_at", 0.0)),
            )[0]
            _STREAM_STATES.pop(oldest_key, None)
        state = _new_stream_state(now)
        _STREAM_STATES[state_key] = state
    state["updated_at"] = now
    return state


def _set_stream_session_active(state_key: str, is_active: bool) -> None:
    now = time.time()
    with _STREAM_STATE_LOCK:
        state = _get_or_create_stream_state_locked(state_key, now)
        state["session_active"] = bool(is_active)
        if not is_active:
            state["stream_id"] = ""


def _activate_stream(state_key: str, stream_id: str) -> None:
    stream_key = str(stream_id or "")
    now = time.time()
    with _STREAM_STATE_LOCK:
        state = _get_or_create_stream_state_locked(state_key, now)
        state["session_active"] = True
        state["stream_id"] = stream_key


def _deactivate_stream(state_key: str, stream_id: str) -> None:
    stream_key = str(stream_id or "")
    now = time.time()
    with _STREAM_STATE_LOCK:
        state = _get_or_create_stream_state_locked(state_key, now)
        if str(state.get("stream_id") or "") == stream_key:
            state["stream_id"] = ""


def _is_stream_current(state_key: str, stream_id: str) -> tuple[bool, str]:
    stream_key = str(stream_id or "")
    now = time.time()
    with _STREAM_STATE_LOCK:
        state = _get_or_create_stream_state_locked(state_key, now).copy()
    if not bool(state.get("session_active")):
        return False, "session_inactive"
    active_stream_id = str(state.get("stream_id") or "")
    if not active_stream_id:
        return False, "stream_inactive"
    if active_stream_id != stream_key:
        return False, "superseded"
    return True, "ok"




def _looks_blank(rgb: bytes) -> bool:
    if not rgb:
        return True
    sample = rgb[:5000]
    return not any(sample)


def _grab_frame_bytes(
    sct: Any,
    monitor_id: int,
    quality: int,
    scale: float,
) -> tuple[Optional[bytes], str, bool]:
    try:
        monitor = _select_monitor(sct, monitor_id)
        if not monitor or monitor.get("width", 0) <= 0:
            return None, "Монитор недоступен или не имеет размеров.", False
        shot = sct.grab(monitor)
        blank = False
        blank_hint = ""
        if _looks_blank(shot.rgb):
            blank = True
            blank_hint = (
                "Кадр пустой. Возможна блокировка экрана, отсутствие прав или запуск без GUI."
            )
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        if scale < 1.0:
            new_size = (
                max(1, int(img.width * scale)),
                max(1, int(img.height * scale)),
            )
            img = img.resize(new_size, Image.BICUBIC)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=False)
        frame = buffer.getvalue()
        if not frame:
            return None, "Получен пустой кадр.", False
        return frame, blank_hint, blank
    except Exception as exc:
        return None, f"Ошибка захвата экрана: {exc}", False


def _build_mjpeg_chunk(frame: bytes, boundary: str) -> bytes:
    return (
        f"--{boundary}\r\n".encode("ascii")
        + b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
        + frame
        + b"\r\n"
    )


def _ensure_shared_dir(base_dir: str) -> Path:
    path = Path(base_dir) / "data" / "shared"
    ensure_dir(path)
    return path


def _resolve_shared_path(root: Path, rel_path: str | None) -> Optional[Path]:
    rel_path = (rel_path or "").strip().lstrip("/\\")
    if not rel_path:
        return root
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except Exception:
        return None
    return candidate


def _list_shared_dir(root: Path, rel_path: str | None) -> tuple[str, list[dict[str, Any]]]:
    target = _resolve_shared_path(root, rel_path)
    if not target or not target.exists() or not target.is_dir():
        return str(root), []

    items: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = entry.stat()
            size = stat.st_size if entry.is_file() else None
            modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        except Exception:
            size = None
            modified = ""
        rel = entry.relative_to(root).as_posix()
        items.append(
            {
                "name": entry.name,
                "path": rel,
                "is_dir": entry.is_dir(),
                "is_file": entry.is_file(),
                "size": size,
                "modified": modified,
            }
        )
    return str(target), items


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _normalize_button(value: str | None) -> str:
    value = (value or "").strip().lower()
    if value in ("right", "middle"):
        return value
    return "left"


def _normalize_key(value: str | None) -> Optional[str]:
    if not value:
        return None
    key = value.strip().lower()
    key = _KEY_ALIASES.get(key, key)
    if _is_pyautogui_key_supported(key):
        return key
    if _resolve_windows_key_token(key):
        return key
    if len(key) == 1:
        return key
    return None


def _is_pyautogui_key_supported(key: str) -> bool:
    if not pyautogui:
        return False
    token = str(key or "").strip().lower()
    if not token:
        return False

    platform_module = getattr(pyautogui, "platformModule", None)
    keyboard_mapping = getattr(platform_module, "keyboardMapping", None)
    if isinstance(keyboard_mapping, dict):
        mapped = keyboard_mapping.get(token)
        if mapped is None or mapped == -1:
            return False
        return True

    if hasattr(pyautogui, "KEYBOARD_KEYS"):
        try:
            return token in pyautogui.KEYBOARD_KEYS
        except Exception:
            return False
    return False


def _windows_keyboard_backend_ready() -> bool:
    return bool(
        os.name == "nt"
        and ctypes
        and _WINDOWS_SENDINPUT
        and _WINDOWS_INPUT_STRUCT
        and _WINDOWS_KEYBDINPUT_STRUCT
    )


def _windows_secure_attention_backend_ready() -> bool:
    return bool(os.name == "nt" and ctypes and _WINDOWS_SENDSAS)


def _windows_session_probe_ready() -> bool:
    return bool(
        os.name == "nt"
        and ctypes
        and wintypes
        and _WINDOWS_PROCESSIDTOSESSIONID
        and _WINDOWS_WTSGETACTIVECONSOLESESSIONID
    )


def _windows_desktop_probe_ready() -> bool:
    return bool(
        os.name == "nt"
        and ctypes
        and wintypes
        and _WINDOWS_OPENINPUTDESKTOP
        and _WINDOWS_GETUSEROBJECTINFORMATION
        and _WINDOWS_CLOSEDESKTOP
    )


def _get_windows_session_context() -> tuple[Optional[int], Optional[int], str]:
    if not _windows_session_probe_ready():
        return None, None, "Session API недоступен."
    if not ctypes or not wintypes:
        return None, None, "ctypes/wintypes недоступен."
    try:
        process_session = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        ok = _WINDOWS_PROCESSIDTOSESSIONID(
            wintypes.DWORD(int(os.getpid())),
            ctypes.byref(process_session),
        )
        if not ok:
            return None, None, f"ProcessIdToSessionId WinError={ctypes.get_last_error()}."

        active_session = int(_WINDOWS_WTSGETACTIVECONSOLESESSIONID())
        if active_session == 0xFFFFFFFF:
            return int(process_session.value), None, "Активная консольная сессия не определена."
        return int(process_session.value), active_session, ""
    except Exception as exc:
        return None, None, f"Ошибка определения сессии: {exc}"


def _get_input_desktop_name() -> tuple[Optional[str], str]:
    if not _windows_desktop_probe_ready():
        return None, "Desktop API недоступен."
    if not ctypes or not wintypes:
        return None, "ctypes/wintypes недоступен."

    try:
        ctypes.set_last_error(0)
        h_desktop = _WINDOWS_OPENINPUTDESKTOP(0, False, _WINDOWS_DESKTOP_READOBJECTS)
    except Exception as exc:
        return None, f"OpenInputDesktop exception: {exc}"

    if not h_desktop:
        return None, f"OpenInputDesktop WinError={ctypes.get_last_error()}."

    try:
        needed = wintypes.DWORD(0)
        try:
            _WINDOWS_GETUSEROBJECTINFORMATION(
                h_desktop,
                _WINDOWS_UOI_NAME,
                None,
                0,
                ctypes.byref(needed),
            )
        except Exception:
            pass

        byte_len = int(needed.value)
        if byte_len <= 0:
            byte_len = 512
        char_len = max(2, int(byte_len / ctypes.sizeof(wintypes.WCHAR)))
        buffer = ctypes.create_unicode_buffer(char_len)
        needed = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        ok = _WINDOWS_GETUSEROBJECTINFORMATION(
            h_desktop,
            _WINDOWS_UOI_NAME,
            buffer,
            wintypes.DWORD(ctypes.sizeof(buffer)),
            ctypes.byref(needed),
        )
        if not ok:
            return None, f"GetUserObjectInformation WinError={ctypes.get_last_error()}."
        return str(buffer.value or "").strip(), ""
    finally:
        try:
            _WINDOWS_CLOSEDESKTOP(h_desktop)
        except Exception:
            pass


def _confirm_secure_attention_transition(
    desktop_before: str | None,
    timeout_sec: float = _SECURE_ATTENTION_CONFIRM_TIMEOUT_SEC,
) -> tuple[bool, str]:
    normalized_before = str(desktop_before or "").strip().lower()
    deadline = time.time() + max(0.3, float(timeout_sec))
    observed = normalized_before
    probe_error = ""

    while time.time() <= deadline:
        current_desktop, current_error = _get_input_desktop_name()
        normalized_current = str(current_desktop or "").strip().lower()
        if normalized_current:
            observed = normalized_current
            if normalized_current == "winlogon":
                return True, "desktop=winlogon"
            if normalized_before and normalized_current != normalized_before:
                return True, f"desktop_changed:{normalized_before}->{normalized_current}"
        if current_error:
            probe_error = current_error
        time.sleep(0.12)

    details = f"desktop={observed or 'unknown'}"
    if probe_error:
        details = f"{details}; probe_error={probe_error}"
    return False, details


def _secure_attention_policy_hint() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except Exception:
        return ""

    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as reg_key:
            value, _ = winreg.QueryValueEx(reg_key, "SoftwareSASGeneration")
    except FileNotFoundError:
        return (
            "SoftwareSASGeneration не задан: по умолчанию SAS разрешен только "
            "для Ease of Access на защищенном рабочем столе."
        )
    except Exception as exc:
        return f"Не удалось прочитать SoftwareSASGeneration: {exc}"

    try:
        number = int(value)
    except Exception:
        return f"Некорректное значение SoftwareSASGeneration: {value!r}"

    labels = {
        0: "None",
        1: "Services",
        2: "Ease of Access applications",
        3: "Services and Ease of Access applications",
    }
    return f"SoftwareSASGeneration={number} ({labels.get(number, 'unknown')})."


def _send_windows_secure_attention() -> tuple[bool, str]:
    if not _windows_secure_attention_backend_ready():
        return False, "Windows SAS API недоступен."
    if not ctypes:
        return False, "ctypes недоступен."

    errors: list[str] = []
    for as_user in (True, False):
        try:
            ctypes.set_last_error(0)
            _WINDOWS_SENDSAS(bool(as_user))
            win_error = int(ctypes.get_last_error())
            if win_error == 0:
                return True, ""
            errors.append(f"SendSAS(as_user={int(as_user)}) WinError={win_error}")
        except Exception as exc:
            errors.append(f"SendSAS(as_user={int(as_user)}): {exc}")
    return False, "; ".join(errors) if errors else "SendSAS не выполнился."


def _send_windows_security_via_shell() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Shell.WindowsSecurity поддерживается только на Windows."

    script = "$ErrorActionPreference='Stop'; $s = New-Object -ComObject Shell.Application; $s.WindowsSecurity()"
    ps_candidates: list[str] = []
    system_root = str(os.environ.get("SystemRoot") or r"C:\Windows")
    ps_path = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if ps_path.exists():
        ps_candidates.append(str(ps_path))
    ps_candidates.append("powershell")

    errors: list[str] = []
    for executable in ps_candidates:
        try:
            completed = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            errors.append(f"{executable}: {exc}")
            continue

        if int(completed.returncode) == 0:
            return True, ""
        stderr_text = str(completed.stderr or "").strip().replace("\r", " ").replace("\n", " ")
        errors.append(
            f"{executable}: rc={completed.returncode}"
            + (f" stderr={stderr_text}" if stderr_text else "")
        )

    return False, "; ".join(errors) if errors else "WindowsSecurity не выполнился."


def _send_windows_lock_workstation() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "LockWorkStation поддерживается только на Windows."
    if not ctypes:
        return False, "ctypes недоступен."
    if not _WINDOWS_LOCKWORKSTATION:
        return False, "WinAPI LockWorkStation недоступен."
    try:
        ctypes.set_last_error(0)
        ok = bool(_WINDOWS_LOCKWORKSTATION())
        if ok:
            return True, ""
        return False, f"LockWorkStation WinError={ctypes.get_last_error()}."
    except Exception as exc:
        return False, f"LockWorkStation exception: {exc}"


def _resolve_windows_key_token(token: str) -> tuple[int, tuple[int, ...]] | None:
    normalized = str(token or "").strip().lower()
    if not normalized:
        return None
    if normalized in _WIN_VK_KEYS:
        return _WIN_VK_KEYS[normalized], ()

    fn_match = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", normalized)
    if fn_match:
        return 0x6F + int(fn_match.group(1)), ()

    if len(normalized) == 1:
        char = normalized
        if "a" <= char <= "z":
            return ord(char.upper()), ()
        if "0" <= char <= "9":
            return ord(char), ()

        mapped = _WIN_CHAR_KEYMAP.get(char)
        if mapped:
            vk_code, implicit_names = mapped
            implicit_vks = tuple(_WIN_VK_KEYS[name] for name in implicit_names if name in _WIN_VK_KEYS)
            return vk_code, implicit_vks

        if _WINDOWS_VK_KEYSCAN:
            try:
                vk_state = int(_WINDOWS_VK_KEYSCAN(char))
            except Exception:
                vk_state = -1
            if vk_state != -1:
                vk_code = vk_state & 0xFF
                modifiers = (vk_state >> 8) & 0xFF
                implicit_vks: list[int] = []
                if modifiers & 1:
                    implicit_vks.append(_WIN_VK_KEYS["shift"])
                if modifiers & 2:
                    implicit_vks.append(_WIN_VK_KEYS["ctrl"])
                if modifiers & 4:
                    implicit_vks.append(_WIN_VK_KEYS["alt"])
                if vk_code:
                    return vk_code, tuple(implicit_vks)
    return None


def _send_windows_key_event(
    vk_code: int,
    *,
    key_up: bool = False,
    scan_code: int = 0,
    unicode_mode: bool = False,
) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для клавиатуры недоступен."
    if not ctypes:
        return False, "ctypes недоступен."

    flags = _WINDOWS_KEYEVENTF_KEYUP if key_up else 0
    if unicode_mode:
        flags |= _WINDOWS_KEYEVENTF_UNICODE
        vk_code = 0

    input_obj = _WINDOWS_INPUT_STRUCT()
    input_obj.type = _WINDOWS_INPUT_KEYBOARD
    input_obj.ki = _WINDOWS_KEYBDINPUT_STRUCT(
        wVk=vk_code,
        wScan=scan_code,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )

    ctypes.set_last_error(0)
    sent = int(_WINDOWS_SENDINPUT(1, ctypes.byref(input_obj), ctypes.sizeof(_WINDOWS_INPUT_STRUCT)))
    if sent != 1:
        return False, f"SendInput вернул {sent}, WinError={ctypes.get_last_error()}."
    return True, ""


def _send_windows_key_transition(key: str, key_up: bool) -> tuple[bool, str]:
    resolved = _resolve_windows_key_token(key)
    if not resolved:
        return False, f"Клавиша '{key}' не поддерживается Windows API."
    vk_code, _implicit_modifiers = resolved
    return _send_windows_key_event(vk_code, key_up=key_up)


def _send_hotkey_windows(keys: list[str]) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для hotkey недоступен."
    if not keys:
        return False, "Для hotkey не указаны клавиши."

    pressed: list[int] = []
    pressed_set: set[int] = set()

    def _press_vk(vk_code: int) -> tuple[bool, str]:
        if vk_code in pressed_set:
            return True, ""
        ok_press, message_press = _send_windows_key_event(vk_code, key_up=False)
        if not ok_press:
            return False, message_press
        pressed.append(vk_code)
        pressed_set.add(vk_code)
        return True, ""

    try:
        for raw_key in keys:
            resolved = _resolve_windows_key_token(raw_key)
            if not resolved:
                return False, f"Клавиша '{raw_key}' не поддерживается Windows API."
            vk_code, implicit_modifiers = resolved
            for modifier_vk in implicit_modifiers:
                ok_modifier, message_modifier = _press_vk(modifier_vk)
                if not ok_modifier:
                    return False, f"Не удалось зажать модификатор для '{raw_key}': {message_modifier}"
            ok_down, message_down = _press_vk(vk_code)
            if not ok_down:
                return False, f"Не удалось нажать '{raw_key}': {message_down}"
    finally:
        for vk_code in reversed(pressed):
            _send_windows_key_event(vk_code, key_up=True)
    return True, ""


def _send_key_transition_with_fallback(key: str, key_up: bool) -> str:
    ok_windows, message_windows = _send_windows_key_transition(key, key_up=key_up)
    if ok_windows:
        return "windows_api"

    pyautogui_error = ""
    if pyautogui:
        try:
            if key_up:
                pyautogui.keyUp(key)
            else:
                pyautogui.keyDown(key)
            return "pyautogui"
        except Exception as exc:
            pyautogui_error = str(exc)

    message = message_windows or "Не удалось отправить клавишу."
    if pyautogui_error:
        message = f"{message} (ошибка pyautogui: {pyautogui_error})"
    raise RuntimeError(message)


def _send_hotkey_with_fallback(keys: list[str]) -> str:
    ok_windows, message_windows = _send_hotkey_windows(keys)
    if ok_windows:
        return "windows_api"

    if pyautogui:
        pyautogui.hotkey(*keys)
        return "pyautogui"
    raise RuntimeError(message_windows or "Не удалось отправить hotkey.")


def _release_keyboard_state() -> tuple[list[str], list[str]]:
    released: list[str] = []
    errors: list[str] = []
    for token in _KEYBOARD_RELEASE_KEYS:
        released_one = False
        if pyautogui:
            try:
                pyautogui.keyUp(token)
                released_one = True
            except Exception:
                pass
        if not released_one and _windows_keyboard_backend_ready():
            ok_release, message_release = _send_windows_key_transition(token, key_up=True)
            if ok_release:
                released_one = True
            elif message_release:
                errors.append(f"{token}: {message_release}")
        if released_one:
            released.append(token)
    return released, errors


def _is_secure_attention_hotkey(keys: list[str]) -> bool:
    signature = tuple(sorted(str(item).strip().lower() for item in keys if item))
    return signature == ("alt", "ctrl", "delete")


def _send_secure_attention_hotkey(keys: list[str]) -> tuple[str, str]:
    if os.name != "nt":
        backend = _send_hotkey_with_fallback(keys)
        return backend, "Комбинация Ctrl + Alt + Del отправлена."

    session_process, session_active, session_diag = _get_windows_session_context()
    desktop_before, desktop_diag = _get_input_desktop_name()
    attempts: list[str] = []

    def _with_confirmation(method_label: str) -> tuple[bool, str]:
        ok_confirm, confirm_details = _confirm_secure_attention_transition(desktop_before)
        if ok_confirm:
            context = [method_label, confirm_details]
            if session_process is not None and session_active is not None:
                context.append(f"session={session_process}/{session_active}")
            return True, f"Ctrl + Alt + Del подтвержден ({'; '.join(context)})."
        attempts.append(f"{method_label}: {confirm_details}")
        return False, ""

    ok_sas, sas_error = _send_windows_secure_attention()
    if ok_sas:
        ok_confirm, confirm_message = _with_confirmation("SendSAS")
        if ok_confirm:
            return "windows_sas", confirm_message
    else:
        attempts.append(f"SendSAS error: {sas_error}")

    ok_shell, shell_error = _send_windows_security_via_shell()
    if ok_shell:
        ok_confirm, confirm_message = _with_confirmation("Shell.WindowsSecurity")
        if ok_confirm:
            return "windows_shell_security", confirm_message
    else:
        attempts.append(f"Shell.WindowsSecurity error: {shell_error}")

    try:
        fallback_backend = _send_hotkey_with_fallback(keys)
        ok_confirm, confirm_message = _with_confirmation("hotkey_fallback_ctrl_alt_del")
        if ok_confirm:
            return fallback_backend, confirm_message
    except Exception as exc:
        attempts.append(f"hotkey_fallback_ctrl_alt_del error: {exc}")

    ok_lock_api, lock_api_error = _send_windows_lock_workstation()
    if ok_lock_api:
        ok_confirm, confirm_details = _confirm_secure_attention_transition(desktop_before)
        if ok_confirm:
            context = ["LockWorkStation fallback", confirm_details]
            if session_process is not None and session_active is not None:
                context.append(f"session={session_process}/{session_active}")
            return (
                "windows_lock_workstation",
                "Ctrl + Alt + Del недоступен в текущем контексте, выполнена блокировка компьютера "
                f"({'; '.join(context)}).",
            )
        attempts.append(f"LockWorkStation fallback: {confirm_details}")
    else:
        attempts.append(f"LockWorkStation error: {lock_api_error}")

    try:
        win_l_backend = _send_hotkey_with_fallback(["winleft", "l"])
        ok_confirm, confirm_details = _confirm_secure_attention_transition(desktop_before)
        if ok_confirm:
            context = ["Win+L fallback", confirm_details]
            if session_process is not None and session_active is not None:
                context.append(f"session={session_process}/{session_active}")
            return (
                win_l_backend,
                "Ctrl + Alt + Del недоступен в текущем контексте, выполнена блокировка компьютера "
                f"через Win + L ({'; '.join(context)}).",
            )
        attempts.append(f"Win+L fallback: {confirm_details}")
    except Exception as exc:
        attempts.append(f"Win+L fallback error: {exc}")

    if (
        not session_diag
        and session_process is not None
        and session_active is not None
        and session_process != session_active
    ):
        session_diag = (
            f"Процесс работает в сессии {session_process}, "
            f"активная консольная сессия {session_active}."
        )

    policy_hint = _secure_attention_policy_hint()
    details = " | ".join(item for item in attempts if item).strip()
    if len(details) > 900:
        details = details[:900] + "...(truncated)"

    parts = [
        "Ctrl + Alt + Del не подтвержден, и fallback блокировки тоже не сработал: "
        "защищенный рабочий стол не активировался."
    ]
    if desktop_diag:
        parts.append(f"Desktop API: {desktop_diag}")
    if session_diag:
        parts.append(session_diag)
    if policy_hint:
        parts.append(policy_hint)
    if details:
        parts.append(f"Попытки: {details}")
    parts.append(
        "Проверьте политику «Disable or enable software Secure Attention Sequence» "
        "и запуск панели в подходящем контексте (service/uiAccess)."
    )
    raise RuntimeError(" ".join(parts))


def _type_text(text: str) -> None:
    if not text:
        return
    text = text[:_MAX_TEXT_LEN]
    if pyautogui:
        try:
            pyautogui.write(text, interval=0)
            return
        except Exception:
            pass
    _send_unicode_text(text)


def _send_unicode_text(text: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _I)]

    def _send_scan(scan_code: int, flags: int) -> None:
        inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan_code, flags, 0, None))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    if not text:
        return
    data = text.encode("utf-16-le")
    for idx in range(0, len(data), 2):
        scan = int.from_bytes(data[idx : idx + 2], "little")
        _send_scan(scan, KEYEVENTF_UNICODE)
        _send_scan(scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def _get_monitors() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not mss:
        return [], {}
    monitors: list[dict[str, Any]] = []
    with mss.mss() as sct:
        for idx, mon in enumerate(sct.monitors[1:], start=1):
            monitors.append(
                {
                    "id": idx,
                    "left": mon.get("left", 0),
                    "top": mon.get("top", 0),
                    "width": mon.get("width", 0),
                    "height": mon.get("height", 0),
                    "primary": idx == 1,
                }
            )
        virtual = sct.monitors[0] if sct.monitors else {}
    return monitors, {
        "left": virtual.get("left", 0) if virtual else 0,
        "top": virtual.get("top", 0) if virtual else 0,
        "width": virtual.get("width", 0) if virtual else 0,
        "height": virtual.get("height", 0) if virtual else 0,
    }


def _select_monitor(sct: Any, monitor_id: int) -> dict[str, Any]:
    try:
        monitors = sct.monitors
    except Exception:
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    if monitor_id < 0:
        monitor_id = 0
    if monitor_id >= len(monitors):
        monitor_id = 1 if len(monitors) > 1 else 0
    return monitors[monitor_id]


def _iter_frames(
    monitor_id: int,
    fps: int,
    quality: int,
    scale: float,
    boundary: str = "frame",
    state_key: str | None = None,
) -> Iterable[bytes]:
    if not mss or not Image:
        return
    fps = max(_MIN_FPS, min(_MAX_FPS, fps))
    quality = max(_MIN_QUALITY, min(_MAX_QUALITY, quality))
    scale = max(0.2, min(1.0, scale))

    frame_delay = 1.0 / fps
    with mss.mss() as sct:
        while True:
            start = time.time()
            frame, error, blank = _grab_frame_bytes(sct, monitor_id, quality, scale)
            if error:
                _set_capture_error(error, blank=blank, state_key=state_key)
            else:
                _clear_capture_error(state_key=state_key)
            if not frame:
                time.sleep(max(frame_delay, 0.3))
                continue
            yield _build_mjpeg_chunk(frame, boundary)
            elapsed = time.time() - start
            sleep_for = frame_delay - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)


class RemoteDesktopView(BaseView):
    route_base = "/remote-desktop"
    default_view = "list"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        base_dir = current_app.config.get("BASE_DIR", os.getcwd())
        shared_dir = _ensure_shared_dir(base_dir)
        token = _get_session_token()
        try:
            assets_path = Path(__file__).resolve().parents[1] / "static" / "js" / "remote_desktop.js"
            assets_version = int(assets_path.stat().st_mtime)
        except Exception:
            assets_version = int(time.time())
        capabilities = {
            "screen": bool(mss and Image),
            "control": bool(pyautogui),
            "platform": os.name,
        }
        _audit("remote_desktop_open")
        _rd_log(
            logging.INFO,
            "remote_desktop_open",
            screen_ok=capabilities.get("screen"),
            control_ok=capabilities.get("control"),
            platform=os.name,
        )
        return self.render_template(
            "remote_desktop.html",
            shared_dir=str(shared_dir),
            rdp_token=token,
            rd_assets_version=assets_version,
            capabilities=capabilities,
        )

    @expose("/info")
    @has_access_api
    @permission_name("list")
    def info(self):
        monitors, virtual = _get_monitors()
        _rd_log(logging.DEBUG, "remote_desktop_info", monitors=len(monitors))
        return jsonify(
            {
                "ok": True,
                "monitors": monitors,
                "virtual": virtual,
                "has_control": bool(pyautogui),
                "has_screen": bool(mss and Image),
            }
        )

    @expose("/stream")
    @has_access_api
    @permission_name("list")
    def stream(self):
        if not mss or not Image:
            _set_capture_error("Модуль захвата экрана не доступен.")
            return _json_error("Модуль захвата экрана не доступен.", 503)
        state_key = _capture_state_key()
        stream_id = secrets.token_hex(8)
        _activate_stream(state_key, stream_id)
        diag = (request.args.get("diag") or "").strip()
        trace = (request.args.get("trace") or "").lower() in ("1", "true", "yes", "on")
        transport = (request.args.get("transport") or "").strip().lower() or "img"
        monitor_id = _coerce_int(request.args.get("monitor"), 1) or 1
        fps = _coerce_int(request.args.get("fps"), _DEFAULT_FPS) or _DEFAULT_FPS
        quality = _coerce_int(request.args.get("quality"), _DEFAULT_QUALITY) or _DEFAULT_QUALITY
        scale = request.args.get("scale", "1")
        try:
            scale_val = float(scale)
        except Exception:
            scale_val = 1.0
        fps = max(_MIN_FPS, min(_MAX_FPS, fps))
        quality = max(_MIN_QUALITY, min(_MAX_QUALITY, quality))
        scale_val = max(0.2, min(1.0, scale_val))
        boundary = f"{_MJPEG_BOUNDARY_PREFIX}_{secrets.token_hex(8)}"
        _rd_log(
            logging.INFO,
            "stream_http_request",
            monitor=monitor_id,
            fps=fps,
            quality=quality,
            scale=scale_val,
            diag=diag or "",
            trace=trace,
            transport=transport,
            stream_id=stream_id,
            accept=request.headers.get("Accept"),
            ua=request.headers.get("User-Agent"),
            ip=request.headers.get("X-Forwarded-For") or request.remote_addr or "",
        )

        try:
            with mss.mss() as sct:
                first_frame, first_error, first_blank = _grab_frame_bytes(sct, monitor_id, quality, scale_val)
        except Exception as exc:
            message = f"Ошибка запуска потока: {exc}"
            _set_capture_error(message, state_key=state_key)
            _rd_log(
                logging.ERROR,
                "stream_preflight_exception",
                monitor=monitor_id,
                transport=transport,
                stream_id=stream_id,
                diag=diag or "",
                error=repr(exc),
            )
            return _json_error(message, 503)
        if first_error:
            _set_capture_error(first_error, blank=first_blank, state_key=state_key)
            _rd_log(
                logging.WARNING,
                "stream_preflight_failed",
                monitor=monitor_id,
                transport=transport,
                stream_id=stream_id,
                diag=diag or "",
                error=first_error,
            )
            return _json_error(first_error, 503)
        if not first_frame:
            message = "Не удалось получить кадр потока."
            _set_capture_error(message, state_key=state_key)
            _rd_log(
                logging.WARNING,
                "stream_preflight_failed",
                monitor=monitor_id,
                transport=transport,
                stream_id=stream_id,
                diag=diag or "",
                error=message,
            )
            return _json_error(message, 503)
        _clear_capture_error(state_key=state_key)
        _rd_log(logging.INFO, "stream_preflight_ok", monitor=monitor_id, bytes=len(first_frame))
        _rd_log(
            logging.INFO,
            "stream_start",
            monitor=monitor_id,
            fps=fps,
            quality=quality,
            scale=scale_val,
            boundary=boundary,
            transport=transport,
            stream_id=stream_id,
            diag=diag or "",
            trace=trace,
        )

        def _gen() -> Iterable[bytes]:
            started_at = time.time()
            frame_count = 0
            end_reason = "finished"
            _rd_log(
                logging.INFO,
                "stream_generator_begin",
                monitor=monitor_id,
                transport=transport,
                stream_id=stream_id,
                diag=diag or "",
            )
            try:
                is_current, current_reason = _is_stream_current(state_key, stream_id)
                if not is_current:
                    end_reason = current_reason
                    return
                yield _build_mjpeg_chunk(first_frame, boundary)
                frame_count = 1
                _rd_log(
                    logging.INFO,
                    "stream_first_frame_sent",
                    monitor=monitor_id,
                    transport=transport,
                    stream_id=stream_id,
                    bytes=len(first_frame),
                    diag=diag or "",
                )
                for frame in _iter_frames(
                    monitor_id,
                    fps,
                    quality,
                    scale_val,
                    boundary=boundary,
                    state_key=state_key,
                ):
                    is_current, current_reason = _is_stream_current(state_key, stream_id)
                    if not is_current:
                        end_reason = current_reason
                        _rd_log(
                            logging.INFO,
                            "stream_generator_cancelled",
                            monitor=monitor_id,
                            transport=transport,
                            stream_id=stream_id,
                            reason=current_reason,
                            diag=diag or "",
                        )
                        break
                    frame_count += 1
                    if trace and (frame_count <= 10 or frame_count % 120 == 0):
                        _rd_log(
                            logging.INFO,
                            "stream_frame_sent",
                            monitor=monitor_id,
                            transport=transport,
                            stream_id=stream_id,
                            frame=frame_count,
                            diag=diag or "",
                        )
                    yield frame
            except GeneratorExit:
                end_reason = "client_disconnect"
                return
            except Exception as exc:
                end_reason = f"error:{exc.__class__.__name__}"
                _set_capture_error(f"Ошибка потока: {exc}", state_key=state_key)
                _rd_log(
                    logging.ERROR,
                    "stream_generator_error",
                    monitor=monitor_id,
                    transport=transport,
                    stream_id=stream_id,
                    error=repr(exc),
                )
                return
            finally:
                _deactivate_stream(state_key, stream_id)
                _rd_log(
                    logging.INFO,
                    "stream_generator_end",
                    monitor=monitor_id,
                    transport=transport,
                    stream_id=stream_id,
                    diag=diag or "",
                    frames=frame_count,
                    seconds=round(max(0.0, time.time() - started_at), 3),
                    reason=end_reason,
                )

        return Response(
            _gen(),
            content_type=f"multipart/x-mixed-replace; boundary={boundary}",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
            direct_passthrough=True,
        )

    @expose("/snapshot")
    @has_access_api
    @permission_name("list")
    def snapshot(self):
        if not mss or not Image:
            _set_capture_error("Модуль захвата экрана не доступен.")
            return _json_error("Модуль захвата экрана не доступен.", 503)
        diag = (request.args.get("diag") or "").strip()
        monitor_id = _coerce_int(request.args.get("monitor"), 1) or 1
        quality = _coerce_int(request.args.get("quality"), _DEFAULT_QUALITY) or _DEFAULT_QUALITY
        scale = request.args.get("scale", "1")
        try:
            scale_val = float(scale)
        except Exception:
            scale_val = 1.0
        quality = max(_MIN_QUALITY, min(_MAX_QUALITY, quality))
        scale_val = max(0.2, min(1.0, scale_val))
        _rd_log(
            logging.DEBUG,
            "snapshot_request",
            monitor=monitor_id,
            quality=quality,
            scale=scale_val,
        )
        if diag:
            _rd_log(
                logging.INFO,
                "snapshot_diag_request",
                diag=diag,
                monitor=monitor_id,
                quality=quality,
                scale=scale_val,
            )

        with mss.mss() as sct:
            frame, error, blank = _grab_frame_bytes(sct, monitor_id, quality, scale_val)
        if error:
            _set_capture_error(error, blank=blank)
        else:
            _clear_capture_error()
        if not frame:
            _rd_log(logging.WARNING, "snapshot_failed", error=error or "no_frame")
            if diag:
                _rd_log(
                    logging.WARNING,
                    "snapshot_diag_failed",
                    diag=diag,
                    monitor=monitor_id,
                    error=error or "no_frame",
                    blank=blank,
                )
            return _json_error(error or "Не удалось получить кадр.", 503)
        if diag:
            _rd_log(
                logging.INFO,
                "snapshot_diag_ok",
                diag=diag,
                monitor=monitor_id,
                bytes=len(frame),
            )
        return Response(frame, mimetype="image/jpeg", headers={"Cache-Control": "no-cache"})

    @expose("/status")
    @has_access_api
    @permission_name("list")
    def status(self):
        ok_screen = bool(mss and Image)
        diag = (request.args.get("diag") or "").strip()
        monitor_id = _coerce_int(request.args.get("monitor"), 1) or 1
        check = (request.args.get("check") or "").lower() in ("1", "true", "yes", "on")
        capture_state = _get_capture_snapshot()
        error = str(capture_state.get("error") or "")
        screen_blank = bool(capture_state.get("blank"))
        last_error_at = float(capture_state.get("last_error_at", 0.0) or 0.0)
        last_ok_at = float(capture_state.get("last_ok_at", 0.0) or 0.0)
        if not ok_screen and not error:
            error = "Модуль захвата экрана не доступен."
        if check or diag:
            _rd_log(logging.INFO, "status_check", monitor=monitor_id, check=check, diag=diag or "")
        if check and ok_screen:
            quality = _coerce_int(request.args.get("quality"), _DEFAULT_QUALITY) or _DEFAULT_QUALITY
            scale = request.args.get("scale", "1")
            try:
                scale_val = float(scale)
            except Exception:
                scale_val = 1.0
            quality = max(_MIN_QUALITY, min(_MAX_QUALITY, quality))
            scale_val = max(0.2, min(1.0, scale_val))
            with mss.mss() as sct:
                _frame, error, blank = _grab_frame_bytes(sct, monitor_id, quality, scale_val)
            if error:
                _set_capture_error(error, blank=blank)
                ok_screen = False
            else:
                _clear_capture_error()
                error = ""
            capture_state = _get_capture_snapshot()
            screen_blank = bool(capture_state.get("blank"))
            last_error_at = float(capture_state.get("last_error_at", 0.0) or 0.0)
            last_ok_at = float(capture_state.get("last_ok_at", 0.0) or 0.0)

        payload = {
            "ok": ok_screen and not bool(error),
            "message": error or "",
            "screen_ok": ok_screen,
            "screen_error": error or "",
            "screen_blank": screen_blank,
            "screen_hint": error if screen_blank else "",
            "last_error_at": last_error_at,
            "last_ok_at": last_ok_at,
        }
        if diag:
            _rd_log(
                logging.INFO,
                "status_diag_result",
                diag=diag,
                monitor=monitor_id,
                ok=payload["ok"],
                screen_ok=payload["screen_ok"],
                screen_blank=payload["screen_blank"],
                screen_error=payload["screen_error"],
                last_error_at=payload["last_error_at"],
                last_ok_at=payload["last_ok_at"],
            )
        return jsonify(payload)

    @expose("/input", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def input(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, 403)

        payload = request.get_json(silent=True) or {}
        token = (
            request.headers.get("X-RDP-Token")
            or payload.get("token")
        )
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "input_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)

        action = (payload.get("action") or "").strip().lower()
        x = _coerce_int(payload.get("x"))
        y = _coerce_int(payload.get("y"))
        button = _normalize_button(payload.get("button"))
        keyboard_ok = bool(pyautogui or _windows_keyboard_backend_ready())
        hotkey_ok = bool(
            keyboard_ok
            or _windows_secure_attention_backend_ready()
            or _WINDOWS_LOCKWORKSTATION
        )
        backend = ""
        message = ""

        if action in {"mouse_move", "mouse_down", "mouse_up", "click", "double_click", "scroll"} and not pyautogui:
            return _json_error("Модуль управления мышью не доступен.", 503)
        if action in {"key_down", "key_up", "release_keys"} and not keyboard_ok:
            return _json_error("Модуль управления клавиатурой не доступен.", 503)
        if action == "hotkey" and not hotkey_ok:
            return _json_error("Модуль hotkey не доступен.", 503)
        if action == "text" and os.name != "nt" and not pyautogui:
            return _json_error("Модуль ввода текста не доступен.", 503)

        try:
            if action == "mouse_move":
                if x is None or y is None:
                    return _json_error("Некорректные координаты.", 400)
                pyautogui.moveTo(x, y)
                backend = "pyautogui"
            elif action == "mouse_down":
                if x is not None and y is not None:
                    pyautogui.mouseDown(x=x, y=y, button=button)
                else:
                    pyautogui.mouseDown(button=button)
                backend = "pyautogui"
            elif action == "mouse_up":
                if x is not None and y is not None:
                    pyautogui.mouseUp(x=x, y=y, button=button)
                else:
                    pyautogui.mouseUp(button=button)
                backend = "pyautogui"
            elif action == "click":
                if x is None or y is None:
                    return _json_error("Некорректные координаты.", 400)
                pyautogui.click(x=x, y=y, button=button)
                backend = "pyautogui"
            elif action == "double_click":
                if x is None or y is None:
                    return _json_error("Некорректные координаты.", 400)
                pyautogui.doubleClick(x=x, y=y, button=button)
                backend = "pyautogui"
            elif action == "scroll":
                delta = _coerce_int(payload.get("delta_y"), 0) or 0
                amount = int(-delta / 100 * 3)
                if amount == 0 and delta:
                    amount = -1 if delta > 0 else 1
                pyautogui.scroll(amount, x=x, y=y)
                delta_x = _coerce_int(payload.get("delta_x"), 0) or 0
                if hasattr(pyautogui, "hscroll") and delta_x:
                    h_amount = int(-delta_x / 100 * 3)
                    if h_amount == 0:
                        h_amount = -1 if delta_x > 0 else 1
                    pyautogui.hscroll(h_amount, x=x, y=y)
                backend = "pyautogui"
            elif action == "key_down":
                key = _normalize_key(payload.get("key"))
                if not key:
                    return _json_error("Неизвестная клавиша.", 400)
                backend = _send_key_transition_with_fallback(key, key_up=False)
            elif action == "key_up":
                key = _normalize_key(payload.get("key"))
                if not key:
                    return _json_error("Неизвестная клавиша.", 400)
                backend = _send_key_transition_with_fallback(key, key_up=True)
            elif action == "hotkey":
                keys = payload.get("keys") or []
                if not isinstance(keys, list):
                    return _json_error("Некорректные данные хоткея.", 400)
                normalized = []
                for item in keys:
                    key = _normalize_key(str(item))
                    if key:
                        normalized.append(key)
                if not normalized:
                    return _json_error("Некорректные клавиши хоткея.", 400)
                # Clean stale modifier state before explicit hotkey injection.
                _release_keyboard_state()
                if _is_secure_attention_hotkey(normalized):
                    backend, message = _send_secure_attention_hotkey(normalized)
                else:
                    backend = _send_hotkey_with_fallback(normalized)
                    message = "Комбинация клавиш отправлена."
            elif action == "text":
                text = str(payload.get("text") or "")
                _rd_log(logging.DEBUG, "input_text", length=len(text))
                _type_text(text)
                backend = "pyautogui" if pyautogui else "windows_api"
                message = "Текст отправлен."
            elif action == "release_keys":
                released, release_errors = _release_keyboard_state()
                _rd_log(
                    logging.DEBUG,
                    "input_release_keys",
                    released=",".join(released),
                    errors=" | ".join(release_errors),
                )
                return jsonify(
                    {
                        "ok": True,
                        "action": action,
                        "backend": "pyautogui" if pyautogui else "windows_api",
                        "released": released,
                        "errors": release_errors,
                        "message": "Состояние клавиш сброшено.",
                    }
                )
            else:
                return _json_error("Неизвестное действие.", 400)
        except RuntimeError as exc:
            _rd_log(logging.WARNING, "input_runtime_error", action=action, error=repr(exc))
            return _json_error(str(exc), 500)
        except Exception as exc:
            _rd_log(logging.ERROR, "input_error", action=action, error=repr(exc))
            return _json_error(f"Ошибка выполнения: {exc}", 500)

        if action in ("click", "double_click", "mouse_down", "mouse_up", "key_down", "key_up", "hotkey"):
            _rd_log(logging.DEBUG, "input_action", action=action, x=x, y=y, button=button)
        elif action == "scroll":
            _rd_log(logging.DEBUG, "input_scroll", x=x, y=y, dx=payload.get("delta_x"), dy=payload.get("delta_y"))
        response = {"ok": True, "action": action}
        if backend:
            response["backend"] = backend
        if message:
            response["message"] = message
        return jsonify(response)

    @expose("/clipboard", methods=["GET", "POST"])
    @has_access_api
    @permission_name("action")
    def clipboard(self):
        token = request.headers.get("X-RDP-Token")
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            token = token or payload.get("token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "clipboard_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)
        if request.method == "POST" and not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, 403)

        if request.method == "GET":
            text = _get_clipboard_text()
            _audit("remote_desktop_clipboard_get")
            _rd_log(logging.DEBUG, "clipboard_get", length=len(text or ""))
            return jsonify({"ok": True, "text": text})

        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text") or "")[:_MAX_TEXT_LEN]
        ok, error = _set_clipboard_text(text)
        if ok:
            _audit("remote_desktop_clipboard_set")
            _rd_log(logging.DEBUG, "clipboard_set", length=len(text))
            return jsonify({"ok": True})
        return _json_error(error or "Не удалось установить буфер обмена.", 500)

    @expose("/share/list")
    @has_access_api
    @permission_name("list")
    def share_list(self):
        token = request.headers.get("X-RDP-Token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "share_list_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)
        base_dir = current_app.config.get("BASE_DIR", os.getcwd())
        shared_dir = _ensure_shared_dir(base_dir)
        rel_path = request.args.get("path")
        absolute, items = _list_shared_dir(shared_dir, rel_path)
        _rd_log(logging.DEBUG, "share_list", path=rel_path or "", items=len(items))
        return jsonify({"ok": True, "root": str(shared_dir), "path": absolute, "items": items})

    @expose("/share/upload", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def share_upload(self):
        token = request.headers.get("X-RDP-Token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "share_upload_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, 403)

        base_dir = current_app.config.get("BASE_DIR", os.getcwd())
        shared_dir = _ensure_shared_dir(base_dir)
        target = _resolve_shared_path(shared_dir, request.form.get("path"))
        if not target:
            return _json_error("Недопустимый путь.", 400)
        if target.exists() and not target.is_dir():
            return _json_error("Целевая папка недоступна.", 400)
        if not target.exists():
            try:
                ensure_dir(target)
            except Exception as exc:
                return _json_error(f"Не удалось создать папку: {exc}", 500)

        files = request.files.getlist("files")
        if not files:
            return _json_error("Файлы не переданы.", 400)

        saved = []
        for storage in files:
            filename = Path(storage.filename or "").name
            if not filename:
                continue
            dest = (target / filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                continue
            storage.save(dest)
            saved.append(filename)

        if saved:
            _audit("remote_desktop_file_upload", target=str(target), details="; ".join(saved))
            _rd_log(logging.INFO, "share_upload", count=len(saved), path=str(target))
        return jsonify({"ok": True, "saved": saved})

    @expose("/share/download")
    @has_access_api
    @permission_name("action")
    def share_download(self):
        token = request.headers.get("X-RDP-Token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "share_download_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)
        base_dir = current_app.config.get("BASE_DIR", os.getcwd())
        shared_dir = _ensure_shared_dir(base_dir)
        rel_path = request.args.get("path") or ""
        target = _resolve_shared_path(shared_dir, rel_path)
        if not target or not target.exists() or not target.is_file():
            return _json_error("Файл не найден.", 404)
        _audit("remote_desktop_file_download", target=rel_path)
        _rd_log(logging.INFO, "share_download", path=rel_path)
        return send_file(target, as_attachment=True, download_name=target.name)

    @expose("/session", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def session_event(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, 403)
        payload = request.get_json(silent=True) or {}
        token = payload.get("token") or request.headers.get("X-RDP-Token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "session_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)
        state = str(payload.get("state") or "")
        if state not in ("start", "stop"):
            return _json_error("Некорректный статус.", 400)
        state_key = _capture_state_key()
        _set_stream_session_active(state_key, state == "start")
        _audit(f"remote_desktop_{state}")
        _rd_log(
            logging.INFO,
            "session_state",
            state=state,
            ua=request.headers.get("User-Agent"),
            referer=request.headers.get("Referer"),
        )
        return jsonify({"ok": True})

    @expose("/client-log", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def client_log(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, 403)
        payload = request.get_json(silent=True) or {}
        token = payload.get("token") or request.headers.get("X-RDP-Token")
        if not _is_token_valid(token):
            _rd_log(logging.WARNING, "client_log_denied", reason="token")
            return _json_error("Сессия не подтверждена.", 403)

        event = str(payload.get("event") or "").strip()[:80] or "client_event"
        level = str(payload.get("level") or "info").strip().lower()
        data = payload.get("data") or {}

        fields: dict[str, Any] = {"event": event}
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(key, str):
                    continue
                fields[f"c_{key}"] = value

        fields["ua"] = request.headers.get("User-Agent")
        fields["referer"] = request.headers.get("Referer")
        fields["ip"] = request.headers.get("X-Forwarded-For") or request.remote_addr or ""
        fields["origin"] = request.headers.get("Origin")
        fields["sec_fetch_mode"] = request.headers.get("Sec-Fetch-Mode")
        fields["sec_fetch_site"] = request.headers.get("Sec-Fetch-Site")

        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "warn": logging.WARNING,
            "error": logging.ERROR,
        }
        _rd_log(level_map.get(level, logging.INFO), "client_event", **fields)
        return jsonify({"ok": True})


def _get_clipboard_text() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CF_UNICODETEXT = 13

    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard_text(text: str) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Буфер обмена доступен только на Windows."
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False, "Не удалось импортировать системные библиотеки."

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    if not user32.OpenClipboard(None):
        return False, "Не удалось открыть буфер обмена."
    try:
        user32.EmptyClipboard()
        data = (text or "").encode("utf-16-le") + b"\x00\x00"
        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_global:
            return False, "Не удалось выделить память."
        ptr = kernel32.GlobalLock(h_global)
        if not ptr:
            return False, "Не удалось заблокировать память."
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h_global)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
            return False, "Не удалось записать буфер обмена."
        return True, ""
    finally:
        user32.CloseClipboard()
