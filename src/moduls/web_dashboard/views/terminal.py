from __future__ import annotations

import re
import subprocess
import time
from typing import Iterable, Tuple

from flask import Response, request, stream_with_context
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from flask_wtf.csrf import validate_csrf

_UNSAFE_PATTERN = re.compile(r"[&|><`;$]")

_SAFE_CMD_COMMANDS = {
    "dir",
    "ipconfig",
    "whoami",
    "tasklist",
    "systeminfo",
    "netstat",
    "ping",
    "tracert",
    "hostname",
    "ver",
}

_SAFE_PS_COMMANDS = {
    "get-process",
    "get-service",
    "get-computerinfo",
    "get-childitem",
    "get-volume",
    "get-psdrive",
    "get-netipconfiguration",
    "get-netipaddress",
    "get-date",
    "get-uptime",
    "get-eventlog",
}

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)


def _is_csrf_valid(token: str) -> bool:
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _get_csrf_token() -> str:
    return (
        request.args.get("csrf_token", "")
        or request.headers.get("X-CSRFToken", "")
        or request.headers.get("X-CSRF-Token", "")
        or request.form.get("csrf_token", "")
    )


def _normalize_shell(value: str) -> str:
    value = (value or "").strip().lower()
    return "cmd" if value == "cmd" else "powershell"


def _first_token(command: str) -> str:
    match = re.match(r"\s*(\S+)", command)
    if not match:
        return ""
    token = match.group(1).strip().strip('"').strip("'")
    return token


def _is_safe_allowed(command: str, shell: str) -> bool:
    token = _first_token(command).lower()
    if token.endswith(".exe"):
        token = token[:-4]
    if shell == "cmd":
        return token in _SAFE_CMD_COMMANDS
    return token in _SAFE_PS_COMMANDS


def _needs_confirmation(command: str, shell: str) -> bool:
    if _UNSAFE_PATTERN.search(command):
        return True
    return not _is_safe_allowed(command, shell)


def _validate_command(
    command: str,
    shell: str,
    safe_mode: bool,
    confirmed: bool,
) -> Tuple[bool, str, str]:
    command = (command or "").strip()
    if not command:
        return False, "Введите команду.", command
    if len(command) > 4000:
        return False, "Слишком длинная команда.", command
    if "\n" in command or "\r" in command:
        return False, "Команда должна быть одной строкой.", command
    if not safe_mode:
        return True, "", command

    if _needs_confirmation(command, shell) and not confirmed:
        return (
            False,
            "Команда не входит в список безопасных или содержит спецсимволы. "
            "Подтвердите выполнение.",
            command,
        )
    return True, "", command


def _build_process(command: str, shell: str) -> Tuple[subprocess.Popen, str]:
    if shell == "cmd":
        args = ["cmd.exe", "/d", "/c", command]
        encoding = "cp866"
    else:
        ps_prefix = (
            "$OutputEncoding=[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            " $ProgressPreference='SilentlyContinue'; "
        )
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_prefix + command,
        ]
        encoding = "utf-8"

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=encoding,
        errors="ignore",
        bufsize=1,
    )
    return proc, encoding


def _terminate_process(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


class TerminalView(BaseView):
    route_base = "/terminal"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        return self.render_template(
            "terminal.html",
            safe_cmd_commands=sorted(_SAFE_CMD_COMMANDS),
            safe_ps_commands=sorted(_SAFE_PS_COMMANDS),
            unsafe_pattern=_UNSAFE_PATTERN.pattern,
        )

    @expose("/stream")
    @has_access_api
    @permission_name("action")
    def stream(self):
        csrf_token = _get_csrf_token()
        if not _is_csrf_valid(csrf_token):
            return Response(
                f"data: {_CSRF_FAILURE_MESSAGE}\n\nevent: done\ndata: 1\n\n",
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        command = request.args.get("cmd", "")
        shell = _normalize_shell(request.args.get("shell", "powershell"))
        safe_mode = (request.args.get("safe", "1") or "1") in ("1", "true", "yes", "on")
        confirmed = (request.args.get("confirm", "0") or "0") in ("1", "true", "yes", "on")

        ok, error, command = _validate_command(command, shell, safe_mode, confirmed)
        if not ok:
            return Response(
                f"data: {error}\n\nevent: done\ndata: 1\n\n",
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        @stream_with_context
        def generate() -> Iterable[str]:
            proc = None
            try:
                proc, _encoding = _build_process(command, shell)
                yield "data: Запуск команды...\n\n"
                assert proc.stdout is not None
                assert proc.stdout is not None
                buffer = ""
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while True:
                        match = re.search(r"[\r\n]", buffer)
                        if not match:
                            break
                        idx = match.start()
                        if buffer[idx] == "\r" and idx + 1 >= len(buffer):
                            break
                        line = buffer[:idx]
                        if buffer[idx : idx + 2] == "\r\n":
                            buffer = buffer[idx + 2 :]
                        else:
                            buffer = buffer[idx + 1 :]
                        yield f"data: {line}\n\n"
                if buffer:
                    yield f"data: {buffer}\n\n"
                proc.wait()
                yield f"event: done\ndata: {proc.returncode}\n\n"
            except GeneratorExit:
                if proc is not None:
                    _terminate_process(proc)
                raise
            except Exception as exc:
                if proc is not None:
                    _terminate_process(proc)
                yield f"data: Ошибка выполнения: {exc}\n\n"
                yield "event: done\ndata: 1\n\n"
            finally:
                if proc is not None and proc.poll() is None:
                    _terminate_process(proc)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
