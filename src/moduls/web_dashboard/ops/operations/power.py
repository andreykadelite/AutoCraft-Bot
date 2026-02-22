from __future__ import annotations

import subprocess
from typing import Dict, Tuple

_ACTION_ALIASES = {
    "shutdown": "shutdown",
    "poweroff": "shutdown",
    "off": "shutdown",
    "restart": "restart",
    "reboot": "restart",
    "sleep": "sleep",
    "standby": "sleep",
    "hibernate": "hibernate",
}

_ACTION_LABELS = {
    "shutdown": "Выключение",
    "restart": "Перезагрузка",
    "sleep": "Спящий режим",
    "hibernate": "Гибернация",
}

_COMMAND_TIMEOUT_SECONDS = 30


def power_action_label(action: str) -> str:
    normalized = normalize_power_action(action)
    return _ACTION_LABELS.get(normalized, normalized)


def normalize_power_action(action: str | None) -> str:
    text = (action or "").strip().lower()
    return _ACTION_ALIASES.get(text, "")


def available_power_actions() -> list[str]:
    return ["shutdown", "restart", "sleep", "hibernate"]


def _run_command(args: list[str], timeout: int = _COMMAND_TIMEOUT_SECONDS) -> Dict[str, object]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Команда превысила таймаут {timeout} сек."}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return {
            "ok": False,
            "stdout": stdout,
            "stderr": stderr or stdout or f"Код {result.returncode}",
        }
    return {"ok": True, "stdout": stdout, "stderr": stderr}


def _build_action_command(action: str) -> Tuple[list[str], str]:
    if action == "shutdown":
        return (
            ["shutdown", "/s", "/f", "/t", "0"],
            "Команда выключения отправлена системе.",
        )
    if action == "restart":
        return (
            ["shutdown", "/r", "/f", "/t", "0"],
            "Команда перезагрузки отправлена системе.",
        )
    if action == "hibernate":
        return (
            ["shutdown", "/h"],
            "Команда гибернации отправлена системе.",
        )
    if action == "sleep":
        script = (
            "$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$ok=[System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false);"
            "if (-not $ok) { throw 'ОС отклонила переход в спящий режим.' }"
            "'Команда спящего режима отправлена системе.'"
        )
        return (
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            "Команда спящего режима отправлена системе.",
        )
    raise ValueError("Неизвестное действие питания.")


def execute_power_action(action: str) -> Dict[str, object]:
    normalized = normalize_power_action(action)
    if not normalized:
        return {"ok": False, "stdout": "", "stderr": "Неизвестное действие питания."}

    command, success_message = _build_action_command(normalized)
    result = _run_command(command)
    if not result.get("ok"):
        return result

    output = str(result.get("stdout") or "").strip()
    verification_note = "Проверка применения: команда принята ОС (код завершения 0)."
    message = success_message
    if output:
        message = f"{success_message} {output}"
    return {
        "ok": True,
        "stdout": f"{message} {verification_note}".strip(),
        "stderr": "",
    }


def power_shutdown() -> Dict[str, object]:
    return execute_power_action("shutdown")


def power_restart() -> Dict[str, object]:
    return execute_power_action("restart")


def power_sleep() -> Dict[str, object]:
    return execute_power_action("sleep")


def power_hibernate() -> Dict[str, object]:
    return execute_power_action("hibernate")
