# -*- coding: utf-8 -*-
"""
sys_core/full_restart.py

Единый "полный перезапуск" (watchdog-режим), вынесенный из modulpsw.py.

Задача:
- Логика перезапуска живёт в одном месте (в этом файле).
- Вызывается из любых модулей (aiogram-хэндлеров и т.п.).
- Работает как из исходников, так и из EXE (Nuitka).

ВНИМАНИЕ:
Этот перезапуск завершает процесс через os._exit(exit_code). После вызова функция не возвращается.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from typing import Awaitable, Callable, Optional


# ----------------------------
# Вспомогательные функции логирования
# ----------------------------

def _ts() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_line(buf: list[str], text: str) -> None:
    line = f"{_ts()} [restart] {text}"
    try:
        logging.info(line)
    except Exception:
        pass
    buf.append(line)


# ----------------------------
# Определение путей (PY / EXE)
# ----------------------------

def _guess_base_dir() -> str:
    """Определяем базовую папку проекта (рядом с exe/главным скриптом)."""
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
    except Exception:
        pass

    # Если этот файл лежит в папке sys_core или moduls, базовая папка на уровень выше
    try:
        here = os.path.abspath(__file__)
        parent = os.path.dirname(here)
        if os.path.basename(parent).lower() in ("moduls", "sys_core"):
            return os.path.dirname(parent)
    except Exception:
        pass

    # Fallback: текущая рабочая папка (часто бот делает os.chdir(base_dir))
    try:
        return os.getcwd()
    except Exception:
        return os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.path.abspath(os.curdir)


def _ensure_dir_on_syspath(path: str) -> None:
    """Добавляет путь в начало sys.path (если существует)."""
    try:
        if path and os.path.isdir(path):
            if path in sys.path:
                sys.path.remove(path)
            sys.path.insert(0, path)
            try:
                import importlib
                importlib.invalidate_caches()
            except Exception:
                pass
    except Exception:
        pass


def _ensure_project_paths() -> str:
    """
    Гарантируем, что базовая папка проекта и типовые подпапки присутствуют в sys.path.
    Это важно и для исходников, и для EXE (Nuitka onefile/standalone).
    """
    base_dir = _guess_base_dir()
    _ensure_dir_on_syspath(base_dir)

    # Частые варианты структуры в проекте
    for rel in ("src", os.path.join("src", "moduls"), os.path.join("src", "gui_win"), "gui_win", "moduls"):
        _ensure_dir_on_syspath(os.path.join(base_dir, rel))

    return base_dir


def _ensure_moduls_on_syspath() -> str:
    """Гарантируем, что папка moduls стоит в начале sys.path для корректного импорта."""
    base_dir = _ensure_project_paths()
    moduls_dir = os.path.join(base_dir, "moduls")
    _ensure_dir_on_syspath(moduls_dir)
    return moduls_dir


# ----------------------------
# Управляемый браузер (nostartrunmodulbrowsrem)
# ----------------------------

def _get_browser_ctrl():
    """Возвращает CTRL из nostartrunmodulbrowsrem, строго из папки moduls (если доступно)."""
    # 1) Если модуль уже загружен где-то в процессе, берём именно его (важно для сохранения состояния!)
    for name in ("nostartrunmodulbrowsrem", "moduls.nostartrunmodulbrowsrem"):
        try:
            m = sys.modules.get(name)
            if m is not None:
                ctrl = getattr(m, "CTRL", None)
                if ctrl is not None:
                    return ctrl
        except Exception:
            pass

    # 2) Иначе поднимаем moduls в sys.path и импортируем по имени
    _ensure_moduls_on_syspath()
    try:
        import importlib
        m = importlib.import_module("nostartrunmodulbrowsrem")
        return getattr(m, "CTRL", None)
    except Exception:
        return None


# ----------------------------
# Отправка логов (в несколько сообщений)
# ----------------------------

SendFunc = Callable[[str], Awaitable[None]]


async def _send_log_chunks(send: SendFunc, buf: list[str]) -> None:
    """Отправляет лог в несколько сообщений, чтобы не упереться в лимиты Telegram."""
    if not buf:
        return

    max_len = 3500
    block = ""
    for line in buf:
        add = ("\n" if block else "") + line
        if len(block) + len(add) > max_len:
            try:
                await send(f"```log\n{block}\n```")
            except Exception:
                pass
            block = line
        else:
            block += add

    if block:
        try:
            await send(f"```log\n{block}\n```")
        except Exception:
            pass


# ----------------------------
# Основной сценарий полного перезапуска (watchdog mode)
# ----------------------------

async def full_restart(
    send: SendFunc,
    *,
    exit_code: int = 42,
    close_managed_browser: bool = True,
    stop_local_bot_api: bool = True,
) -> None:
    """
    Полный перезапуск в watchdog-режиме:
    1) Собрать подробный лог.
    2) Мягко закрыть управляемый браузер (не мешая доставке сообщений).
    3) Отправить логи.
    4) Остановить локальный Telegram Bot API сервер (если используется).
    5) Завершить процесс os._exit(exit_code), watchdog поднимет новый.

    send: асинхронная функция отправки текста (например message.answer).
    """
    buf: list[str] = []
    try:
        await send("Запускаю полный перезапуск… Сначала пришлю отчёт, потом выключу локальный API-сервер.")
    except Exception:
        # даже если не смогли сообщить, всё равно продолжаем
        pass

    # БАЗОВАЯ СВОДКА О ПРОЦЕССЕ
    try:
        _append_line(buf, f"cwd={os.getcwd()}")
        _append_line(buf, f"sys.executable={sys.executable}")
        _append_line(buf, f"sys.argv={sys.argv}")
        _append_line(buf, f"frozen={getattr(sys, 'frozen', False)}")
        _append_line(buf, f"NUITKA_ONEFILE_PARENT={os.environ.get('NUITKA_ONEFILE_PARENT')}")
        _append_line(buf, f"--child in argv={('--child' in sys.argv)} (эвристика watchdog)")
    except Exception as e:
        _append_line(buf, f"Ошибка при сборе сводки о процессе: {e!r}")

    # МЯГКО ЗАКРОЕМ УПРАВЛЯЕМЫЙ БРАУЗЕР (НЕ МЕШАЕТ ОТПРАВКЕ СООБЩЕНИЙ)
    if close_managed_browser:
        try:
            BROWSER_CTRL = _get_browser_ctrl()  # CTRL из moduls/nostartrunmodulbrowsrem.py
            selected = None
            try:
                selected = BROWSER_CTRL.get_selected()
                _append_line(buf, f"Управляемый браузер выбран: {selected!r}")
            except Exception as e_sel:
                _append_line(buf, f"Не удалось получить выбранный браузер: {e_sel!r}")

            if selected is not None:
                try:
                    await asyncio.wait_for(BROWSER_CTRL.quit(selected), timeout=5.0)
                    _append_line(buf, "Сигнал на закрытие браузера отправлен и выполнен.")
                except Exception as e_quit:
                    _append_line(buf, f"Ошибка закрытия браузера (таймаут/исключение): {e_quit!r}")
            else:
                _append_line(buf, "Управляемый браузер не обнаружен/не выбран — пропускаем закрытие.")
        except ModuleNotFoundError:
            _append_line(buf, "Модуль управления браузером (modulbrowsrem) не найден — пропускаем закрытие.")
        except Exception as e:
            _append_line(buf, f"Неожиданная ошибка при работе с браузером: {e!r}")

    # ДОПОЛНИТЕЛЬНО: ИНФО О ПОТОКАХ
    try:
        alive_threads = [t.name for t in threading.enumerate() if t.is_alive()]
        _append_line(buf, f"Активные потоки на момент перезапуска: {alive_threads}")
    except Exception as e:
        _append_line(buf, f"Не удалось получить список потоков: {e!r}")

    # 3) СНАЧАЛА ОТПРАВЛЯЕМ ЛОГИ
    await _send_log_chunks(send, buf)
    try:
        await send("Отчёт отправлен. Отключаю локальный API-сервер…")
    except Exception:
        # если не смогли отправить это сообщение — не критично, логи уже ушли
        pass

    # 4) ТЕПЕРЬ ОСТАНАВЛИВАЕМ ЛОКАЛЬНЫЙ API-СЕРВЕР И ЗАКРЫВАЕМ ЕГО ОКНО
    if stop_local_bot_api:
        srv_buf: list[str] = []

        def srv_line(text: str) -> None:
            _append_line(srv_buf, text)

        try:
            # gui_serverapi может жить в разных местах, поэтому заранее подстрахуемся путями
            _ensure_project_paths()
            import gui_serverapi as _api  # type: ignore

            # Состояние сервера (если модуль даёт индикатор)
            running_flag = None
            for probe in ("is_server_running", "server_is_running", "is_running", "running"):
                try:
                    attr = getattr(_api, probe, None)
                    if attr is None:
                        continue
                    running_flag = attr() if callable(attr) else bool(attr)
                    break
                except Exception:
                    pass

            if running_flag is None:
                srv_line("Состояние локального API-сервера: определить не удалось (нет явного индикатора).")
            else:
                srv_line(f"Состояние локального API-сервера до остановки: {'запущен' if running_flag else 'остановлен'}.")

            # Остановка серверной части
            stopped = False
            for stop_name in ("stop_server_globally", "stop_server", "shutdown"):
                try:
                    stop_fn = getattr(_api, stop_name, None)
                    if stop_fn:
                        stop_fn()
                        stopped = True
                        srv_line(f"Вызвана функция остановки сервера: {_api.__name__}.{stop_name}()")
                        break
                except Exception as e_stop:
                    srv_line(f"Ошибка при вызове {_api.__name__}.{stop_name}(): {e_stop!r}")

            if not stopped:
                srv_line("Подходящая функция остановки API-сервера не найдена — возможно, сервер не поднимался.")

        except ModuleNotFoundError:
            srv_line("Модуль gui_serverapi не найден — сервер, вероятно, не поднимался.")
        except Exception as e:
            srv_line(f"Неожиданная ошибка при остановке API-сервера: {e!r}")

        # Попытка закрыть окно процесса telegram-bot-api.exe (Windows)
        if os.name == "nt":
            for name in ("telegram-bot-api.exe", "telegram-bot-api"):
                try:
                    # Сначала мягко (без /F)
                    r1 = subprocess.run(["taskkill", "/IM", name, "/T"], capture_output=True, text=True)
                    if r1.returncode != 0:
                        # Если не получилось — форсированно
                        r2 = subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True, text=True)
                        srv_line(f"taskkill {name}: soft_rc={r1.returncode} hard_rc={r2.returncode}")
                    else:
                        srv_line(f"taskkill {name}: закрыт мягко (rc=0)")
                except Exception as e:
                    srv_line(f"taskkill {name} вызвал исключение: {e!r}")

        # Отправим краткий хвостик после выключения сервера (если ещё удастся)
        try:
            await _send_log_chunks(send, srv_buf)
        except Exception:
            # Возможно, к этому моменту Bot API уже недоступен — это нормально.
            pass

    # ФИНАЛЬНОЕ СООБЩЕНИЕ (если возможно)
    try:
        await send("Сервер остановлен. Перезапускаюсь…")
    except Exception:
        pass

    # Небольшая пауза на закрытие окна сервера
    try:
        await asyncio.sleep(0.8)
    except Exception:
        pass

    # 5) СИГНАЛ WATCHDOG'У
    # ВАЖНО: os._exit НЕЛЬЗЯ ОТКЛАДЫВАТЬ через loop.call_later(),
    # потому что full_restart() часто запускается через asyncio.run() в отдельном потоке.
    # В этом случае event loop закрывается сразу после выхода из корутины,
    # и отложенный callback просто не успевает выполниться.
    os._exit(exit_code)


# ----------------------------
# Удобные обёртки для aiogram
# ----------------------------

async def full_restart_from_message(message, **kwargs) -> None:
    """
    Обёртка для aiogram message:
        await full_restart_from_message(message)

    kwargs прокидываются в full_restart(...).
    """
    try:
        # локальный импорт, чтобы sys_core оставался полезным и вне aiogram-контекста
        from aiogram import types  # type: ignore
        assert isinstance(message, types.Message)
    except Exception:
        # не валим, просто пытаемся работать как есть
        pass

    async def _send(text: str) -> None:
        await message.answer(text, parse_mode="Markdown")

    await full_restart(_send, **kwargs)


async def full_restart_via_bot(bot, chat_id: int, **kwargs) -> None:
    """
    Обёртка, если у тебя нет message, но есть bot + chat_id:
        await full_restart_via_bot(bot, chat_id)
    """
    async def _send(text: str) -> None:
        await bot.send_message(chat_id, text, parse_mode="Markdown")

    await full_restart(_send, **kwargs)
