"""
Модуль статуса сервера и сети для Telegram‑бота (aiogram 2.x).

✅ Что есть в модуле:
- /status_server  — статус сервера (разбит на несколько сообщений для удобства скринридера)
- /status_network — статус сети (также разбит на сообщения)
- /speedtest      — отдельный тест скорости (и отдельная кнопка «Тест скорости»)

✅ Особенности:
- Все тяжёлые/блокирующие операции вынесены в фоновые потоки через asyncio.to_thread(),
  чтобы не подвешивать event loop бота.
- Сообщения разделены по смысловым блокам: система, CPU, RAM, процессы, диски, сеть и т.д.
- Добавлены полезные штуки для сисадмина: аптайм, I/O, топ процессов по памяти,
  статус пары ключевых Windows‑служб, предупреждения по дискам/памяти.

Подключение:
    import nostartrunmodul_status_info as modul_status_info
    modul_status_info.register_handlers(dp)

Также поддерживаются текстовые триггеры:
    "Статус сервера"
    "Статус сети"
    "Тест скорости"
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil
from aiogram import types  # для типов сообщений

# --- Optional imports (чтобы модуль не падал) ---
try:
    from wmi import WMI  # type: ignore
except Exception:
    WMI = None

try:
    import speedtest  # type: ignore
except Exception:
    speedtest = None

try:
    _lhm_system_info = importlib.import_module("moduls.web_dashboard.ops.operations.system_info")
except Exception:
    try:
        _lhm_system_info = importlib.import_module("web_dashboard.ops.operations.system_info")
    except Exception:
        _lhm_system_info = None

_collect_hardware_snapshot_lhm = getattr(_lhm_system_info, "_collect_hardware_snapshot", None)
_get_lhm_processes_lhm = getattr(_lhm_system_info, "_get_lhm_processes", None)
_load_lhm_listener_settings_lhm = getattr(_lhm_system_info, "_load_lhm_listener_settings", None)
_start_lhm_process_lhm = getattr(_lhm_system_info, "_start_lhm_process", None)
_stop_lhm_processes_lhm = getattr(_lhm_system_info, "_stop_lhm_processes", None)
_stop_managed_lhm_process_lhm = getattr(_lhm_system_info, "_stop_managed_lhm_process", None)
_LHM_DEFAULT_HOST_LHM = str(getattr(_lhm_system_info, "_LHM_DEFAULT_HOST", "127.0.0.1"))
try:
    _LHM_DEFAULT_PORT_LHM = int(getattr(_lhm_system_info, "_LHM_DEFAULT_PORT", 8085))
except Exception:
    _LHM_DEFAULT_PORT_LHM = 8085

# Попробуем подключить реестр главного меню.
# Если его нет, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item  # type: ignore
except Exception:
    register_main_item = None


# ---------------------------
# Логгеры (совместимость)
# ---------------------------
_bot_logger = logging.getLogger("БОТ")
_com_logger = logging.getLogger("КОМ")


def write_bot_log(entry: str) -> None:
    try:
        _bot_logger.info(entry)
    except Exception:
        logging.getLogger(__name__).info(entry)


def write_com_log(entry: str) -> None:
    try:
        _com_logger.info(entry)
    except Exception:
        logging.getLogger(__name__).info(entry)


# ---------------------------
# Регистрация кнопок в главном меню
# ---------------------------
def _register_mainmenu_items() -> None:
    """
    Регистрирует кнопки главного меню:
    - «Статус сервера»
    - «Статус сети»
    - «Тест скорости»

    Если mainmenu_registry отсутствует, тихо ничего не делает.
    """
    if register_main_item is None:
        return
    try:
        import inspect

        sig = inspect.signature(register_main_item)
        supports_desc = "description" in sig.parameters

        def _call(**kwargs):
            if not supports_desc:
                kwargs.pop("description", None)
            register_main_item(**kwargs)

        _call(
            key="status_server",
            title="Статус сервера",
            trigger_text="Статус сервера",
            group="main",
            order=10,
            description="CPU, RAM, процессы, диски, ОС",
        )
        _call(
            key="status_network",
            title="Статус сети",
            trigger_text="Статус сети",
            group="main",
            order=20,
            description="IP, интерфейсы, шлюз/DNS, диагностика",
        )
        _call(
            key="speedtest",
            title="Тест скорости",
            trigger_text="Тест скорости",
            group="main",
            order=30,
            description="Измерение скорости интернета (отдельной командой)",
        )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Не удалось зарегистрировать пункты главного меню для status_info: %s", e
        )


# Автоматическая регистрация при импорте
try:
    _register_mainmenu_items()
except Exception:
    pass


# ---------------------------
# Общие хелперы
# ---------------------------
TELEGRAM_SAFE_LEN = 3900  # чуть меньше лимита 4096, чтобы не ловить ошибки


async def _to_thread(func, *args, timeout: Optional[float] = None, **kwargs):
    """
    Запускает блокирующую функцию в потоке, с опциональным таймаутом.
    """
    coro = asyncio.to_thread(func, *args, **kwargs)
    if timeout is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout)


async def _send_long(message: types.Message, text: str) -> None:
    """
    Отправляет длинный текст частями (чтобы не упереться в лимит Telegram).
    """
    text = (text or "").strip()
    if not text:
        return
    if len(text) <= TELEGRAM_SAFE_LEN:
        await message.answer(text)
        return

    # режем по строкам, стараясь не ломать формат
    buf: List[str] = []
    size = 0
    for line in text.splitlines(True):
        if size + len(line) > TELEGRAM_SAFE_LEN and buf:
            await message.answer("".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        await message.answer("".join(buf))


async def _send_parts(message: types.Message, parts: List[str]) -> None:
    """
    Отправляет список частей по отдельным сообщениям.
    """
    for p in parts:
        p = (p or "").strip()
        if p:
            await _send_long(message, p)
            # микропаузу не делаем: Telegram и так сам всё раскидает


def _fmt_bytes(num: float) -> str:
    """Человекочитаемый размер."""
    step = 1024.0
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"]
    i = 0
    while num >= step and i < len(units) - 1:
        num /= step
        i += 1
    if i == 0:
        return f"{int(num)} {units[i]}"
    return f"{num:.2f} {units[i]}"


def _fmt_mbps(bits_per_sec: float) -> str:
    mbps = bits_per_sec / 1_000_000
    return f"{mbps:.2f} Мбит/с"


def _fmt_dt(ts: float) -> str:
    try:
        dt = _dt.datetime.fromtimestamp(ts)
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(ts)


def _fmt_timedelta(seconds: float) -> str:
    try:
        return str(_dt.timedelta(seconds=int(seconds)))
    except Exception:
        return f"{seconds:.0f} сек"


def _safe(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + "…")


# Небольшой кэш снапшота датчиков, чтобы в рамках одного запроса
# CPU-блок и блок "Датчики компонентов" брали одну и ту же текущую частоту,
# а не расходились из-за разных источников/моментов опроса.
_LHM_SNAPSHOT_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_LHM_SNAPSHOT_LOCK = threading.Lock()
_LHM_SNAPSHOT_TTL_SEC = 2.0
_LHM_TEMP_START_FETCH_ATTEMPTS = 8
_LHM_TEMP_START_FETCH_DELAY_SEC = 0.6


def _list_lhm_processes_sync() -> List[Any]:
    getter = _get_lhm_processes_lhm
    if not callable(getter):
        return []
    try:
        data = getter()
        return list(data or [])
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _list_lhm_processes_sync: {e}")
        return []


def _snapshot_process_ids(processes: List[Any]) -> set[int]:
    pids: set[int] = set()
    for proc in processes:
        try:
            pid = int(getattr(proc, "pid", 0) or 0)
            if pid > 0:
                pids.add(pid)
        except Exception:
            continue
    return pids


def _collect_hardware_snapshot_with_retry_sync(attempts: int, delay_seconds: float) -> Optional[Dict[str, Any]]:
    if _collect_hardware_snapshot_lhm is None:
        return None

    attempts = max(1, int(attempts or 1))
    delay_seconds = max(0.0, float(delay_seconds or 0.0))
    last_snapshot: Optional[Dict[str, Any]] = None

    for idx in range(attempts):
        try:
            snapshot = _collect_hardware_snapshot_lhm(force=True)
        except Exception as e:
            write_bot_log(f"[ОШИБКА] _collect_hardware_snapshot_with_retry_sync: {e}")
            snapshot = None

        if isinstance(snapshot, dict):
            last_snapshot = snapshot
            if bool(snapshot.get("available")):
                return snapshot

        if idx + 1 < attempts and delay_seconds > 0:
            time.sleep(delay_seconds)

    return last_snapshot


def _start_lhm_temporarily_sync() -> Tuple[bool, str]:
    starter = _start_lhm_process_lhm
    if not callable(starter):
        return False, "Функция запуска LibreHardwareMonitor недоступна."

    host = _LHM_DEFAULT_HOST_LHM
    port = _LHM_DEFAULT_PORT_LHM
    loader = _load_lhm_listener_settings_lhm
    if callable(loader):
        try:
            loaded_host, loaded_port, _ = loader()
            host = str(loaded_host or host)
            port = int(loaded_port or port)
        except Exception:
            pass

    try:
        started, msg = starter(host, port, respect_guard=False)
    except TypeError:
        started, msg = starter(host, port)
    except Exception as e:
        return False, f"Не удалось запустить LibreHardwareMonitor: {e}"

    return bool(started), str(msg or "").strip()


def _stop_temporary_lhm_sync(before_pids: set[int]) -> None:
    stopper_managed = _stop_managed_lhm_process_lhm
    if callable(stopper_managed):
        try:
            stopper_managed()
        except Exception as e:
            write_bot_log(f"[ОШИБКА] _stop_temporary_lhm_sync(managed): {e}")

    current = _list_lhm_processes_sync()
    targets: List[Any]
    if before_pids:
        targets = [proc for proc in current if int(getattr(proc, "pid", 0) or 0) not in before_pids]
    else:
        targets = current
    if not targets:
        return

    stopper_bulk = _stop_lhm_processes_lhm
    if callable(stopper_bulk):
        try:
            stopper_bulk(targets)
            return
        except Exception as e:
            write_bot_log(f"[ОШИБКА] _stop_temporary_lhm_sync(bulk): {e}")

    for proc in targets:
        try:
            proc.terminate()
            proc.wait(timeout=2.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                continue


def _collect_hardware_snapshot_for_status_sync() -> Optional[Dict[str, Any]]:
    """
    Политика для status_server:
    - если процесс LHM уже запущен: только читаем данные;
    - если процесса нет: временно запускаем, читаем датчики и останавливаем.
    """
    if _collect_hardware_snapshot_lhm is None:
        return None

    before_processes = _list_lhm_processes_sync()
    before_pids = _snapshot_process_ids(before_processes)
    was_running = bool(before_pids)
    started_temporarily = False
    start_message = ""

    try:
        if not was_running:
            started_temporarily, start_message = _start_lhm_temporarily_sync()

        attempts = _LHM_TEMP_START_FETCH_ATTEMPTS if not was_running else 1
        snapshot = _collect_hardware_snapshot_with_retry_sync(
            attempts=attempts,
            delay_seconds=_LHM_TEMP_START_FETCH_DELAY_SEC,
        )
        if isinstance(snapshot, dict) and start_message:
            diagnostics = snapshot.get("diagnostics")
            if isinstance(diagnostics, list) and start_message not in diagnostics:
                diagnostics.append(start_message)
        return snapshot
    finally:
        if not was_running and started_temporarily:
            _stop_temporary_lhm_sync(before_pids)


def _get_hardware_snapshot_cached_sync() -> Optional[Dict[str, Any]]:
    """
    Возвращает снапшот LibreHardwareMonitor с коротким TTL.
    Это позволяет переиспользовать одни и те же датчики в нескольких
    частях ответа и не дёргать сборщик лишний раз.
    """
    if _collect_hardware_snapshot_lhm is None:
        return None

    now = time.time()
    cached = _LHM_SNAPSHOT_CACHE.get("data")
    ts = float(_LHM_SNAPSHOT_CACHE.get("ts") or 0.0)
    if isinstance(cached, dict) and (now - ts) <= _LHM_SNAPSHOT_TTL_SEC:
        return cached

    with _LHM_SNAPSHOT_LOCK:
        now = time.time()
        cached = _LHM_SNAPSHOT_CACHE.get("data")
        ts = float(_LHM_SNAPSHOT_CACHE.get("ts") or 0.0)
        if isinstance(cached, dict) and (now - ts) <= _LHM_SNAPSHOT_TTL_SEC:
            return cached

        try:
            snapshot = _collect_hardware_snapshot_for_status_sync()
            if isinstance(snapshot, dict):
                _LHM_SNAPSHOT_CACHE["ts"] = now
                _LHM_SNAPSHOT_CACHE["data"] = snapshot
                return snapshot
        except Exception as e:
            write_bot_log(f"[ОШИБКА] _get_hardware_snapshot_cached_sync: {e}")

        return cached if isinstance(cached, dict) else None


def _snapshot_metric_display(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "н/д"
    display = str(entry.get("display") or "").strip()
    if bool(entry.get("available")) and display and display != "-":
        return display
    return "н/д"


def _extract_cpu_clock_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> str:
    """
    Пытается взять именно текущую частоту CPU из того же снапшота датчиков,
    который используется в блоке "Датчики компонентов".
    """
    if not isinstance(snapshot, dict):
        return "н/д"

    summary = snapshot.get("summary")
    if isinstance(summary, dict):
        display = _snapshot_metric_display(summary.get("cpu_clock"))
        if display != "н/д":
            return display

    sensors_raw = snapshot.get("all_sensors")
    sensors: List[Dict[str, Any]] = [
        item for item in (sensors_raw if isinstance(sensors_raw, list) else []) if isinstance(item, dict)
    ]

    candidates: List[Dict[str, Any]] = []
    for sensor in sensors:
        if str(sensor.get("category") or "").lower() != "cpu":
            continue
        if str(sensor.get("group_kind") or "").lower() != "clock":
            continue

        blob = " ".join(
            [
                str(sensor.get("name") or ""),
                str(sensor.get("group") or ""),
                str(sensor.get("path") or ""),
            ]
        ).lower()
        if not any(term in blob for term in ("core", "cpu", "clock")):
            continue
        candidates.append(sensor)

    if not candidates:
        return "н/д"

    numeric = [s for s in candidates if isinstance(s.get("value"), (int, float))]
    chosen = max(numeric, key=lambda s: float(s.get("value") or 0.0)) if numeric else candidates[0]

    display = str(chosen.get("value_display") or "").strip()
    if display and display != "-":
        return display

    raw_value = chosen.get("value")
    if isinstance(raw_value, (int, float)):
        value_s = f"{float(raw_value):.1f}".rstrip("0").rstrip(".")
        unit_s = str(chosen.get("value_unit") or "").strip()
        return f"{value_s} {unit_s}".strip()

    return "н/д"


# ---------------------------
# Сбор статуса сервера (SYNC)
# ---------------------------
def _get_os_status_sync() -> str:
    """
    ОС: Windows через WMI (если доступно), иначе platform.
    """
    try:
        if platform.system() == "Windows" and WMI is not None:
            w = WMI()
            os_info = w.Win32_OperatingSystem()[0]
            name = str(os_info.Caption).strip()
            version = str(os_info.Version).strip()
            arch = str(os_info.OSArchitecture).strip()
            build = str(getattr(os_info, "BuildNumber", "")).strip()
            return f"ОС: {name} (Версия {version}, Build {build}, {arch})"
        uname = platform.uname()
        return f"ОС: {uname.system} {uname.release} ({_safe(uname.version)}), {uname.machine}"
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_os_status_sync: {e}")
        return f"ОС: {platform.system()} {platform.release()} ({_safe(platform.version())})"


def _get_uptime_sync() -> str:
    try:
        bt = psutil.boot_time()
        up = time.time() - bt
        return f"Аптайм: {_fmt_timedelta(up)} (с {_fmt_dt(bt)})"
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_uptime_sync: {e}")
        return "Аптайм: недоступно"


def _get_battery_status_sync() -> str:
    """
    Для ноутов: батарея/питание. Для десктопов чаще всего вернёт 'н/д'.
    """
    try:
        b = psutil.sensors_battery()
        if not b:
            return "Питание: н/д"
        plugged = "от сети" if b.power_plugged else "от батареи"
        left = ""
        if b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN, None):
            left = f", осталось {_fmt_timedelta(b.secsleft)}"
        return f"Питание: {b.percent:.0f}% ({plugged}{left})"
    except Exception:
        return "Питание: н/д"


def _get_cpu_status_sync() -> str:
    """
    CPU: модель + ядра + текущая частота + загрузка (короткий семпл).

    Важно: частота здесь сначала берётся из того же снапшота LibreHardwareMonitor,
    что и в блоке "Датчики компонентов", чтобы значения не расходились.
    """
    try:
        name = platform.processor() or "Неизвестно"
        cores = psutil.cpu_count(logical=False) or 0
        threads = psutil.cpu_count(logical=True) or 0

        # короткий семпл (блокирует, поэтому функция запускается в thread)
        per_core = psutil.cpu_percent(interval=0.35, percpu=True)
        per = sum(per_core) / len(per_core) if per_core else 0.0

        freq_s = "н/д"

        # 1) Предпочитаем ту же текущую частоту, что и в блоке датчиков.
        snapshot = _get_hardware_snapshot_cached_sync()
        sensor_freq = _extract_cpu_clock_from_snapshot(snapshot)
        if sensor_freq != "н/д":
            freq_s = sensor_freq
        else:
            # 2) Fallback на psutil.current, если датчики недоступны.
            cpu_freq = psutil.cpu_freq()
            if cpu_freq and cpu_freq.current:
                freq_s = f"{cpu_freq.current:.0f} МГц"

        # WMI используем для точного имени CPU, но не подменяем им текущую частоту,
        # потому что MaxClockSpeed часто показывает паспортный максимум, а не реальное значение сейчас.
        if platform.system() == "Windows" and WMI is not None:
            try:
                w = WMI()
                cpu_w = w.Win32_Processor()[0]
                name = str(cpu_w.Name).strip()

                if freq_s == "н/д":
                    max_mhz = getattr(cpu_w, "MaxClockSpeed", None)
                    if max_mhz:
                        freq_s = f"{int(max_mhz)} МГц (макс.)"
            except Exception:
                pass

        # Пер-кор цифры компактно
        core_lines: List[str] = []
        lim = min(len(per_core), 16)
        for i in range(lim):
            core_lines.append(f"{i+1}:{per_core[i]:.0f}%")
        if len(per_core) > 16:
            core_lines.append("…")
        core_s = " ".join(core_lines) if core_lines else "н/д"

        return (
            f"CPU: {_safe(name, 160)}\n"
            f"Загрузка общая: {per:.0f}%\n"
            f"Ядер: {cores} физ., {threads} лог.\n"
            f"Частота: {freq_s}\n"
            f"По ядрам: {core_s}"
        )
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_cpu_status_sync: {e}")
        return "CPU: недоступно"


def _get_ram_status_sync() -> str:
    try:
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return (
            f"RAM: {_fmt_bytes(ram.total)} общий\n"
            f"Использовано: {_fmt_bytes(ram.used)} ({ram.percent}%)\n"
            f"Доступно: {_fmt_bytes(ram.available)}\n"
            f"SWAP: {_fmt_bytes(swap.total)} общий, {_fmt_bytes(swap.used)} использовано ({swap.percent}%)"
        )
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_ram_status_sync: {e}")
        return "RAM: недоступно"


def _get_disk_status_sync() -> str:
    try:
        parts = psutil.disk_partitions(all=False)
        out: List[str] = []
        for p in parts:
            # фильтр мусора (CD-ROM, пустые)
            if not p.device or not p.mountpoint:
                continue
            if "cdrom" in (p.opts or "").lower():
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
                out.append(
                    f"{p.device} ({p.fstype}, {p.mountpoint})\n"
                    f"Всего: {_fmt_bytes(u.total)}\n"
                    f"Использовано: {_fmt_bytes(u.used)} ({u.percent}%)\n"
                    f"Свободно: {_fmt_bytes(u.free)}"
                )
            except Exception:
                out.append(f"{p.device}: недоступно")
        if not out:
            out.append("Диски: не найдено разделов")
        # отдельными блоками, чтобы скринридеру было легче
        return "\n\n".join(out)
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_disk_status_sync: {e}")
        return "Диски: недоступно"

def _get_io_status_sync() -> str:
    """
    Дисковый и сетевой I/O counters.
    """
    try:
        dio = psutil.disk_io_counters()
        nio = psutil.net_io_counters()
        lines: List[str] = []
        if dio:
            lines.append(
                "Диск I/O: "
                f"R={_fmt_bytes(dio.read_bytes)} W={_fmt_bytes(dio.write_bytes)} "
                f"(Rops={dio.read_count} Wops={dio.write_count})"
            )
        if nio:
            lines.append(
                "Сеть I/O: "
                f"IN={_fmt_bytes(nio.bytes_recv)} OUT={_fmt_bytes(nio.bytes_sent)} "
                f"(INp={nio.packets_recv} OUTp={nio.packets_sent})"
            )
        return "\n".join(lines) if lines else "I/O: недоступно"
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_io_status_sync: {e}")
        return "I/O: недоступно"


def _get_process_summary_sync() -> str:
    """
    Процессы: количество + топ по памяти.
    """
    try:
        total = len(psutil.pids())
        top: List[Tuple[int, str, int]] = []
        for p in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
            try:
                info = p.info
                rss = getattr(info.get("memory_info"), "rss", 0) if info.get("memory_info") else 0
                top.append((int(info["pid"]), str(info.get("name") or "unknown"), int(rss)))
            except Exception:
                continue
        top.sort(key=lambda x: x[2], reverse=True)
        top = top[:7]
        lines = [f"Процессов: {total}"]
        if top:
            lines.append("Топ по памяти:")
            for pid, name, rss in top:
                lines.append(f"• {name} (PID {pid}) { _fmt_bytes(rss) }")
        return "\n".join(lines)
    except Exception as e:
        write_bot_log(f"[ОШИБКА] _get_process_summary_sync: {e}")
        return "Процессы: недоступно"


def _get_windows_services_status_sync() -> str:
    """
    Короткий статус пары важных Windows‑служб.
    (На не‑Windows вернёт н/д)
    """
    if platform.system() != "Windows":
        return "Службы Windows: н/д"

    services = [
        ("Dnscache", "DNS Client"),
        ("Dhcp", "DHCP Client"),
        ("NlaSvc", "Network Location Awareness"),
        ("LanmanWorkstation", "Workstation"),
        ("W32Time", "Windows Time"),
        ("WinDefend", "Microsoft Defender"),
    ]

    lines: List[str] = []
    for svc_name, human in services:
        try:
            s = psutil.win_service_get(svc_name)  # type: ignore[attr-defined]
            info = s.as_dict()
            status = str(info.get("status") or "unknown")
            # running/stopped...
            icon = "✅" if status.lower() == "running" else "⚠️"
            lines.append(f"{icon} {human}: {status}")
        except Exception:
            lines.append(f"⚠️ {human}: неизвестно")

    return "\n".join(lines)


def _get_server_warnings_sync() -> str:
    """
    Простые предупреждения: память и свободное место.
    """
    warnings: List[str] = []
    try:
        ram = psutil.virtual_memory()
        if ram.percent >= 90:
            warnings.append(f"RAM критично: {ram.percent}%")
        elif ram.percent >= 80:
            warnings.append(f"RAM высокая: {ram.percent}%")
    except Exception:
        pass

    try:
        for p in psutil.disk_partitions(all=False):
            if not p.mountpoint:
                continue
            if "cdrom" in (p.opts or "").lower():
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
                free_pct = (u.free / max(u.total, 1)) * 100
                if free_pct <= 5:
                    warnings.append(f"Диск {p.mountpoint}: свободно {free_pct:.0f}% (ОЧЕНЬ МАЛО)")
                elif free_pct <= 10:
                    warnings.append(f"Диск {p.mountpoint}: свободно {free_pct:.0f}%")
            except Exception:
                continue
    except Exception:
        pass

    if not warnings:
        return ""

    return "\n".join([f"⚠️ {w}" for w in warnings])


def _summary_metric_display(summary: Dict[str, Any], key: str) -> str:
    entry = summary.get(key)
    if not isinstance(entry, dict):
        return "н/д"
    display = str(entry.get("display") or "").strip()
    if bool(entry.get("available")) and display and display != "-":
        return display
    return "н/д"


def _pick_sensor_display(
    sensors: List[Dict[str, Any]],
    *,
    category: Optional[str] = None,
    group_kind: Optional[str] = None,
    include_terms: Tuple[str, ...] = (),
    prefer: str = "max",
) -> str:
    filtered: List[Dict[str, Any]] = []
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue

        if category and str(sensor.get("category") or "").lower() != category.lower():
            continue
        if group_kind and str(sensor.get("group_kind") or "").lower() != group_kind.lower():
            continue

        if include_terms:
            blob = " ".join(
                [
                    str(sensor.get("name") or ""),
                    str(sensor.get("group") or ""),
                    str(sensor.get("path") or ""),
                ]
            ).lower()
            if not any(term in blob for term in include_terms):
                continue

        filtered.append(sensor)

    if not filtered:
        return "н/д"

    numeric = [s for s in filtered if isinstance(s.get("value"), (int, float))]
    chosen: Dict[str, Any]
    if numeric:
        if prefer == "min":
            chosen = min(numeric, key=lambda s: float(s.get("value") or 0.0))
        else:
            chosen = max(numeric, key=lambda s: float(s.get("value") or 0.0))
    else:
        chosen = filtered[0]

    display = str(chosen.get("value_display") or "").strip()
    if display and display != "-":
        return display

    raw_value = chosen.get("value")
    if isinstance(raw_value, (int, float)):
        if isinstance(raw_value, float):
            value_s = f"{raw_value:.1f}".rstrip("0").rstrip(".")
        else:
            value_s = str(raw_value)
        unit_s = str(chosen.get("value_unit") or "").strip()
        return f"{value_s} {unit_s}".strip()

    return "н/д"


def _build_hardware_telemetry_sync() -> str:
    """
    Возвращает отдельный блок с ключевыми температурами/частотами.
    Работает автономно: сам поднимает LibreHardwareMonitor при необходимости.
    """
    if _collect_hardware_snapshot_lhm is None:
        return (
            "🌡️ Датчики компонентов\n"
            "Не удалось подключить сборщик LibreHardwareMonitor (импорт модуля недоступен)."
        )

    snapshot = _get_hardware_snapshot_cached_sync()
    if not isinstance(snapshot, dict):
        return "🌡️ Датчики компонентов\nОшибка: не удалось получить данные LibreHardwareMonitor."

    source = str(snapshot.get("source") or "LibreHardwareMonitor").strip()
    updated = str(snapshot.get("updated_at") or "н/д").strip()
    available = bool(snapshot.get("available"))
    error = str(snapshot.get("error") or "").strip()
    diagnostics = [str(x).strip() for x in (snapshot.get("diagnostics") or []) if str(x).strip()]

    lines: List[str] = [
        "🌡️ Датчики компонентов",
        f"Источник: {source} | Обновлено: {updated}",
    ]

    if not available:
        lines.append("")
        lines.append("Основные датчики сейчас недоступны.")
        if error:
            lines.append(f"Причина: {error}")
        if diagnostics:
            lines.append("Диагностика:")
            for item in diagnostics[:3]:
                lines.append(f"• {item}")
        return "\n".join(lines).strip()

    summary_raw = snapshot.get("summary")
    summary: Dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    sensors_raw = snapshot.get("all_sensors")
    sensors: List[Dict[str, Any]] = [
        item for item in (sensors_raw if isinstance(sensors_raw, list) else []) if isinstance(item, dict)
    ]

    cpu_temp = _summary_metric_display(summary, "cpu_temp")
    cpu_clock = _summary_metric_display(summary, "cpu_clock")
    cpu_load = _summary_metric_display(summary, "cpu_load")
    cpu_power = _summary_metric_display(summary, "cpu_power")

    gpu_temp = _summary_metric_display(summary, "gpu_temp")
    gpu_clock = _pick_sensor_display(
        sensors,
        category="gpu",
        group_kind="clock",
        include_terms=("gpu", "core"),
        prefer="max",
    )
    gpu_load = _summary_metric_display(summary, "gpu_load")
    gpu_power = _summary_metric_display(summary, "gpu_power")

    vrm_temp = _summary_metric_display(summary, "vrm_temp")

    ram_temp = _summary_metric_display(summary, "ram_temp_max")
    ram_clock = _pick_sensor_display(
        sensors,
        category="memory",
        group_kind="clock",
        include_terms=("memory", "dram", "dimm"),
        prefer="max",
    )
    ram_load = _summary_metric_display(summary, "ram_load")
    ram_used = _summary_metric_display(summary, "ram_used")

    disk_temp = _summary_metric_display(summary, "disk_temp_max")
    disk_used = _summary_metric_display(summary, "disk_used_max")
    disk_life = _summary_metric_display(summary, "disk_life_min")
    fan_speed = _summary_metric_display(summary, "fan_speed_max")

    lines.extend(
        [
            "",
            (
                "CPU: "
                f"температура {cpu_temp}, "
                f"частота {cpu_clock}, "
                f"нагрузка {cpu_load}, "
                f"мощность {cpu_power}"
            ),
            (
                "GPU: "
                f"температура {gpu_temp}, "
                f"частота {gpu_clock}, "
                f"нагрузка {gpu_load}, "
                f"мощность {gpu_power}"
            ),
            f"Плата/VRM: температура {vrm_temp}",
            (
                "ОЗУ: "
                f"температура {ram_temp}, "
                f"частота {ram_clock}, "
                f"нагрузка {ram_load}, "
                f"использовано {ram_used}"
            ),
            (
                "Диски: "
                f"макс. температура {disk_temp}, "
                f"макс. занятость {disk_used}, "
                f"мин. ресурс {disk_life}"
            ),
            f"Охлаждение: макс. скорость вентиляторов {fan_speed}",
        ]
    )

    return "\n".join(lines).strip()


# ---------------------------
# Сбор статуса сети (SYNC)
# ---------------------------
def _get_internal_ips_sync() -> Tuple[str, str, List[str]]:
    """
    Возвращает hostname, "лучший" внутренний IPv4, список деталей интерфейсов.
    """
    hostname = socket.gethostname()
    net_if_stats = psutil.net_if_stats()
    net_if_addrs = psutil.net_if_addrs()
    net_io = psutil.net_io_counters(pernic=True)

    internal_ip = "Не удалось получить"
    details: List[str] = []

    for iface, stats in net_if_stats.items():
        if not stats.isup:
            continue

        addrs = net_if_addrs.get(iface, [])
        ipv4_list: List[str] = []
        ipv6_list: List[str] = []
        mac = None

        for a in addrs:
            if a.family == socket.AF_INET:
                if not a.address.startswith("127."):
                    ipv4_list.append(a.address)
            elif a.family == socket.AF_INET6:
                ipv6_list.append(a.address)
            # MAC (на Win может быть psutil.AF_LINK)
            if hasattr(psutil, "AF_LINK") and a.family == psutil.AF_LINK:
                mac = a.address

        speed = getattr(stats, "speed", 0) or 0
        mtu = getattr(stats, "mtu", None)
        duplex = getattr(stats, "duplex", None)

        io = net_io.get(iface)
        io_s = ""
        if io:
            io_s = f" | IN={_fmt_bytes(io.bytes_recv)} OUT={_fmt_bytes(io.bytes_sent)}"

        if ipv4_list and internal_ip == "Не удалось получить":
            internal_ip = ipv4_list[0]

        parts: List[str] = [f"{iface}"]
        if speed:
            parts.append(f"{speed} Mbps")
        if mtu:
            parts.append(f"MTU {mtu}")
        if duplex is not None and duplex != psutil.NIC_DUPLEX_UNKNOWN:
            parts.append("Full" if duplex == psutil.NIC_DUPLEX_FULL else "Half")
        if mac:
            parts.append(f"MAC {mac}")
        if ipv4_list:
            parts.append("IPv4 " + ", ".join(ipv4_list))
        if ipv6_list:
            parts.append("IPv6 " + ", ".join(ipv6_list[:2]) + ("…" if len(ipv6_list) > 2 else ""))

        details.append(" | ".join(parts) + io_s)

    return hostname, internal_ip, details


def _fetch_text_url_sync(url: str, timeout: float = 4.0) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read(128)
    return data.decode("utf-8", errors="ignore").strip()


def _get_external_ip_sync() -> str:
    """
    Публичный IP через HTTP (без curl).
    """
    urls = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
    ]
    last_err = None
    for u in urls:
        try:
            txt = _fetch_text_url_sync(u, timeout=4.0)
            if txt and len(txt) <= 64:
                return txt
        except Exception as e:
            last_err = e
            continue
    return f"Не удалось получить ({last_err})" if last_err else "Не удалось получить"


def _resolve_dns_sync(host: str = "google.com") -> str:
    try:
        ip = socket.gethostbyname(host)
        return f"✅ DNS: {host} -> {ip}"
    except Exception as e:
        return f"❌ DNS: ошибка ({e})"


def _ping_sync(host: str = "8.8.8.8") -> str:
    """
    Быстрый ping (1 пакет). Возвращает краткий результат.
    """
    try:
        if platform.system() == "Windows":
            cmd = ["ping", "-n", "1", "-w", "1000", host]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", host]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        ok = (p.returncode == 0)
        m = re.search(r"time[=<]\s*([\d\.]+)\s*ms", p.stdout, flags=re.IGNORECASE)
        t = (m.group(1) + " ms") if m else ""
        return f"{'✅' if ok else '❌'} Ping {host} {t}".strip()
    except Exception as e:
        return f"❌ Ping {host}: ошибка ({e})"


def _ipconfig_parsed_sync() -> Dict[str, str]:
    """
    На Windows пытается вытащить Default Gateway / DNS Servers из ipconfig /all.
    """
    result = {"gateway": "н/д", "dns": "н/д"}
    if platform.system() != "Windows":
        return result
    try:
        out = subprocess.check_output(["ipconfig", "/all"], text=True, errors="ignore")
        m = re.search(r"Default Gateway[ .]*:\s*([0-9\.\:]+)", out, flags=re.IGNORECASE)
        if m:
            result["gateway"] = m.group(1).strip()

        m2 = re.search(r"DNS Servers[ .]*:\s*([0-9\.\:]+)", out, flags=re.IGNORECASE)
        dns_list: List[str] = []
        if m2:
            dns_list.append(m2.group(1).strip())
            tail = out[m2.end(): m2.end() + 300]
            for line in tail.splitlines():
                if line.startswith(" " * 10) or line.startswith("\t"):
                    ipm = re.search(r"([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line)
                    if ipm:
                        dns_list.append(ipm.group(1))
                else:
                    break
        if dns_list:
            # уникальные (сохранить порядок)
            uniq = []
            for x in dns_list:
                if x not in uniq:
                    uniq.append(x)
            result["dns"] = ", ".join(uniq)
    except Exception:
        pass
    return result


# ---------------------------
# Скорость (SYNC + cache)
# ---------------------------
_SPEED_CACHE = {"ts": 0.0, "text": ""}
_SPEED_LOCK = asyncio.Lock()

SPEED_CACHE_TTL_SEC = 300  # 5 минут
SPEEDTEST_TIMEOUT_SEC = 35  # общий таймаут на измерение


def _speedtest_speedtestlib_sync() -> str:
    """
    Полный тест через speedtest (если установлен).
    """
    if speedtest is None:
        raise RuntimeError("speedtest библиотека не установлена")

    st = speedtest.Speedtest(secure=True)
    st.get_best_server()
    down_bps = st.download()
    up_bps = st.upload()
    ping_ms = getattr(st.results, "ping", None)
    ping_s = f"{ping_ms:.0f} ms" if ping_ms is not None else "н/д"
    return f"🚀 Speedtest: ↓ {_fmt_mbps(down_bps)} | ↑ {_fmt_mbps(up_bps)} | Ping {ping_s}"


def _speedtest_fast_download_sync() -> str:
    """
    Быстрый download‑тест как fallback.
    Не идеален, но часто работает стабильнее speedtest.
    """
    import urllib.request

    urls = [
        "https://speed.cloudflare.com/__down?bytes=5000000",  # 5 MB
        "https://speed.hetzner.de/1MB.bin",
    ]

    last_err = None
    for url in urls:
        try:
            start = time.perf_counter()
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read(5_000_000)  # ограничим
            elapsed = max(time.perf_counter() - start, 0.001)
            bps = (len(data) * 8) / elapsed
            return f"⚡ Быстрый тест: ↓ {_fmt_mbps(bps)} (примерно)"
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"download‑тест не удался: {last_err}")


async def measure_speed_async(force: bool = False) -> str:
    """
    Асинхронный тест скорости:
    - кэш на 5 минут
    - таймаут
    - speedtest -> fallback download‑тест
    """
    now = time.time()
    if not force and _SPEED_CACHE["text"] and (now - _SPEED_CACHE["ts"] < SPEED_CACHE_TTL_SEC):
        age = int(now - _SPEED_CACHE["ts"])
        return f"{_SPEED_CACHE['text']}\n(кэш {age} сек.)"

    async with _SPEED_LOCK:
        now = time.time()
        if not force and _SPEED_CACHE["text"] and (now - _SPEED_CACHE["ts"] < SPEED_CACHE_TTL_SEC):
            age = int(now - _SPEED_CACHE["ts"])
            return f"{_SPEED_CACHE['text']}\n(кэш {age} сек.)"

        try:
            text = await _to_thread(_speedtest_speedtestlib_sync, timeout=SPEEDTEST_TIMEOUT_SEC)
        except Exception as e1:
            try:
                text = await _to_thread(_speedtest_fast_download_sync, timeout=15)
                text += f"\n(Speedtest lib упал: {type(e1).__name__})"
            except Exception as e2:
                text = f"⚠️ Скорость: не удалось измерить.\nПричина: {type(e1).__name__} / {type(e2).__name__}"

        _SPEED_CACHE["ts"] = time.time()
        _SPEED_CACHE["text"] = text
        return text


# ---------------------------
# Асинхронные сборщики (split для скринридера)
# ---------------------------
async def build_server_status_parts() -> List[str]:
    """Собирает серверный статус асинхронно и возвращает список сообщений."""
    tasks = [
        _to_thread(_get_os_status_sync),
        _to_thread(_get_uptime_sync),
        _to_thread(_get_battery_status_sync),
        _to_thread(_get_cpu_status_sync),
        _to_thread(_get_ram_status_sync),
        _to_thread(_build_hardware_telemetry_sync),
        _to_thread(_get_process_summary_sync),
        _to_thread(_get_windows_services_status_sync),
        _to_thread(_get_io_status_sync),
        _to_thread(_get_disk_status_sync),
        _to_thread(_get_server_warnings_sync),
    ]
    (
        os_s,
        up_s,
        power_s,
        cpu_s,
        ram_s,
        hw_s,
        proc_s,
        svc_s,
        io_s,
        disk_s,
        warn_s,
    ) = await asyncio.gather(*tasks, return_exceptions=False)

    parts: List[str] = [
        "🖥 Система\n" + "\n".join([os_s, up_s, power_s]).strip(),
        "🧠 CPU\n" + cpu_s.strip(),
        "🧬 RAM\n" + ram_s.strip(),
        "🧩 Процессы\n" + proc_s.strip(),
        "🧰 Службы\n" + svc_s.strip(),
        "📦 I/O\n" + io_s.strip(),
        "💽 Диски\n" + disk_s.strip(),
        hw_s.strip(),
    ]

    warn_s = (warn_s or "").strip()
    if warn_s:
        parts.insert(1, "⚠️ Предупреждения\n" + warn_s)

    return parts

async def build_network_status_parts() -> List[str]:
    """Собирает сетевой статус асинхронно и возвращает список сообщений (без speedtest)."""
    hostname, internal_ip, iface_details = await _to_thread(_get_internal_ips_sync)
    ext_ip = await _to_thread(_get_external_ip_sync, timeout=6.0)
    ipcfg = await _to_thread(_ipconfig_parsed_sync, timeout=5.0)
    dns_check = await _to_thread(_resolve_dns_sync)
    ping_check = await _to_thread(_ping_sync)

    fqdn = socket.getfqdn()
    userdomain = os.environ.get("USERDOMAIN") or os.environ.get("DOMAIN") or "н/д"

    overview = "\n".join(
        [
            f"Хост: {hostname}",
            f"FQDN: {fqdn}",
            f"Домен/Workgroup: {userdomain}",
            f"Внутренний IP: {internal_ip}",
            f"Внешний IP: {ext_ip}",
            f"Шлюз: {ipcfg.get('gateway','н/д')}",
            f"DNS: {ipcfg.get('dns','н/д')}",
        ]
    )

    ifaces = "📡 Интерфейсы (UP)\n" + (
        "\n".join([f"• {d}" for d in iface_details]) if iface_details else "(нет активных)"
    )

    diag = "🧪 Диагностика\n" + "\n".join([dns_check, ping_check])
    hint = "ℹ️ Скорость: отдельной кнопкой «Тест скорости» или командой /speedtest"

    return [
        "🌐 Сеть (обзор)\n" + overview.strip(),
        ifaces.strip(),
        diag.strip(),
        hint.strip(),
    ]

# ---------------------------
# Регистрация хэндлеров
# ---------------------------
def register_handlers(dp):
    """Регистрация команд статуса сервера и сети.

    Ничего не добавляет в меню утилит, работает по командам / тексту.
    """

    @dp.message_handler(commands=["status_server"])
    async def cmd_status_server(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сервера (через модуль).")
        await message.answer("⏳ Собираю статус сервера…")
        try:
            parts = await build_server_status_parts()
            await _send_parts(message, parts)
        except Exception as e:
            await message.answer(f"❌ Ошибка получения статуса сервера: {e}")

    @dp.message_handler(commands=["status_network"])
    async def cmd_status_network(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сети (через модуль).")
        await message.answer("⏳ Собираю статус сети…")
        try:
            parts = await build_network_status_parts()
            await _send_parts(message, parts)
        except Exception as e:
            await message.answer(f"❌ Ошибка получения статуса сети: {e}")

    @dp.message_handler(commands=["speedtest"])
    async def cmd_speedtest(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил speedtest (через модуль).")
        await message.answer("🚦 Тест скорости… (обычно 10–30 сек)")
        try:
            text = await measure_speed_async(force=True)
            await _send_long(message, "🚀 Тест скорости\n" + text)
        except Exception as e:
            await message.answer(f"⚠️ Ошибка теста скорости: {e}")

    # Старые текстовые триггеры (кнопки/текст)
    @dp.message_handler(lambda m: (m.text or "").strip().lower() == "статус сервера")
    async def text_status_server(message: types.Message):
        await cmd_status_server(message)

    @dp.message_handler(lambda m: (m.text or "").strip().lower() == "статус сети")
    async def text_status_network(message: types.Message):
        await cmd_status_network(message)

    @dp.message_handler(lambda m: (m.text or "").strip().lower() == "тест скорости")
    async def text_speedtest(message: types.Message):
        await cmd_speedtest(message)
