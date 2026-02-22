# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Tuple

from flask import current_app, jsonify, render_template_string, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ...security import panel_has_access as has_access
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

_LOGGER = logging.getLogger("panel.plugins")
_MAX_TITLE_LEN = 80
_MAX_BODY_LEN = 500
_DEFAULT_TITLE = "Уведомление"
_DEFAULT_DURATION_MS = 5000
_TEMPLATE_ROOT: Path | None = None


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


def _normalize_text(value: Any, limit: int) -> tuple[str, bool]:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "", False
    if len(text) <= limit:
        return text, False
    trimmed = text[: max(0, limit - 3)].rstrip()
    return f"{trimmed}...", True


def _escape_powershell(text: str) -> str:
    return text.replace("'", "''")


def _find_powershell() -> str | None:
    for name in ("powershell", "pwsh"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _build_script(title: str, body: str, duration_ms: int) -> str:
    title_safe = _escape_powershell(title)
    body_safe = _escape_powershell(body)
    return (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "Add-Type -AssemblyName System.Drawing | Out-Null; "
        "$notify = New-Object System.Windows.Forms.NotifyIcon; "
        "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
        "$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info; "
        f"$notify.BalloonTipTitle = '{title_safe}'; "
        f"$notify.BalloonTipText = '{body_safe}'; "
        "$notify.Visible = $true; "
        f"$notify.ShowBalloonTip({duration_ms}); "
        f"Start-Sleep -Milliseconds {duration_ms + 500}; "
        "$notify.Dispose();"
    )


def _session_warning() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        session_id = ctypes.c_ulong()
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            return ""
        active_id = kernel32.WTSGetActiveConsoleSessionId()
        if active_id != session_id.value:
            return (
                " Уведомление отправлено в системную сессию; "
                "оно может не появиться на рабочем столе."
            )
    except Exception:
        return ""
    return ""


def _send_notification(title: str, body: str) -> Tuple[bool, str]:
    if os.name != "nt":
        return False, "Уведомления доступны только в Windows."

    powershell = _find_powershell()
    if not powershell:
        return False, "PowerShell не найден. Проверьте установку Windows PowerShell или PowerShell 7."

    script = _build_script(title, body, _DEFAULT_DURATION_MS)
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except FileNotFoundError:
        return False, "PowerShell не найден. Проверьте путь к исполняемому файлу."
    except Exception as exc:
        return False, f"Не удалось отправить уведомление: {exc}"

    warning = _session_warning()
    return True, f"Уведомление отправлено.{warning}"


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


class NotifyCenterView(BaseView):
    route_base = "/plugins/notify_center"
    base_permissions = ["can_list"]

    def _render(self):
        template_source = _load_template()
        if not template_source:
            return "Шаблон расширения не найден.", 500
        send_url = url_for(f"{self.__class__.__name__}.send")
        return render_template_string(
            template_source,
            send_url=send_url,
            static_url="/plugins/notify_center/static",
            max_title_len=_MAX_TITLE_LEN,
            max_body_len=_MAX_BODY_LEN,
            base_template=self.appbuilder.base_template,
            appbuilder=self.appbuilder,
            current_app=current_app,
        )

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        return self._render()

    @expose("/send", methods=["POST"])
    @has_access
    @permission_name("list")
    def send(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403
        payload = request.get_json(silent=True) or {}
        raw_title = payload.get("title") or request.form.get("title") or ""
        raw_text = (
            payload.get("message")
            or payload.get("text")
            or request.form.get("message")
            or request.form.get("text")
            or ""
        )

        title, title_trimmed = _normalize_text(raw_title, _MAX_TITLE_LEN)
        message, message_trimmed = _normalize_text(raw_text, _MAX_BODY_LEN)
        if not message:
            return jsonify({"ok": False, "error": "Текст уведомления пустой."}), 400
        if not title:
            title = _DEFAULT_TITLE

        ok, info = _send_notification(title, message)
        user = getattr(current_user, "username", "web")
        try:
            _LOGGER.info(
                "notify_center user=%s ok=%s title=%s message=%s",
                user,
                ok,
                title[:120],
                message[:200],
            )
        except Exception:
            pass
        status = 200 if ok else 500
        response = {"ok": ok, "message": info}
        if title_trimmed or message_trimmed:
            response["note"] = "Текст был сокращен до допустимой длины."
        if not ok:
            response["error"] = info
        return jsonify(response), status


def register(appbuilder, app, plugin):
    global _TEMPLATE_ROOT
    try:
        if plugin and getattr(plugin, "root", None):
            _TEMPLATE_ROOT = Path(plugin.root)
    except Exception:
        pass
    return NotifyCenterView
