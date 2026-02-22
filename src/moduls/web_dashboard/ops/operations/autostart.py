# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover - platform specific
    winreg = None  # type: ignore

_PS_TIMEOUT_SECONDS = 25
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*]+')
_DISABLED_SUBKEY = "__AutoCraftDisabled"


@dataclass(frozen=True)
class RegistryLocation:
    id: str
    root: str
    subkey: str
    label: str
    view: str  # default | 32 | 64


def _is_windows() -> bool:
    return os.name == "nt"


def _is_64bit_os() -> bool:
    arch = platform.machine().lower()
    return "64" in arch


def get_registry_locations() -> List[Dict[str, str]]:
    return [{"id": loc.id, "label": loc.label} for loc in _registry_locations()]


def get_startup_locations() -> List[Dict[str, str]]:
    return [
        {"id": loc_id, "label": data["label"], "path": data.get("path", "")}
        for loc_id, data in _startup_locations().items()
    ]


def list_registry_autostart() -> Tuple[List[Dict[str, Any]], str | None]:
    if not _is_windows() or winreg is None:
        return [], "Реестр доступен только на Windows."
    entries: List[Dict[str, Any]] = []
    for loc in _registry_locations():
        entries.extend(_read_registry_values(loc, disabled=False))
        entries.extend(_read_registry_values(loc, disabled=True))
    entries.sort(
        key=lambda item: (
            item.get("location_label", ""),
            0 if item.get("enabled", True) else 1,
            item.get("name", "").lower(),
        )
    )
    return entries, None


def list_startup_folders() -> Tuple[List[Dict[str, Any]], str | None]:
    if not _is_windows():
        return [], "Папка автозагрузки доступна только на Windows."
    entries: List[Dict[str, Any]] = []
    for location_id, data in _startup_locations().items():
        path = data.get("path") or ""
        if not path:
            continue
        if not os.path.isdir(path):
            continue
        try:
            for name in sorted(os.listdir(path)):
                full_path = os.path.join(path, name)
                if not os.path.isfile(full_path):
                    continue
                enabled = not name.lower().endswith(".disabled")
                display_name = name[:-9] if name.lower().endswith(".disabled") else name
                entries.append(
                    {
                        "location_id": location_id,
                        "location_label": data.get("label", ""),
                        "name": name,
                        "display_name": display_name,
                        "enabled": enabled,
                        "path": full_path,
                        "kind": os.path.splitext(display_name)[1].lower().lstrip(".") or "file",
                        "command": _read_startup_command_hint(full_path),
                    }
                )
        except Exception:
            continue
    entries.sort(
        key=lambda item: (
            item.get("location_label", ""),
            0 if item.get("enabled", True) else 1,
            item.get("display_name", "").lower(),
        )
    )
    return entries, None


def list_autostart_tasks() -> Tuple[List[Dict[str, Any]], str | None]:
    if not _is_windows():
        return [], "Планировщик задач доступен только на Windows."
    script = """
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$tasks = Get-ScheduledTask | Where-Object { $_.Triggers -and ($_.Triggers | Where-Object { $_.TriggerType -in 'Logon','Boot' }) }
$items = foreach ($task in $tasks) {
    $triggers = $task.Triggers | Where-Object { $_.TriggerType -in 'Logon','Boot' } | ForEach-Object { $_.TriggerType }
    $actions = $task.Actions | ForEach-Object {
        if ($_.Execute) {
            $line = $_.Execute
            if ($_.Arguments) { $line = $line + ' ' + $_.Arguments }
            $line
        } else {
            $_.ToString()
        }
    }
    $fullName = if ($task.TaskPath -eq '\\') { '\\' + $task.TaskName } else { $task.TaskPath + $task.TaskName }
    [PSCustomObject]@{
        Name = $task.TaskName
        Path = $task.TaskPath
        FullName = $fullName
        State = $task.State
        Author = $task.Author
        UserId = $task.Principal.UserId
        RunLevel = $task.Principal.RunLevel
        Triggers = ($triggers -join ', ')
        Actions = ($actions -join ' | ')
    }
}
if (-not $items) { $items = @() }
$items | ConvertTo-Json -Depth 6
"""
    data, err = _run_powershell_json(script)
    if err:
        return [], err
    if data is None:
        return [], "Нет данных от планировщика задач."
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [], "Неожиданный формат данных планировщика."
    result: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        state = str(item.get("State") or "")
        enabled = state.lower() != "disabled"
        result.append(
            {
                "name": str(item.get("Name") or ""),
                "path": str(item.get("Path") or ""),
                "full_name": str(item.get("FullName") or ""),
                "state": state,
                "enabled": enabled,
                "author": str(item.get("Author") or ""),
                "user": str(item.get("UserId") or ""),
                "run_level": str(item.get("RunLevel") or ""),
                "triggers": str(item.get("Triggers") or ""),
                "actions": str(item.get("Actions") or ""),
            }
        )
    result.sort(key=lambda item: (item.get("path", ""), item.get("name", "").lower()))
    return result, None


def autostart_folder_add(
    location_id: str,
    name: str,
    command: str,
    working_dir: str = "",
) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Автозапуск доступен только на Windows."}
    location = _startup_locations().get(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная папка автозагрузки."}
    command = (command or "").strip()
    if not command:
        return {"ok": False, "stdout": "", "stderr": "Укажите команду для запуска."}
    safe_name = _sanitize_filename(name)
    if not safe_name:
        return {"ok": False, "stdout": "", "stderr": "Укажите корректное имя."}
    if not safe_name.lower().endswith((".cmd", ".bat")):
        safe_name += ".cmd"
    base_path = location.get("path") or ""
    if not base_path:
        return {"ok": False, "stdout": "", "stderr": "Не удалось определить путь папки автозагрузки."}
    os.makedirs(base_path, exist_ok=True)
    target_path = os.path.join(base_path, safe_name)
    if os.path.exists(target_path):
        return {"ok": False, "stdout": "", "stderr": "Файл автозагрузки уже существует."}
    try:
        lines = ["@echo off", "chcp 65001 >nul"]
        if working_dir:
            lines.append(f'cd /d "{working_dir}"')
        lines.append(f'start "" {command}')
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("\r\n".join(lines) + "\r\n")
        return {"ok": True, "stdout": f"Добавлено: {safe_name}", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось создать файл автозагрузки: {exc}"}


def autostart_folder_remove(location_id: str, name: str) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Автозапуск доступен только на Windows."}
    location = _startup_locations().get(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная папка автозагрузки."}
    base_path = location.get("path") or ""
    if not base_path:
        return {"ok": False, "stdout": "", "stderr": "Не удалось определить путь папки автозагрузки."}
    filename = (name or "").strip()
    if not filename:
        return {"ok": False, "stdout": "", "stderr": "Имя файла не задано."}
    if any(sep in filename for sep in ("/", "\\")):
        return {"ok": False, "stdout": "", "stderr": "Некорректное имя файла."}
    base_abs = os.path.abspath(base_path)
    target_path = os.path.abspath(os.path.join(base_abs, filename))
    if not target_path.startswith(base_abs + os.sep):
        return {"ok": False, "stdout": "", "stderr": "Неверный путь к файлу."}
    if not os.path.exists(target_path):
        return {"ok": True, "stdout": "Файл автозагрузки уже удален.", "stderr": ""}
    try:
        os.remove(target_path)
        return {"ok": True, "stdout": "Файл удален.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось удалить файл: {exc}"}


def autostart_folder_set_enabled(location_id: str, name: str, enabled: bool) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Автозапуск доступен только на Windows."}
    location = _startup_locations().get(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная папка автозагрузки."}
    base_path = location.get("path") or ""
    if not base_path:
        return {"ok": False, "stdout": "", "stderr": "Не удалось определить путь папки автозагрузки."}
    filename = (name or "").strip()
    if not filename:
        return {"ok": False, "stdout": "", "stderr": "Имя файла не задано."}
    if any(sep in filename for sep in ("/", "\\")):
        return {"ok": False, "stdout": "", "stderr": "Некорректное имя файла."}
    base_abs = os.path.abspath(base_path)
    source_path = os.path.abspath(os.path.join(base_abs, filename))
    if not source_path.startswith(base_abs + os.sep):
        return {"ok": False, "stdout": "", "stderr": "Неверный путь к файлу."}

    is_disabled = filename.lower().endswith(".disabled")
    if enabled and not is_disabled:
        return {"ok": True, "stdout": "Файл уже включен.", "stderr": ""}
    if not enabled and is_disabled:
        return {"ok": True, "stdout": "Файл уже отключен.", "stderr": ""}

    if enabled and is_disabled:
        target_name = filename[:-9]
    else:
        target_name = filename + ".disabled"

    target_path = os.path.abspath(os.path.join(base_abs, target_name))
    if not target_path.startswith(base_abs + os.sep):
        return {"ok": False, "stdout": "", "stderr": "Неверный путь к файлу."}
    if os.path.exists(target_path):
        return {"ok": False, "stdout": "", "stderr": "Файл с целевым именем уже существует."}
    if not os.path.exists(source_path):
        return {"ok": False, "stdout": "", "stderr": "Файл автозагрузки не найден."}

    try:
        os.rename(source_path, target_path)
        status = "включен" if enabled else "отключен"
        return {"ok": True, "stdout": f"Файл {status}.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось изменить статус файла: {exc}"}


def autostart_registry_add(
    location_id: str,
    name: str,
    command: str,
    value_type: str = "REG_SZ",
) -> Dict[str, Any]:
    if not _is_windows() or winreg is None:
        return {"ok": False, "stdout": "", "stderr": "Реестр доступен только на Windows."}
    location = _registry_location_by_id(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная ветка реестра."}
    name = (name or "").strip()
    command = (command or "").strip()
    if not name:
        return {"ok": False, "stdout": "", "stderr": "Укажите имя значения."}
    if not command:
        return {"ok": False, "stdout": "", "stderr": "Укажите команду."}
    reg_type = _registry_type_from_name(value_type)
    try:
        access = _registry_access(write=True, view=location.view)
        root_const = _registry_root_const(location.root)
        if root_const is None:
            return {"ok": False, "stdout": "", "stderr": "Не удалось открыть корень реестра."}
        with winreg.CreateKeyEx(root_const, location.subkey, 0, access) as key:
            winreg.SetValueEx(key, name, 0, reg_type, command)
        return {"ok": True, "stdout": "Запись добавлена.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось добавить запись: {exc}"}


def autostart_registry_remove(location_id: str, name: str) -> Dict[str, Any]:
    if not _is_windows() or winreg is None:
        return {"ok": False, "stdout": "", "stderr": "Реестр доступен только на Windows."}
    location = _registry_location_by_id(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная ветка реестра."}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "stdout": "", "stderr": "Имя значения не задано."}
    try:
        access = _registry_access(write=True, view=location.view)
        root_const = _registry_root_const(location.root)
        if root_const is None:
            return {"ok": False, "stdout": "", "stderr": "Не удалось открыть корень реестра."}
        with winreg.OpenKey(root_const, location.subkey, 0, access) as key:
            try:
                winreg.DeleteValue(key, name)
            except (FileNotFoundError, OSError):
                return {"ok": True, "stdout": "Запись уже удалена.", "stderr": ""}
        return {"ok": True, "stdout": "Запись удалена.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось удалить запись: {exc}"}


def autostart_registry_set_enabled(location_id: str, name: str, enabled: bool) -> Dict[str, Any]:
    if not _is_windows() or winreg is None:
        return {"ok": False, "stdout": "", "stderr": "Реестр доступен только на Windows."}
    location = _registry_location_by_id(location_id)
    if not location:
        return {"ok": False, "stdout": "", "stderr": "Неизвестная ветка реестра."}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "stdout": "", "stderr": "Имя значения не задано."}
    root_const = _registry_root_const(location.root)
    if root_const is None:
        return {"ok": False, "stdout": "", "stderr": "Не удалось открыть корень реестра."}

    access_write = _registry_access(write=True, view=location.view)
    access_read = _registry_access(write=False, view=location.view)
    enabled_subkey = _disabled_subkey_path(location.subkey)

    try:
        if enabled:
            # move from disabled -> main
            try:
                with winreg.OpenKey(root_const, enabled_subkey, 0, access_read) as src_key:
                    data, value_type = winreg.QueryValueEx(src_key, name)
            except FileNotFoundError:
                # already enabled?
                try:
                    with winreg.OpenKey(root_const, location.subkey, 0, access_read) as main_key:
                        winreg.QueryValueEx(main_key, name)
                    return {"ok": True, "stdout": "Запись уже включена.", "stderr": ""}
                except Exception:
                    return {"ok": False, "stdout": "", "stderr": "Отключенная запись не найдена."}
            with winreg.CreateKeyEx(root_const, location.subkey, 0, access_write) as main_key:
                winreg.SetValueEx(main_key, name, 0, value_type, data)
            with winreg.OpenKey(root_const, enabled_subkey, 0, access_write) as src_key:
                try:
                    winreg.DeleteValue(src_key, name)
                except FileNotFoundError:
                    pass
            return {"ok": True, "stdout": "Запись включена.", "stderr": ""}

        # disable: move from main -> disabled subkey
        try:
            with winreg.OpenKey(root_const, location.subkey, 0, access_read) as main_key:
                data, value_type = winreg.QueryValueEx(main_key, name)
        except (FileNotFoundError, OSError):
            try:
                with winreg.OpenKey(root_const, enabled_subkey, 0, access_read) as src_key:
                    winreg.QueryValueEx(src_key, name)
                return {"ok": True, "stdout": "Запись уже отключена.", "stderr": ""}
            except Exception:
                return {"ok": False, "stdout": "", "stderr": "Запись не найдена."}

        with winreg.CreateKeyEx(root_const, enabled_subkey, 0, access_write) as dst_key:
            winreg.SetValueEx(dst_key, name, 0, value_type, data)
        with winreg.OpenKey(root_const, location.subkey, 0, access_write) as main_key:
            try:
                winreg.DeleteValue(main_key, name)
            except FileNotFoundError:
                pass
        return {"ok": True, "stdout": "Запись отключена.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось изменить статус: {exc}"}


def autostart_task_add(
    name: str,
    command: str,
    trigger: str = "logon",
    run_level: str = "limited",
    task_path: str = "\\",
    working_dir: str = "",
) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Планировщик доступен только на Windows."}
    name = (name or "").strip()
    command = (command or "").strip()
    if not name:
        return {"ok": False, "stdout": "", "stderr": "Укажите имя задачи."}
    if not command:
        return {"ok": False, "stdout": "", "stderr": "Укажите команду для запуска."}
    full_name = _build_task_full_name(name, task_path)
    if not full_name:
        return {"ok": False, "stdout": "", "stderr": "Некорректное имя задачи."}

    trigger = (trigger or "logon").lower()
    if trigger not in ("logon", "boot"):
        trigger = "logon"
    schedule = "ONLOGON" if trigger == "logon" else "ONSTART"
    run_level = (run_level or "limited").lower()
    rl = "HIGHEST" if run_level in ("highest", "admin") else "LIMITED"

    task_command = _compose_task_command(command, working_dir)

    args = [
        "schtasks",
        "/Create",
        "/SC",
        schedule,
        "/TN",
        full_name,
        "/TR",
        task_command,
        "/RL",
        rl,
        "/F",
    ]

    if trigger == "boot":
        args += ["/RU", "SYSTEM"]

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return {"ok": False, "stdout": output, "stderr": err or output or "Ошибка создания задачи."}
    return {"ok": True, "stdout": output or "Задача создана.", "stderr": ""}


def autostart_task_remove(full_name: str) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Планировщик доступен только на Windows."}
    full_name = (full_name or "").strip()
    if not full_name:
        return {"ok": False, "stdout": "", "stderr": "Имя задачи не задано."}
    args = ["schtasks", "/Delete", "/TN", full_name, "/F"]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return {"ok": False, "stdout": output, "stderr": err or output or "Ошибка удаления задачи."}
    return {"ok": True, "stdout": output or "Задача удалена.", "stderr": ""}


def autostart_task_set_enabled(full_name: str, enabled: bool) -> Dict[str, Any]:
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Планировщик доступен только на Windows."}
    full_name = (full_name or "").strip()
    if not full_name:
        return {"ok": False, "stdout": "", "stderr": "Имя задачи не задано."}
    switch = "/ENABLE" if enabled else "/DISABLE"
    args = ["schtasks", "/Change", "/TN", full_name, switch]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return {"ok": False, "stdout": output, "stderr": err or output or "Ошибка изменения задачи."}
    return {"ok": True, "stdout": output or "Статус задачи изменен.", "stderr": ""}


def _registry_locations() -> List[RegistryLocation]:
    locations = [
        RegistryLocation(
            "hkcu_run",
            "HKCU",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            "HKCU\\...\\Run",
            "default",
        ),
        RegistryLocation(
            "hkcu_runonce",
            "HKCU",
            r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            "HKCU\\...\\RunOnce",
            "default",
        ),
        RegistryLocation(
            "hkcu_policy_run",
            "HKCU",
            r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
            "HKCU\\Policies\\Explorer\\Run",
            "default",
        ),
    ]

    if _is_64bit_os():
        locations += [
            RegistryLocation(
                "hklm_run_64",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "HKLM\\...\\Run (64-bit)",
                "64",
            ),
            RegistryLocation(
                "hklm_runonce_64",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                "HKLM\\...\\RunOnce (64-bit)",
                "64",
            ),
            RegistryLocation(
                "hklm_policy_run_64",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
                "HKLM\\Policies\\Explorer\\Run (64-bit)",
                "64",
            ),
            RegistryLocation(
                "hklm_run_32",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "HKLM\\...\\Run (32-bit)",
                "32",
            ),
            RegistryLocation(
                "hklm_runonce_32",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                "HKLM\\...\\RunOnce (32-bit)",
                "32",
            ),
            RegistryLocation(
                "hklm_policy_run_32",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
                "HKLM\\Policies\\Explorer\\Run (32-bit)",
                "32",
            ),
        ]
    else:
        locations += [
            RegistryLocation(
                "hklm_run",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "HKLM\\...\\Run",
                "default",
            ),
            RegistryLocation(
                "hklm_runonce",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                "HKLM\\...\\RunOnce",
                "default",
            ),
            RegistryLocation(
                "hklm_policy_run",
                "HKLM",
                r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
                "HKLM\\Policies\\Explorer\\Run",
                "default",
            ),
        ]

    return locations


def _registry_location_by_id(location_id: str) -> RegistryLocation | None:
    for loc in _registry_locations():
        if loc.id == location_id:
            return loc
    return None


def _disabled_subkey_path(subkey: str) -> str:
    if not subkey:
        return _DISABLED_SUBKEY
    return f"{subkey}\\{_DISABLED_SUBKEY}"


def _registry_root_const(root: str):
    if winreg is None:
        return None
    if root == "HKLM":
        return winreg.HKEY_LOCAL_MACHINE
    return winreg.HKEY_CURRENT_USER


def _registry_access(write: bool, view: str) -> int:
    if winreg is None:
        return 0
    if write:
        access = winreg.KEY_SET_VALUE | getattr(winreg, "KEY_CREATE_SUB_KEY", 0)
    else:
        access = winreg.KEY_READ
    access |= winreg.KEY_QUERY_VALUE
    view_flag = _registry_view_flag(view)
    return access | view_flag


def _registry_view_flag(view: str) -> int:
    if winreg is None:
        return 0
    if view == "32":
        return getattr(winreg, "KEY_WOW64_32KEY", 0)
    if view == "64":
        return getattr(winreg, "KEY_WOW64_64KEY", 0)
    return 0


def _registry_type_from_name(name: str) -> int:
    if winreg is None:
        return 1
    name = (name or "").upper()
    if name == "REG_EXPAND_SZ":
        return winreg.REG_EXPAND_SZ
    return winreg.REG_SZ


def _registry_type_name(value_type: int) -> str:
    if winreg is None:
        return str(value_type)
    mapping = {
        winreg.REG_SZ: "REG_SZ",
        winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
        winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
        winreg.REG_DWORD: "REG_DWORD",
        winreg.REG_QWORD: "REG_QWORD",
        winreg.REG_BINARY: "REG_BINARY",
    }
    return mapping.get(value_type, f"REG_{value_type}")


def _format_registry_value(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, list):
        return "; ".join(str(item) for item in data)
    if isinstance(data, bytes):
        return data.hex()
    return str(data)


def _read_registry_values(location: RegistryLocation, disabled: bool) -> List[Dict[str, Any]]:
    if winreg is None:
        return []
    root_const = _registry_root_const(location.root)
    if root_const is None:
        return []
    access = _registry_access(write=False, view=location.view)
    subkey = location.subkey
    if disabled:
        subkey = _disabled_subkey_path(subkey)
    try:
        with winreg.OpenKey(root_const, subkey, 0, access) as key:
            _, value_count, _ = winreg.QueryInfoKey(key)
            items: List[Dict[str, Any]] = []
            for idx in range(value_count):
                try:
                    name, data, value_type = winreg.EnumValue(key, idx)
                except OSError:
                    break
                items.append(
                    {
                        "location_id": location.id,
                        "location_label": location.label,
                        "name": name,
                        "command": _format_registry_value(data),
                        "value_type": _registry_type_name(value_type),
                        "enabled": not disabled,
                    }
                )
            return items
    except FileNotFoundError:
        return []
    except OSError:
        return []


def _startup_locations() -> Dict[str, Dict[str, str]]:
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", "") or os.environ.get("ALLUSERSPROFILE", "")
    return {
        "startup_user": {
            "label": "Папка автозагрузки (пользователь)",
            "path": os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup") if appdata else "",
        },
        "startup_common": {
            "label": "Папка автозагрузки (все пользователи)",
            "path": os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs\StartUp") if programdata else "",
        },
    }


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip().strip(".")
    name = _INVALID_FILENAME_RE.sub("_", name)
    return name


def _read_startup_command_hint(path: str) -> str:
    filename = os.path.basename(path)
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".disabled":
        base = filename[:-9]
        ext = os.path.splitext(base)[1].lower()
    if ext not in (".cmd", ".bat"):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                low = text.lower()
                if low.startswith("@echo") or low.startswith("chcp "):
                    continue
                return text
    except Exception:
        return ""
    return ""


def _compose_task_command(command: str, working_dir: str) -> str:
    command = (command or "").strip()
    working_dir = (working_dir or "").strip()
    if not working_dir:
        return command
    safe_dir = working_dir.replace('"', '""')
    return f'cmd.exe /c cd /d "{safe_dir}" && {command}'


def _build_task_full_name(name: str, task_path: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    if "\\" in name:
        return name
    task_path = (task_path or "").strip() or "\\"
    if not task_path.startswith("\\"):
        task_path = "\\" + task_path
    if not task_path.endswith("\\"):
        task_path += "\\"
    if task_path == "\\":
        return "\\" + name
    return task_path + name


def _run_powershell_json(script: str) -> Tuple[Any | None, str | None]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"PowerShell превысил {_PS_TIMEOUT_SECONDS} сек."
    except Exception as exc:
        return None, str(exc)

    stdout = (result.stdout or "").strip().lstrip("\ufeff")
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return None, stderr or stdout or f"PowerShell вернул код {result.returncode}"
    if not stdout:
        return None, stderr or "Нет данных PowerShell"
    try:
        return json.loads(stdout), None
    except Exception as exc:
        return None, f"Не удалось разобрать JSON PowerShell: {exc}"
