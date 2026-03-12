from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict
from urllib import error as urllib_error
from urllib import request as urllib_request

import psutil

_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL_SECONDS = 30.0
_PS_TIMEOUT_SECONDS = 45
_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_WAIT_FOR_CACHE_SECONDS = 20.0
_SNAPSHOT_WAIT_POLL_SECONDS = 0.05

_OVERVIEW_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_OVERVIEW_CACHE_TTL_SECONDS = 3.0
_OVERVIEW_LOCK = threading.Lock()

_HARDWARE_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_HARDWARE_CACHE_TTL_SECONDS = 4.0
_HARDWARE_LOCK = threading.Lock()
_HARDWARE_START_GUARD: Dict[str, float] = {"ts": 0.0}
_HARDWARE_START_INTERVAL_SECONDS = 5.0
_HARDWARE_START_WAIT_SECONDS = 0.75
_HARDWARE_START_FETCH_ATTEMPTS = 8
_HARDWARE_HTTP_TIMEOUT_SECONDS = 1.8

_LHM_DEFAULT_HOST = "127.0.0.1"
_LHM_DEFAULT_PORT = 8085
_LHM_PROCESS_LOCK = threading.Lock()
_LHM_MANAGED_PROCESS: subprocess.Popen | None = None
_LHM_MANAGED_PID: int | None = None
_LHM_PANEL_OWNS_PROCESS = False
_LHM_AUTOSTART_ENABLED = False


def _pick_onefile_dir(path: str) -> str | None:
    current = os.path.abspath(path or "")
    if not current:
        return None
    while True:
        name = os.path.basename(current).lower()
        if name.startswith("onefile_") or name.startswith("onefil") or name.startswith("_mei"):
            return current
        parent = os.path.dirname(current)
        if not parent or parent == current:
            return None
        current = parent


def _guess_onefile_extract_dir() -> str | None:
    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if value:
            try:
                value_path = os.path.abspath(value)
                if os.path.isdir(value_path):
                    return value_path
                if os.path.exists(value_path):
                    return os.path.dirname(value_path)
            except Exception:
                pass

    try:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None)
        if main_file:
            found = _pick_onefile_dir(main_file)
            if found:
                return found
    except Exception:
        pass

    try:
        found = _pick_onefile_dir(sys.executable)
        if found:
            return found
    except Exception:
        pass
    return None


def _resolve_lhm_paths() -> tuple[str, str, str, str]:
    candidates: list[str] = []

    def _add(path: str | None) -> None:
        if not path:
            return
        try:
            resolved = os.path.abspath(path)
        except Exception:
            return
        if resolved not in candidates:
            candidates.append(resolved)

    _add(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    _add(os.environ.get("PANEL_BASE_DIR"))
    _add(os.getcwd())
    try:
        _add(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
    try:
        _add(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass

    onefile_dir = _guess_onefile_extract_dir()
    _add(onefile_dir)

    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if value:
            _add(value)
            try:
                _add(os.path.dirname(os.path.abspath(value)))
            except Exception:
                pass

    for root in candidates:
        lhm_dir = os.path.join(root, "data", "LibreHardwareMonitor.NET.10")
        exe_path = os.path.join(lhm_dir, "LibreHardwareMonitor.exe")
        if os.path.isfile(exe_path):
            return root, lhm_dir, exe_path, os.path.join(lhm_dir, "LibreHardwareMonitor.config")

    fallback_root = candidates[0] if candidates else os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    fallback_dir = os.path.join(fallback_root, "data", "LibreHardwareMonitor.NET.10")
    return (
        fallback_root,
        fallback_dir,
        os.path.join(fallback_dir, "LibreHardwareMonitor.exe"),
        os.path.join(fallback_dir, "LibreHardwareMonitor.config"),
    )


_LHM_ROOT_DIR, _LHM_DIR, _LHM_EXE_PATH, _LHM_CONFIG_PATH = _resolve_lhm_paths()

_HARDWARE_CATEGORY_LABELS: dict[str, str] = {
    "cpu": "ЦП",
    "gpu": "ГП",
    "mainboard": "Плата/VRM",
    "memory": "ОЗУ",
    "storage": "Диски",
    "power": "Питание",
    "network": "Сеть",
    "other": "Прочее",
}

_HARDWARE_KIND_ORDER: tuple[str, ...] = (
    "temperature",
    "load",
    "power",
    "clock",
    "fan",
    "level",
    "voltage",
    "current",
    "throughput",
    "data",
    "factor",
    "other",
)

_HARDWARE_KIND_LABELS: dict[str, str] = {
    "temperature": "Температура",
    "load": "Нагрузка",
    "power": "Мощность",
    "clock": "Частоты",
    "fan": "Вентиляторы",
    "level": "Ресурс Рё уровни",
    "voltage": "Напряжение",
    "current": "Ток",
    "throughput": "Скорость/поток",
    "data": "Объем Рё данные",
    "factor": "Счетчики",
    "other": "Прочее",
}

_HARDWARE_SUMMARY_SPECS: list[tuple[str, str, str]] = [
    ("cpu_load", "Нагрузка", "ЦП"),
    ("cpu_temp", "Температура", "ЦП"),
    ("cpu_power", "Мощность пакета", "ЦП"),
    ("cpu_clock", "Частота", "ЦП"),
    ("gpu_load", "Нагрузка", "ГП"),
    ("gpu_temp", "Температура", "ГП"),
    ("gpu_power", "Мощность", "ГП"),
    ("vrm_temp", "Температура VRM/платы", "Плата/VRM"),
    ("ram_load", "Нагрузка", "ОЗУ"),
    ("ram_used", "Рспользовано", "ОЗУ"),
    ("ram_temp_max", "Макс. температура", "ОЗУ"),
    ("disk_temp_max", "Макс. температура", "Диски"),
    ("disk_used_max", "Макс. занятость", "Диски"),
    ("disk_life_min", "Мин. ресурс", "Диски"),
    ("fan_speed_max", "Макс. скорость", "Охлаждение"),
]

_LHM_SKIP_NAME_PARTS = (
    "distance to tjmax",
    "warning temperature",
    "critical temperature",
    "thermal sensor low limit",
    "thermal sensor high limit",
    "critical low limit",
    "critical high limit",
    "temperature sensor resolution",
)

_NAME_PLACEHOLDERS = {
    "to be filled by o.e.m.",
    "to be filled by oem",
    "o.e.m.",
    "oem",
    "default string",
    "system manufacturer",
    "system product name",
    "not specified",
    "unknown",
    "none",
    "n/a",
    "na",
    "not applicable",
}

_SERIAL_PLACEHOLDERS = _NAME_PLACEHOLDERS | {
    "system serial number",
    "system serial#",
    "serial number",
    "invalid",
}

_CHASSIS_TYPE_MAP = {
    1: "Другой",
    2: "Неизвестно",
    3: "Десктоп",
    4: "Низкопрофильный десктоп",
    5: "Pizza Box",
    6: "Мини-тауэр",
    7: "Тауэр",
    8: "Портативный",
    9: "Ноутбук",
    10: "Notebook",
    11: "Карманный",
    12: "Док-станция",
    13: "Моноблок",
    14: "Субноутбук",
    15: "Компактный",
    16: "Lunch Box",
    17: "Основное шасси",
    18: "Расширительное шасси",
    19: "Подшасси",
    20: "Шасси шины",
    21: "Периферийное шасси",
    22: "Шасси хранения",
    23: "Стоечное (Rack)",
    24: "Герметичный корпус",
    30: "Планшет",
    31: "Трансформер",
    32: "Съемный",
    33: "IoT-шлюз",
    34: "Встраиваемое",
    35: "Мини-ПК",
    36: "Stick PC",
}

_PC_SYSTEM_TYPE_MAP = {
    0: "Неизвестно",
    1: "Десктоп",
    2: "Мобильный",
    3: "Рабочая станция",
    4: "Сервер (Enterprise)",
    5: "Сервер (SOHO)",
    6: "Аппаратный ПК",
    7: "Сервер (Performance)",
    8: "Максимум",
}

_DESKTOP_CHASSIS = {3, 4, 5, 6, 7, 13, 15, 16, 35}
_LAPTOP_CHASSIS = {8, 9, 10, 11, 12, 14, 30, 31, 32}
_SERVER_CHASSIS = {17, 18, 19, 20, 21, 22, 23}
_EMBEDDED_CHASSIS = {24, 33, 34, 36}

_BATTERY_STATUS_MAP = {
    1: "Разряжается",
    2: "Подключено к сети",
    3: "Полностью заряжен",
    4: "Низкий заряд",
    5: "Критический заряд",
    6: "Заряжается",
    7: "Заряжается (высокий уровень)",
    8: "Заряжается (низкий уровень)",
    9: "Заряжается (критический уровень)",
    10: "Не определено",
    11: "Частично заряжен",
}

_BATTERY_CHEMISTRY_MAP = {
    1: "Другое",
    2: "Неизвестно",
    3: "Свинцово-кислотный",
    4: "NiCd",
    5: "NiMH",
    6: "Li-ion",
    7: "Zinc-Air",
    8: "Li-Poly",
}

_UPS_VENDOR_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("APC / Schneider", ("apc", "schneider", "smart-ups", "back-ups")),
    ("CyberPower", ("cyberpower",)),
    ("Eaton", ("eaton",)),
    ("Tripp Lite", ("tripp", "tripplite")),
    ("Vertiv / Liebert", ("vertiv", "liebert")),
    ("Riello", ("riello",)),
    ("Socomec", ("socomec",)),
    ("Legrand", ("legrand",)),
    ("Powercom", ("powercom",)),
    ("Ippon", ("ippon",)),
    ("FSP", ("fsp", "fortron")),
    ("Delta", ("delta",)),
    ("Mustek", ("mustek",)),
    ("SVC", ("svc",)),
    ("Sven", ("sven",)),
    ("Hiden", ("hiden",)),
    ("IPP", ("ipp",)),
    ("Rucelf", ("rucelf",)),
    ("Exegate", ("exegate",)),
    ("PCM", ("pcm",)),
    ("Hikvision", ("hikvision",)),
    ("Huawei", ("huawei",)),
    ("DEXP", ("dexp",)),
]

_UPS_KEYWORDS = (
    "ups",
    "uninterruptible",
    "uninterruptable",
    "smart-ups",
    "back-ups",
    "ups battery",
    "hid ups",
    "ибп",
    "nobreak",
)

_INTERNAL_BATTERY_KEYWORDS = (
    "control method battery",
    "smart battery",
    "internal battery",
    "embedded battery",
    "surface battery",
    "lithium ion battery",
)

_INTERNAL_BATTERY_INSTANCE_HINTS = (
    "pnp0c0a",
    "ven_pnp&dev_0c0a",
    "cmbatt",
)

_AC_ADAPTER_KEYWORDS = (
    "ac adapter",
    "microsoft ac adapter",
    "acpi0003",
    "ven_acpi&dev_0003",
)

_TPM_KEYWORDS = (
    "trusted platform module",
    "доверенный платформенный модуль",
    "tpm",
    "security processor",
    "msft0101",
)


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _filter_dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in _ensure_list(value) if isinstance(item, dict)]


def _clean_text(value: Any, placeholders: set[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    lowered = normalized.lower().strip()
    if lowered in placeholders:
        return ""
    compact = re.sub(r"[.\s]", "", lowered)
    placeholder_compact = {re.sub(r"[.\s]", "", p) for p in placeholders}
    if compact in placeholder_compact:
        return ""
    return normalized


def _clean_name(value: Any) -> str:
    return _clean_text(value, _NAME_PLACEHOLDERS)


def _resolve_os_name(
    os_info: dict[str, Any],
    winver_info: dict[str, Any],
    build_number: Any,
    ps_resolved: Any | None = None,
) -> str:
    candidates = [
        ps_resolved,
        winver_info.get("ProductName"),
        winver_info.get("WindowsProductName"),
        winver_info.get("OsName"),
        os_info.get("Caption"),
        os_info.get("Name"),
    ]
    resolved = ""
    for item in candidates:
        text = _clean_text(item, _NAME_PLACEHOLDERS)
        if text:
            resolved = text
            break
    build = _to_int(build_number)
    if build and build >= 22000:
        if not resolved:
            return "Windows 11"
        if "windows 10" in resolved.lower():
            return resolved.replace("Windows 10", "Windows 11")
    if resolved:
        return resolved
    if build and build >= 22000:
        return "Windows 11"
    if build:
        return "Windows 10"
    return ""


def _clean_serial(value: Any) -> str:
    text = _clean_text(value, _SERIAL_PLACEHOLDERS)
    if not text:
        return ""
    compact = re.sub(r"[^0-9a-fA-F]", "", text)
    if compact and len(set(compact.lower())) == 1 and compact.lower()[0] in {"0", "f"}:
        return ""
    return text


def _clean_uuid(value: Any) -> str:
    text = _clean_serial(value)
    if not text:
        return ""
    compact = re.sub(r"[^0-9a-fA-F]", "", text)
    if compact and len(set(compact.lower())) == 1:
        return ""
    return text


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"true", "yes", "1", "on", "enabled", "present"}:
        return True
    if text in {"false", "no", "0", "off", "disabled", "absent", "notpresent"}:
        return False
    return None


def _dict_has_meaningful_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for item in value.values():
        if item is None:
            continue
        if isinstance(item, str) and not item.strip():
            continue
        return True
    return False


def _parse_ps_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value))
        except Exception:
            return None
    if isinstance(value, str):
        match = re.search(r"/Date\((\d+)([+-]\d+)?\)/", value)
        if match:
            try:
                ms = int(match.group(1))
                return dt.datetime.utcfromtimestamp(ms / 1000)
            except Exception:
                return None
        match = re.match(
            r"^(?P<date>\d{14})\.(?P<micro>\d{6})(?P<sign>[+-])(?P<offset>\d{3})$",
            value,
        )
        if match:
            try:
                dt_raw = dt.datetime.strptime(match.group("date"), "%Y%m%d%H%M%S")
                offset_minutes = int(match.group("offset"))
                if match.group("sign") == "-":
                    offset_minutes = -offset_minutes
                tz = dt.timezone(dt.timedelta(minutes=offset_minutes))
                return dt_raw.replace(tzinfo=tz)
            except Exception:
                return None
        try:
            return dt.datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _to_gb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / 1024 / 1024 / 1024, 2)


def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(int(seconds), 0)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{sec} сек")
    return " ".join(parts)


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return "-"
    try:
        return value.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "-"


def _map_product_type(value: Any) -> str:
    mapping = {
        1: "Рабочая станция",
        2: "Контроллер домена",
        3: "Сервер",
    }
    try:
        return mapping.get(int(value), "-")
    except Exception:
        return "-"


def _map_domain_role(value: Any) -> str:
    mapping = {
        0: "Автономная рабочая станция",
        1: "Член домена (рабочая станция)",
        2: "Автономный сервер",
        3: "Член домена (сервер)",
        4: "Резервный контроллер домена",
        5: "Основной контроллер домена",
    }
    try:
        return mapping.get(int(value), "-")
    except Exception:
        return "-"


def _map_pc_system_type(value: Any) -> str:
    try:
        return _PC_SYSTEM_TYPE_MAP.get(int(value), "-")
    except Exception:
        return "-"


def _map_chassis_types(values: Any) -> list[str]:
    labels: list[str] = []
    for item in _ensure_list(values):
        try:
            idx = int(item)
        except Exception:
            continue
        label = _CHASSIS_TYPE_MAP.get(idx, f"Тип {idx}")
        if label not in labels:
            labels.append(label)
    return labels


def _infer_device_type(chassis_ids: Any, pc_system_type: Any) -> str:
    ids: set[int] = set()
    for item in _ensure_list(chassis_ids):
        try:
            ids.add(int(item))
        except Exception:
            continue
    if ids & _SERVER_CHASSIS:
        return "Сервер"
    if ids & {30, 31, 32}:
        return "Планшет/трансформер"
    if ids & _LAPTOP_CHASSIS:
        return "Ноутбук"
    if ids & _EMBEDDED_CHASSIS:
        return "Встраиваемое устройство"
    if ids & _DESKTOP_CHASSIS:
        return "Десктоп"
    try:
        pc_type = int(pc_system_type) if pc_system_type is not None else None
    except Exception:
        pc_type = None
    if pc_type in {4, 5, 7}:
        return "Сервер"
    if pc_type == 2:
        return "Ноутбук"
    if pc_type == 3:
        return "Рабочая станция"
    if pc_type == 1:
        return "Десктоп"
    return ""


def _mb_to_gb(value: int | None) -> float | None:
    if value is None:
        return None
    try:
        return round(value / 1024, 2)
    except Exception:
        return None


def _map_bitlocker_protection(value: Any) -> str:
    mapping = {
        0: "Выключена",
        1: "Включена",
        2: "Неизвестно",
    }
    text_mapping = {
        "off": "Выключена",
        "on": "Включена",
        "unknown": "Неизвестно",
        "protection off": "Выключена",
        "protection on": "Включена",
    }
    try:
        return mapping.get(int(value), "-")
    except Exception:
        text = str(value or "").strip().lower()
        if not text:
            return "-"
        if text in text_mapping:
            return text_mapping[text]
        if "off" in text:
            return "Выключена"
        if "on" in text:
            return "Включена"
        if "unknown" in text:
            return "Неизвестно"
        return str(value)


def _map_bitlocker_lock(value: Any) -> str:
    mapping = {
        0: "Разблокирован",
        1: "Заблокирован",
        2: "Неизвестно",
    }
    text_mapping = {
        "unlocked": "Разблокирован",
        "locked": "Заблокирован",
        "unknown": "Неизвестно",
    }
    try:
        return mapping.get(int(value), "-")
    except Exception:
        text = str(value or "").strip().lower()
        if not text:
            return "-"
        if text in text_mapping:
            return text_mapping[text]
        if "unlock" in text:
            return "Разблокирован"
        if "lock" in text:
            return "Заблокирован"
        if "unknown" in text:
            return "Неизвестно"
        return str(value)


def _normalize_tpm_spec(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value)


def _decode_tpm_manufacturer(manufacturer_id: Any, manufacturer_txt: Any) -> str:
    if manufacturer_txt:
        return str(manufacturer_txt)
    if manufacturer_id is None:
        return ""
    try:
        value = int(manufacturer_id)
    except Exception:
        return str(manufacturer_id)
    for order in ("big", "little"):
        try:
            raw = value.to_bytes(4, order)
            text = raw.decode("ascii", errors="ignore").strip("\x00").strip()
            if text:
                return text
        except Exception:
            continue
    return str(manufacturer_id)


def _format_ps_time(value: Any) -> str:
    parsed = _parse_ps_datetime(value)
    if parsed:
        return _format_datetime(parsed)
    return ""


def _normalize_percent(value: Any) -> float | None:
    try:
        if value is None:
            return None
        percent = float(value)
    except Exception:
        return None
    if percent < 0:
        return None
    if percent > 100:
        percent = 100.0
    return round(percent, 1)


def _map_battery_status(value: Any) -> str:
    try:
        if value is None:
            return ""
        return _BATTERY_STATUS_MAP.get(int(value), str(value))
    except Exception:
        return str(value) if value is not None else ""


def _map_battery_chemistry(value: Any) -> str:
    try:
        if value is None:
            return ""
        return _BATTERY_CHEMISTRY_MAP.get(int(value), str(value))
    except Exception:
        return str(value) if value is not None else ""


def _runtime_minutes_to_human(minutes: Any) -> str:
    mins = _to_int(minutes)
    if mins is None or mins < 0:
        return ""
    return _format_uptime(mins * 60)


def _power_connection_type(instance_id: Any, name: Any = "") -> str:
    text = f"{instance_id or ''} {name or ''}".lower()
    if "usb\\" in text:
        return "USB"
    if "hid\\" in text:
        return "USB HID"
    if "bluetooth" in text or "bth\\" in text:
        return "Bluetooth"
    if "com" in text or "serial" in text or "uart" in text:
        return "COM/Serial"
    if "pci\\" in text:
        return "PCI/ACPI"
    return ""


def _power_haystack(*values: Any) -> str:
    return " ".join(str(v or "").lower() for v in values if v is not None).strip()


def _find_keyword_match(haystack: str, keywords: tuple[str, ...]) -> str:
    for keyword in keywords:
        if keyword and keyword in haystack:
            return keyword
    return ""


def _looks_like_ups(*values: Any) -> bool:
    haystack = _power_haystack(*values)
    if not haystack:
        return False
    return any(keyword in haystack for keyword in _UPS_KEYWORDS)


def _looks_like_ac_adapter(*values: Any) -> bool:
    haystack = _power_haystack(*values)
    if not haystack:
        return False
    return any(keyword in haystack for keyword in _AC_ADAPTER_KEYWORDS)


def _looks_like_internal_battery(*values: Any) -> bool:
    haystack = _power_haystack(*values)
    if not haystack:
        return False
    if _looks_like_ups(haystack) or _looks_like_ac_adapter(haystack):
        return False
    if any(hint in haystack for hint in _INTERNAL_BATTERY_INSTANCE_HINTS):
        return True
    return any(keyword in haystack for keyword in _INTERNAL_BATTERY_KEYWORDS)


def _portable_detected_label(detected_type: str) -> str:
    labels = {
        "internal_battery": "Встроенная батарея",
        "ups": "РБП",
        "ac_adapter": "AC Adapter (не батарея)",
        "unknown": "Не классифицировано",
    }
    return labels.get(detected_type, detected_type or "unknown")


def _classify_portable_battery(*values: Any) -> tuple[str, str]:
    haystack = _power_haystack(*values)
    if not haystack:
        return "unknown", "Пустые свойства устройства."

    ac_hint = _find_keyword_match(haystack, _AC_ADAPTER_KEYWORDS)
    if ac_hint:
        return "ac_adapter", f"Совпадение с сигнатурой AC Adapter: {ac_hint}."

    ups_hint = _find_keyword_match(haystack, _UPS_KEYWORDS)
    if ups_hint:
        return "ups", f"Совпадение с сигнатурой РБП: {ups_hint}."

    internal_hint = _find_keyword_match(haystack, _INTERNAL_BATTERY_INSTANCE_HINTS)
    if internal_hint:
        return "internal_battery", f"Совпадение с ACPI/driver сигнатурой батареи: {internal_hint}."

    internal_kw = _find_keyword_match(haystack, _INTERNAL_BATTERY_KEYWORDS)
    if internal_kw:
        return "internal_battery", f"Совпадение с сигнатурой внутренней батареи: {internal_kw}."

    return "unknown", "Явные сигнатуры батареи/РБП не найдены."


def _detect_ups_vendors(*values: Any) -> list[str]:
    haystack = _power_haystack(*values)
    if not haystack:
        return []
    found: list[str] = []
    for label, patterns in _UPS_VENDOR_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            found.append(label)
    return found


def _looks_like_tpm_device(*values: Any) -> bool:
    haystack = _power_haystack(*values)
    if not haystack:
        return False
    return any(keyword in haystack for keyword in _TPM_KEYWORDS)


def _extract_tpm_spec(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        match = re.search(r"\b(2\.0|1\.2)\b", text)
        if match:
            return match.group(1)
    return ""


def _extract_tpm_vendor_id(values: list[str]) -> str:
    for value in values:
        if re.fullmatch(r"[A-Z0-9]{4}", value):
            return value
    return ""


def _extract_tpm_vendor_name(values: list[str], vendor_id: str) -> str:
    if not values:
        return ""
    if vendor_id and vendor_id in values:
        idx = values.index(vendor_id)
        if idx + 1 < len(values):
            candidate = values[idx + 1]
            if re.search(r"[A-Za-zА-Яа-яЁё]", candidate):
                return candidate
    for value in values:
        lowered = value.lower()
        if lowered in {"tpm", "2.0", "1.2"}:
            continue
        if re.search(r"[A-Za-zА-Яа-яЁё]", value):
            return value
    return ""


def _collect_overview_power() -> dict[str, Any]:
    status: dict[str, Any] = {
        "present": False,
        "percent": None,
        "on_ac": None,
        "charging": None,
        "runtime_seconds": None,
        "runtime_human": "",
        "summary": "Не обнаружено",
        "source": "",
        "battery_flag": None,
        "ac_line_status": None,
        "no_system_battery": None,
        "diagnostics": [],
    }

    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None

    if battery is not None:
        status["diagnostics"].append("psutil.sensors_battery: данные получены.")
        percent = _normalize_percent(getattr(battery, "percent", None))
        plugged = getattr(battery, "power_plugged", None)
        secsleft = getattr(battery, "secsleft", None)
        runtime_seconds = secsleft if isinstance(secsleft, int) and secsleft >= 0 else None
        status.update(
            {
                "present": percent is not None or plugged is not None,
                "percent": percent,
                "on_ac": bool(plugged) if plugged is not None else None,
                "charging": bool(plugged) if plugged is not None else None,
                "runtime_seconds": runtime_seconds,
                "runtime_human": _format_uptime(runtime_seconds) if runtime_seconds else "",
                "source": "psutil",
            }
        )
    else:
        status["diagnostics"].append("psutil.sensors_battery: системная батарея не найдена.")

    if os.name == "nt":
        try:
            import ctypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("Reserved1", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            sps = SYSTEM_POWER_STATUS()
            ok = bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)))
            if ok:
                battery_flag = int(sps.BatteryFlag)
                ac_line_status = int(sps.ACLineStatus)
                battery_flag_unknown = battery_flag == 255
                no_system_battery = bool((battery_flag & 128) and not battery_flag_unknown)
                percent = _normalize_percent(None if sps.BatteryLifePercent == 255 else sps.BatteryLifePercent)
                on_ac = None if sps.ACLineStatus == 255 else bool(sps.ACLineStatus == 1)
                runtime_seconds = (
                    None
                    if sps.BatteryLifeTime in (0xFFFFFFFF, 0)
                    else int(sps.BatteryLifeTime)
                )
                battery_present = False if (battery_flag_unknown or no_system_battery) else True

                status["battery_flag"] = battery_flag
                status["ac_line_status"] = ac_line_status
                status["no_system_battery"] = no_system_battery
                if battery_flag_unknown:
                    status["diagnostics"].append(
                        "GetSystemPowerStatus: BatteryFlag=255 (неизвестно)."
                    )
                elif no_system_battery:
                    status["diagnostics"].append(
                        "GetSystemPowerStatus: BatteryFlag=128 (No system battery)."
                    )
                else:
                    status["diagnostics"].append(
                        f"GetSystemPowerStatus: BatteryFlag={battery_flag}, батарея подтверждена."
                    )

                if status.get("percent") is None and percent is not None:
                    status["percent"] = percent
                if status.get("on_ac") is None:
                    status["on_ac"] = on_ac
                if status.get("runtime_seconds") is None and runtime_seconds:
                    status["runtime_seconds"] = runtime_seconds
                    status["runtime_human"] = _format_uptime(runtime_seconds)
                if not status.get("source"):
                    status["source"] = "GetSystemPowerStatus"
                status["present"] = bool(status.get("present") or battery_present)
                if status.get("charging") is None:
                    if not battery_flag_unknown and not no_system_battery:
                        status["charging"] = bool(battery_flag & 8)
                    elif on_ac is not None:
                        status["charging"] = on_ac
            else:
                status["diagnostics"].append("GetSystemPowerStatus: API вернул ошибку.")
        except Exception:
            status["diagnostics"].append("GetSystemPowerStatus: исключение при получении статуса.")

    if not status.get("present"):
        status["summary"] = "Не обнаружено"
        return status

    parts: list[str] = []
    percent = status.get("percent")
    if percent is not None:
        parts.append(f"{int(round(percent))}%")
    if status.get("on_ac") is True:
        parts.append("от сети")
    elif status.get("on_ac") is False:
        parts.append("от батареи")
    runtime_human = status.get("runtime_human")
    if runtime_human and status.get("on_ac") is False:
        parts.append(f"остаток {runtime_human}")
    status["summary"] = ", ".join(parts) if parts else "Обнаружено"
    return status


def _calc_disk_totals() -> dict[str, Any]:
    total = 0
    used = 0
    free = 0
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        partitions = []
    for part in partitions:
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        total += usage.total
        used += usage.used
        free += usage.free
    percent = round((used / total) * 100, 1) if total else None
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free if total else None,
        "total_gb": _to_gb(total),
        "used_gb": _to_gb(used),
        "free_gb": _to_gb(free) if total else None,
        "percent": percent,
    }


def _clean_sensor_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_sensor_metric(raw: Any) -> tuple[float | None, str, str]:
    text = _clean_sensor_text(raw)
    if not text or text in {"-", "--"}:
        return None, "", "-"

    normalized = text.lower().replace(" ", "")
    if "nan" in normalized:
        unit = "%" if "%" in text else ""
        return None, unit, text

    clean_text = text.replace("\u2212", "-")
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", clean_text)
    if not match:
        return None, "", text

    num_token = match.group(0).replace(",", ".")
    try:
        value = float(num_token)
    except Exception:
        value = None

    unit = clean_text[match.end() :].strip()
    return value, unit, text


def _sensor_has_metric(node: dict[str, Any]) -> bool:
    for key in ("Value", "Min", "Max"):
        metric = _clean_sensor_text(node.get(key))
        if metric and metric not in {"-", "--"}:
            return True
    return False


def _sensor_group_kind(group_name: str, sensor_name: str, unit: str) -> str:
    group = (group_name or "").strip().lower()
    sensor = (sensor_name or "").strip().lower()
    unit_l = (unit or "").strip().lower()

    if "temperature" in group or "temp" in group:
        return "temperature"
    if "level" in group:
        return "level"
    if "load" in group:
        return "load"
    if "clock" in group or unit_l == "mhz":
        return "clock"
    if "power" in group or unit_l == "w":
        return "power"
    if "fan" in group or unit_l == "rpm":
        return "fan"
    if "voltage" in group or unit_l == "v":
        return "voltage"
    if "current" in group or unit_l == "a":
        return "current"
    if "throughput" in group or "/s" in unit_l:
        return "throughput"
    if "data" in group:
        return "data"
    if "factor" in group:
        return "factor"
    if unit_l == "\u00b0c":
        return "temperature"
    if unit_l == "%":
        return "load"
    if sensor.endswith("temperature") or "temperature #" in sensor:
        return "temperature"
    return "other"


def _classify_hardware_category(hardware_id: str, hardware_name: str) -> str:
    hid = (hardware_id or "").lower()
    name = (hardware_name or "").lower()
    haystack = f"{hid} {name}"

    if "/gpu" in hid or "graphics" in haystack:
        return "gpu"
    if "/intelcpu" in hid or "/amdcpu" in hid or ("/cpu" in hid and "/gpu" not in hid):
        return "cpu"
    if (
        "/motherboard" in hid
        or "/lpc/" in hid
        or "/superio" in hid
        or "/ec/" in hid
        or "/embeddedcontroller" in hid
    ):
        return "mainboard"
    if "/ram" in hid or "/vram" in hid or "/memory" in hid:
        return "memory"
    if any(token in hid for token in ("/nvme", "/hdd", "/ssd", "/storage", "/ata", "/drive")):
        return "storage"
    if "/battery" in hid or "/psu" in hid:
        return "power"
    if "/nic" in hid:
        return "network"
    return "other"


def _lhm_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("Children")
    if not isinstance(children, list):
        return []
    return [item for item in children if isinstance(item, dict)]


def _normalize_lhm_host(host: str, port: int) -> str:
    host = (host or "").strip()
    if host in {"*", "+", "0.0.0.0", "::", "?"}:
        return _LHM_DEFAULT_HOST
    if "://" in host:
        return _LHM_DEFAULT_HOST
    if not host:
        return _LHM_DEFAULT_HOST
    try:
        socket.getaddrinfo(host, port)
    except Exception:
        return _LHM_DEFAULT_HOST
    return host


def _load_lhm_listener_settings() -> tuple[str, int, bool]:
    host = _LHM_DEFAULT_HOST
    port = _LHM_DEFAULT_PORT
    # LibreHardwareMonitor defaults web listener to disabled until enabled in settings.
    web_enabled = False

    if not os.path.isfile(_LHM_CONFIG_PATH):
        return host, port, web_enabled

    try:
        tree = ET.parse(_LHM_CONFIG_PATH)
        app_settings = tree.getroot().find("appSettings")
        if app_settings is None:
            return host, port, web_enabled
        for add in app_settings.findall("add"):
            key = (add.get("key") or "").strip()
            value = (add.get("value") or "").strip()
            if key == "listenerIp" and value:
                host = value
            elif key == "listenerPort":
                parsed_port = _to_int(value)
                if parsed_port and 1 <= parsed_port <= 65535:
                    port = parsed_port
            elif key == "runWebServerMenuItem":
                enabled = _to_bool(value)
                if enabled is not None:
                    web_enabled = enabled
    except Exception:
        return host, port, web_enabled

    host = _normalize_lhm_host(host, port)
    return host or _LHM_DEFAULT_HOST, port, web_enabled


def _upsert_lhm_setting(app_settings: ET.Element, key: str, value: str) -> bool:
    changed = False
    found: list[ET.Element] = []
    for add in app_settings.findall("add"):
        current_key = (add.get("key") or "").strip()
        if current_key == key:
            found.append(add)
    if not found:
        add = ET.SubElement(app_settings, "add")
        add.set("key", key)
        add.set("value", value)
        return True

    first = found[0]
    current = (first.get("value") or "").strip()
    if current != value:
        first.set("value", value)
        changed = True

    for extra in found[1:]:
        app_settings.remove(extra)
        changed = True
    return changed


def _ensure_lhm_listener_settings(host: str, port: int) -> tuple[bool, str | None]:
    host = _normalize_lhm_host(host, port)
    try:
        os.makedirs(_LHM_DIR, exist_ok=True)
    except Exception as exc:
        return False, f"Cannot prepare LibreHardwareMonitor directory: {exc}."

    changed = False
    try:
        if os.path.isfile(_LHM_CONFIG_PATH):
            try:
                tree = ET.parse(_LHM_CONFIG_PATH)
                root = tree.getroot()
            except Exception:
                root = ET.Element("configuration")
                tree = ET.ElementTree(root)
                changed = True
        else:
            root = ET.Element("configuration")
            tree = ET.ElementTree(root)
            changed = True

        app_settings = root.find("appSettings")
        if app_settings is None:
            app_settings = ET.SubElement(root, "appSettings")
            changed = True

        changed = _upsert_lhm_setting(app_settings, "runWebServerMenuItem", "true") or changed
        changed = _upsert_lhm_setting(app_settings, "listenerIp", host) or changed
        changed = _upsert_lhm_setting(app_settings, "listenerPort", str(int(port))) or changed
        changed = _upsert_lhm_setting(app_settings, "startMinMenuItem", "true") or changed
        changed = _upsert_lhm_setting(app_settings, "minTrayMenuItem", "true") or changed

        if changed:
            if hasattr(ET, "indent"):
                ET.indent(tree, space="  ")
            tree.write(_LHM_CONFIG_PATH, encoding="utf-8", xml_declaration=True)
    except Exception as exc:
        return False, f"Cannot update LibreHardwareMonitor config: {exc}."
    return changed, None


def _fetch_lhm_tree(url: str) -> tuple[dict[str, Any] | None, str | None]:
    request = urllib_request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib_request.urlopen(request, timeout=_HARDWARE_HTTP_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    except urllib_error.HTTPError as exc:
        return None, f"HTTP {exc.code} от endpoint LibreHardwareMonitor."
    except urllib_error.URLError as exc:
        return None, f"Не удалось подключиться к endpoint LibreHardwareMonitor: {exc.reason}."
    except socket.timeout:
        return None, "Таймаут при обращении к endpoint LibreHardwareMonitor."
    except Exception as exc:
        return None, f"Ошибка endpoint LibreHardwareMonitor: {exc}."

    if not payload:
        return None, "Endpoint LibreHardwareMonitor вернул пустой ответ."

    try:
        data = json.loads(payload)
    except Exception as exc:
        return None, f"Unable to parse LibreHardwareMonitor JSON: {exc}."

    if not isinstance(data, dict):
        return None, "Endpoint LibreHardwareMonitor вернул некорректный формат данных."
    return data, None


def _can_connect(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


def _fetch_lhm_tree_with_retries(
    url: str,
    attempts: int,
    delay_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    attempts = max(1, int(attempts or 1))
    delay_seconds = max(0.0, float(delay_seconds or 0.0))
    last_error: str | None = None
    for idx in range(attempts):
        tree, error = _fetch_lhm_tree(url)
        if tree is not None:
            return tree, None
        last_error = error
        if idx + 1 < attempts and delay_seconds > 0:
            time.sleep(delay_seconds)
    return None, last_error


def _get_lhm_processes() -> list[psutil.Process]:
    target_exe = os.path.normcase(os.path.abspath(_LHM_EXE_PATH))
    processes: list[psutil.Process] = []
    managed_pid = _LHM_MANAGED_PID
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = str(proc.info.get("name") or "").strip().lower()
            if name != "librehardwaremonitor.exe":
                continue
            exe = proc.info.get("exe")
            if exe:
                exe_norm = os.path.normcase(os.path.abspath(exe))
                if exe_norm != target_exe:
                    continue
            elif managed_pid is None or proc.pid != managed_pid:
                continue
            processes.append(proc)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return processes


def _stop_lhm_processes(processes: list[psutil.Process], timeout: float = 2.5) -> tuple[int, list[str]]:
    stopped = 0
    issues: list[str] = []
    seen: set[int] = set()
    for proc in processes:
        try:
            pid = int(proc.pid)
        except Exception:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        try:
            if not proc.is_running():
                continue
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
            stopped += 1
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            issues.append(f"Access denied while stopping LibreHardwareMonitor PID {pid}.")
        except Exception as exc:
            issues.append(f"Failed to stop LibreHardwareMonitor PID {pid}: {exc}.")
    return stopped, issues


def _stop_managed_lhm_process() -> tuple[bool, str | None]:
    global _LHM_MANAGED_PROCESS, _LHM_MANAGED_PID
    with _LHM_PROCESS_LOCK:
        managed_proc = _LHM_MANAGED_PROCESS
        managed_pid = _LHM_MANAGED_PID
        _LHM_MANAGED_PROCESS = None
        _LHM_MANAGED_PID = None

    if managed_proc is None and managed_pid is None:
        return False, None

    if managed_proc is not None:
        try:
            if managed_proc.poll() is None:
                managed_proc.terminate()
                try:
                    managed_proc.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    managed_proc.kill()
                return True, "Managed LibreHardwareMonitor process stopped."
            return False, None
        except Exception as exc:
            return False, f"Failed to stop managed LibreHardwareMonitor process: {exc}."

    if managed_pid is not None:
        try:
            proc = psutil.Process(managed_pid)
            stopped, issues = _stop_lhm_processes([proc])
            if stopped:
                return True, "Managed LibreHardwareMonitor process stopped."
            if issues:
                return False, issues[0]
        except Exception:
            return False, None
    return False, None


def _start_lhm_process_via_shell_runas() -> tuple[bool, str | None]:
    if os.name != "nt":
        return False, None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    exe_path = _LHM_EXE_PATH.replace("'", "''")
    work_dir = _LHM_DIR.replace("'", "''")
    ps_command = (
        f"Start-Process -FilePath '{exe_path}' "
        f"-WorkingDirectory '{work_dir}' "
        "-Verb RunAs -WindowStyle Hidden"
    )
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            close_fds=os.name != "nt",
        )
        return True, "Requested elevated launch for LibreHardwareMonitor (UAC may appear)."
    except Exception as exc:
        return False, f"Unable to request elevated launch for LibreHardwareMonitor: {exc}."


def _start_lhm_process(host: str, port: int, respect_guard: bool = True) -> tuple[bool, str | None]:
    host = _normalize_lhm_host(host, port)
    if _can_connect(host, port):
        return False, None
    if not os.path.isfile(_LHM_EXE_PATH):
        return False, f"LibreHardwareMonitor executable not found: {_LHM_EXE_PATH}."

    cfg_changed, cfg_error = _ensure_lhm_listener_settings(host, port)
    if cfg_error:
        return False, cfg_error

    now = time.monotonic()
    last = _HARDWARE_START_GUARD.get("ts", 0.0)
    if respect_guard and now - last < _HARDWARE_START_INTERVAL_SECONDS:
        return False, "Repeated LibreHardwareMonitor launch skipped by start-rate guard."

    existing = _get_lhm_processes()
    restarted_existing = False
    if existing:
        stopped, issues = _stop_lhm_processes(existing)
        restarted_existing = stopped > 0
        if issues and stopped == 0:
            return False, issues[0]
        if restarted_existing:
            time.sleep(0.35)

    _HARDWARE_START_GUARD["ts"] = now
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    try:
        process = subprocess.Popen(
            [_LHM_EXE_PATH],
            cwd=_LHM_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            close_fds=os.name != "nt",
        )
        with _LHM_PROCESS_LOCK:
            global _LHM_MANAGED_PROCESS, _LHM_MANAGED_PID, _LHM_PANEL_OWNS_PROCESS
            _LHM_MANAGED_PROCESS = process
            _LHM_MANAGED_PID = process.pid
            _LHM_PANEL_OWNS_PROCESS = True
        parts = []
        if cfg_changed:
            parts.append("LibreHardwareMonitor config synchronized.")
        if restarted_existing:
            parts.append("Existing LibreHardwareMonitor process restarted.")
        parts.append("LibreHardwareMonitor process started.")
        return True, " ".join(parts)
    except Exception as exc:
        winerror = getattr(exc, "winerror", None)
        if os.name == "nt" and winerror == 740:
            elevated_started, elevated_msg = _start_lhm_process_via_shell_runas()
            if elevated_started:
                _LHM_PANEL_OWNS_PROCESS = True
                parts = []
                if cfg_changed:
                    parts.append("LibreHardwareMonitor config synchronized.")
                if restarted_existing:
                    parts.append("Existing LibreHardwareMonitor process restarted.")
                parts.append(elevated_msg or "Requested elevated launch for LibreHardwareMonitor.")
                return True, " ".join(parts)
            if elevated_msg:
                return False, elevated_msg
        return False, f"Unable to start LibreHardwareMonitor: {exc}."


def ensure_lhm_running_for_panel() -> tuple[bool, str | None]:
    global _LHM_AUTOSTART_ENABLED
    _LHM_AUTOSTART_ENABLED = True
    host, port, _ = _load_lhm_listener_settings()
    return _start_lhm_process(host, port, respect_guard=False)


def stop_lhm_for_panel() -> tuple[bool, str | None]:
    global _LHM_PANEL_OWNS_PROCESS, _LHM_AUTOSTART_ENABLED
    _LHM_AUTOSTART_ENABLED = False
    owned_by_panel = bool(_LHM_PANEL_OWNS_PROCESS)
    _LHM_PANEL_OWNS_PROCESS = False

    stopped, msg = _stop_managed_lhm_process()
    if not owned_by_panel:
        return stopped, msg

    extra_stopped = 0
    issues: list[str] = []
    try:
        processes = _get_lhm_processes()
        if processes:
            extra_stopped, issues = _stop_lhm_processes(processes)
    except Exception as exc:
        issues.append(f"Unable to inspect LibreHardwareMonitor processes: {exc}.")

    if stopped or extra_stopped > 0:
        total = (1 if stopped else 0) + extra_stopped
        return True, msg or f"Stopped LibreHardwareMonitor processes: {total}."
    if issues:
        return False, issues[0]
    return False, msg


def _atexit_stop_lhm_for_panel() -> None:
    try:
        stop_lhm_for_panel()
    except Exception:
        pass


atexit.register(_atexit_stop_lhm_for_panel)


def _flatten_lhm_sensors(tree: dict[str, Any]) -> list[dict[str, Any]]:
    sensors: list[dict[str, Any]] = []

    def walk(
        node: dict[str, Any],
        path: list[str],
        hardware_name: str,
        hardware_id: str,
        group_name: str,
    ) -> None:
        text = _clean_sensor_text(node.get("Text"))
        current_path = list(path)
        if text:
            current_path.append(text)

        current_hardware_name = hardware_name
        current_hardware_id = hardware_id
        current_group_name = group_name

        node_hardware_id = _clean_sensor_text(node.get("HardwareId"))
        children = _lhm_children(node)

        if node_hardware_id:
            current_hardware_id = node_hardware_id
            current_hardware_name = text or current_hardware_name or node_hardware_id
            current_group_name = ""
        elif current_hardware_id and not current_group_name and children:
            current_group_name = text

        if current_hardware_id and _sensor_has_metric(node):
            value, value_unit, value_display = _parse_sensor_metric(node.get("Value"))
            min_value, min_unit, min_display = _parse_sensor_metric(node.get("Min"))
            max_value, max_unit, max_display = _parse_sensor_metric(node.get("Max"))
            name_norm = (text or "").lower()
            path_norm = " > ".join(current_path).lower()
            blob_norm = f"{name_norm} {path_norm} {current_group_name.lower()} {current_hardware_name.lower()}"
            category = _classify_hardware_category(current_hardware_id, current_hardware_name)
            group_kind = _sensor_group_kind(current_group_name, text, value_unit)

            sensors.append(
                {
                    "id": _to_int(node.get("id")),
                    "category": category,
                    "category_label": _HARDWARE_CATEGORY_LABELS.get(category, category.title()),
                    "hardware_name": current_hardware_name,
                    "hardware_id": current_hardware_id,
                    "group": current_group_name or "-",
                    "group_kind": group_kind,
                    "name": text or "-",
                    "value": value,
                    "value_unit": value_unit,
                    "value_display": value_display,
                    "min": min_value,
                    "min_unit": min_unit,
                    "min_display": min_display,
                    "max": max_value,
                    "max_unit": max_unit,
                    "max_display": max_display,
                    "path": " > ".join(current_path),
                    "name_norm": name_norm,
                    "blob_norm": blob_norm,
                }
            )

        for child in children:
            walk(child, current_path, current_hardware_name, current_hardware_id, current_group_name)

    walk(tree, [], "", "", "")

    category_order = {name: idx for idx, name in enumerate(_HARDWARE_CATEGORY_LABELS.keys())}
    sensors.sort(
        key=lambda item: (
            category_order.get(item.get("category", ""), 99),
            str(item.get("hardware_name", "")).lower(),
            str(item.get("group", "")).lower(),
            str(item.get("name", "")).lower(),
        )
    )
    return sensors


def _select_sensor(
    sensors: list[dict[str, Any]],
    *,
    categories: tuple[str, ...] | None = None,
    groups: tuple[str, ...] | None = None,
    include_terms: tuple[str, ...] = (),
    exclude_terms: tuple[str, ...] = (),
    prefer: str = "first",
    require_numeric: bool = True,
) -> dict[str, Any] | None:
    ranked: list[tuple[int, dict[str, Any]]] = []
    include_norm = tuple(term.lower() for term in include_terms if term)
    exclude_norm = tuple(term.lower() for term in exclude_terms if term)

    for sensor in sensors:
        if categories and sensor.get("category") not in categories:
            continue
        if groups and sensor.get("group_kind") not in groups:
            continue
        if require_numeric and sensor.get("value") is None:
            continue

        blob = sensor.get("blob_norm", "")
        if any(term in blob for term in exclude_norm):
            continue

        score = 1 if sensor.get("value") is not None else 0
        if include_norm:
            for term in include_norm:
                if term in blob:
                    score += 5
        ranked.append((score, sensor))

    if not ranked:
        return None

    best_score = max(score for score, _ in ranked)
    candidates = [sensor for score, sensor in ranked if score == best_score]
    numeric_candidates = [sensor for sensor in candidates if sensor.get("value") is not None]

    if prefer == "max" and numeric_candidates:
        return max(numeric_candidates, key=lambda item: float(item.get("value") or 0.0))
    if prefer == "min" and numeric_candidates:
        return min(numeric_candidates, key=lambda item: float(item.get("value") or 0.0))
    if prefer == "abs_max" and numeric_candidates:
        return max(numeric_candidates, key=lambda item: abs(float(item.get("value") or 0.0)))
    if prefer == "first_numeric" and numeric_candidates:
        return numeric_candidates[0]
    if numeric_candidates:
        return numeric_candidates[0]
    return candidates[0]


def _build_summary_entry(
    key: str,
    label: str,
    section: str,
    sensor: dict[str, Any] | None,
) -> dict[str, Any]:
    if not sensor:
        return {
            "key": key,
            "section": section,
            "label": label,
            "available": False,
            "display": "-",
            "value": None,
            "unit": "",
            "device": "",
            "group": "",
            "path": "",
            "category": "",
        }

    return {
        "key": key,
        "section": section,
        "label": label,
        "available": True,
        "display": sensor.get("value_display") or "-",
        "value": sensor.get("value"),
        "unit": sensor.get("value_unit") or "",
        "device": sensor.get("hardware_name") or "",
        "group": sensor.get("group") or "",
        "path": sensor.get("path") or "",
        "category": sensor.get("category_label") or "",
        "min_display": sensor.get("min_display") or "-",
        "max_display": sensor.get("max_display") or "-",
    }


def _build_empty_hardware_snapshot(error: str = "", diagnostics: list[str] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    summary_order: list[str] = []
    important: list[dict[str, Any]] = []
    for key, label, section in _HARDWARE_SUMMARY_SPECS:
        entry = _build_summary_entry(key, label, section, None)
        summary[key] = entry
        summary_order.append(key)
        important.append(entry)
    return {
        "available": False,
        "source": "LibreHardwareMonitor",
        "updated_at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "error": error,
        "diagnostics": diagnostics or [],
        "summary": summary,
        "summary_order": summary_order,
        "important": important,
        "all_sensors": [],
        "total_sensors": 0,
        "category_counts": [],
        "kind_groups": [],
    }


def _build_hardware_summary(sensors: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    cleaned = [
        sensor
        for sensor in sensors
        if not any(skip in sensor.get("blob_norm", "") for skip in _LHM_SKIP_NAME_PARTS)
    ]

    def pick(**kwargs: Any) -> dict[str, Any] | None:
        return _select_sensor(cleaned, **kwargs)

    selected: dict[str, dict[str, Any] | None] = {}
    selected["cpu_load"] = pick(
        categories=("cpu",),
        groups=("load",),
        include_terms=("cpu total",),
        prefer="max",
    ) or pick(
        categories=("cpu",),
        groups=("load",),
        include_terms=("cpu",),
        prefer="max",
    )
    selected["cpu_temp"] = pick(
        categories=("cpu",),
        groups=("temperature",),
        include_terms=("cpu package",),
        prefer="max",
    ) or pick(
        categories=("cpu",),
        groups=("temperature",),
        include_terms=("core max", "package"),
        prefer="max",
    )
    selected["cpu_power"] = pick(
        categories=("cpu",),
        groups=("power",),
        include_terms=("cpu package", "package"),
        prefer="max",
    ) or pick(
        categories=("cpu",),
        groups=("power",),
        include_terms=("cpu cores",),
        prefer="max",
    )
    selected["cpu_clock"] = pick(
        categories=("cpu",),
        groups=("clock",),
        include_terms=("core",),
        prefer="max",
    )

    selected["gpu_load"] = pick(
        categories=("gpu",),
        groups=("load",),
        include_terms=("3d",),
        prefer="max",
    ) or pick(
        categories=("gpu",),
        groups=("load",),
        include_terms=("gpu",),
        prefer="max",
    )
    selected["gpu_temp"] = pick(
        categories=("gpu",),
        groups=("temperature",),
        include_terms=("gpu", "hot spot", "core"),
        prefer="max",
    )
    selected["gpu_power"] = pick(
        categories=("gpu",),
        groups=("power",),
        include_terms=("gpu power", "package"),
        prefer="max",
    )

    selected["vrm_temp"] = pick(
        categories=("mainboard", "other"),
        groups=("temperature",),
        include_terms=("vrm", "mos", "motherboard", "pch", "chipset", "soc", "tmpin"),
        prefer="max",
    )

    selected["ram_load"] = pick(
        categories=("memory",),
        groups=("load",),
        include_terms=("total memory", "memory"),
        prefer="max",
    )
    selected["ram_used"] = pick(
        categories=("memory",),
        groups=("data",),
        include_terms=("total memory", "memory used"),
        prefer="max",
    ) or pick(
        categories=("memory",),
        groups=("data",),
        include_terms=("memory used",),
        prefer="max",
    )
    selected["ram_temp_max"] = pick(
        categories=("memory",),
        groups=("temperature",),
        include_terms=("dimm", "memory"),
        prefer="max",
    )

    selected["disk_temp_max"] = pick(
        categories=("storage",),
        groups=("temperature",),
        include_terms=("composite", "temperature", "drive"),
        prefer="max",
    )
    selected["disk_used_max"] = pick(
        categories=("storage",),
        groups=("load",),
        include_terms=("used space",),
        prefer="max",
    )
    selected["disk_life_min"] = pick(
        categories=("storage",),
        groups=("level",),
        include_terms=("life",),
        prefer="min",
    ) or pick(
        categories=("storage",),
        groups=("level",),
        include_terms=("percentage used",),
        prefer="max",
    )

    selected["fan_speed_max"] = pick(
        categories=("cpu", "gpu", "mainboard", "other"),
        groups=("fan",),
        include_terms=("fan",),
        prefer="max",
    )

    summary: dict[str, Any] = {}
    summary_order: list[str] = []
    important: list[dict[str, Any]] = []
    for key, label, section in _HARDWARE_SUMMARY_SPECS:
        entry = _build_summary_entry(key, label, section, selected.get(key))
        summary[key] = entry
        summary_order.append(key)
        important.append(entry)
    return summary, summary_order, important


def _build_kind_groups(sensors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kind_map: dict[str, list[dict[str, Any]]] = {}
    for sensor in sensors:
        kind = str(sensor.get("group_kind") or "other")
        kind_map.setdefault(kind, []).append(sensor)

    ordered_keys = [key for key in _HARDWARE_KIND_ORDER if key in kind_map]
    extra_keys = sorted(key for key in kind_map.keys() if key not in _HARDWARE_KIND_ORDER)
    result: list[dict[str, Any]] = []

    for key in ordered_keys + extra_keys:
        items = kind_map.get(key, [])
        if not items:
            continue

        category_counts: dict[str, int] = {}
        for sensor in items:
            category = str(sensor.get("category") or "other")
            category_counts[category] = category_counts.get(category, 0) + 1

        categories = [
            {
                "key": category_key,
                "label": _HARDWARE_CATEGORY_LABELS.get(category_key, category_key.title()),
                "count": category_counts.get(category_key, 0),
            }
            for category_key in _HARDWARE_CATEGORY_LABELS.keys()
            if category_counts.get(category_key, 0) > 0
        ]
        result.append(
            {
                "key": key,
                "label": _HARDWARE_KIND_LABELS.get(key, key.title()),
                "count": len(items),
                "categories": categories,
                "sensors": items,
            }
        )

    return result


def _collect_hardware_snapshot(force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cached = _HARDWARE_CACHE.get("data")
    if not force and cached and (now - _HARDWARE_CACHE.get("ts", 0.0) < _HARDWARE_CACHE_TTL_SECONDS):
        return cached

    if not _HARDWARE_LOCK.acquire(blocking=False):
        return cached or _build_empty_hardware_snapshot()

    try:
        host, port, web_enabled = _load_lhm_listener_settings()
        url = f"http://{host}:{port}/data.json"
        diagnostics: list[str] = [f"Точка данных: {url}"]
        if not web_enabled:
            diagnostics.append("В конфигурации LibreHardwareMonitor отключен веб-сервер.")

        tree, error = _fetch_lhm_tree(url)
        if tree is None and _LHM_AUTOSTART_ENABLED:
            started, start_msg = _start_lhm_process(host, port)
            if start_msg:
                diagnostics.append(start_msg)
            if started:
                tree, error = _fetch_lhm_tree_with_retries(
                    url,
                    attempts=_HARDWARE_START_FETCH_ATTEMPTS,
                    delay_seconds=_HARDWARE_START_WAIT_SECONDS,
                )
        elif tree is None:
            diagnostics.append("LibreHardwareMonitor autostart is disabled because the web panel is not running.")

        if tree is None:
            snapshot = _build_empty_hardware_snapshot(
                error=error or "Данные LibreHardwareMonitor недоступны.",
                diagnostics=diagnostics,
            )
            _HARDWARE_CACHE["ts"] = now
            _HARDWARE_CACHE["data"] = snapshot
            return snapshot

        all_sensors = _flatten_lhm_sensors(tree)
        summary, summary_order, important = _build_hardware_summary(all_sensors)
        public_sensors = []
        for sensor in all_sensors:
            public_sensors.append(
                {
                    "id": sensor.get("id"),
                    "category": sensor.get("category"),
                    "category_label": sensor.get("category_label"),
                    "hardware_name": sensor.get("hardware_name"),
                    "hardware_id": sensor.get("hardware_id"),
                    "group": sensor.get("group"),
                    "group_kind": sensor.get("group_kind"),
                    "name": sensor.get("name"),
                    "value": sensor.get("value"),
                    "value_unit": sensor.get("value_unit"),
                    "value_display": sensor.get("value_display"),
                    "min": sensor.get("min"),
                    "min_unit": sensor.get("min_unit"),
                    "min_display": sensor.get("min_display"),
                    "max": sensor.get("max"),
                    "max_unit": sensor.get("max_unit"),
                    "max_display": sensor.get("max_display"),
                    "path": sensor.get("path"),
                }
            )

        category_counts_map: dict[str, int] = {}
        for sensor in all_sensors:
            category = sensor.get("category") or "other"
            category_counts_map[category] = category_counts_map.get(category, 0) + 1

        category_counts = [
            {
                "key": key,
                "label": _HARDWARE_CATEGORY_LABELS.get(key, key.title()),
                "count": category_counts_map.get(key, 0),
            }
            for key in _HARDWARE_CATEGORY_LABELS.keys()
            if category_counts_map.get(key, 0) > 0
        ]
        kind_groups = _build_kind_groups(public_sensors)

        snapshot = {
            "available": bool(all_sensors),
            "source": "LibreHardwareMonitor",
            "updated_at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "error": "",
            "diagnostics": diagnostics,
            "summary": summary,
            "summary_order": summary_order,
            "important": important,
            "all_sensors": public_sensors,
            "total_sensors": len(public_sensors),
            "category_counts": category_counts,
            "kind_groups": kind_groups,
        }
        _HARDWARE_CACHE["ts"] = now
        _HARDWARE_CACHE["data"] = snapshot
        return snapshot
    finally:
        _HARDWARE_LOCK.release()


def _hardware_overview_payload(hardware: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(hardware, dict):
        return {}
    return {
        "available": bool(hardware.get("available")),
        "source": hardware.get("source") or "",
        "updated_at": hardware.get("updated_at") or "",
        "error": hardware.get("error") or "",
        "summary": hardware.get("summary") or {},
        "summary_order": hardware.get("summary_order") or [],
        "total_sensors": _to_int(hardware.get("total_sensors")) or 0,
    }


def get_overview_snapshot(force: bool = False) -> Dict[str, Any]:
    now = time.monotonic()
    cached = _OVERVIEW_CACHE.get("data")
    if not force and cached and (now - _OVERVIEW_CACHE.get("ts", 0.0) < _OVERVIEW_CACHE_TTL_SECONDS):
        return cached

    if not _OVERVIEW_LOCK.acquire(blocking=False):
        return cached or {}

    try:
        vm = psutil.virtual_memory()
        net = psutil.net_io_counters()
        disk_totals = _calc_disk_totals()
        hardware = _collect_hardware_snapshot()
        uptime_seconds = None
        uptime_human = "-"
        try:
            boot_time = psutil.boot_time()
            uptime_seconds = int(time.time() - boot_time)
            uptime_human = _format_uptime(uptime_seconds)
        except Exception:
            uptime_seconds = None
            uptime_human = "-"

        data = {
            "cpu_percent": round(psutil.cpu_percent(interval=0.1), 1),
            "uptime_seconds": uptime_seconds,
            "uptime_human": uptime_human,
            "memory": {
                "total_bytes": vm.total,
                "used_bytes": vm.used,
                "free_bytes": vm.available,
                "percent": round(vm.percent, 1),
                "total_gb": _to_gb(vm.total),
                "used_gb": _to_gb(vm.used),
                "free_gb": _to_gb(vm.available),
            },
            "disk_percent": disk_totals.get("percent"),
            "disk_totals": disk_totals,
            "net_io": {
                "sent_bytes": net.bytes_sent,
                "recv_bytes": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "sent_human": _human_bytes(net.bytes_sent),
                "recv_human": _human_bytes(net.bytes_recv),
            },
            "power": _collect_overview_power(),
            "hardware": _hardware_overview_payload(hardware),
        }
        _OVERVIEW_CACHE["ts"] = now
        _OVERVIEW_CACHE["data"] = data
        return data
    finally:
        _OVERVIEW_LOCK.release()


def _run_powershell_json(script: str) -> tuple[Any | None, str | None]:
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
        return None, f"PowerShell превысил таймаут {_PS_TIMEOUT_SECONDS}с"
    except Exception as exc:
        return None, str(exc)

    stdout = (result.stdout or "").strip().lstrip("\ufeff")
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return None, stderr or stdout or f"Ошибка выполнения PowerShell: {result.returncode}"
    if not stdout:
        return None, stderr or "Пустой вывод PowerShell"
    try:
        return json.loads(stdout), None
    except Exception as exc:
        return None, f"Не удалось разобрать JSON PowerShell: {exc}"


def _collect_ps_snapshot(fast: bool = False) -> tuple[Dict[str, Any], str | None]:
    script = """
$ErrorActionPreference = "Stop";
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;
$FastMode = __FAST_MODE__;

$useCim = Get-Command Get-CimInstance -ErrorAction SilentlyContinue;
$useCimMethod = Get-Command Invoke-CimMethod -ErrorAction SilentlyContinue;

function Get-WmiCompat {
  param(
    [Parameter(Mandatory=$true)][string]$ClassName,
    [string]$Namespace = "root\\cimv2",
    [string]$Filter = $null
  )
  if ($useCim) {
    if ($Filter) { return Get-CimInstance -Namespace $Namespace -ClassName $ClassName -Filter $Filter }
    return Get-CimInstance -Namespace $Namespace -ClassName $ClassName
  }
  if ($Filter) { return Get-WmiObject -Namespace $Namespace -Class $ClassName -Filter $Filter }
  return Get-WmiObject -Namespace $Namespace -Class $ClassName
}

function Invoke-WmiCompat {
  param(
    [Parameter(Mandatory=$true)]$InputObject,
    [Parameter(Mandatory=$true)][string]$MethodName
  )
  if ($useCimMethod -and $useCim) {
    return Invoke-CimMethod -InputObject $InputObject -MethodName $MethodName
  }
  return Invoke-WmiMethod -InputObject $InputObject -Name $MethodName
}

$os = Get-WmiCompat -ClassName Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,LastBootUpTime,TotalVisibleMemorySize,FreePhysicalMemory,InstallDate,Locale,MUILanguages,OSLanguage,SerialNumber,RegisteredUser,Organization,SystemDirectory,WindowsDirectory,ProductType;
$cs = Get-WmiCompat -ClassName Win32_ComputerSystem | Select-Object Manufacturer,Model,SystemType,TotalPhysicalMemory,Domain,DomainRole,PartOfDomain,HypervisorPresent,Workgroup,SystemFamily,PCSystemType,PCSystemTypeEx;
$bios = Get-WmiCompat -ClassName Win32_BIOS | Select-Object SMBIOSBIOSVersion,SerialNumber,ReleaseDate,Manufacturer;
$board = Get-WmiCompat -ClassName Win32_BaseBoard | Select-Object Manufacturer,Product,SerialNumber,Version;
$csprod = Get-WmiCompat -ClassName Win32_ComputerSystemProduct | Select-Object UUID,IdentifyingNumber,Name,Vendor;
$enclosure = Get-WmiCompat -ClassName Win32_SystemEnclosure | Select-Object ChassisTypes,SerialNumber,SMBIOSAssetTag,Manufacturer,Version;
$cpu = Get-WmiCompat -ClassName Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,Manufacturer;
$disks = Get-WmiCompat -ClassName Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,VolumeName,FileSystem,Size,FreeSpace;
$phys = Get-WmiCompat -ClassName Win32_DiskDrive | Select-Object Model,SerialNumber,InterfaceType,MediaType,Size,DeviceID,FirmwareRevision;
$pagefiles = Get-WmiCompat -ClassName Win32_PageFileUsage | Select-Object Name,AllocatedBaseSize,CurrentUsage,PeakUsage;
$nics = Get-WmiCompat -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled=TRUE" | Select-Object Description,MACAddress,IPAddress,IPSubnet,DefaultIPGateway,DNSServerSearchOrder;

$batteries = @();
try {
  $batteries = Get-WmiCompat -ClassName Win32_Battery | Select-Object Name,DeviceID,BatteryStatus,EstimatedChargeRemaining,EstimatedRunTime,TimeOnBattery,TimeToFullCharge,Chemistry,DesignCapacity,FullChargeCapacity,Status,Availability,Manufacturer,Description,PNPDeviceID,SmartBatteryVersion;
} catch {}

$portableBatteries = @();
try {
  $portableBatteries = Get-WmiCompat -ClassName Win32_PortableBattery | Select-Object Name,DeviceID,Manufacturer,Description,Chemistry,DesignCapacity,DesignVoltage,CapacityMultiplier,Status;
} catch {}

$ups = @();
try {
  $ups = Get-WmiCompat -ClassName Win32_UninterruptiblePowerSupply | Select-Object Name,DeviceID,EstimatedChargeRemaining,EstimatedRunTime,TimeOnBackup,BatteryInstalled,CanTurnOffRemotely,IsSwitchingSupply,Status,Availability,Manufacturer,SerialNumber,TotalOutputPower;
} catch {}

$pnpBattery = @();
if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
  try {
    $pnpBattery = Get-PnpDevice -Class Battery -PresentOnly | Select-Object Class,FriendlyName,InstanceId,Status,Problem,Manufacturer,Service;
  } catch {}
}

$pnpPowerDevices = @();
try {
  $pnpPowerDevices = Get-WmiCompat -ClassName Win32_PnPEntity -Filter "PNPClass='Battery'" | Select-Object Name,DeviceID,PNPClass,Manufacturer,Status,Service;
} catch {}

$serialPorts = @();
try {
  $serialPorts = Get-WmiCompat -ClassName Win32_SerialPort | Select-Object DeviceID,Name,Description,PNPDeviceID,Status,ProviderType;
} catch {}

$powerStatus = $null;
try {
  Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue | Out-Null;
  $ps = [System.Windows.Forms.SystemInformation]::PowerStatus;
  $lifePercent = $null;
  try {
    if ($ps.BatteryLifePercent -ge 0) {
      $lifePercent = [math]::Round(($ps.BatteryLifePercent * 100), 1);
    }
  } catch {}
  $powerStatus = [pscustomobject]@{
    PowerLineStatus = [string]$ps.PowerLineStatus;
    BatteryChargeStatus = [string]$ps.BatteryChargeStatus;
    BatteryLifePercent = $lifePercent;
    BatteryLifeRemaining = $ps.BatteryLifeRemaining;
  };
} catch {}

$upsTools = [pscustomobject]@{
  ApcAccess = [bool](Get-Command apcaccess -ErrorAction SilentlyContinue);
  Upsc = [bool](Get-Command upsc -ErrorAction SilentlyContinue);
  SnmpService = [bool](Get-Service -Name SNMP -ErrorAction SilentlyContinue);
};

$tz = $null;
try {
  $tzobj = Get-TimeZone;
  $tz = [pscustomobject]@{Id=$tzobj.Id;DisplayName=$tzobj.DisplayName;BaseUtcOffset=$tzobj.BaseUtcOffset.ToString()};
} catch {
  try {
    $tzobj = [System.TimeZoneInfo]::Local;
    $tz = [pscustomobject]@{Id=$tzobj.Id;DisplayName=$tzobj.DisplayName;BaseUtcOffset=$tzobj.BaseUtcOffset.ToString()};
  } catch {}
}

$winver = $null;
try {
  $cv = Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion';
  $winver = [pscustomobject]@{
    ReleaseId=$cv.ReleaseId;DisplayVersion=$cv.DisplayVersion;EditionID=$cv.EditionID;
    InstallationType=$cv.InstallationType;UBR=$cv.UBR;CurrentBuild=$cv.CurrentBuild;ProductName=$cv.ProductName
  };
} catch {}

$osNameResolved = $null;
try {
  $osNameResolved = $os.Caption;
  $buildNum = $null;
  try { $buildNum = [int]$os.BuildNumber } catch {}
  if ($buildNum -ge 22000 -and $osNameResolved -match "Windows 10") {
    $osNameResolved = $osNameResolved -replace "Windows 10", "Windows 11";
  }
} catch {}
try {
  $ci = Get-ComputerInfo -Property WindowsProductName,WindowsVersion,OsName,BiosFirmwareType -ErrorAction SilentlyContinue;
  if ($ci) {
    if (-not $winver) { $winver = [pscustomobject]@{} }
    if (-not $winver.ProductName -and $ci.WindowsProductName) { $winver | Add-Member -NotePropertyName ProductName -NotePropertyValue $ci.WindowsProductName }
    if (-not $winver.DisplayVersion -and $ci.WindowsVersion) { $winver | Add-Member -NotePropertyName DisplayVersion -NotePropertyValue $ci.WindowsVersion }
    if (-not $winver.ReleaseId -and $ci.WindowsVersion) { $winver | Add-Member -NotePropertyName ReleaseId -NotePropertyValue $ci.WindowsVersion }
    if (-not $winver.OsName -and $ci.OsName) { $winver | Add-Member -NotePropertyName OsName -NotePropertyValue $ci.OsName }
    if ($null -eq $winver.BiosFirmwareType -and $null -ne $ci.BiosFirmwareType) { $winver | Add-Member -NotePropertyName BiosFirmwareType -NotePropertyValue $ci.BiosFirmwareType -Force }
  }
} catch {}

$secureBootDiagnostics = @();
$secureBoot = [pscustomobject]@{
  Enabled = $null;
  Supported = $null;
  RegistryValue = $null;
  FirmwareType = $null;
  Source = "";
  Error = "";
  Diagnostics = $secureBootDiagnostics;
};
try {
  if ($winver -and $null -ne $winver.BiosFirmwareType) {
    $secureBoot.FirmwareType = [int]$winver.BiosFirmwareType;
  }
} catch {}
if ($null -eq $secureBoot.FirmwareType) {
  try {
    $ciFw = Get-ComputerInfo -Property BiosFirmwareType -ErrorAction SilentlyContinue;
    if ($ciFw -and $null -ne $ciFw.BiosFirmwareType) {
      $secureBoot.FirmwareType = [int]$ciFw.BiosFirmwareType;
    }
  } catch {}
}
if ($null -ne $secureBoot.FirmwareType) {
  if ([int]$secureBoot.FirmwareType -eq 2) {
    $secureBoot.Supported = $true;
  } else {
    $secureBoot.Supported = $false;
  }
}
try {
  $sbResult = Confirm-SecureBootUEFI -ErrorAction Stop;
  if ($sbResult -is [bool]) {
    $secureBoot.Enabled = [bool]$sbResult;
    $secureBoot.Supported = $true;
    $secureBoot.Source = "Confirm-SecureBootUEFI";
  } elseif ($null -ne $sbResult) {
    $secureBoot.Enabled = [bool]$sbResult;
    $secureBoot.Supported = $true;
    $secureBoot.Source = "Confirm-SecureBootUEFI";
  }
} catch {
  $msg = [string]$_.Exception.Message;
  if ($msg) {
    $secureBootDiagnostics += "Confirm-SecureBootUEFI: $msg";
    if (-not $secureBoot.Error) { $secureBoot.Error = $msg; }
    $lowerMsg = $msg.ToLowerInvariant();
    if ($lowerMsg.Contains("not supported")) { $secureBoot.Supported = $false; }
    if ($lowerMsg.Contains("access was denied") -or $lowerMsg.Contains("unable to set proper privileges") -or $lowerMsg.Contains("отказано")) {
      $secureBootDiagnostics += "Confirm-SecureBootUEFI требует повышенных прав.";
    }
  }
}
try {
  $sbReg = Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State' -Name UEFISecureBootEnabled -ErrorAction Stop;
  if ($sbReg -and $null -ne $sbReg.UEFISecureBootEnabled) {
    $regValue = [int]$sbReg.UEFISecureBootEnabled;
    $secureBoot.RegistryValue = $regValue;
    if ($null -eq $secureBoot.Enabled) {
      $secureBoot.Enabled = ($regValue -eq 1);
    }
    if ($null -eq $secureBoot.Supported) {
      $secureBoot.Supported = $true;
    }
    if (-not $secureBoot.Source) {
      $secureBoot.Source = "Registry SecureBoot\\State";
    } else {
      $secureBoot.Source = "$($secureBoot.Source) + Registry";
    }
  }
} catch {
  $msg = [string]$_.Exception.Message;
  if ($msg) {
    $secureBootDiagnostics += "Registry SecureBoot\\State: $msg";
  }
}
$secureBoot.Diagnostics = $secureBootDiagnostics;

$tpmWarnings = @();
$tpmSignals = [pscustomobject]@{
  GetTpmAttempted = $false;
  GetTpmElevationRequired = $false;
  Win32TpmAttempted = $false;
  Win32TpmAccessDenied = $false;
  PnpSecurityAttempted = $false;
  TpmToolAttempted = $false;
};

$tpmGet = $null;
if (Get-Command Get-Tpm -ErrorAction SilentlyContinue) {
  $tpmSignals.GetTpmAttempted = $true;
  try {
    $tpmRawResult = Get-Tpm *>&1;
    $tpmCandidate = $null;
    foreach ($item in @($tpmRawResult)) {
      if ($null -eq $item) { continue }
      if ($item -is [System.Management.Automation.ErrorRecord]) {
        $msg = [string]$item.Exception.Message;
        if (-not $msg) { $msg = [string]$item; }
        if ($msg) {
          $tpmWarnings += "Get-Tpm: $msg";
          if ($msg.ToLowerInvariant().Contains("administrator privilege is required")) {
            $tpmSignals.GetTpmElevationRequired = $true;
          }
        }
        continue
      }
      if ($item -is [string]) {
        $msg = $item.Trim();
        if ($msg) {
          $tpmWarnings += "Get-Tpm: $msg";
          if ($msg.ToLowerInvariant().Contains("administrator privilege is required")) {
            $tpmSignals.GetTpmElevationRequired = $true;
          }
        }
        continue
      }
      if (-not $tpmCandidate) {
        $tpmCandidate = $item;
      }
    }
    if ($tpmCandidate) {
      $tpmGet = $tpmCandidate | Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated,TpmOwned,RestartPending,ManufacturerId,ManufacturerIdTxt,SpecVersion,ManufacturerVersion,ManufacturerVersionFull20,ManagedAuthLevel,AutoProvisioning,LockedOut,LockoutHealTime,LockoutCount,LockoutMax,SelfTest;
    }
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) { $tpmWarnings += "Get-Tpm exception: $msg"; }
  }
}

$tpmWmiRaw = $null;
$tpmWmi = $null;
$tpmWmiState = $null;
$tpmSignals.Win32TpmAttempted = $false;
if (-not $FastMode) {
  $tpmSignals.Win32TpmAttempted = $true;
  try {
    $tpmWmiRaw = Get-WmiCompat -Namespace "root\\CIMV2\\Security\\MicrosoftTpm" -ClassName Win32_Tpm;
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) {
      $tpmWarnings += "Win32_Tpm: $msg";
      $lowerMsg = $msg.ToLowerInvariant();
      if ($lowerMsg.Contains("access is denied") -or $lowerMsg.Contains("отказано в доступе") -or $lowerMsg.Contains("0x80041003")) {
        $tpmSignals.Win32TpmAccessDenied = $true;
      }
    }
  }
  if ($tpmWmiRaw) {
    $tpmWmi = $tpmWmiRaw | Select-Object IsEnabled_InitialValue,IsActivated_InitialValue,IsOwned_InitialValue,ManufacturerId,ManufacturerIdTxt,ManufacturerVersion,ManufacturerVersionInfo,SpecVersion,PhysicalPresenceVersionInfo;
    $tpmWmiState = [pscustomobject]@{IsEnabled=$null;IsActivated=$null;IsOwned=$null};
    try {
      $res = Invoke-WmiCompat -InputObject $tpmWmiRaw -MethodName IsEnabled;
      if ($null -ne $res.IsEnabled) { $tpmWmiState.IsEnabled = $res.IsEnabled }
    } catch {
      $msg = [string]$_.Exception.Message;
      if ($msg) { $tpmWarnings += "Win32_Tpm IsEnabled: $msg"; }
    }
    try {
      $res = Invoke-WmiCompat -InputObject $tpmWmiRaw -MethodName IsActivated;
      if ($null -ne $res.IsActivated) { $tpmWmiState.IsActivated = $res.IsActivated }
    } catch {
      $msg = [string]$_.Exception.Message;
      if ($msg) { $tpmWarnings += "Win32_Tpm IsActivated: $msg"; }
    }
    try {
      $res = Invoke-WmiCompat -InputObject $tpmWmiRaw -MethodName IsOwned;
      if ($null -ne $res.IsOwned) { $tpmWmiState.IsOwned = $res.IsOwned }
    } catch {
      $msg = [string]$_.Exception.Message;
      if ($msg) { $tpmWarnings += "Win32_Tpm IsOwned: $msg"; }
    }
  }
}

$tpmPnpSecurity = @();
if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
  $tpmSignals.PnpSecurityAttempted = $true;
  try {
    $tpmPnpSecurity = Get-PnpDevice -Class SecurityDevices -PresentOnly | Select-Object Class,FriendlyName,InstanceId,Status,Problem,Manufacturer,Service;
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) { $tpmWarnings += "Get-PnpDevice SecurityDevices: $msg"; }
  }
}

$tpmWmiSecurity = @();
try {
  $tpmWmiSecurity = Get-WmiCompat -ClassName Win32_PnPEntity -Filter "PNPClass='SecurityDevices'" | Select-Object Name,DeviceID,PNPClass,Manufacturer,Status,Service;
} catch {
  $msg = [string]$_.Exception.Message;
  if ($msg) { $tpmWarnings += "Win32_PnPEntity SecurityDevices: $msg"; }
}

$tpmTool = $null;
if ((-not $FastMode) -and (Get-Command tpmtool -ErrorAction SilentlyContinue)) {
  $tpmSignals.TpmToolAttempted = $true;
  try {
    $raw = (& tpmtool getdeviceinformation 2>&1 | Out-String);
    $values = @();
    foreach ($line in ($raw -split "`r?`n")) {
      $trim = $line.Trim();
      if (-not $trim) { continue }
      if ($trim -match "^\-\s*(.+)$") {
        $val = $matches[1].Trim();
        if ($val) { $values += $val }
        continue
      }
      if ($trim -match "^[A-Za-z0-9].+") {
        $values += $trim;
      }
    }
    $tpmTool = [pscustomobject]@{
      Raw = $raw;
      Values = $values;
    };
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) { $tpmWarnings += "tpmtool: $msg"; }
  }
}

$tpm = $null;
if ($tpmGet -or $tpmWmi -or $tpmWmiState -or $tpmPnpSecurity -or $tpmWmiSecurity -or $tpmTool -or $tpmWarnings) {
  $tpm = [pscustomobject]@{
    GetTpm=$tpmGet;
    Wmi=$tpmWmi;
    WmiState=$tpmWmiState;
    PnpSecurity=$tpmPnpSecurity;
    WmiSecurity=$tpmWmiSecurity;
    Tool=$tpmTool;
    Warnings=$tpmWarnings;
    Signals=$tpmSignals
  };
}

$defender = $null;
if (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {
  try {
    $defender = Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,AntispywareEnabled,RealTimeProtectionEnabled,NISEnabled,IsTamperProtected,SignatureVersion,EngineVersion,ProductVersion,FullScanEndTime,QuickScanEndTime,AntivirusSignatureLastUpdated,AntispywareSignatureLastUpdated,NISSignatureLastUpdated
  } catch {}
}

$hotfix = $null;
if (-not $FastMode) {
  try {
    $hf = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1;
    if ($hf) {
      $installed = $null;
      try { $installed = (Get-Date $hf.InstalledOn).ToString("o") } catch {}
      $hotfix = [pscustomobject]@{HotFixID=$hf.HotFixID;InstalledOn=$installed;Description=$hf.Description;InstalledBy=$hf.InstalledBy};
    }
  } catch {}
} else {
  $hotfix = [pscustomobject]@{Skipped=$true;Reason="fast_mode"};
  }

$svcNames = @("wuauserv","WinRM","TermService","w32time");
$services = @();
foreach ($svcName in $svcNames) {
  try {
    $svc = Get-WmiCompat -ClassName Win32_Service -Filter "Name='$svcName'";
    if ($svc) {
      $svc = $svc | Select-Object -First 1;
      $services += [pscustomobject]@{
        Name=$svc.Name;
        DisplayName=$svc.DisplayName;
        State=$svc.State;
        StartMode=$svc.StartMode;
        StartName=$svc.StartName
      };
    }
  } catch {}
}

$firewall = $null;
if (Get-Command Get-NetFirewallProfile -ErrorAction SilentlyContinue) {
  try { $firewall = Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction } catch {}
}

$bitlocker = @();
$bitlockerMeta = [pscustomobject]@{
  Available = $null;
  AccessDenied = $false;
  Sources = @();
  Errors = @();
  Diagnostics = @();
  ManageBdeAttempted = $false;
  ManageBdeExitCode = $null;
  ManageBdeRawSnippet = "";
};
if ($FastMode) {
  $bitlockerMeta.Diagnostics += "BitLocker collection skipped in fast mode.";
} elseif (Get-Command Get-BitLockerVolume -ErrorAction SilentlyContinue) {
  $bitlockerMeta.Available = $true;
  try {
    $bitlocker = Get-BitLockerVolume | Select-Object MountPoint,VolumeType,ProtectionStatus,LockStatus,EncryptionMethod,EncryptionPercentage;
    if ($bitlocker) {
      $bitlockerMeta.Sources += "Get-BitLockerVolume";
    } else {
      $bitlockerMeta.Diagnostics += "Get-BitLockerVolume вернул пустой список томов.";
    }
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) {
      $bitlockerMeta.Errors += "Get-BitLockerVolume: $msg";
      $lowerMsg = $msg.ToLowerInvariant();
      if ($lowerMsg.Contains("access is denied") -or $lowerMsg.Contains("отказано в доступе") -or $lowerMsg.Contains("0x80041003")) {
        $bitlockerMeta.AccessDenied = $true;
        $bitlockerMeta.Diagnostics += "Get-BitLockerVolume требует прав администратора.";
      }
    }
  }
} else {
  $bitlockerMeta.Available = $false;
  $bitlockerMeta.Errors += "Get-BitLockerVolume недоступен (модуль BitLocker не найден).";
}
if ((-not $FastMode) -and (-not $bitlocker) -and (-not $bitlockerMeta.AccessDenied) -and (Get-Command manage-bde -ErrorAction SilentlyContinue)) {
  $bitlockerMeta.ManageBdeAttempted = $true;
  try {
    $manageBdeRaw = (& manage-bde -status 2>&1 | Out-String);
    $bitlockerMeta.ManageBdeExitCode = $LASTEXITCODE;
    if ($manageBdeRaw) {
      $snippetLines = @();
      foreach ($line in ($manageBdeRaw -split "`r?`n")) {
        if ($line.Trim()) { $snippetLines += $line.Trim() }
        if ($snippetLines.Count -ge 20) { break }
      }
      $bitlockerMeta.ManageBdeRawSnippet = ($snippetLines -join "`n");
    }
    if ($bitlockerMeta.ManageBdeExitCode -eq 0) {
      $bitlockerMeta.Sources += "manage-bde";
      $bitlockerMeta.Diagnostics += "manage-bde -status выполнен, но детальный парсинг локализованного вывода отключен.";
    } else {
      $bitlockerMeta.Errors += "manage-bde -status завершился с кодом $($bitlockerMeta.ManageBdeExitCode).";
      $lowerRaw = [string]$manageBdeRaw;
      if ($lowerRaw.ToLowerInvariant().Contains("administrative rights") -or $lowerRaw.ToLowerInvariant().Contains("требуются права")) {
        $bitlockerMeta.AccessDenied = $true;
      }
    }
  } catch {
    $msg = [string]$_.Exception.Message;
    if ($msg) {
      $bitlockerMeta.Errors += "manage-bde: $msg";
    }
  }
}

$rebootChecks = [ordered]@{
  CbsRebootPending = $false;
  CbsPackagesPending = $false;
  WindowsUpdateRebootRequired = $false;
  SessionManagerPendingFileRenameOperations = $false;
  SessionManagerPendingFileRenameOperations2 = $false;
  UpdateExeVolatile = $false;
  ServerManagerCurrentRebootAttempts = $false;
  NetlogonJoinDomain = $false;
  NetlogonAvoidSpnSet = $false;
  ComputerNameChangePending = $false;
};
$rebootReasons = @();
$rebootDiagnostics = @();
try {
  if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending') {
    $rebootChecks.CbsRebootPending = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "cbs_reboot_pending";
      Title = "Component Based Servicing: RebootPending";
      Details = "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending";
    };
  }
  if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\PackagesPending') {
    $rebootChecks.CbsPackagesPending = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "cbs_packages_pending";
      Title = "Component Based Servicing: PackagesPending";
      Details = "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\PackagesPending";
    };
  }
  if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') {
    $rebootChecks.WindowsUpdateRebootRequired = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "wu_reboot_required";
      Title = "Windows Update: RebootRequired";
      Details = "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired";
    };
  }
  $sessionManager = Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' -ErrorAction SilentlyContinue;
  if ($sessionManager) {
    $pendingRename1Raw = @($sessionManager.PendingFileRenameOperations);
    $pendingRename1 = @();
    foreach ($entry in $pendingRename1Raw) {
      if ($null -eq $entry) { continue }
      $text = [string]$entry;
      if (-not $text) { continue }
      $text = $text.Trim();
      if (-not $text) { continue }
      $pendingRename1 += $text;
    }
    if ($pendingRename1.Count -gt 0) {
      $rebootChecks.SessionManagerPendingFileRenameOperations = $true;
      $sample1 = ($pendingRename1 | Select-Object -First 6) -join " | ";
      if ($pendingRename1.Count -gt 6) { $sample1 = "$sample1 | ..."; }
      $rebootReasons += [pscustomobject]@{
        Id = "session_manager_pending_file_rename";
        Title = "Session Manager: PendingFileRenameOperations";
        Details = "Найдено записей: $($pendingRename1.Count). Примеры: $sample1";
      };
    }
    $pendingRename2Raw = @($sessionManager.PendingFileRenameOperations2);
    $pendingRename2 = @();
    foreach ($entry in $pendingRename2Raw) {
      if ($null -eq $entry) { continue }
      $text = [string]$entry;
      if (-not $text) { continue }
      $text = $text.Trim();
      if (-not $text) { continue }
      $pendingRename2 += $text;
    }
    if ($pendingRename2.Count -gt 0) {
      $rebootChecks.SessionManagerPendingFileRenameOperations2 = $true;
      $sample2 = ($pendingRename2 | Select-Object -First 6) -join " | ";
      if ($pendingRename2.Count -gt 6) { $sample2 = "$sample2 | ..."; }
      $rebootReasons += [pscustomobject]@{
        Id = "session_manager_pending_file_rename_2";
        Title = "Session Manager: PendingFileRenameOperations2";
        Details = "Найдено записей: $($pendingRename2.Count). Примеры: $sample2";
      };
    }
  }
  $updatesReg = Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Updates' -Name UpdateExeVolatile -ErrorAction SilentlyContinue;
  if ($updatesReg -and $null -ne $updatesReg.UpdateExeVolatile) {
    $volatileValue = [int]$updatesReg.UpdateExeVolatile;
    if ($volatileValue -ne 0) {
      $rebootChecks.UpdateExeVolatile = $true;
      $rebootReasons += [pscustomobject]@{
        Id = "updates_updateexevolatile";
        Title = "Updates: UpdateExeVolatile";
        Details = "HKLM\\SOFTWARE\\Microsoft\\Updates\\UpdateExeVolatile=$volatileValue";
      };
    }
  }
  $serverManagerReg = Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\ServerManager' -Name CurrentRebootAttempts -ErrorAction SilentlyContinue;
  if ($serverManagerReg -and $null -ne $serverManagerReg.CurrentRebootAttempts) {
    $attempts = [int]$serverManagerReg.CurrentRebootAttempts;
    if ($attempts -gt 0) {
      $rebootChecks.ServerManagerCurrentRebootAttempts = $true;
      $rebootReasons += [pscustomobject]@{
        Id = "server_manager_current_reboot_attempts";
        Title = "ServerManager: CurrentRebootAttempts";
        Details = "HKLM\\SOFTWARE\\Microsoft\\ServerManager\\CurrentRebootAttempts=$attempts";
      };
    }
  }
  if (Test-Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Netlogon\\JoinDomain') {
    $rebootChecks.NetlogonJoinDomain = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "netlogon_join_domain";
      Title = "Netlogon: JoinDomain";
      Details = "Есть отложенное применение присоединения к домену.";
    };
  }
  if (Test-Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Netlogon\\AvoidSpnSet') {
    $rebootChecks.NetlogonAvoidSpnSet = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "netlogon_avoid_spn_set";
      Title = "Netlogon: AvoidSpnSet";
      Details = "Есть отложенное обновление SPN/Netlogon.";
    };
  }
  $activeName = (Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\ComputerName\\ActiveComputerName' -Name ComputerName -ErrorAction SilentlyContinue).ComputerName;
  $pendingName = (Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\ComputerName\\ComputerName' -Name ComputerName -ErrorAction SilentlyContinue).ComputerName;
  if ($activeName -and $pendingName -and ($activeName -ne $pendingName)) {
    $rebootChecks.ComputerNameChangePending = $true;
    $rebootReasons += [pscustomobject]@{
      Id = "computer_name_change_pending";
      Title = "Рзменение имени компьютера";
      Details = "ActiveComputerName=$activeName, PendingComputerName=$pendingName";
    };
  }
} catch {
  $msg = [string]$_.Exception.Message;
  if ($msg) { $rebootDiagnostics += $msg; }
}
$rebootActiveChecks = @();
foreach ($kv in $rebootChecks.GetEnumerator()) {
  if ($kv.Value) { $rebootActiveChecks += [string]$kv.Key; }
}
if ($rebootActiveChecks.Count -gt 0) {
  $rebootDiagnostics += "СработалРё проверки: $($rebootActiveChecks -join ', ')";
} else {
  $rebootDiagnostics += "Признаки pending reboot не обнаружены.";
}
$rebootPending = [bool]($rebootReasons.Count -gt 0);
$rebootPendingInfo = [pscustomobject]@{
  Pending = $rebootPending;
  Reasons = $rebootReasons;
  Checks = [pscustomobject]$rebootChecks;
  Diagnostics = $rebootDiagnostics;
};

[pscustomobject]@{
  OS=$os;Computer=$cs;BIOS=$bios;CPU=$cpu;Disks=$disks;NICs=$nics;
  PhysicalDisks=$phys;Pagefiles=$pagefiles;Board=$board;SystemProduct=$csprod;
  Enclosure=$enclosure;Timezone=$tz;WinVersion=$winver;OSNameResolved=$osNameResolved;SecureBoot=$secureBoot;TPM=$tpm;Defender=$defender;
  Firewall=$firewall;BitLocker=$bitlocker;BitLockerMeta=$bitlockerMeta;RebootPending=$rebootPendingInfo;Hotfix=$hotfix;Services=$services;
  Batteries=$batteries;PortableBatteries=$portableBatteries;UPS=$ups;PnpBattery=$pnpBattery;PnpPowerDevices=$pnpPowerDevices;
  SerialPorts=$serialPorts;PowerStatus=$powerStatus;UpsTools=$upsTools
} | ConvertTo-Json -Depth 6 -Compress
"""
    script = script.replace("__FAST_MODE__", "$true" if fast else "$false")
    data, err = _run_powershell_json(script)
    if err:
        return {}, err
    if isinstance(data, dict):
        return data, None
    return {}, "PowerShell вернул неожиданный формат."


def _wait_for_snapshot_cache(timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while time.monotonic() < deadline:
        cached = _CACHE.get("data")
        if isinstance(cached, dict) and cached:
            return cached
        if not _SNAPSHOT_LOCK.locked():
            break
        time.sleep(_SNAPSHOT_WAIT_POLL_SECONDS)
    cached = _CACHE.get("data")
    if isinstance(cached, dict):
        return cached
    return {}


def get_system_snapshot(force: bool = False) -> Dict[str, Any]:
    now = time.monotonic()
    cached = _CACHE.get("data")
    if not force and cached and (now - _CACHE.get("ts", 0.0) < _CACHE_TTL_SECONDS):
        return cached
    lock_acquired = _SNAPSHOT_LOCK.acquire(blocking=False)
    if not lock_acquired:
        if not force:
            waited = _wait_for_snapshot_cache(_SNAPSHOT_WAIT_FOR_CACHE_SECONDS)
            if waited:
                return waited
        _SNAPSHOT_LOCK.acquire()
        lock_acquired = True
    try:
        now = time.monotonic()
        cached = _CACHE.get("data")
        if not force and cached and (now - _CACHE.get("ts", 0.0) < _CACHE_TTL_SECONDS):
            return cached

        cpu_percent = psutil.cpu_percent(interval=0.1)
        net = psutil.net_io_counters()

        ps_data, ps_error = _collect_ps_snapshot(fast=not force)
        os_info = _ensure_dict(ps_data.get("OS"))
        cs_info = _ensure_dict(ps_data.get("Computer"))
        bios_info = _ensure_dict(ps_data.get("BIOS"))
        board_info = _ensure_dict(ps_data.get("Board"))
        product_info = _ensure_dict(ps_data.get("SystemProduct"))
        timezone_info = _ensure_dict(ps_data.get("Timezone"))
        winver_info = _ensure_dict(ps_data.get("WinVersion"))

        cpu_list = _ensure_list(ps_data.get("CPU"))
        disks_raw = _filter_dict_list(ps_data.get("Disks"))
        nics_raw = _filter_dict_list(ps_data.get("NICs"))
        pagefiles_raw = _filter_dict_list(ps_data.get("Pagefiles"))
        phys_raw = _filter_dict_list(ps_data.get("PhysicalDisks"))
        batteries_raw = _filter_dict_list(ps_data.get("Batteries"))
        portable_batteries_raw = _filter_dict_list(ps_data.get("PortableBatteries"))
        ups_raw = _filter_dict_list(ps_data.get("UPS"))
        pnp_battery_raw = _filter_dict_list(ps_data.get("PnpBattery"))
        pnp_power_raw = _filter_dict_list(ps_data.get("PnpPowerDevices"))
        serial_ports_raw = _filter_dict_list(ps_data.get("SerialPorts"))
        power_status_raw = _ensure_dict(ps_data.get("PowerStatus"))
        ups_tools_raw = _ensure_dict(ps_data.get("UpsTools"))
        tpm_raw = _ensure_dict(ps_data.get("TPM"))
        secure_boot_raw = ps_data.get("SecureBoot")
        defender_info = _ensure_dict(ps_data.get("Defender"))
        firewall_raw = _filter_dict_list(ps_data.get("Firewall"))
        bitlocker_raw = _filter_dict_list(ps_data.get("BitLocker"))
        bitlocker_meta_raw = _ensure_dict(ps_data.get("BitLockerMeta"))
        hotfix_info = _ensure_dict(ps_data.get("Hotfix"))
        services_raw = _filter_dict_list(ps_data.get("Services"))
        reboot_pending_raw = ps_data.get("RebootPending")

        enclosure_raw = ps_data.get("Enclosure")
        enclosure_list = _filter_dict_list(enclosure_raw)
        enclosure_info = enclosure_list[0] if enclosure_list else _ensure_dict(enclosure_raw)

        total_kb = _to_int(os_info.get("TotalVisibleMemorySize"))
        free_kb = _to_int(os_info.get("FreePhysicalMemory"))
        mem_total = total_kb * 1024 if total_kb else None
        mem_free = free_kb * 1024 if free_kb else None
        mem_used = None
        mem_percent = None
        vm = psutil.virtual_memory()
        mem_available = getattr(vm, "available", None)
        mem_cached = getattr(vm, "cached", None)
        mem_buffers = getattr(vm, "buffers", None)
        if mem_total is not None and mem_free is not None:
            mem_used = max(mem_total - mem_free, 0)
            mem_percent = round((mem_used / mem_total) * 100, 1) if mem_total else 0.0
        else:
            mem_total = vm.total
            mem_free = vm.available
            mem_used = vm.used
            mem_percent = round(vm.percent, 1)
            mem_available = mem_free

        boot_time = _parse_ps_datetime(os_info.get("LastBootUpTime"))
        install_time = _parse_ps_datetime(os_info.get("InstallDate"))
        bios_release = _parse_ps_datetime(bios_info.get("ReleaseDate"))
        uptime_seconds = None
        if boot_time:
            now_dt = dt.datetime.now(boot_time.tzinfo) if boot_time.tzinfo else dt.datetime.now()
            uptime_seconds = int((now_dt - boot_time).total_seconds())
        else:
            try:
                uptime_seconds = int(time.time() - psutil.boot_time())
            except Exception:
                uptime_seconds = None

        disks: list[Dict[str, Any]] = []
        total_disk_bytes = 0
        used_disk_bytes = 0
        for disk in disks_raw:
            size = _to_int(disk.get("Size"))
            free = _to_int(disk.get("FreeSpace"))
            used = None
            percent = None
            if size is not None and free is not None:
                used = max(size - free, 0)
                percent = round((used / size) * 100, 1) if size else 0.0
            if size:
                total_disk_bytes += size
            if used:
                used_disk_bytes += used
            disks.append(
                {
                    "device": disk.get("DeviceID") or "",
                    "label": disk.get("VolumeName") or "",
                    "fs": disk.get("FileSystem") or "",
                    "total_bytes": size,
                    "used_bytes": used,
                    "free_bytes": free,
                    "percent": percent,
                    "total_gb": _to_gb(size),
                    "used_gb": _to_gb(used),
                    "free_gb": _to_gb(free),
                }
            )
    
        disk_percent = None
        if total_disk_bytes:
            disk_percent = round((used_disk_bytes / total_disk_bytes) * 100, 1)
    
        cpu_name = ""
        cpu_cores = 0
        cpu_logical = 0
        cpu_max = None
        cpu_current = None
        cpu_vendor = ""
        for idx, cpu in enumerate(cpu_list):
            if idx == 0:
                cpu_name = cpu.get("Name") or ""
                cpu_max = _to_int(cpu.get("MaxClockSpeed"))
                cpu_current = _to_int(cpu.get("CurrentClockSpeed"))
                cpu_vendor = cpu.get("Manufacturer") or ""
            cpu_cores += _to_int(cpu.get("NumberOfCores")) or 0
            cpu_logical += _to_int(cpu.get("NumberOfLogicalProcessors")) or 0
    
        interfaces: list[Dict[str, Any]] = []
        for nic in nics_raw:
            ip = _ensure_list(nic.get("IPAddress"))
            subnet = _ensure_list(nic.get("IPSubnet"))
            gw = _ensure_list(nic.get("DefaultIPGateway"))
            dns = _ensure_list(nic.get("DNSServerSearchOrder"))
            interfaces.append(
                {
                    "name": nic.get("Description") or "",
                    "mac": nic.get("MACAddress") or "",
                    "ip": [addr for addr in ip if addr],
                    "subnet": [addr for addr in subnet if addr],
                    "gateway": [addr for addr in gw if addr],
                    "dns": [addr for addr in dns if addr],
                }
            )
    
        pagefiles: list[Dict[str, Any]] = []
        for pf in pagefiles_raw:
            allocated_mb = _to_int(pf.get("AllocatedBaseSize"))
            current_mb = _to_int(pf.get("CurrentUsage"))
            peak_mb = _to_int(pf.get("PeakUsage"))
            pagefiles.append(
                {
                    "name": pf.get("Name") or "",
                    "allocated_mb": allocated_mb,
                    "current_mb": current_mb,
                    "peak_mb": peak_mb,
                    "allocated_gb": _mb_to_gb(allocated_mb),
                    "current_gb": _mb_to_gb(current_mb),
                    "peak_gb": _mb_to_gb(peak_mb),
                }
            )
    
        physical_disks: list[Dict[str, Any]] = []
        for disk in phys_raw:
            size = _to_int(disk.get("Size"))
            physical_disks.append(
                {
                    "model": disk.get("Model") or "",
                    "serial": _clean_serial(disk.get("SerialNumber")),
                    "interface": disk.get("InterfaceType") or "",
                    "media_type": disk.get("MediaType") or "",
                    "device": disk.get("DeviceID") or "",
                    "firmware": disk.get("FirmwareRevision") or "",
                    "size_bytes": size,
                    "size_gb": _to_gb(size),
                }
            )
    
        firewall_profiles: list[Dict[str, Any]] = []
        for profile in firewall_raw:
            firewall_profiles.append(
                {
                    "name": profile.get("Name") or "",
                    "enabled": profile.get("Enabled"),
                    "inbound": profile.get("DefaultInboundAction") or "",
                    "outbound": profile.get("DefaultOutboundAction") or "",
                }
            )
    
        bitlocker_volumes: list[Dict[str, Any]] = []
        for volume in bitlocker_raw:
            bitlocker_volumes.append(
                {
                    "mount": volume.get("MountPoint") or "",
                    "type": volume.get("VolumeType") or "",
                    "protection": _map_bitlocker_protection(volume.get("ProtectionStatus")),
                    "lock": _map_bitlocker_lock(volume.get("LockStatus")),
                    "method": volume.get("EncryptionMethod") or "",
                    "percent": volume.get("EncryptionPercentage"),
                }
            )
        bitlocker_sources = [
            str(item).strip()
            for item in _ensure_list(bitlocker_meta_raw.get("Sources"))
            if str(item or "").strip()
        ]
        bitlocker_errors = [
            str(item).strip()
            for item in _ensure_list(bitlocker_meta_raw.get("Errors"))
            if str(item or "").strip()
        ]
        bitlocker_diagnostics = [
            str(item).strip()
            for item in _ensure_list(bitlocker_meta_raw.get("Diagnostics"))
            if str(item or "").strip()
        ]
        bitlocker_access_denied = _to_bool(bitlocker_meta_raw.get("AccessDenied"))
        bitlocker_available = _to_bool(bitlocker_meta_raw.get("Available"))
        bitlocker_manage_bde_attempted = _to_bool(bitlocker_meta_raw.get("ManageBdeAttempted"))
        bitlocker_manage_bde_exit_code = _to_int(bitlocker_meta_raw.get("ManageBdeExitCode"))
        bitlocker_manage_bde_snippet = str(bitlocker_meta_raw.get("ManageBdeRawSnippet") or "").strip()
        bitlocker_error = bitlocker_errors[0] if bitlocker_errors else ""
        if bitlocker_volumes:
            bitlocker_summary = f"Обнаружено томов: {len(bitlocker_volumes)}"
        elif bitlocker_access_denied:
            bitlocker_summary = "Доступ ограничен: нужны права администратора."
        elif bitlocker_available is False:
            bitlocker_summary = "Рнструменты BitLocker недоступны в системе."
        elif bitlocker_error:
            bitlocker_summary = bitlocker_error
        else:
            bitlocker_summary = "Нет данных"
        bitlocker_readability_reasons: list[Dict[str, str]] = []
        if bitlocker_available is False:
            bitlocker_readability_reasons.append(
                {
                    "id": "module_unavailable",
                    "title": "Модуль BitLocker недоступен",
                    "details": "Команда Get-BitLockerVolume не найдена в системе.",
                }
            )
        if bitlocker_access_denied:
            bitlocker_readability_reasons.append(
                {
                    "id": "access_denied",
                    "title": "Недостаточно прав",
                    "details": "Для чтения статуса BitLocker нужны права администратора.",
                }
            )
        if (
            bitlocker_available is not False
            and not bitlocker_access_denied
            and not bitlocker_volumes
        ):
            bitlocker_readability_reasons.append(
                {
                    "id": "empty_result",
                    "title": "Нет доступных томов",
                    "details": "Get-BitLockerVolume вернул пустой список томов.",
                }
            )
        if bitlocker_manage_bde_attempted:
            if bitlocker_manage_bde_exit_code == 0:
                bitlocker_readability_reasons.append(
                    {
                        "id": "fallback_manage_bde_ok",
                        "title": "Рспользован fallback manage-bde",
                        "details": "Команда manage-bde -status выполнена успешно.",
                    }
                )
            elif bitlocker_manage_bde_exit_code is not None:
                bitlocker_readability_reasons.append(
                    {
                        "id": "fallback_manage_bde_failed",
                        "title": "Fallback manage-bde завершился с ошибкой",
                        "details": f"Код завершения: {bitlocker_manage_bde_exit_code}.",
                    }
                )
            else:
                bitlocker_readability_reasons.append(
                    {
                        "id": "fallback_manage_bde_unknown",
                        "title": "Fallback manage-bde не дал кода завершения",
                        "details": "Нужно проверить выполнение команды вручную.",
                    }
                )
        elif not bitlocker_volumes and bitlocker_access_denied:
            bitlocker_readability_reasons.append(
                {
                    "id": "fallback_skipped_access_denied",
                    "title": "Fallback не запускался",
                    "details": "Пропущен из-за ранее обнаруженного отказа в доступе.",
                }
            )
        if bitlocker_error:
            bitlocker_readability_reasons.append(
                {
                    "id": "source_error",
                    "title": "Ошибка источника BitLocker",
                    "details": bitlocker_error,
                }
            )

        services: list[Dict[str, Any]] = []
        for svc in services_raw:
            services.append(
                {
                    "name": svc.get("Name") or "",
                    "display_name": svc.get("DisplayName") or "",
                    "state": svc.get("State") or "",
                    "start_mode": svc.get("StartMode") or "",
                    "start_name": svc.get("StartName") or "",
                }
            )
        services.sort(key=lambda item: (item.get("display_name") or item.get("name") or "").lower())

        hotfix_installed = _parse_ps_datetime(hotfix_info.get("InstalledOn"))
        updates = {
            "hotfix_id": hotfix_info.get("HotFixID") or "",
            "hotfix_installed": hotfix_installed.isoformat() if hotfix_installed else "",
            "hotfix_installed_human": _format_datetime(hotfix_installed),
            "hotfix_description": hotfix_info.get("Description") or "",
            "hotfix_installed_by": hotfix_info.get("InstalledBy") or "",
        }

        internal_batteries: list[Dict[str, Any]] = []
        for battery in batteries_raw:
            charge_percent = _normalize_percent(battery.get("EstimatedChargeRemaining"))
            runtime_min = _to_int(battery.get("EstimatedRunTime"))
            design_capacity = _to_int(battery.get("DesignCapacity"))
            full_charge_capacity = _to_int(battery.get("FullChargeCapacity"))
            status_code = _to_int(battery.get("BatteryStatus"))
            internal_batteries.append(
                {
                    "name": battery.get("Name") or battery.get("Description") or battery.get("DeviceID") or "",
                    "device_id": battery.get("DeviceID") or "",
                    "manufacturer": _clean_name(battery.get("Manufacturer")),
                    "status": battery.get("Status") or "",
                    "status_code": status_code,
                    "status_text": _map_battery_status(status_code),
                    "availability": battery.get("Availability"),
                    "charge_percent": charge_percent,
                    "runtime_min": runtime_min,
                    "runtime_human": _runtime_minutes_to_human(runtime_min),
                    "time_on_battery_min": _to_int(battery.get("TimeOnBattery")),
                    "time_to_full_charge_min": _to_int(battery.get("TimeToFullCharge")),
                    "chemistry": _map_battery_chemistry(battery.get("Chemistry")),
                    "design_capacity_mwh": design_capacity,
                    "full_charge_capacity_mwh": full_charge_capacity,
                    "smart_battery_version": battery.get("SmartBatteryVersion") or "",
                    "pnp_device_id": battery.get("PNPDeviceID") or "",
                    "source": "Win32_Battery",
                }
            )

        portable_batteries: list[Dict[str, Any]] = []
        portable_battery_candidates: list[Dict[str, Any]] = []
        portable_ups_from_portable: list[Dict[str, Any]] = []
        portable_ac_adapters: list[Dict[str, Any]] = []
        portable_unknown_devices: list[Dict[str, Any]] = []
        for battery in portable_batteries_raw:
            charge_percent = _normalize_percent(battery.get("EstimatedChargeRemaining"))
            runtime_min = _to_int(battery.get("EstimatedRunTime"))
            status_code = _to_int(battery.get("BatteryStatus"))
            payload = {
                "name": battery.get("Name") or battery.get("Description") or battery.get("DeviceID") or "",
                "description": battery.get("Description") or "",
                "device_id": battery.get("DeviceID") or "",
                "manufacturer": _clean_name(battery.get("Manufacturer")),
                "status": battery.get("Status") or "",
                "status_code": status_code,
                "status_text": _map_battery_status(status_code),
                "availability": battery.get("Availability"),
                "charge_percent": charge_percent,
                "runtime_min": runtime_min,
                "runtime_human": _runtime_minutes_to_human(runtime_min),
                "time_on_battery_min": _to_int(battery.get("TimeOnBattery")),
                "time_to_full_charge_min": _to_int(battery.get("TimeToFullCharge")),
                "chemistry": _map_battery_chemistry(battery.get("Chemistry")),
                "design_capacity": _to_int(battery.get("DesignCapacity")),
                "design_voltage": _to_int(battery.get("DesignVoltage")),
                "capacity_multiplier": _to_int(battery.get("CapacityMultiplier")),
                "pnp_device_id": battery.get("PNPDeviceID") or "",
                "connection": _power_connection_type(battery.get("DeviceID"), battery.get("Name")),
                "source": "Win32_PortableBattery",
                "detected_type": "unknown",
                "detected_label": "",
                "detection_reason": "",
            }
            signature = (
                payload.get("name"),
                payload.get("description"),
                payload.get("device_id"),
                payload.get("manufacturer"),
                payload.get("pnp_device_id"),
            )
            detected_type, detection_reason = _classify_portable_battery(*signature)
            payload["detected_type"] = detected_type
            payload["detected_label"] = _portable_detected_label(detected_type)
            payload["detection_reason"] = detection_reason
            if detected_type == "ac_adapter":
                portable_ac_adapters.append(payload)
            elif detected_type == "ups":
                portable_ups_from_portable.append(payload)
            elif detected_type == "internal_battery":
                portable_batteries.append(payload)
            else:
                portable_unknown_devices.append(payload)
            portable_battery_candidates.append(payload)

        ups_devices: list[Dict[str, Any]] = []
        for ups in ups_raw:
            charge_percent = _normalize_percent(ups.get("EstimatedChargeRemaining"))
            runtime_min = _to_int(ups.get("EstimatedRunTime"))
            ups_devices.append(
                {
                    "name": ups.get("Name") or ups.get("DeviceID") or "",
                    "device_id": ups.get("DeviceID") or "",
                    "manufacturer": _clean_name(ups.get("Manufacturer")),
                    "status": ups.get("Status") or "",
                    "availability": ups.get("Availability"),
                    "charge_percent": charge_percent,
                    "runtime_min": runtime_min,
                    "runtime_human": _runtime_minutes_to_human(runtime_min),
                    "time_on_backup_min": _to_int(ups.get("TimeOnBackup")),
                    "battery_installed": ups.get("BatteryInstalled"),
                    "switching_supply": ups.get("IsSwitchingSupply"),
                    "remote_off_supported": ups.get("CanTurnOffRemotely"),
                    "output_power_watts": _to_int(ups.get("TotalOutputPower")),
                    "serial": _clean_serial(ups.get("SerialNumber")),
                    "source": "Win32_UninterruptiblePowerSupply",
                }
            )

        for portable_ups in portable_ups_from_portable:
            ups_devices.append(
                {
                    "name": portable_ups.get("name") or "",
                    "device_id": portable_ups.get("device_id") or "",
                    "manufacturer": portable_ups.get("manufacturer") or "",
                    "status": portable_ups.get("status") or "",
                    "availability": portable_ups.get("availability"),
                    "charge_percent": portable_ups.get("charge_percent"),
                    "runtime_min": portable_ups.get("runtime_min"),
                    "runtime_human": portable_ups.get("runtime_human"),
                    "time_on_backup_min": portable_ups.get("time_on_battery_min"),
                    "battery_installed": True,
                    "switching_supply": None,
                    "remote_off_supported": None,
                    "output_power_watts": None,
                    "serial": "",
                    "connection": portable_ups.get("connection") or "",
                    "source": "Win32_PortableBattery (UPS hint)",
                }
            )

        pnp_devices: list[Dict[str, Any]] = []
        pnp_seen: set[str] = set()
        for dev in pnp_battery_raw:
            instance_id = str(dev.get("InstanceId") or "").strip()
            if not instance_id:
                continue
            if instance_id in pnp_seen:
                continue
            pnp_seen.add(instance_id)
            friendly_name = dev.get("FriendlyName") or instance_id
            pnp_devices.append(
                {
                    "name": friendly_name,
                    "instance_id": instance_id,
                    "manufacturer": _clean_name(dev.get("Manufacturer")),
                    "status": dev.get("Status") or "",
                    "service": dev.get("Service") or "",
                    "connection": _power_connection_type(instance_id, friendly_name),
                    "source": "Get-PnpDevice",
                }
            )

        for dev in pnp_power_raw:
            instance_id = str(dev.get("DeviceID") or "").strip()
            if not instance_id:
                continue
            if instance_id in pnp_seen:
                continue
            pnp_seen.add(instance_id)
            name = dev.get("Name") or instance_id
            pnp_devices.append(
                {
                    "name": name,
                    "instance_id": instance_id,
                    "manufacturer": _clean_name(dev.get("Manufacturer")),
                    "status": dev.get("Status") or "",
                    "service": dev.get("Service") or "",
                    "connection": _power_connection_type(instance_id, name),
                    "source": "Win32_PnPEntity",
                }
            )

        pnp_ups_hints = [
            dev
            for dev in pnp_devices
            if _looks_like_ups(dev.get("name"), dev.get("instance_id"), dev.get("manufacturer"))
        ]
        pnp_internal_hints = [
            dev
            for dev in pnp_devices
            if _looks_like_internal_battery(
                dev.get("name"),
                dev.get("instance_id"),
                dev.get("manufacturer"),
                dev.get("service"),
            )
        ]
        pnp_ac_adapter_hints = [
            dev
            for dev in pnp_devices
            if _looks_like_ac_adapter(
                dev.get("name"),
                dev.get("instance_id"),
                dev.get("manufacturer"),
                dev.get("service"),
            )
        ]

        serial_ups_candidates: list[Dict[str, Any]] = []
        for port in serial_ports_raw:
            name = port.get("Name") or ""
            description = port.get("Description") or ""
            device_id = port.get("DeviceID") or ""
            pnp_id = port.get("PNPDeviceID") or ""
            if not _looks_like_ups(name, description, pnp_id):
                continue
            serial_ups_candidates.append(
                {
                    "port": device_id,
                    "name": name,
                    "description": description,
                    "pnp_device_id": pnp_id,
                    "status": port.get("Status") or "",
                    "connection": "COM/Serial",
                    "source": "Win32_SerialPort",
                }
            )

        detected_vendors: set[str] = set()
        for payload in internal_batteries:
            detected_vendors.update(
                _detect_ups_vendors(payload.get("name"), payload.get("manufacturer"), payload.get("device_id"))
            )
        for payload in portable_battery_candidates:
            detected_vendors.update(
                _detect_ups_vendors(payload.get("name"), payload.get("manufacturer"), payload.get("device_id"))
            )
        for payload in ups_devices:
            detected_vendors.update(
                _detect_ups_vendors(payload.get("name"), payload.get("manufacturer"), payload.get("device_id"))
            )
        for payload in pnp_devices:
            detected_vendors.update(
                _detect_ups_vendors(payload.get("name"), payload.get("manufacturer"), payload.get("instance_id"))
            )
        for payload in serial_ups_candidates:
            detected_vendors.update(
                _detect_ups_vendors(payload.get("name"), payload.get("description"), payload.get("pnp_device_id"))
            )

        overview_power = _collect_overview_power()
        power_line_raw = str(power_status_raw.get("PowerLineStatus") or "").strip().lower()
        on_ac = None
        if power_line_raw in {"online", "1", "true"}:
            on_ac = True
        elif power_line_raw in {"offline", "0", "false"}:
            on_ac = False
        elif overview_power.get("on_ac") is not None:
            on_ac = bool(overview_power.get("on_ac"))

        charge_status_raw = str(power_status_raw.get("BatteryChargeStatus") or "").strip()
        charge_status_compact = re.sub(r"\s+", "", charge_status_raw).lower()
        no_system_battery = bool(
            "nosystembattery" in charge_status_compact
            or overview_power.get("no_system_battery") is True
        )

        charging = None
        if charge_status_raw:
            lowered = charge_status_raw.lower()
            if "nosystembattery" in charge_status_compact:
                charging = False
            elif "charging" in lowered:
                charging = True
        if charging is None and on_ac is not None and not no_system_battery:
            charging = bool(on_ac)

        power_percent = None
        for candidate in (
            power_status_raw.get("BatteryLifePercent"),
            overview_power.get("percent"),
            *(item.get("charge_percent") for item in internal_batteries),
            *(item.get("charge_percent") for item in portable_batteries),
            *(item.get("charge_percent") for item in ups_devices),
        ):
            normalized = _normalize_percent(candidate)
            if normalized is not None:
                power_percent = normalized
                break

        runtime_seconds = _to_int(power_status_raw.get("BatteryLifeRemaining"))
        if runtime_seconds is None or runtime_seconds < 0:
            runtime_seconds = overview_power.get("runtime_seconds")
        runtime_human = _format_uptime(runtime_seconds) if runtime_seconds else ""
        if runtime_human == "-":
            runtime_human = ""

        has_ups = bool(ups_devices or serial_ups_candidates or pnp_ups_hints)
        if not has_ups:
            has_ups = any(item.get("detected_type") == "ups" for item in portable_battery_candidates)

        has_internal = bool(internal_batteries or portable_batteries or pnp_internal_hints)
        if no_system_battery and not (internal_batteries or portable_batteries):
            has_internal = False
        elif not has_internal and overview_power.get("present"):
            # Fallback for cases when detailed WMI snapshot is unavailable,
            # but psutil/GetSystemPowerStatus confirms a battery.
            has_internal = True

        if no_system_battery and not has_internal and not has_ups:
            power_percent = None
            runtime_seconds = None
            runtime_human = ""
            charging = False

        power_present = bool(has_internal or has_ups)
        if not power_present and power_percent is not None and not no_system_battery:
            power_present = True
        if not power_present and overview_power.get("present") and not no_system_battery:
            power_present = True
        if (
            not power_present
            and charge_status_raw
            and "unknown" not in charge_status_compact
            and not no_system_battery
        ):
            power_present = True

        summary_parts: list[str] = []
        if power_present and (has_internal or has_ups or not no_system_battery):
            if power_percent is not None:
                summary_parts.append(f"{int(round(power_percent))}%")
            if on_ac is True:
                summary_parts.append("от сети")
            elif on_ac is False:
                summary_parts.append("от батареи")
            if runtime_human and on_ac is False:
                summary_parts.append(f"остаток {runtime_human}")
        if not summary_parts:
            if has_internal and has_ups:
                summary = "Обнаружены аккумулятор Рё РБП"
            elif has_internal:
                summary = "Обнаружен аккумулятор"
            elif has_ups:
                summary = "Обнаружен РБП"
            elif power_present:
                summary = "Питание обнаружено"
            else:
                summary = "Не обнаружено"
        else:
            summary = ", ".join(summary_parts)

        power_diagnostics: list[str] = list(overview_power.get("diagnostics") or [])
        if no_system_battery and not has_internal:
            power_diagnostics.append(
                "PowerStatus сообщает NoSystemBattery: встроенная батарея в системе не обнаружена."
            )
        if portable_ac_adapters:
            power_diagnostics.append(
                f"Win32_PortableBattery: отфильтровано как AC Adapter: {len(portable_ac_adapters)}."
            )
        if portable_ups_from_portable:
            power_diagnostics.append(
                f"Win32_PortableBattery: классифицировано как UPS: {len(portable_ups_from_portable)}."
            )
        if portable_unknown_devices:
            power_diagnostics.append(
                f"Win32_PortableBattery: не удалось однозначно классифицировать: {len(portable_unknown_devices)}."
            )
        if pnp_ac_adapter_hints:
            power_diagnostics.append(
                f"PnP Class Battery содержит AC Adapter-подобные устройства: {len(pnp_ac_adapter_hints)}."
            )
        if pnp_internal_hints and not (internal_batteries or portable_batteries):
            power_diagnostics.append(
                "PnP нашел Battery-устройства, но Win32_Battery не вернул подтвержденные встроенные аккумуляторы."
            )
        if ps_error:
            power_diagnostics.append(f"PowerShell snapshot: {ps_error}")

        power_error = ""
        if ps_error:
            power_error = ps_error
        elif (
            no_system_battery
            and portable_battery_candidates
            and not has_internal
            and not has_ups
        ):
            power_error = (
                "Win32_PortableBattery вернул устройства, но ОС сообщает NoSystemBattery; "
                "записи отфильтрованы как AC Adapter/не-РБП."
            )
        elif overview_power.get("battery_flag") == 255 and not has_internal and not has_ups:
            power_error = (
                "Статус батареи неизвестен (BatteryFlag=255). Проверьте драйверы ACPI/Battery Рё WMI."
            )

        power_paths = [
            {
                "channel": "ACPI/WMI",
                "target": "Ноутбук/встроенный аккумулятор",
                "status": "доступно" if has_internal else "нет данных",
                "details": "Win32_Battery + фильтрация Win32_PortableBattery",
            },
            {
                "channel": "UPS WMI",
                "target": "РБП через системные классы",
                "status": "доступно" if ups_raw else ("обнаружено" if portable_ups_from_portable else "нет данных"),
                "details": "Win32_UninterruptiblePowerSupply Рё UPS-подсказки из Win32_PortableBattery",
            },
            {
                "channel": "USB HID/PnP",
                "target": "РБП по USB (в т.ч. APC, CyberPower, DEXP Рё др.)",
                "status": "обнаружено" if pnp_ups_hints else "нет данных",
                "details": "Get-PnpDevice (Class Battery), Win32_PnPEntity",
            },
            {
                "channel": "COM/Serial",
                "target": "РБП через COM/USB-Serial",
                "status": "обнаружено" if serial_ups_candidates else "нет данных",
                "details": "Win32_SerialPort (по сигнатурам устройства)",
            },
            {
                "channel": "SNMP / Network UPS",
                "target": "Сетевой РБП",
                "status": "требуется настройка",
                "details": "автоопрос невозможен без IP/учетных данных; используйте NUT/вендор-агент",
            },
        ]

        power = {
            "present": power_present,
            "summary": summary,
            "percent": power_percent,
            "on_ac": on_ac,
            "charging": charging,
            "runtime_seconds": runtime_seconds,
            "runtime_human": runtime_human,
            "line_status": power_status_raw.get("PowerLineStatus") or "",
            "charge_status": charge_status_raw,
            "no_system_battery": no_system_battery,
            "battery_flag": overview_power.get("battery_flag"),
            "has_internal_battery": has_internal,
            "has_ups": has_ups,
            "internal_batteries": internal_batteries,
            "portable_batteries": portable_batteries,
            "portable_battery_candidates": portable_battery_candidates,
            "portable_ac_adapters": portable_ac_adapters,
            "portable_unknown_devices": portable_unknown_devices,
            "ups_devices": ups_devices,
            "pnp_devices": pnp_devices,
            "pnp_ups_hints": pnp_ups_hints,
            "pnp_internal_hints": pnp_internal_hints,
            "serial_ups_candidates": serial_ups_candidates,
            "detected_vendors": sorted(detected_vendors),
            "diagnostics": power_diagnostics,
            "error": power_error,
            "paths": power_paths,
            "tools": {
                "apcaccess": bool(ups_tools_raw.get("ApcAccess")),
                "upsc": bool(ups_tools_raw.get("Upsc")),
                "snmp_service": bool(ups_tools_raw.get("SnmpService")),
            },
            "notes": [
                "Статус зависит от драйверов устройства Рё вендорского ПО.",
                "Для SNMP-РБП нужен адрес устройства Рё параметры доступа.",
                "Для некоторых РБП расширенная телеметрия доступна только через утилиты производителя.",
            ],
            "source": "WMI/PnP + PowerStatus",
        }
     
        tpm_get = _ensure_dict(tpm_raw.get("GetTpm"))
        if not tpm_get:
            tpm_get_list = _filter_dict_list(tpm_raw.get("GetTpm"))
            tpm_get = tpm_get_list[0] if tpm_get_list else {}

        tpm_wmi = _ensure_dict(tpm_raw.get("Wmi"))
        if not tpm_wmi:
            tpm_wmi_list = _filter_dict_list(tpm_raw.get("Wmi"))
            tpm_wmi = tpm_wmi_list[0] if tpm_wmi_list else {}

        tpm_wmi_state = _ensure_dict(tpm_raw.get("WmiState"))
        if not tpm_wmi_state:
            tpm_wmi_state_list = _filter_dict_list(tpm_raw.get("WmiState"))
            tpm_wmi_state = tpm_wmi_state_list[0] if tpm_wmi_state_list else {}

        if not _dict_has_meaningful_value(tpm_get):
            tpm_get = {}
        if not _dict_has_meaningful_value(tpm_wmi):
            tpm_wmi = {}
        if not _dict_has_meaningful_value(tpm_wmi_state):
            tpm_wmi_state = {}

        tpm_pnp_security_raw = _filter_dict_list(tpm_raw.get("PnpSecurity"))
        tpm_wmi_security_raw = _filter_dict_list(tpm_raw.get("WmiSecurity"))
        tpm_tool_raw = _ensure_dict(tpm_raw.get("Tool"))
        tpm_signals = _ensure_dict(tpm_raw.get("Signals"))
        tpm_warnings = [
            str(item).strip()
            for item in _ensure_list(tpm_raw.get("Warnings"))
            if str(item or "").strip()
        ]

        tpm_devices: list[Dict[str, Any]] = []
        tpm_seen: set[str] = set()
        for source_name, entries, name_key, id_key in (
            ("Get-PnpDevice SecurityDevices", tpm_pnp_security_raw, "FriendlyName", "InstanceId"),
            ("Win32_PnPEntity SecurityDevices", tpm_wmi_security_raw, "Name", "DeviceID"),
        ):
            for entry in entries:
                instance_id = str(entry.get(id_key) or "").strip()
                name = str(entry.get(name_key) or "").strip()
                manufacturer = _clean_name(entry.get("Manufacturer"))
                service = str(entry.get("Service") or "").strip()
                status = str(entry.get("Status") or "").strip()
                signature = f"{instance_id}|{name}|{manufacturer}|{service}"
                if signature in tpm_seen:
                    continue
                tpm_seen.add(signature)
                if not _looks_like_tpm_device(name, instance_id, manufacturer, service):
                    if service.lower() != "tpm":
                        continue
                tpm_devices.append(
                    {
                        "name": name or instance_id,
                        "instance_id": instance_id,
                        "manufacturer": manufacturer,
                        "service": service,
                        "status": status,
                        "source": source_name,
                    }
                )

        tpm_tool_values = [
            str(item).strip()
            for item in _ensure_list(tpm_tool_raw.get("Values"))
            if str(item or "").strip()
        ]
        tpm_tool_raw_text = str(tpm_tool_raw.get("Raw") or "")
        tpm_tool_spec = _extract_tpm_spec(*tpm_tool_values, tpm_tool_raw_text)
        tpm_tool_vendor_id = _extract_tpm_vendor_id(tpm_tool_values)
        tpm_tool_vendor_name = _extract_tpm_vendor_name(tpm_tool_values, tpm_tool_vendor_id)
        tpm_tool_versions = [
            value
            for value in tpm_tool_values
            if re.fullmatch(r"\d+(?:\.\d+){1,5}", value)
        ]
        tpm_tool_fw_version = ""
        for value in tpm_tool_versions:
            if value.count(".") >= 2:
                tpm_tool_fw_version = value
                break
        if not tpm_tool_fw_version:
            for value in tpm_tool_versions:
                if value != tpm_tool_spec:
                    tpm_tool_fw_version = value
                    break
        tpm_tool_version_info = ""
        for value in tpm_tool_versions:
            if value in {tpm_tool_spec, tpm_tool_fw_version}:
                continue
            tpm_tool_version_info = value
            break
        tpm_tool_present = bool(
            tpm_tool_spec
            or _looks_like_tpm_device(tpm_tool_raw_text)
            or any(_looks_like_tpm_device(value) for value in tpm_tool_values)
        )

        tpm_sources: list[str] = []
        if tpm_get:
            tpm_sources.append("Get-Tpm")
        if tpm_wmi:
            tpm_sources.append("Win32_Tpm")
        if tpm_devices:
            tpm_sources.append("SecurityDevices")
        if tpm_tool_present:
            tpm_sources.append("tpmtool")
        tpm_source = ", ".join(dict.fromkeys(tpm_sources))

        manufacturer_id = _first_not_none(
            tpm_get.get("ManufacturerId"),
            tpm_wmi.get("ManufacturerId"),
            tpm_tool_vendor_id or None,
        )
        manufacturer_txt = _first_not_none(
            tpm_get.get("ManufacturerIdTxt"),
            tpm_wmi.get("ManufacturerIdTxt"),
            tpm_tool_vendor_name or None,
        )
        if not manufacturer_txt and tpm_devices:
            manufacturer_txt = tpm_devices[0].get("manufacturer")

        tpm_present = _first_not_none(
            _to_bool(tpm_get.get("TpmPresent")),
            True if tpm_wmi else None,
            True if tpm_devices else None,
            True if tpm_tool_present else None,
        )
        if tpm_present is None and tpm_signals:
            attempted = any(
                bool(_to_bool(tpm_signals.get(key)))
                for key in (
                    "GetTpmAttempted",
                    "Win32TpmAttempted",
                    "PnpSecurityAttempted",
                    "TpmToolAttempted",
                )
            )
            if attempted and not (tpm_get or tpm_wmi or tpm_devices or tpm_tool_present):
                tpm_present = False

        tpm_spec = _normalize_tpm_spec(
            _first_not_none(
                tpm_get.get("SpecVersion"),
                tpm_wmi.get("SpecVersion"),
                _extract_tpm_spec(*(item.get("name") for item in tpm_devices)),
                tpm_tool_spec or None,
            )
        )
        tpm_diagnostics: list[str] = []
        tpm_diagnostics.extend(tpm_warnings)
        if tpm_signals.get("GetTpmElevationRequired"):
            tpm_diagnostics.append(
                "Get-Tpm требует запуск PowerShell от имени администратора."
            )
        if tpm_signals.get("Win32TpmAccessDenied"):
            tpm_diagnostics.append(
                "Доступ к Win32_Tpm ограничен (root\\CIMV2\\Security\\MicrosoftTpm)."
            )
        if tpm_devices:
            tpm_diagnostics.append(
                f"TPM обнаружен через SecurityDevices: {len(tpm_devices)} устройство(а)."
            )
        if tpm_tool_present and not tpm_get and not tpm_wmi:
            tpm_diagnostics.append(
                "Рспользован fallback tpmtool для подтверждения присутствия TPM."
            )

        tpm_error = ""
        if tpm_present is None:
            if tpm_warnings:
                tpm_error = tpm_warnings[0]
            else:
                tpm_error = "Не удалось определить состояние TPM."

        tpm = {}
        if tpm_get or tpm_wmi or tpm_wmi_state or tpm_devices or tpm_tool_present or tpm_warnings or tpm_present is not None:
            tpm = {
                "present": tpm_present,
                "ready": _first_not_none(
                    _to_bool(tpm_get.get("TpmReady")),
                    None if tpm_present is not True else None,
                ),
                "enabled": _first_not_none(
                    _to_bool(tpm_get.get("TpmEnabled")),
                    _to_bool(tpm_wmi_state.get("IsEnabled")),
                    _to_bool(tpm_wmi.get("IsEnabled_InitialValue")),
                ),
                "activated": _first_not_none(
                    _to_bool(tpm_get.get("TpmActivated")),
                    _to_bool(tpm_wmi_state.get("IsActivated")),
                    _to_bool(tpm_wmi.get("IsActivated_InitialValue")),
                ),
                "owned": _first_not_none(
                    _to_bool(tpm_get.get("TpmOwned")),
                    _to_bool(tpm_wmi_state.get("IsOwned")),
                    _to_bool(tpm_wmi.get("IsOwned_InitialValue")),
                ),
                "manufacturer": _decode_tpm_manufacturer(manufacturer_id, manufacturer_txt),
                "manufacturer_id": manufacturer_id,
                "spec": tpm_spec,
                "manufacturer_version": _first_not_none(
                    tpm_get.get("ManufacturerVersionFull20"),
                    tpm_get.get("ManufacturerVersion"),
                    tpm_wmi.get("ManufacturerVersionInfo"),
                    tpm_wmi.get("ManufacturerVersion"),
                    tpm_tool_fw_version or None,
                ),
                "manufacturer_version_info": _first_not_none(
                    tpm_wmi.get("ManufacturerVersionInfo"),
                    tpm_get.get("ManufacturerVersion"),
                    tpm_tool_version_info or None,
                ),
                "physical_presence": tpm_wmi.get("PhysicalPresenceVersionInfo") or "",
                "managed_auth_level": tpm_get.get("ManagedAuthLevel"),
                "auto_provisioning": tpm_get.get("AutoProvisioning"),
                "locked_out": tpm_get.get("LockedOut"),
                "lockout_heal_time": tpm_get.get("LockoutHealTime"),
                "lockout_count": tpm_get.get("LockoutCount"),
                "lockout_max": tpm_get.get("LockoutMax"),
                "self_test": tpm_get.get("SelfTest"),
                "restart_pending": tpm_get.get("RestartPending"),
                "devices": tpm_devices,
                "diagnostics": tpm_diagnostics,
                "error": tpm_error,
                "source": tpm_source,
            }

        secure_boot_info = _ensure_dict(secure_boot_raw)
        secure_boot_enabled = _to_bool(secure_boot_info.get("Enabled"))
        if secure_boot_enabled is None:
            secure_boot_enabled = _to_bool(secure_boot_raw)
        secure_boot_supported = _to_bool(secure_boot_info.get("Supported"))
        secure_boot_registry_value = _to_int(secure_boot_info.get("RegistryValue"))
        secure_boot_firmware_type = _to_int(
            _first_not_none(
                secure_boot_info.get("FirmwareType"),
                winver_info.get("BiosFirmwareType"),
            )
        )
        secure_boot_source = str(secure_boot_info.get("Source") or "").strip()
        secure_boot_error = str(secure_boot_info.get("Error") or "").strip()
        secure_boot_diagnostics = [
            str(item).strip()
            for item in _ensure_list(secure_boot_info.get("Diagnostics"))
            if str(item or "").strip()
        ]

        if secure_boot_supported is None and secure_boot_firmware_type is not None:
            secure_boot_supported = bool(secure_boot_firmware_type == 2)
        if secure_boot_enabled is None and secure_boot_registry_value in {0, 1}:
            secure_boot_enabled = bool(secure_boot_registry_value == 1)
        if secure_boot_enabled is not None and secure_boot_supported is None:
            secure_boot_supported = True
        if not secure_boot_source:
            if secure_boot_registry_value is not None:
                secure_boot_source = "Registry SecureBoot\\State"
            elif secure_boot_enabled is not None:
                secure_boot_source = "Confirm-SecureBootUEFI"
        if (
            not secure_boot_error
            and secure_boot_enabled is None
            and secure_boot_supported is False
            and secure_boot_firmware_type is not None
            and secure_boot_firmware_type != 2
        ):
            secure_boot_error = "Secure Boot недоступен: система загружена не в UEFI-режиме."
        if secure_boot_enabled is not None:
            secure_boot_error = ""

        if secure_boot_enabled is True:
            secure_boot_state = "Включен"
        elif secure_boot_enabled is False and secure_boot_supported is True:
            secure_boot_state = "Выключен"
        elif secure_boot_supported is False:
            secure_boot_state = "Не поддерживается (Legacy BIOS)"
        else:
            secure_boot_state = "Не определено"

        reboot_pending_reasons: list[Dict[str, Any]] = []
        reboot_pending_checks: dict[str, Any] = {}
        reboot_pending_diagnostics: list[str] = []
        reboot_pending = _to_bool(reboot_pending_raw)
        if isinstance(reboot_pending_raw, dict):
            reboot_pending = _to_bool(reboot_pending_raw.get("Pending"))
            reboot_pending_checks = _ensure_dict(reboot_pending_raw.get("Checks"))
            reboot_pending_diagnostics = [
                str(item).strip()
                for item in _ensure_list(reboot_pending_raw.get("Diagnostics"))
                if str(item or "").strip()
            ]
            for reason in _filter_dict_list(reboot_pending_raw.get("Reasons")):
                title = str(reason.get("Title") or reason.get("title") or reason.get("Id") or "").strip()
                if not title:
                    continue
                reboot_pending_reasons.append(
                    {
                        "id": str(reason.get("Id") or reason.get("id") or "").strip(),
                        "title": title,
                        "details": str(reason.get("Details") or reason.get("details") or "").strip(),
                    }
                )
        if reboot_pending is None and reboot_pending_reasons:
            reboot_pending = True
        if reboot_pending is False and reboot_pending_reasons:
            reboot_pending = True
        if reboot_pending and not reboot_pending_reasons:
            reboot_pending_reasons = [
                {
                    "id": "generic_pending_reboot",
                    "title": "Обнаружен признак необходимости перезагрузки.",
                    "details": "",
                }
            ]
        reboot_pending_summary = "; ".join(
            item.get("title", "")
            for item in reboot_pending_reasons
            if item.get("title")
        )

        bitlocker_meta = {
            "available": bitlocker_available,
            "access_denied": bitlocker_access_denied,
            "sources": bitlocker_sources,
            "errors": bitlocker_errors,
            "diagnostics": bitlocker_diagnostics,
            "summary": bitlocker_summary,
            "error": bitlocker_error,
            "manage_bde_attempted": bitlocker_manage_bde_attempted,
            "manage_bde_exit_code": bitlocker_manage_bde_exit_code,
            "manage_bde_raw_snippet": bitlocker_manage_bde_snippet,
            "readability": {
                "readable": bool(bitlocker_volumes),
                "reasons": bitlocker_readability_reasons,
            },
        }
    
        security = {
            "secure_boot": secure_boot_enabled,
            "secure_boot_details": {
                "supported": secure_boot_supported,
                "state": secure_boot_state,
                "source": secure_boot_source,
                "error": secure_boot_error,
                "diagnostics": secure_boot_diagnostics,
                "registry_value": secure_boot_registry_value,
                "firmware_type": secure_boot_firmware_type,
            },
            "reboot_pending": reboot_pending,
            "reboot_pending_details": {
                "reasons": reboot_pending_reasons,
                "checks": reboot_pending_checks,
                "diagnostics": reboot_pending_diagnostics,
                "summary": reboot_pending_summary,
            },
            "tpm": tpm,
            "defender": {
                "service": defender_info.get("AMServiceEnabled"),
                "antivirus": defender_info.get("AntivirusEnabled"),
                "antispyware": defender_info.get("AntispywareEnabled"),
                "realtime": defender_info.get("RealTimeProtectionEnabled"),
                "nis": defender_info.get("NISEnabled"),
                "tamper": defender_info.get("IsTamperProtected"),
                "signature_version": defender_info.get("SignatureVersion"),
                "signature_updated": _format_ps_time(
                    defender_info.get("AntivirusSignatureLastUpdated")
                ),
                "antispyware_signature_updated": _format_ps_time(
                    defender_info.get("AntispywareSignatureLastUpdated")
                ),
                "nis_signature_updated": _format_ps_time(
                    defender_info.get("NISSignatureLastUpdated")
                ),
                "engine_version": defender_info.get("EngineVersion"),
                "product_version": defender_info.get("ProductVersion"),
                "quick_scan_end": _format_ps_time(defender_info.get("QuickScanEndTime")),
                "full_scan_end": _format_ps_time(defender_info.get("FullScanEndTime")),
            }
            if defender_info
            else {},
            "firewall": firewall_profiles,
            "bitlocker": bitlocker_volumes,
            "bitlocker_meta": bitlocker_meta,
        }
    
        chassis_types_raw = _ensure_list(enclosure_info.get("ChassisTypes"))
        chassis_labels = _map_chassis_types(chassis_types_raw)
        device_type = _infer_device_type(chassis_types_raw, cs_info.get("PCSystemType"))
    
        os_name = _resolve_os_name(
            os_info,
            winver_info,
            os_info.get("BuildNumber"),
            ps_data.get("OSNameResolved"),
        )
        system = {
            "hostname": platform.node() or os.environ.get("COMPUTERNAME", ""),
            "os_name": os_name,
            "os_version": os_info.get("Version") or "",
            "os_build": os_info.get("BuildNumber") or "",
            "architecture": os_info.get("OSArchitecture") or "",
            "manufacturer": _clean_name(cs_info.get("Manufacturer")),
            "model": _clean_name(cs_info.get("Model")),
            "system_family": _clean_name(cs_info.get("SystemFamily")),
            "system_type": cs_info.get("SystemType") or "",
            "pc_system_type": _map_pc_system_type(cs_info.get("PCSystemType")),
            "pc_system_type_ex": _map_pc_system_type(cs_info.get("PCSystemTypeEx")),
            "device_type": device_type,
            "chassis_types": chassis_labels,
            "enclosure_manufacturer": _clean_name(enclosure_info.get("Manufacturer")),
            "enclosure_version": _clean_name(enclosure_info.get("Version")),
            "enclosure_serial": _clean_serial(enclosure_info.get("SerialNumber")),
            "asset_tag": _clean_serial(enclosure_info.get("SMBIOSAssetTag")),
            "domain": cs_info.get("Domain") or "",
            "domain_role": _map_domain_role(cs_info.get("DomainRole")),
            "part_of_domain": cs_info.get("PartOfDomain"),
            "workgroup": cs_info.get("Workgroup") or "",
            "hypervisor_present": cs_info.get("HypervisorPresent"),
            "bios_version": bios_info.get("SMBIOSBIOSVersion") or "",
            "bios_serial": _clean_serial(bios_info.get("SerialNumber")),
            "bios_manufacturer": _clean_name(bios_info.get("Manufacturer")),
            "bios_release": _format_datetime(bios_release),
            "baseboard_manufacturer": _clean_name(board_info.get("Manufacturer")),
            "baseboard_product": _clean_name(board_info.get("Product")),
            "baseboard_serial": _clean_serial(board_info.get("SerialNumber")),
            "baseboard_version": _clean_name(board_info.get("Version")),
            "system_uuid": _clean_uuid(product_info.get("UUID")),
            "system_product_name": _clean_name(product_info.get("Name")),
            "system_product_vendor": _clean_name(product_info.get("Vendor")),
            "system_product_id": _clean_serial(product_info.get("IdentifyingNumber")),
            "cpu_name": cpu_name,
            "cpu_vendor": cpu_vendor,
            "cpu_cores": cpu_cores or None,
            "cpu_logical": cpu_logical or None,
            "cpu_max_mhz": cpu_max,
            "cpu_current_mhz": cpu_current,
            "boot_time": boot_time.isoformat() if boot_time else "",
            "boot_time_human": _format_datetime(boot_time),
            "install_time": install_time.isoformat() if install_time else "",
            "install_time_human": _format_datetime(install_time),
            "uptime_seconds": uptime_seconds,
            "uptime_human": _format_uptime(uptime_seconds),
            "product_type": _map_product_type(os_info.get("ProductType")),
            "system_directory": os_info.get("SystemDirectory") or "",
            "windows_directory": os_info.get("WindowsDirectory") or "",
            "registered_user": os_info.get("RegisteredUser") or "",
            "organization": os_info.get("Organization") or "",
            "os_serial": _clean_serial(os_info.get("SerialNumber")),
            "locale": os_info.get("Locale") or "",
            "os_language": os_info.get("OSLanguage") or "",
            "mui_languages": [lang for lang in _ensure_list(os_info.get("MUILanguages")) if lang],
        }
    
        winver = {
            "product_name": os_name or winver_info.get("ProductName") or "",
            "release_id": winver_info.get("ReleaseId") or "",
            "display_version": winver_info.get("DisplayVersion") or "",
            "edition_id": winver_info.get("EditionID") or "",
            "installation_type": winver_info.get("InstallationType") or "",
            "ubr": winver_info.get("UBR"),
            "current_build": winver_info.get("CurrentBuild") or "",
        }
    
        timezone = {
            "id": timezone_info.get("Id") or "",
            "name": timezone_info.get("DisplayName") or "",
            "offset": timezone_info.get("BaseUtcOffset") or "",
        }
    
        swap = {}
        try:
            swap_info = psutil.swap_memory()
            swap = {
                "total_bytes": swap_info.total,
                "used_bytes": swap_info.used,
                "free_bytes": swap_info.free,
                "percent": round(swap_info.percent, 1),
                "total_gb": _to_gb(swap_info.total),
                "used_gb": _to_gb(swap_info.used),
                "free_gb": _to_gb(swap_info.free),
            }
        except Exception:
            swap = {}

        hardware = _collect_hardware_snapshot()
    
        snapshot = {
            "cpu_percent": round(cpu_percent, 1),
            "memory": {
                "total_bytes": mem_total,
                "used_bytes": mem_used,
                "free_bytes": mem_free,
                "available_bytes": mem_available,
                "cached_bytes": mem_cached,
                "buffers_bytes": mem_buffers,
                "percent": mem_percent,
                "total_gb": _to_gb(mem_total),
                "used_gb": _to_gb(mem_used),
                "free_gb": _to_gb(mem_free),
                "available_gb": _to_gb(mem_available),
                "cached_gb": _to_gb(mem_cached),
                "buffers_gb": _to_gb(mem_buffers),
            },
            "disk_percent": disk_percent,
            "disk_totals": {
                "total_bytes": total_disk_bytes,
                "used_bytes": used_disk_bytes,
                "free_bytes": max(total_disk_bytes - used_disk_bytes, 0) if total_disk_bytes else None,
                "total_gb": _to_gb(total_disk_bytes),
                "used_gb": _to_gb(used_disk_bytes),
                "free_gb": _to_gb(total_disk_bytes - used_disk_bytes) if total_disk_bytes else None,
            },
            "disks": disks,
            "net_io": {
                "sent_bytes": net.bytes_sent,
                "recv_bytes": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "sent_human": _human_bytes(net.bytes_sent),
                "recv_human": _human_bytes(net.bytes_recv),
            },
            "interfaces": interfaces,
            "system": system,
            "power": power,
            "winver": winver,
            "timezone": timezone,
            "security": security,
            "updates": updates,
            "services": services,
            "pagefiles": pagefiles,
            "physical_disks": physical_disks,
            "swap": swap,
            "hardware": hardware,
            "error": ps_error,
        }

        _CACHE["ts"] = time.monotonic()
        _CACHE["data"] = snapshot
        return snapshot
    finally:
        if lock_acquired:
            _SNAPSHOT_LOCK.release()


def get_cached_system_snapshot() -> Dict[str, Any]:
    cached = _CACHE.get("data")
    if isinstance(cached, dict):
        return cached
    return {}
