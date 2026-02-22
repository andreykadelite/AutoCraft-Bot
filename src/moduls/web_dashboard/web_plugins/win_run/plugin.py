# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Tuple

from flask import jsonify, request, url_for, current_app
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ...security import panel_has_access as has_access
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from flask import render_template_string

_LOGGER = logging.getLogger("panel.plugins")
_MAX_CMD_LEN = 500
_BUILTIN_CMDS = {
    "cd",
    "cls",
    "copy",
    "del",
    "dir",
    "echo",
    "erase",
    "exit",
    "mkdir",
    "move",
    "rd",
    "ren",
    "rmdir",
    "set",
    "start",
    "type",
}
_PROTOCOL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_SHELL_META = ("&", "|", ">", "<", "^")
_EXECUTABLE_EXTS = (".exe", ".bat", ".cmd", ".com")
_OPENABLE_EXTS = _EXECUTABLE_EXTS + (".msc", ".lnk", ".cpl")
_PATH_EXTS = _OPENABLE_EXTS
_SHELLEXEC_ERRORS = {
    2: "Файл не найден.",
    3: "Путь не найден.",
    5: "Доступ запрещен.",
    8: "Недостаточно памяти.",
    26: "Не найдено связанное приложение.",
    31: "Не удалось запустить приложение.",
}
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


def _is_url(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _is_protocol(text: str) -> bool:
    if not _PROTOCOL_RE.match(text):
        return False
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        # Drive path like C:\ or C:/ should not be treated as protocol.
        if len(text) == 2 or (len(text) >= 3 and text[2] in ("\\", "/")):
            return False
    return True


def _split_command(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=False)
    except ValueError:
        return [cmd]


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "on", "y", "да")


def _strip_quotes(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _normalize_command(cmd: str) -> str:
    cmd = os.path.expandvars(cmd or "")
    cmd = os.path.expanduser(cmd)
    return cmd.strip()


def _looks_like_unc(value: str) -> bool:
    return value.startswith("\\\\")


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if _looks_like_unc(value):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", value):
        return True
    return any(sep in value for sep in ("\\", "/"))


def _format_shell_params(args: list[str]) -> str:
    if not args:
        return ""
    try:
        return subprocess.list2cmdline(args)
    except Exception:
        return " ".join(args)


def _extract_path_from_command(cmd: str) -> tuple[str | None, str]:
    lowered = cmd.lower()
    for ext in _PATH_EXTS:
        idx = lowered.find(ext)
        if idx == -1:
            continue
        candidate = cmd[: idx + len(ext)].strip().strip('"')
        if os.path.exists(candidate):
            rest = cmd[idx + len(ext) :].strip()
            return candidate, rest
    return None, ""


def _resolve_app_path(exe: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except Exception:
        return None
    name = exe if exe.lower().endswith(".exe") else f"{exe}.exe"
    subkeys = [
        fr"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}",
        fr"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\App Paths\{name}",
    ]
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in subkeys:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    value = os.path.expandvars(str(value)) if value else ""
                    if value and os.path.isabs(value) and os.path.exists(value):
                        return value
                    try:
                        extra_path, _ = winreg.QueryValueEx(key, "Path")
                    except OSError:
                        extra_path = ""
                    if value and extra_path:
                        candidate = os.path.join(extra_path, value)
                        if os.path.exists(candidate):
                            return candidate
            except FileNotFoundError:
                continue
            except Exception:
                continue
    return None


def _resolve_executable(exe: str) -> str | None:
    if not exe:
        return None

    expanded = _normalize_command(exe)
    expanded = _strip_quotes(expanded)
    if os.path.isabs(expanded) or _looks_like_path(expanded):
        return expanded if os.path.exists(expanded) else None

    app_path = _resolve_app_path(expanded)
    if app_path:
        return app_path

    resolved = shutil.which(expanded)
    if resolved:
        return resolved

    if not os.path.splitext(expanded)[1]:
        for ext in _EXECUTABLE_EXTS:
            resolved = shutil.which(expanded + ext)
            if resolved:
                return resolved

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        for base in (Path(system_root), Path(system_root) / "System32", Path(system_root) / "SysWOW64"):
            candidate = base / expanded
            if candidate.exists():
                return str(candidate)
            if not os.path.splitext(expanded)[1]:
                candidate = base / f"{expanded}.exe"
                if candidate.exists():
                    return str(candidate)

    stem = os.path.splitext(expanded)[0]
    expanded_with_ext = expanded
    if not os.path.splitext(expanded)[1]:
        expanded_with_ext = f"{expanded}.exe"

    common_roots: list[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(env)
        if value:
            common_roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        common_roots.append(Path(local) / "Programs")
        common_roots.append(Path(local))
    program_data = os.environ.get("ProgramData")
    if program_data:
        common_roots.append(Path(program_data))

    for root in common_roots:
        candidate = root / stem / expanded_with_ext
        if candidate.exists():
            return str(candidate)
        candidate = root / stem / "bin" / expanded_with_ext
        if candidate.exists():
            return str(candidate)

    return None


def _needs_shell(cmd: str, parts: list[str]) -> bool:
    if any(token in cmd for token in _SHELL_META):
        return True
    if not parts:
        return True
    if parts[0].lower() in _BUILTIN_CMDS:
        return True
    return False


def _is_executable_path(path: str) -> bool:
    return Path(path).suffix.lower() in _EXECUTABLE_EXTS


def _shell_execute(target: str, params: str = "", verb: str = "open") -> tuple[bool, str]:
    if os.name != "nt":
        return False, "ShellExecute доступен только в Windows."
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell_execute = shell32.ShellExecuteW
        shell_execute.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        shell_execute.restype = wintypes.HINSTANCE
        result = shell_execute(None, verb, target, params or None, None, 1)
        if result <= 32:
            return False, _SHELLEXEC_ERRORS.get(result, f"Код ошибки {result}.")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _open_default_target(target: str) -> Tuple[bool, str]:
    try:
        if hasattr(os, "startfile"):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([target])
        return True, f"Открыто: {target}"
    except Exception as exc:
        return False, f"Не удалось открыть: {exc}"


def _launch_executable(path: str, args: list[str], run_as_admin: bool, label: str) -> Tuple[bool, str]:
    admin_note = ""
    if run_as_admin and os.name != "nt":
        admin_note = " Запуск от имени администратора доступен только в Windows."
        run_as_admin = False

    if os.name == "nt":
        params = _format_shell_params(args)
        verb = "runas" if run_as_admin else "open"
        ok, error = _shell_execute(path, params, verb)
        if ok:
            note = " Запрошено повышение прав." if run_as_admin else ""
            warning = _session_warning()
            return True, f"Запущено: {label}.{note}{warning}{admin_note}"
        if run_as_admin:
            return False, f"Не удалось запустить от имени администратора: {error}"

    try:
        proc = subprocess.Popen([path] + args)
        time.sleep(0.2)
        code = proc.poll()
        if code is not None and code != 0:
            return False, f"Команда завершилась с ошибкой (код {code}). Проверьте путь."
        warning = _session_warning()
        return True, f"Запущено: {label}.{warning}{admin_note}"
    except FileNotFoundError:
        return False, "Команда не найдена. Укажите полный путь к файлу."
    except Exception as exc:
        return False, f"Ошибка запуска: {exc}"


def _open_target(target: str, args: list[str], run_as_admin: bool, label: str) -> Tuple[bool, str]:
    target = _strip_quotes(target)
    if not _is_executable_path(target):
        ok, message = _open_default_target(target)
        if ok:
            admin_note = ""
            if run_as_admin:
                admin_note = " Запуск от имени администратора доступен только для исполняемых файлов."
            warning = _session_warning()
            return True, f"{message}.{admin_note}{warning}"
        return False, message
    return _launch_executable(target, args, run_as_admin, label)


def _run_shell_command(cmd: str, run_as_admin: bool) -> Tuple[bool, str]:
    if run_as_admin and os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        ok, error = _shell_execute(comspec, f"/c {cmd}", "runas")
        if ok:
            warning = _session_warning()
            return True, f"Команда отправлена на выполнение с повышением прав: {cmd}.{warning}"
        return False, f"Не удалось запустить от имени администратора: {error}"
    if run_as_admin:
        return False, "Запуск от имени администратора доступен только в Windows."
    try:
        proc = subprocess.Popen(cmd, shell=True)
        time.sleep(0.2)
        code = proc.poll()
        if code is not None and code != 0:
            return False, f"Команда завершилась с ошибкой (код {code})."
        pid_info = f" (PID {proc.pid})" if getattr(proc, "pid", None) else ""
        warning = _session_warning()
        return True, f"Команда отправлена на выполнение{pid_info}: {cmd}.{warning}"
    except FileNotFoundError:
        return False, "Команда не найдена. Проверьте имя или путь."
    except Exception as exc:
        return False, f"Ошибка запуска: {exc}"


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
                " Команда запущена в системной сессии; "
                "GUI-приложение может не отобразиться на рабочем столе."
            )
    except Exception:
        return ""
    return ""


def _run_winr_command(raw_command: str, run_as_admin: bool = False) -> Tuple[bool, str]:
    cmd = _normalize_command(raw_command)
    if not cmd:
        return False, "Команда не указана. Введите путь, URL или команду."

    extracted_path, extracted_args = _extract_path_from_command(cmd)
    parts = _split_command(cmd)
    simple_token = parts[0] if parts else ""
    args = parts[1:] if len(parts) > 1 else []

    if _is_url(cmd):
        ok, message = _open_default_target(cmd)
        if ok:
            admin_note = ""
            if run_as_admin:
                admin_note = " Запуск от имени администратора недоступен для URL."
            warning = _session_warning()
            return True, f"{message}.{admin_note}{warning}"
        return False, message

    if extracted_path:
        arg_parts = _split_command(extracted_args) if extracted_args else []
        return _open_target(extracted_path, arg_parts, run_as_admin, cmd)

    token = _strip_quotes(simple_token)
    if token and _is_protocol(token):
        ok, message = _open_default_target(token)
        if ok:
            admin_note = ""
            if run_as_admin:
                admin_note = " Запуск от имени администратора недоступен для протоколов."
            warning = _session_warning()
            return True, f"{message}.{admin_note}{warning}"
        return False, message

    if token and _looks_like_path(token):
        candidate = _normalize_command(token)
        if os.path.exists(candidate) or _looks_like_unc(candidate):
            return _open_target(candidate, args, run_as_admin, cmd)

    resolved = _resolve_executable(token)
    if resolved:
        return _open_target(resolved, args, run_as_admin, cmd)

    if cmd == simple_token and "." in simple_token:
        return False, "Файл не найден. Укажите полный путь или добавьте программу в PATH."

    if not _needs_shell(cmd, parts):
        return False, "Команда не найдена. Укажите полный путь или добавьте программу в PATH."

    return _run_shell_command(cmd, run_as_admin)


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


class WinRunView(BaseView):
    route_base = "/plugins/winrun"
    base_permissions = ["can_list"]

    def _render(self):
        template_source = _load_template()
        if not template_source:
            return "Шаблон расширения не найден.", 500
        run_url = url_for(f"{self.__class__.__name__}.run")
        return render_template_string(
            template_source,
            run_url=run_url,
            static_url="/plugins/winrun/static",
            base_template=self.appbuilder.base_template,
            appbuilder=self.appbuilder,
            current_app=current_app,
        )

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        return self._render()

    @expose("/run", methods=["POST"])
    @has_access
    @permission_name("list")
    def run(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403
        payload = request.get_json(silent=True) or {}
        command = str(payload.get("command") or request.form.get("command") or "").strip()
        run_as_admin = _parse_bool(payload.get("run_as_admin") or request.form.get("run_as_admin"))
        if not command:
            return jsonify({"ok": False, "error": "Команда пустая."}), 400
        if len(command) > _MAX_CMD_LEN:
            return jsonify({"ok": False, "error": "Слишком длинная команда."}), 400

        ok, message = _run_winr_command(command, run_as_admin=run_as_admin)
        user = getattr(current_user, "username", "web")
        try:
            _LOGGER.info(
                "winrun user=%s ok=%s admin=%s cmd=%s msg=%s",
                user,
                ok,
                run_as_admin,
                command[:200],
                message[:200],
            )
        except Exception:
            pass
        status = 200 if ok else 500
        payload = {"ok": ok, "message": message}
        if not ok:
            payload["error"] = message
        return jsonify(payload), status


def register(appbuilder, app, plugin):
    global _TEMPLATE_ROOT
    try:
        if plugin and getattr(plugin, "root", None):
            _TEMPLATE_ROOT = Path(plugin.root)
    except Exception:
        pass
    return WinRunView
