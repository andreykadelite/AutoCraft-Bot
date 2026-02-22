from __future__ import annotations

import asyncio
import configparser
import importlib
import importlib.util
import ipaddress
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import requests

from ...utils import tail_file

_PROCESS_SCAN_ATTRS = [
    "pid",
    "name",
    "exe",
    "cmdline",
    "create_time",
    "status",
    "username",
    "cwd",
    "cpu_percent",
    "memory_percent",
]
_CPU_SAMPLE_SECONDS = 0.12
_HEARTBEAT_FILE = Path("log") / "heartbeat_main.txt"
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_ACTIVATED_USERS_STORE: Any = None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _safe_text(value: Any, limit: int = 255) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _import_activated_users_store():
    global _ACTIVATED_USERS_STORE
    if _ACTIVATED_USERS_STORE is not None:
        return _ACTIVATED_USERS_STORE

    last_error: Optional[Exception] = None
    for name in ("activated_users_store", "moduls.activated_users_store"):
        try:
            _ACTIVATED_USERS_STORE = importlib.import_module(name)
            return _ACTIVATED_USERS_STORE
        except Exception as exc:
            last_error = exc

    try:
        moduls_dir = Path(__file__).resolve().parents[3]
        moduls_dir_str = str(moduls_dir)
        if moduls_dir_str not in sys.path:
            sys.path.insert(0, moduls_dir_str)

        try:
            _ACTIVATED_USERS_STORE = importlib.import_module("activated_users_store")
            return _ACTIVATED_USERS_STORE
        except Exception as exc:
            last_error = exc

        file_path = moduls_dir / "activated_users_store.py"
        if file_path.exists():
            spec = importlib.util.spec_from_file_location("activated_users_store", str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules.setdefault("activated_users_store", module)
                _ACTIVATED_USERS_STORE = module
                return _ACTIVATED_USERS_STORE
    except Exception as exc:
        last_error = exc

    if last_error:
        raise last_error
    raise ImportError("Не удалось импортировать activated_users_store")


def _format_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    total = max(int(seconds), 0)
    minutes, sec = divmod(total, 60)
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


def _read_config(base_dir: str) -> tuple[configparser.ConfigParser, Path, str]:
    cfg = configparser.ConfigParser()
    cfg_path = Path(base_dir) / "config.ini"
    if not cfg_path.exists():
        return cfg, cfg_path, ""
    encodings = ["utf-8-sig", "utf-8", "cp1251", "cp866"]
    for enc in encodings:
        try:
            cfg.read(cfg_path, encoding=enc)
            return cfg, cfg_path, enc
        except Exception:
            cfg = configparser.ConfigParser()
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="ignore")
        cfg.read_string(text)
    except Exception:
        return configparser.ConfigParser(), cfg_path, ""
    return cfg, cfg_path, "utf-8-raw"


def _config_get(cfg: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    try:
        return cfg.get(section, key, fallback=fallback)
    except Exception:
        return fallback


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _normalize_host(value: str) -> str:
    return str(value or "").strip()


def _validate_host(value: str) -> bool:
    host = _normalize_host(value)
    if not host:
        return False
    if host.lower() in ("localhost",):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except Exception:
        pass
    return bool(_HOST_RE.match(host))


def _validate_token(token: str) -> bool:
    token = str(token or "").strip()
    return bool(_TOKEN_RE.match(token))


def _normalize_allowed_ids(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", ""
    values: List[int] = []
    invalid: List[str] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit():
            invalid.append(item)
            continue
        if not (7 <= len(item) <= 10):
            invalid.append(item)
            continue
        values.append(int(item))
    if invalid:
        return "", "Некорректные ID: " + ", ".join(invalid)
    normalized = ", ".join(str(v) for v in sorted(set(values)))
    return normalized, ""


def _parse_int_in_range(
    value: Any,
    field_name: str,
    minimum: int,
    maximum: int,
) -> tuple[Optional[int], str]:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return None, f"{field_name}: требуется целое число."
    if parsed < minimum or parsed > maximum:
        return None, f"{field_name}: допустимо от {minimum} до {maximum}."
    return parsed, ""


def _normalize_startup_method(method: Any, fallback: str = "startup") -> str:
    text = str(method or "").strip().lower()
    if text in ("startup_lnk", "startup_bat"):
        return "startup"
    if text in ("auto", "startup", "registry", "schtask", "none"):
        return text
    return fallback


def _startup_method_label(method: Any) -> str:
    normalized = _normalize_startup_method(method, fallback="none")
    labels = {
        "auto": "Автовыбор",
        "startup": "Папка автозагрузки",
        "registry": "Реестр (HKCU\\Run)",
        "schtask": "Планировщик задач",
        "none": "Не обнаружен",
    }
    return labels.get(normalized, "Неизвестно")


def _collect_autorun_status(base_dir: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "supported": False,
        "enabled": False,
        "start_in_tray": False,
        "start_in_tray_known": True,
        "method": "none",
        "method_label": _startup_method_label("none"),
        "configured_enabled": False,
        "configured_start_in_tray": False,
        "configured_method": "startup",
        "configured_method_label": _startup_method_label("startup"),
        "detected_method": "none",
        "detected_method_label": _startup_method_label("none"),
        "in_sync": False,
        "error": "",
    }
    try:
        from windows_startup import _is_windows, load_startup_full, detect_autorun
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if not _is_windows():
        result["error"] = "Доступно только на Windows."
        return result

    result["supported"] = True
    try:
        cfg_enabled, cfg_tray, cfg_method_raw = load_startup_full()
        det_enabled, det_tray, det_method_raw = detect_autorun()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    cfg_method = _normalize_startup_method(cfg_method_raw, fallback="startup")
    det_method = _normalize_startup_method(det_method_raw, fallback="none")
    if det_enabled and det_method == "none":
        det_method = cfg_method

    tray_known = det_tray is not None
    tray_value = bool(det_tray) if tray_known else bool(cfg_tray)

    result.update(
        {
            "enabled": bool(det_enabled),
            "start_in_tray": tray_value,
            "start_in_tray_known": tray_known,
            "method": det_method if det_enabled else "none",
            "method_label": _startup_method_label(det_method if det_enabled else "none"),
            "configured_enabled": bool(cfg_enabled),
            "configured_start_in_tray": bool(cfg_tray),
            "configured_method": cfg_method,
            "configured_method_label": _startup_method_label(cfg_method),
            "detected_method": det_method,
            "detected_method_label": _startup_method_label(det_method),
        }
    )
    result["in_sync"] = (
        (result["enabled"] == result["configured_enabled"])
        and ((result["enabled"] and result["method"] == result["configured_method"]) or not result["enabled"])
        and (not result["start_in_tray_known"] or result["start_in_tray"] == result["configured_start_in_tray"])
    )
    return result


def _detect_connection(cfg: configparser.ConfigParser) -> Dict[str, Any]:
    use_standard = False
    try:
        use_standard = cfg.getboolean("api_server", "use_standard_api", fallback=False)
    except Exception:
        use_standard = False

    address = _config_get(cfg, "telegram_api", "address", "").strip()
    port = _config_get(cfg, "telegram_api", "port", "").strip()
    api_server = ""
    if address and port:
        api_server = f"http://{address}:{port}"
    elif address:
        api_server = address

    if use_standard:
        mode = "Стандартный Telegram API"
    elif api_server:
        mode = "Локальный Telegram API"
    else:
        mode = "Не настроено"

    return {
        "mode": mode,
        "use_standard_api": use_standard,
        "address": address,
        "port": port,
        "api_server": api_server,
    }


def _is_autocraft_main_module() -> bool:
    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod, "__file__", "") if main_mod else ""
    if not main_file:
        main_file = sys.argv[0] if sys.argv else ""
    if not main_file:
        main_file = sys.executable
    if not main_file:
        return False
    lower = str(main_file).lower()
    return "bot-ok" in lower or "autocraft" in lower


def _process_tag(lower_cmd: str, lower_name: str, lower_exe: str, pid: int) -> List[str]:
    tags: List[str] = []
    if pid == os.getpid():
        tags.append("панель")
    if "--child" in lower_cmd:
        tags.append("child")
    if "--api-watchdog" in lower_cmd:
        tags.append("api-watchdog")
    if "watchdog" in lower_cmd and "api-watchdog" not in lower_cmd:
        tags.append("watchdog")
    if "telegram-bot-api" in lower_cmd or "telegram-bot-api" in lower_name or "telegram-bot-api" in lower_exe:
        tags.append("telegram-bot-api")
    return tags


def _score_autocraft_process(text: str, lower_cmd: str, base_dir: str) -> int:
    score = 0
    if "bot-ok" in text:
        score += 6
    if "autocraft" in text:
        score += 5
    if base_dir and base_dir.lower() in text:
        score += 3
    if "--child" in lower_cmd:
        score += 2
    if "--api-watchdog" in lower_cmd:
        score -= 5
    if "telegram-bot-api" in text:
        score -= 8
    return score


def _process_details(proc: psutil.Process, info: Dict[str, Any], include_cpu: bool) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "pid": info.get("pid"),
        "name": info.get("name") or "",
        "exe": info.get("exe") or "",
        "cmdline": info.get("cmdline") or [],
        "status": info.get("status") or "",
        "username": info.get("username") or "",
        "cwd": info.get("cwd") or "",
    }

    create_time = info.get("create_time")
    created = _safe_float(create_time)
    if created:
        details["create_time"] = created
        details["uptime_sec"] = max(time.time() - created, 0)
    else:
        details["create_time"] = None
        details["uptime_sec"] = None

    try:
        mem = proc.memory_info()
        details["memory_rss"] = mem.rss
        details["memory_rss_mb"] = round(mem.rss / 1024 / 1024, 1)
    except Exception:
        details["memory_rss"] = None
        details["memory_rss_mb"] = None

    try:
        details["memory_percent"] = round(proc.memory_percent(), 2)
    except Exception:
        details["memory_percent"] = None

    try:
        details["threads"] = proc.num_threads()
    except Exception:
        details["threads"] = None

    if include_cpu:
        try:
            details["cpu_percent"] = round(proc.cpu_percent(interval=_CPU_SAMPLE_SECONDS), 1)
        except Exception:
            details["cpu_percent"] = None
    else:
        cpu_hint = _safe_float(info.get("cpu_percent"))
        details["cpu_percent"] = round(cpu_hint, 1) if cpu_hint is not None else None

    try:
        handles = getattr(proc, "num_handles", None)
        details["handles"] = handles() if callable(handles) else None
    except Exception:
        details["handles"] = None

    return details


def _scan_processes(base_dir: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[int]]:
    autocraft: List[Dict[str, Any]] = []
    telegram_api: List[Dict[str, Any]] = []
    primary_pid: Optional[int] = None

    for proc in psutil.process_iter(attrs=_PROCESS_SCAN_ATTRS):
        try:
            info = proc.info
        except Exception:
            continue

        name = (info.get("name") or "").lower()
        exe = (info.get("exe") or "").lower()
        cmdline = " ".join(info.get("cmdline") or []).lower()
        text = " ".join([name, exe, cmdline]).strip()

        tags = _process_tag(cmdline, name, exe, proc.pid)
        info["tags"] = tags

        if "telegram-bot-api" in text:
            telegram_api.append(_process_details(proc, info, include_cpu=False))
            continue

        if not text:
            continue

        if "bot-ok" in text or "autocraft" in text or (base_dir and base_dir.lower() in text):
            details = _process_details(proc, info, include_cpu=False)
            details["tags"] = tags
            details["score"] = _score_autocraft_process(text, cmdline, base_dir)
            autocraft.append(details)

    autocraft.sort(key=lambda item: item.get("score", 0), reverse=True)
    if autocraft:
        primary_pid = autocraft[0].get("pid")
    return autocraft, telegram_api, primary_pid


def _read_heartbeat(base_dir: str) -> Dict[str, Any]:
    path = Path(base_dir) / _HEARTBEAT_FILE
    if not path.exists():
        return {"path": str(path), "status": "нет файла", "age_sec": None}
    try:
        mtime = path.stat().st_mtime
        age = max(time.time() - mtime, 0)
        status = "живой" if age < 15 else "нет сигнала"
        return {"path": str(path), "status": status, "age_sec": round(age, 1)}
    except Exception:
        return {"path": str(path), "status": "ошибка чтения", "age_sec": None}


def _runtime_info() -> Dict[str, Any]:
    data: Dict[str, Any] = {"in_process": False}
    main_mod = sys.modules.get("__main__")
    if not main_mod:
        return data

    data["in_process"] = _is_autocraft_main_module()
    data["connection_summary"] = getattr(main_mod, "connection_summary", "")
    data["bot_running"] = bool(getattr(main_mod, "current_bot", None))
    bot_thread = getattr(main_mod, "bot_thread", None)
    data["bot_thread_alive"] = bool(getattr(bot_thread, "is_alive", lambda: False)())
    loop = getattr(main_mod, "current_loop", None)
    data["loop_running"] = bool(getattr(loop, "is_running", lambda: False)())
    data["authorized_users"] = len(getattr(main_mod, "authorized_users", []) or [])
    data["allowed_accounts"] = len(getattr(main_mod, "allowed_accounts", []) or [])
    data["gui_ready"] = getattr(main_mod, "gui_ready", None)
    return data


def _collect_activated_users(base_dir: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "ok": True,
        "count": 0,
        "db_path": str(Path(base_dir) / "data" / "activated_users.db"),
        "items": [],
        "error": "",
    }

    try:
        store = _import_activated_users_store()
    except Exception as exc:
        data["ok"] = False
        data["error"] = f"Не удалось загрузить activated_users_store: {exc}"
        return data

    try:
        ensure_storage = getattr(store, "ensure_storage", None)
        if callable(ensure_storage):
            data["db_path"] = str(ensure_storage(base_dir))
    except Exception:
        pass

    try:
        get_db_path = getattr(store, "get_db_path", None)
        if callable(get_db_path):
            data["db_path"] = str(get_db_path(base_dir))
    except Exception:
        pass

    list_fn = getattr(store, "list_activated_users", None)
    if not callable(list_fn):
        data["ok"] = False
        data["error"] = "В activated_users_store нет функции list_activated_users."
        return data

    try:
        rows = list_fn(base_dir) or []
    except Exception as exc:
        data["ok"] = False
        data["error"] = f"Ошибка чтения базы активированных пользователей: {exc}"
        return data

    items: List[Dict[str, Any]] = []
    for row in rows:
        payload = dict(row) if isinstance(row, dict) else {}
        raw_is_bot = payload.get("is_bot")
        raw_is_bot_num = _safe_int(raw_is_bot)
        is_bot = bool(raw_is_bot_num) if raw_is_bot_num is not None else _to_bool(raw_is_bot, default=False)

        items.append(
            {
                "user_id": _safe_int(payload.get("user_id")),
                "chat_id": _safe_int(payload.get("chat_id")),
                "username": _safe_text(payload.get("username")),
                "first_name": _safe_text(payload.get("first_name")),
                "last_name": _safe_text(payload.get("last_name")),
                "language_code": _safe_text(payload.get("language_code"), limit=32),
                "is_bot": is_bot,
                "activated_at": _safe_text(payload.get("activated_at"), limit=64),
                "last_activated_at": _safe_text(payload.get("last_activated_at"), limit=64),
                "activation_count": _safe_int(payload.get("activation_count")) or 0,
                "last_source": _safe_text(payload.get("last_source"), limit=64),
            }
        )

    data["items"] = items
    data["count"] = len(items)
    return data


def autocraft_activated_users_clear(base_dir: str) -> Dict[str, Any]:
    try:
        store = _import_activated_users_store()
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось загрузить activated_users_store: {exc}"}

    clear_fn = getattr(store, "clear_activated_users", None)
    if not callable(clear_fn):
        return {"ok": False, "stdout": "", "stderr": "В activated_users_store нет функции clear_activated_users."}

    try:
        deleted = int(clear_fn(base_dir))
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Ошибка очистки базы активированных пользователей: {exc}"}

    return {
        "ok": True,
        "stdout": f"База активированных пользователей очищена. Удалено записей: {deleted}",
        "stderr": "",
        "deleted": deleted,
    }


def collect_autocraft_status(base_dir: str) -> Dict[str, Any]:
    cfg, cfg_path, cfg_encoding = _read_config(base_dir)
    connection = _detect_connection(cfg)
    runtime = _runtime_info()
    autorun = _collect_autorun_status(base_dir)
    activated_users = _collect_activated_users(base_dir)

    autocraft_processes, api_processes, primary_pid = _scan_processes(base_dir)
    if runtime.get("in_process") and _is_autocraft_main_module():
        primary_pid = os.getpid()

    primary = None
    if primary_pid:
        try:
            proc = psutil.Process(primary_pid)
            info = proc.as_dict(attrs=_PROCESS_SCAN_ATTRS)
            primary = _process_details(proc, info, include_cpu=True)
            primary["tags"] = _process_tag(
                " ".join(primary.get("cmdline", [])).lower(),
                (primary.get("name") or "").lower(),
                (primary.get("exe") or "").lower(),
                primary_pid,
            )
        except Exception:
            primary = None

    running = bool(primary)
    heartbeat = _read_heartbeat(base_dir)

    if primary and not any(item.get("pid") == primary_pid for item in autocraft_processes):
        extra = dict(primary)
        extra["score"] = _score_autocraft_process(
            " ".join([(extra.get("name") or "").lower(), (extra.get("exe") or "").lower(), " ".join(extra.get("cmdline") or [])]).strip(),
            " ".join(extra.get("cmdline") or []).lower(),
            base_dir,
        )
        autocraft_processes.insert(0, extra)

    return {
        "ok": True,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_dir": base_dir,
        "config_path": str(cfg_path),
        "config_encoding": cfg_encoding,
        "running": running,
        "primary": primary,
        "processes": autocraft_processes,
        "api_processes": api_processes,
        "connection": connection,
        "runtime": runtime,
        "heartbeat": heartbeat,
        "autorun": autorun,
        "activated_users": activated_users,
    }


def _latest_log_file(log_dir: Path, pattern: str) -> Optional[Path]:
    try:
        candidates = list(log_dir.glob(pattern))
    except Exception:
        candidates = []
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except Exception:
        return candidates[0]


def collect_autocraft_logs(base_dir: str, lines: int = 140) -> Dict[str, Any]:
    log_dir = Path(base_dir) / "log"
    sources = [
        ("Бот", "log_*_bot.txt"),
        ("Команды", "log_*_kom.txt"),
        ("Ошибки", "log_*_oshibka.txt"),
        ("Плагины", "log_*_plagin.txt"),
        ("Дебаг", "log_*_debаг.txt"),
        ("Вотчдог", "watchdog.log"),
    ]

    items = []
    for title, pattern in sources:
        path = _latest_log_file(log_dir, pattern) if "*" in pattern else (log_dir / pattern)
        if not path or not path.exists():
            items.append({"title": title, "path": str(path) if path else "", "tail": "Лог не найден."})
            continue
        tail = tail_file(path, lines=lines)
        items.append({"title": title, "path": str(path), "tail": tail})

    combined_parts = []
    for item in items:
        header = f"=== {item['title']} ==="
        combined_parts.append(header)
        combined_parts.append(item.get("tail") or "")
        combined_parts.append("")

    combined = "\n".join(combined_parts).strip()
    return {
        "ok": True,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "lines": lines,
        "items": items,
        "combined": combined,
    }


def list_autocraft_plugins(base_dir: str) -> Dict[str, Any]:
    plugins_dir = Path(base_dir) / "plugins"
    items: List[Dict[str, Any]] = []
    loaded = set()

    main_mod = sys.modules.get("__main__")
    if main_mod:
        loaded_plugins = getattr(main_mod, "loaded_plugins", None)
        if isinstance(loaded_plugins, dict):
            loaded = {str(k) for k in loaded_plugins.keys()}

    if not plugins_dir.exists():
        return {"ok": True, "items": [], "loaded": list(loaded)}

    for folder in sorted(plugins_dir.iterdir()):
        if not folder.is_dir():
            continue
        meta_path = folder / f"{folder.name}.json"
        meta = {}
        if meta_path.exists():
            try:
                meta_text = meta_path.read_text(encoding="utf-8", errors="ignore")
                import json

                meta = json.loads(meta_text) if meta_text else {}
            except Exception:
                meta = {}
        items.append(
            {
                "name": meta.get("name") or folder.name,
                "title": meta.get("title") or meta.get("name") or folder.name,
                "version": meta.get("version") or "",
                "description": meta.get("description") or "",
                "loaded": folder.name in loaded,
                "path": str(folder),
            }
        )

    return {"ok": True, "items": items, "loaded": list(loaded)}


def _schedule_exit(code: int) -> None:
    def _exit():
        time.sleep(0.8)
        os._exit(code)

    threading.Thread(target=_exit, daemon=True).start()


def _launch_autocraft(base_dir: str) -> Dict[str, Any]:
    exe_path = Path(base_dir) / "bot-ok.exe"
    py_path = Path(base_dir) / "bot-ok.py"
    if exe_path.exists():
        cmd = [str(exe_path)]
    elif py_path.exists():
        cmd = [sys.executable, str(py_path)]
    else:
        return {"ok": False, "stdout": "", "stderr": "Не найден bot-ok.exe или bot-ok.py"}

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008
    try:
        subprocess.Popen(cmd, cwd=base_dir, creationflags=creationflags)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}
    return {"ok": True, "stdout": "AutoCraft запущен.", "stderr": ""}


def autocraft_start(base_dir: str) -> Dict[str, Any]:
    status = collect_autocraft_status(base_dir)
    if status.get("running"):
        return {"ok": False, "stdout": "", "stderr": "AutoCraft уже запущен."}
    return _launch_autocraft(base_dir)


def autocraft_stop(base_dir: str) -> Dict[str, Any]:
    if _is_autocraft_main_module():
        _schedule_exit(0)
        return {"ok": True, "stdout": "Остановка запланирована.", "stderr": ""}

    status = collect_autocraft_status(base_dir)
    primary = status.get("primary")
    if not primary:
        return {"ok": False, "stdout": "", "stderr": "Процесс AutoCraft не найден."}
    try:
        proc = psutil.Process(int(primary["pid"]))
        proc.terminate()
        return {"ok": True, "stdout": "Процесс AutoCraft остановлен.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def autocraft_kill(base_dir: str) -> Dict[str, Any]:
    if _is_autocraft_main_module():
        _schedule_exit(9)
        return {"ok": True, "stdout": "Принудительное завершение запланировано.", "stderr": ""}

    status = collect_autocraft_status(base_dir)
    primary = status.get("primary")
    if not primary:
        return {"ok": False, "stdout": "", "stderr": "Процесс AutoCraft не найден."}
    try:
        proc = psutil.Process(int(primary["pid"]))
        proc.kill()
        return {"ok": True, "stdout": "Процесс AutoCraft завершён принудительно.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def autocraft_restart_full(base_dir: str) -> Dict[str, Any]:
    if not _is_autocraft_main_module():
        status = collect_autocraft_status(base_dir)
        if not status.get("running"):
            return _launch_autocraft(base_dir)
        stop_res = autocraft_stop(base_dir)
        if not stop_res.get("ok"):
            return stop_res
        time.sleep(1.0)
        return _launch_autocraft(base_dir)

    def _run():
        try:
            from sys_core.full_restart import full_restart

            async def _send(text: str) -> None:
                try:
                    log_dir = Path(base_dir) / "log"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    with (log_dir / "autocraft_restart.log").open("a", encoding="utf-8") as fh:
                        fh.write(text + "\n")
                except Exception:
                    pass

            asyncio.run(full_restart(_send))
        except Exception:
            _schedule_exit(42)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "stdout": "Полный перезапуск запущен.", "stderr": ""}


def _read_gui_settings(base_dir: str) -> Dict[str, Any]:
    cfg, _path, _enc = _read_config(base_dir)
    defaults = {
        "api_id": "",
        "api_hash": "",
        "local_mode": "True",
        "http_ip": "0.0.0.0",
        "http_port": "8081",
        "max_webhook_connections": "100000",
        "verbosity": "0",
        "data_dir": str(Path(base_dir) / "serverapibot" / "data"),
        "temp_dir": str(Path(base_dir) / "serverapibot" / "temp"),
        "exe_path": str(Path(base_dir) / "serverapibot" / "telegram-bot-api.exe"),
        "auto_start": "False",
        "log_max_size": "1",
        "ui_max_lines": "2000",
        "api_max_lines": "5000",
        "log_flush_ms": "200",
        "api_log_to_file": "False",
        "auto_detect_paths": "False",
    }
    settings = {k: _config_get(cfg, "gui_settings", k, defaults[k]) for k in defaults}
    return settings


def _read_credentials_settings(base_dir: str) -> Dict[str, Any]:
    cfg, _path, _enc = _read_config(base_dir)
    return {
        "token": _config_get(cfg, "credentials", "token", "").strip(),
        "pin": _config_get(cfg, "credentials", "pin", "").strip(),
        "allowed_ids": _config_get(cfg, "credentials", "allowed_ids", "").strip(),
        "address": _config_get(cfg, "telegram_api", "address", "").strip(),
        "port": _config_get(cfg, "telegram_api", "port", "").strip(),
        "use_standard_api": _to_bool(_config_get(cfg, "api_server", "use_standard_api", "False")),
    }


def _normalize_config_encoding(encoding: str) -> str:
    text = str(encoding or "").strip().lower()
    if not text:
        return "utf-8"
    if text == "utf-8-raw":
        return "utf-8"
    return text


def _write_config(base_dir: str, cfg: configparser.ConfigParser, encoding: str = "utf-8") -> tuple[bool, str]:
    cfg_path = Path(base_dir) / "config.ini"
    write_encoding = _normalize_config_encoding(encoding)
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        with cfg_path.open("w", encoding=write_encoding) as f:
            cfg.write(f)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _set_cfg_value(cfg: configparser.ConfigParser, section: str, key: str, value: str) -> None:
    if section not in cfg:
        cfg[section] = {}
    cfg[section][key] = str(value)


def get_autocraft_settings(base_dir: str) -> Dict[str, Any]:
    credentials = _read_credentials_settings(base_dir)
    gui_settings = _read_gui_settings(base_dir)
    return {
        "credentials": credentials,
        "local_api": gui_settings,
    }


def _telegram_api_processes() -> List[psutil.Process]:
    found = []
    for proc in psutil.process_iter(attrs=["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
        except Exception:
            continue
        name = (info.get("name") or "").lower()
        exe = (info.get("exe") or "").lower()
        cmdline = " ".join(info.get("cmdline") or []).lower()
        if "telegram-bot-api" in name or "telegram-bot-api" in exe or "telegram-bot-api" in cmdline:
            found.append(proc)
    return found


def autocraft_api_stop(base_dir: str) -> Dict[str, Any]:
    stopped = False
    try:
        import gui_serverapi as _api

        stop_fn = getattr(_api, "stop_server_globally", None)
        if callable(stop_fn):
            stop_fn()
            stopped = True
    except Exception:
        pass

    for proc in _telegram_api_processes():
        try:
            proc.terminate()
            stopped = True
        except Exception:
            continue

    return {
        "ok": True if stopped else False,
        "stdout": "Локальный Telegram API остановлен." if stopped else "",
        "stderr": "" if stopped else "Процесс telegram-bot-api не найден.",
    }


def autocraft_api_start(base_dir: str) -> Dict[str, Any]:
    if _telegram_api_processes():
        return {"ok": False, "stdout": "", "stderr": "Локальный Telegram API уже запущен."}

    settings = _read_gui_settings(base_dir)
    api_id = (settings.get("api_id") or "").strip()
    api_hash = (settings.get("api_hash") or "").strip()
    if not api_id or not api_hash:
        return {"ok": False, "stdout": "", "stderr": "Не задан API ID/API Hash в config.ini."}

    exe_path = Path(settings.get("exe_path") or "")
    if not exe_path.exists():
        return {"ok": False, "stdout": "", "stderr": "telegram-bot-api.exe не найден."}

    data_dir = Path(settings.get("data_dir") or exe_path.parent / "data")
    temp_dir = Path(settings.get("temp_dir") or exe_path.parent / "temp")
    data_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    args = [
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        f"--http-ip-address={settings.get('http_ip')}",
        f"--http-port={settings.get('http_port')}",
        f"--max-webhook-connections={settings.get('max_webhook_connections')}",
        f"--dir={data_dir}",
        f"--temp-dir={temp_dir}",
    ]
    local_mode = str(settings.get("local_mode", "")).lower() in ("true", "1", "yes", "on")
    if local_mode:
        args.append("--local")

    verbosity = _safe_int(settings.get("verbosity")) or 0
    if verbosity > 0:
        args.append(f"--verbosity={verbosity}")

    log_max_size = _safe_int(settings.get("log_max_size")) or 1
    args.append(f"--log-max-file-size={log_max_size * 1024 * 1024}")

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008
    try:
        subprocess.Popen([str(exe_path)] + args, cwd=str(exe_path.parent), creationflags=creationflags)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    return {"ok": True, "stdout": "Локальный Telegram API запущен.", "stderr": ""}


def autocraft_api_restart(base_dir: str) -> Dict[str, Any]:
    stop_res = autocraft_api_stop(base_dir)
    if stop_res.get("ok") or "не найден" in (stop_res.get("stderr") or "").lower():
        return autocraft_api_start(base_dir)
    return stop_res


def autocraft_bot_check(
    base_dir: str,
    token: str,
    address: str = "",
    port: str = "",
    use_standard_api: Any = False,
    timeout_sec: Any = 6,
) -> Dict[str, Any]:
    token = str(token or "").strip()
    if not _validate_token(token):
        return {"ok": False, "stdout": "", "stderr": "Некорректный токен бота."}

    use_standard = _to_bool(use_standard_api, default=False)
    host = _normalize_host(address)
    port_text = str(port or "").strip()
    timeout = _safe_float(timeout_sec) or 6.0
    timeout = min(max(timeout, 2.0), 20.0)

    if not use_standard:
        if host or port_text:
            if not host or not port_text:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Для локальной проверки укажите и адрес, и порт.",
                }
            if not _validate_host(host):
                return {"ok": False, "stdout": "", "stderr": "Некорректный адрес API."}
            parsed_port, err = _parse_int_in_range(port_text, "Порт API", 1, 65535)
            if err:
                return {"ok": False, "stdout": "", "stderr": err}
            port_text = str(parsed_port)
        else:
            use_standard = True

    if use_standard:
        check_url = f"https://api.telegram.org/bot{token}/getMe"
        source = "standard"
    else:
        check_url = f"http://{host}:{port_text}/bot{token}/getMe"
        source = "local"

    try:
        resp = requests.get(check_url, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Ошибка запроса: {exc}"}

    if not resp.ok:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"Сервер вернул HTTP {resp.status_code}.",
        }

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "stdout": "", "stderr": "Ответ сервера не JSON."}

    if not data.get("ok"):
        desc = str(data.get("description") or "API вернул ok=false.")
        return {"ok": False, "stdout": "", "stderr": desc}

    result = data.get("result") or {}
    username = str(result.get("username") or "")
    first_name = str(result.get("first_name") or "")
    bot_id = str(result.get("id") or "")
    bot_text = "@%s" % username if username else first_name or "unknown"
    return {
        "ok": True,
        "stdout": f"Проверка успешна ({source}): {bot_text} id={bot_id}",
        "stderr": "",
    }


def autocraft_bot_settings_save(
    base_dir: str,
    token: str,
    pin: str = "",
    allowed_ids: str = "",
    address: str = "",
    port: str = "",
    use_standard_api: Any = False,
) -> Dict[str, Any]:
    token = str(token or "").strip()
    pin = str(pin or "").strip()
    address = _normalize_host(address)
    port_text = str(port or "").strip()
    use_standard = _to_bool(use_standard_api, default=False)

    if not _validate_token(token):
        return {"ok": False, "stdout": "", "stderr": "Некорректный токен бота."}

    normalized_ids, ids_err = _normalize_allowed_ids(allowed_ids)
    if ids_err:
        return {"ok": False, "stdout": "", "stderr": ids_err}

    if address and not _validate_host(address):
        return {"ok": False, "stdout": "", "stderr": "Некорректный адрес API."}

    if port_text:
        parsed_port, err = _parse_int_in_range(port_text, "Порт API", 1, 65535)
        if err:
            return {"ok": False, "stdout": "", "stderr": err}
        port_text = str(parsed_port)

    cfg, _cfg_path, cfg_enc = _read_config(base_dir)
    _set_cfg_value(cfg, "credentials", "token", token)
    _set_cfg_value(cfg, "credentials", "pin", pin)
    _set_cfg_value(cfg, "credentials", "allowed_ids", normalized_ids)
    _set_cfg_value(cfg, "telegram_api", "address", address)
    _set_cfg_value(cfg, "telegram_api", "port", port_text)
    _set_cfg_value(cfg, "api_server", "use_standard_api", "true" if use_standard else "false")

    ok, err = _write_config(base_dir, cfg, cfg_enc)
    if not ok:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось сохранить config.ini: {err}"}
    return {
        "ok": True,
        "stdout": "Настройки подключения бота сохранены в config.ini.",
        "stderr": "",
    }


def autocraft_local_api_settings_save(
    base_dir: str,
    api_id: str = "",
    api_hash: str = "",
    local_mode: Any = True,
    http_ip: str = "0.0.0.0",
    http_port: Any = "8081",
    max_webhook_connections: Any = "100000",
    verbosity: Any = "0",
    data_dir: str = "",
    temp_dir: str = "",
    exe_path: str = "",
    auto_start: Any = False,
    log_max_size: Any = "1",
    ui_max_lines: Any = "2000",
    api_max_lines: Any = "5000",
    log_flush_ms: Any = "200",
    api_log_to_file: Any = False,
    auto_detect_paths: Any = False,
) -> Dict[str, Any]:
    api_id = str(api_id or "").strip()
    api_hash = str(api_hash or "").strip()
    http_ip = _normalize_host(http_ip) or "0.0.0.0"
    data_dir = str(data_dir or "").strip() or str(Path(base_dir) / "serverapibot" / "data")
    temp_dir = str(temp_dir or "").strip() or str(Path(base_dir) / "serverapibot" / "temp")
    exe_path = str(exe_path or "").strip() or str(Path(base_dir) / "serverapibot" / "telegram-bot-api.exe")

    if api_id and not api_id.isdigit():
        return {"ok": False, "stdout": "", "stderr": "API ID должен содержать только цифры."}
    if not _validate_host(http_ip):
        return {"ok": False, "stdout": "", "stderr": "Некорректный Listen IP."}

    port_num, err = _parse_int_in_range(http_port, "HTTP порт", 1, 65535)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    max_conn_num, err = _parse_int_in_range(
        max_webhook_connections,
        "Max Webhook Connections",
        1,
        1_000_000,
    )
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    verbosity_num, err = _parse_int_in_range(verbosity, "Verbosity", 0, 5)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    log_max_size_num, err = _parse_int_in_range(log_max_size, "Лимит лога (MB)", 1, 1024)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    ui_max_lines_num, err = _parse_int_in_range(ui_max_lines, "UI max lines", 200, 200000)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    api_max_lines_num, err = _parse_int_in_range(api_max_lines, "API max lines", 200, 200000)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    flush_ms_num, err = _parse_int_in_range(log_flush_ms, "Flush ms", 50, 5000)
    if err:
        return {"ok": False, "stdout": "", "stderr": err}

    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось подготовить каталоги: {exc}"}

    cfg, _cfg_path, cfg_enc = _read_config(base_dir)
    _set_cfg_value(cfg, "gui_settings", "api_id", api_id)
    _set_cfg_value(cfg, "gui_settings", "api_hash", api_hash)
    _set_cfg_value(cfg, "gui_settings", "local_mode", str(_to_bool(local_mode, default=True)))
    _set_cfg_value(cfg, "gui_settings", "http_ip", http_ip)
    _set_cfg_value(cfg, "gui_settings", "http_port", str(port_num))
    _set_cfg_value(cfg, "gui_settings", "max_webhook_connections", str(max_conn_num))
    _set_cfg_value(cfg, "gui_settings", "verbosity", str(verbosity_num))
    _set_cfg_value(cfg, "gui_settings", "data_dir", data_dir)
    _set_cfg_value(cfg, "gui_settings", "temp_dir", temp_dir)
    _set_cfg_value(cfg, "gui_settings", "exe_path", exe_path)
    _set_cfg_value(cfg, "gui_settings", "auto_start", str(_to_bool(auto_start, default=False)))
    _set_cfg_value(cfg, "gui_settings", "log_max_size", str(log_max_size_num))
    _set_cfg_value(cfg, "gui_settings", "ui_max_lines", str(ui_max_lines_num))
    _set_cfg_value(cfg, "gui_settings", "api_max_lines", str(api_max_lines_num))
    _set_cfg_value(cfg, "gui_settings", "log_flush_ms", str(flush_ms_num))
    _set_cfg_value(cfg, "gui_settings", "api_log_to_file", str(_to_bool(api_log_to_file, default=False)))
    _set_cfg_value(cfg, "gui_settings", "auto_detect_paths", str(_to_bool(auto_detect_paths, default=False)))

    ok, write_err = _write_config(base_dir, cfg, cfg_enc)
    if not ok:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось сохранить config.ini: {write_err}"}
    return {
        "ok": True,
        "stdout": "Настройки локального Telegram API сохранены.",
        "stderr": "",
    }


def autocraft_plugins_scan(base_dir: str) -> Dict[str, Any]:
    data = list_autocraft_plugins(base_dir)
    return {
        "ok": True,
        "stdout": f"Найдено плагинов: {len(data.get('items', []))}.",
        "stderr": "",
    }


def autocraft_plugins_reload(base_dir: str) -> Dict[str, Any]:
    main_mod = sys.modules.get("__main__")
    if not main_mod:
        return {"ok": False, "stdout": "", "stderr": "AutoCraft не запущен в этом процессе."}
    reload_fn = getattr(main_mod, "reload_all_plugins", None)
    bot_obj = getattr(main_mod, "current_bot", None)
    dispatcher = getattr(bot_obj, "dispatcher", None) if bot_obj else None
    if not callable(reload_fn) or dispatcher is None:
        return {"ok": False, "stdout": "", "stderr": "Нет доступа к Dispatcher для перезагрузки плагинов."}
    try:
        reload_fn(dispatcher)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}
    return {"ok": True, "stdout": "Плагины перезагружены.", "stderr": ""}


def autocraft_autorun_configure(
    base_dir: str,
    enabled: Any = False,
    start_in_tray: Any = False,
    method: str = "startup",
) -> Dict[str, Any]:
    try:
        from windows_startup import (
            _is_windows,
            save_startup_method,
            save_startup_settings,
            apply_autorun_selected,
        )
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Доступно только на Windows."}

    enabled_flag = _to_bool(enabled, default=False)
    method_value = _normalize_startup_method(method, fallback="startup")
    if method_value not in ("auto", "startup", "registry", "schtask"):
        method_value = "startup"
    tray_flag = _to_bool(start_in_tray, default=False) if enabled_flag else False

    try:
        save_startup_method(method_value)
        save_startup_settings(enabled_flag, tray_flag)
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": f"Не удалось сохранить startup-настройки: {exc}"}

    ok = bool(apply_autorun_selected(enabled_flag, tray_flag, method_value))
    if not ok:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "Не удалось применить настройки автозапуска в системе.",
        }

    status = _collect_autorun_status(base_dir)
    state_text = "включен" if status.get("enabled") else "выключен"
    method_label = status.get("method_label") or _startup_method_label(method_value)
    tray_text = "да" if status.get("start_in_tray") else "нет"
    return {
        "ok": True,
        "stdout": f"Автозапуск {state_text}. Способ: {method_label}. Запуск в трее: {tray_text}.",
        "stderr": "",
    }


def autocraft_autorun_enable(base_dir: str) -> Dict[str, Any]:
    try:
        from windows_startup import load_startup_full, _is_windows
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Доступно только на Windows."}
    _enabled, start_in_tray, method = load_startup_full()
    return autocraft_autorun_configure(
        base_dir=base_dir,
        enabled=True,
        start_in_tray=bool(start_in_tray),
        method=method,
    )


def autocraft_autorun_disable(base_dir: str) -> Dict[str, Any]:
    try:
        from windows_startup import load_startup_full, _is_windows
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}
    if not _is_windows():
        return {"ok": False, "stdout": "", "stderr": "Доступно только на Windows."}
    _, start_in_tray, method = load_startup_full()
    return autocraft_autorun_configure(
        base_dir=base_dir,
        enabled=False,
        start_in_tray=bool(start_in_tray),
        method=method,
    )
