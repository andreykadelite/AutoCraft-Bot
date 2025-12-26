# -*- coding: utf-8 -*-
"""
logging_system.py

Единая точка правки логирования для bot-ok.py.

Здесь собраны:
- буферы логов (до авторизации/до старта GUI),
- аудит доступа,
- сбор "важных событий" и дайджест,
- настройка root-логирования,
- файловые логи (bot/kom/plugin/error/debug),
- доставка логов в GUI через сигнал log_emitter.log_message,
- простая трассировка (sys.settrace) при включенном дебаге.

Важно: модуль не импортирует aiogram и не требует знания структуры бота.
"""

from __future__ import annotations

import configparser
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

# -------------------------------
# Public state/buffers
# -------------------------------

PENDING_TG_MAX = 1000
AUTH_AUDIT_MAX = 200
IMPORTANT_EVENTS_MAX = 200

pending_log_messages: List[str] = []
pending_tg_logs: List[str] = []

auth_audit_events: List[str] = []
important_events: List[str] = []

APP_START_TS = time.time()

# Debug flag (shared by debug logger + trace filter)
debug_enabled: bool = False

# -------------------------------
# Internal runtime bindings (GUI)
# -------------------------------

_log_emitter = None  # object with attribute log_message.emit(str)
_gui_ready_getter: Callable[[], bool] = lambda: False

# -------------------------------
# File paths + loggers (populated by init_app_logging)
# -------------------------------

_base_dir: Optional[str] = None
current_time_str: Optional[str] = None

bot_log_file: Optional[str] = None
com_log_file: Optional[str] = None
plugin_log_file: Optional[str] = None
error_log_file: Optional[str] = None
debug_log_file: Optional[str] = None

formatter: Optional[logging.Formatter] = None

bot_logger: Optional[logging.Logger] = None
com_logger: Optional[logging.Logger] = None
plugin_logger: Optional[logging.Logger] = None
error_logger: Optional[logging.Logger] = None
debug_logger: Optional[logging.Logger] = None


# -------------------------------
# Helpers: safe formatting
# -------------------------------

def set_debug_enabled(value: bool) -> None:
    global debug_enabled
    debug_enabled = bool(value)


def bind_gui(
    *,
    log_emitter,
    pending_log_messages_ref: Optional[List[str]] = None,
    pending_tg_logs_ref: Optional[List[str]] = None,
    gui_ready_getter: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Привязка лог-системы к GUI и внешним буферам.
    Вызывать один раз из bot-ok.py после создания log_emitter.
    """
    global _log_emitter, _gui_ready_getter, pending_log_messages, pending_tg_logs
    _log_emitter = log_emitter
    if pending_log_messages_ref is not None:
        pending_log_messages = pending_log_messages_ref
    if pending_tg_logs_ref is not None:
        pending_tg_logs = pending_tg_logs_ref
    if gui_ready_getter is not None:
        _gui_ready_getter = gui_ready_getter


def _safe_one_line(s: str) -> str:
    return " ".join(str(s).split()).strip()


def _chunk_lines(lines: List[str], max_chars: int = 3500) -> List[str]:
    chunks: List[str] = []
    buf = ""
    for line in lines:
        part = line + "\n"
        if len(buf) + len(part) > max_chars:
            if buf:
                chunks.append(buf.rstrip())
            buf = part
        else:
            buf += part
    if buf:
        chunks.append(buf.rstrip())
    return chunks


def _format_uptime(start_ts: float) -> str:
    try:
        delta = int(time.time() - start_ts)
    except Exception:
        return "неизвестно"
    h = delta // 3600
    m = (delta % 3600) // 60
    s = delta % 60
    if h:
        return f"{h}ч {m:02d}м {s:02d}с"
    if m:
        return f"{m}м {s:02d}с"
    return f"{s}с"


def _prepare_log_tail(lines: List[str], max_lines: int = 40) -> List[str]:
    if not lines:
        return []
    return lines[-max_lines:]


# -------------------------------
# Exceptions hook
# -------------------------------

def install_excepthook(logger: Optional[logging.Logger] = None) -> None:
    """
    Глобальный обработчик необработанных исключений.
    """
    def _hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        try:
            (logger or logging.getLogger()).exception(
                "Необработанное исключение",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
        except Exception:
            # Последний шанс: не падаем из-за логирования
            try:
                print("Необработанное исключение:", exc_value, file=sys.stderr)
            except Exception:
                pass
        sys.exit(1)

    sys.excepthook = _hook


# -------------------------------
# Root logging bootstrap
# -------------------------------

def configure_bootstrap_logging(
    *,
    base_dir: str,
    config_path: str,
    config_section: str = "credentials",
    stdout=None,
) -> tuple[bool, logging.Logger]:
    """
    Базовая настройка logging.basicConfig до старта бота.
    Возвращает (debug_enabled, root_logger).
    """
    out = stdout or sys.stdout
    try:
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        dbg = config.getboolean(config_section, "debug", fallback=False)
    except Exception:
        dbg = False

    handlers: List[logging.Handler] = []
    if dbg:
        try:
            log_dir = Path(base_dir) / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            exe_name = Path(sys.executable).stem
            log_file = log_dir / f"{exe_name}.log"
            handlers.append(logging.FileHandler(str(log_file), mode="w", encoding="utf-8"))
        except Exception:
            # если не смогли создать файл, идём в stdout
            pass
        handlers.append(logging.StreamHandler(out))
        level = logging.DEBUG
    else:
        handlers.append(logging.StreamHandler(out))
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d — %(message)s",
        handlers=handlers,
    )

    set_debug_enabled(dbg)
    root_logger = logging.getLogger()
    install_excepthook(root_logger)
    return dbg, root_logger


# -------------------------------
# Audit / important events
# -------------------------------

def add_auth_audit(event: str) -> None:
    try:
        line = _safe_one_line(event)
        auth_audit_events.append(line)
        if len(auth_audit_events) > AUTH_AUDIT_MAX:
            del auth_audit_events[: len(auth_audit_events) - AUTH_AUDIT_MAX]
    except Exception:
        pass


def add_important_event(event: str) -> None:
    try:
        line = _safe_one_line(event)
        important_events.append(line)
        if len(important_events) > IMPORTANT_EVENTS_MAX:
            del important_events[: len(important_events) - IMPORTANT_EVENTS_MAX]
    except Exception:
        pass


_IMPORTANT_KEYWORDS = (
    "ошибка", "исключение", "exception", "error", "critical",
    "упал", "перезапуск", "watchdog", "отключ", "не удалось",
    "unauthorized", "forbidden", "timeout", "таймаут",
)

def _maybe_track_important(msg: str) -> None:
    try:
        low = msg.lower()
        if any(k in low for k in _IMPORTANT_KEYWORDS):
            add_important_event(msg)
    except Exception:
        pass


# -------------------------------
# GUI handler
# -------------------------------

class SignalHandler(logging.Handler):
    """
    Дублирует лог-сообщения в GUI и в буферы для Telegram.
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)

            # Собираем "важные события"
            _maybe_track_important(msg)

            # GUI
            try:
                if _log_emitter is not None:
                    _log_emitter.log_message.emit(msg)
            except Exception:
                pass

            # Буфер до готовности GUI
            try:
                if not _gui_ready_getter():
                    pending_log_messages.append(msg)
            except Exception:
                pass

            # Буфер для TG дайджеста
            if record.levelno >= logging.INFO:
                try:
                    pending_tg_logs.append(msg)
                    if len(pending_tg_logs) > PENDING_TG_MAX:
                        del pending_tg_logs[: len(pending_tg_logs) - PENDING_TG_MAX]
                except Exception:
                    pass

        except Exception:
            # Никогда не валим основной процесс из-за логирования
            pass


# -------------------------------
# Per-component loggers
# -------------------------------

def create_logger(name: str, file_path: str, level=logging.INFO) -> logging.Logger:
    """
    Создает/переинициализирует logger с FileHandler + SignalHandler.
    """
    global formatter
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if formatter is None:
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Чтобы не плодить хендлеры при повторных init
    logger.handlers.clear()

    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = SignalHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def init_app_logging(base_dir: str) -> None:
    """
    Инициализация файловых логов приложения.
    Вызывать после определения base_dir.
    """
    global _base_dir, current_time_str
    global bot_log_file, com_log_file, plugin_log_file, error_log_file, debug_log_file
    global bot_logger, com_logger, plugin_logger, error_logger, debug_logger

    _base_dir = base_dir

    # timestamp один на запуск
    if not current_time_str:
        current_time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    log_dir = Path(base_dir) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    bot_log_file = str(log_dir / f"log_{current_time_str}_bot.txt")
    com_log_file = str(log_dir / f"log_{current_time_str}_kom.txt")
    plugin_log_file = str(log_dir / f"log_{current_time_str}_plagin.txt")
    error_log_file = str(log_dir / f"log_{current_time_str}_oshibka.txt")
    # Оставляем "debаг" как было (для совместимости)
    debug_log_file = str(log_dir / f"log_{current_time_str}_debаг.txt")

    bot_logger = create_logger("BOT", bot_log_file, logging.INFO)
    com_logger = create_logger("KOM", com_log_file, logging.INFO)
    plugin_logger = create_logger("PLAGIN", plugin_log_file, logging.INFO)
    error_logger = create_logger("OSHIBKA", error_log_file, logging.ERROR)
    debug_logger = create_logger("DEBUG", debug_log_file, logging.DEBUG)


# -------------------------------
# Error mapping + write helpers
# -------------------------------

_ERROR_DESCRIPTIONS = {
    "aiogram.utils.exceptions": "Ошибка в aiogram (Telegram API)",
    "NetworkError": "Ошибка сети (NetworkError)",
    "RetryAfter": "Слишком много запросов (RetryAfter)",
    "Unauthorized": "Неверный токен Telegram-бота (Unauthorized)",
    "TelegramAPIError": "Ошибка Telegram API",
    "TimeoutError": "Таймаут (TimeoutError)",
    "ConnectionError": "Ошибка соединения (ConnectionError)",
    "KeyboardInterrupt": "Прерывание пользователем (KeyboardInterrupt)",
    "MemoryError": "Недостаточно памяти (MemoryError)",
    "OSError": "Ошибка ОС (OSError)",
    "FileNotFoundError": "Файл не найден (FileNotFoundError)",
    "PermissionError": "Нет доступа (PermissionError)",
    "ValueError": "Ошибка значения (ValueError)",
    "TypeError": "Ошибка типа (TypeError)",
    "AttributeError": "Ошибка атрибута (AttributeError)",
    "ImportError": "Ошибка импорта (ImportError)",
    "ZeroDivisionError": "Деление на ноль (ZeroDivisionError)",
}


def get_error_description(error_text: str) -> str:
    for key, desc in _ERROR_DESCRIPTIONS.items():
        if key.lower() in error_text.lower():
            return desc
    return "Неизвестная ошибка"


def write_error_log(entry: str) -> None:
    if error_logger is None:
        return
    desc = get_error_description(entry)
    error_logger.error(f"{desc}: {entry}")


def write_bot_log(entry: str, is_error: bool = False) -> None:
    if bot_logger is None:
        return
    _maybe_track_important(entry)
    if is_error or "[ОШИБКА]" in entry:
        write_error_log(entry)
    bot_logger.info(entry)


def write_com_log(entry: str, is_error: bool = False) -> None:
    if com_logger is None:
        return
    _maybe_track_important(entry)
    if is_error or "[ОШИБКА]" in entry:
        write_error_log(entry)
    com_logger.info(entry)


def write_plugin_log(entry: str, is_error: bool = False) -> None:
    if plugin_logger is None:
        return
    _maybe_track_important(entry)
    if is_error or "[ОШИБКА]" in entry:
        write_error_log(entry)
    plugin_logger.info(entry)


def write_debug_log(entry: str) -> None:
    """
    Пишет в debug лог только если debug_enabled == True.
    """
    if not debug_enabled or debug_logger is None:
        return
    try:
        debug_logger.debug(entry)
    except Exception:
        pass


# -------------------------------
# Tracing (sys.settrace)
# -------------------------------

_TRACE_IGNORE_SUBSTRINGS = (
    os.sep + "venv" + os.sep,
    os.sep + "site-packages" + os.sep,
    os.sep + "aiogram" + os.sep,
    os.sep + "asyncio" + os.sep,
    os.sep + "importlib" + os.sep,
    os.sep + "logging" + os.sep,
    os.sep + "threading" + os.sep,
    os.sep + "encodings" + os.sep,
    os.sep + "PyQt5" + os.sep,
)

def trace_calls(frame, event, arg):
    """
    Лёгкая трассировка для разработчика.

    Важно:
    - sys.settrace() вызывает колбэк НА КАЖДОЙ строке (event='line') и это очень тормозит.
    - Поэтому мы логируем только 'call' и 'return' (остальное игнорируем).
    - По умолчанию в проекте трассировка НЕ включается автоматически, только вручную.
    """
    if not debug_enabled:
        return trace_calls

    # Игнорируем супер-частые события (особенно 'line', иначе будет адская просадка по скорости)
    if event not in ("call", "return"):
        return trace_calls

    try:
        filename = frame.f_code.co_filename or ""
        if _base_dir and not filename.startswith(_base_dir):
            return trace_calls
        if any(x in filename for x in _TRACE_IGNORE_SUBSTRINGS):
            return trace_calls
        func_name = frame.f_code.co_name
        if func_name.startswith("<"):
            return trace_calls
        lineno = frame.f_lineno
        write_debug_log(f"[TRACE] {event.upper()} {func_name} (line {lineno}) in {filename}")
    except Exception:
        pass
    return trace_calls
# -------------------------------
# Post-auth report to Telegram
# -------------------------------

async def send_post_auth_report(message, *, debug_enabled: bool, connection_summary: str) -> None:
    """
    Отправляет отчет после успешной авторизации:
    - при debug: хвост логов до авторизации
    - без debug: краткая сводка (подключение, дебаг, неудачные попытки)
    """
    try:
        uptime = _format_uptime(APP_START_TS)

        # Базовое сообщение
        await message.answer(
            "✅ Авторизация успешна.\n"
            f"⏱ Аптайм процесса: {uptime}\n"
            f"🔧 Дебаг: {'включен' if debug_enabled else 'выключен'}"
        )

        # Дайджест "важных событий" до авторизации
        if important_events:
            tail = _prepare_log_tail(important_events, max_lines=20)
            chunks = _chunk_lines(["📌 Важное до авторизации:"] + [f"• {x}" for x in tail])
            for chunk in chunks:
                await message.answer(chunk)

        # Режим DEBUG: сливаем хвост накопленных логов в TG
        if debug_enabled:
            if pending_tg_logs:
                tail = _prepare_log_tail(pending_tg_logs, max_lines=60)
                chunks = _chunk_lines(["🧾 Логи до авторизации (хвост):"] + tail)
                for chunk in chunks:
                    await message.answer(chunk)
            else:
                await message.answer("🧾 Логи до авторизации: пусто.")
        else:
            # Режим без DEBUG: короткая сводка подключения + попытки входа
            summary_lines = [
                "📡 Подключение:",
                f"{connection_summary}",
            ]
            fails = len(auth_audit_events)
            summary_lines.append(f"🔐 Неверных попыток авторизации: {fails}")

            chunks = _chunk_lines(summary_lines)
            for chunk in chunks:
                await message.answer(chunk)

            if auth_audit_events:
                tail = _prepare_log_tail(auth_audit_events, max_lines=15)
                chunks = _chunk_lines(["🧯 Последние неудачные попытки:"] + tail)
                for chunk in chunks:
                    await message.answer(chunk)

        # Чистим буферы после отчета
        pending_tg_logs.clear()
        auth_audit_events.clear()

    except Exception as e:
        # Не ломаем основную логику, но в debug можно записать
        write_debug_log(f"[send_post_auth_report] error: {e!r}")
