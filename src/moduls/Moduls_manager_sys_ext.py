import os
import sys
import time
import threading
import importlib
import asyncio
import pkgutil
import configparser
from aiogram import Dispatcher

"""
Системный менеджер модулей, лежащий в папке "moduls".

Как работает:
- просто кладём сюда скрипты *.py и НИЧЕГО не меняем в менеджерах;
- если имя файла начинается с "startrun", модуль:
    * импортируется сразу при вызове register_handlers();
    * если внутри есть функция register_handlers(dp) — она вызывается;
- если имя файла начинается с "nostartrun", модуль:
    * импортируется только после авторизации пользователя;
    * если есть register_handlers(dp) — вызывается;
- все результаты импорта (и startrun, и nostartrun) собираются в один общий отчёт;
  при включённом debug этот отчёт отправляется в Telegram после авторизации.

Чтобы Nuitka подхватывал все модули из папки moduls, в батнике сборки добавь:
    --include-plugin-directory=moduls
"""

# ---------- Глобальные переменные ----------

_import_results_lock = threading.Lock()
_import_results: list[tuple[str, bool, Exception | None]] | None = None

_startrun_modules: list[str] = []
_nostartrun_modules: list[str] = []


# ---------- Вспомогательные функции для хранения результатов ----------

def _set_import_results_list(lst: list[tuple[str, bool, Exception | None]] | None) -> None:
    global _import_results
    with _import_results_lock:
        _import_results = lst


def _get_import_results_list() -> list[tuple[str, bool, Exception | None]] | None:
    with _import_results_lock:
        return _import_results


def _log_import_result(module_name: str, success: bool, error: Exception | None = None) -> None:
    """
    Записывает результат загрузки модуля в общий список (если он активен).
    """
    results = _get_import_results_list()
    if results is not None:
        results.append((module_name, success, error))


# ---------- Debug-флаг ----------

def is_debug_enabled() -> bool:
    """
    Определяем, включён ли debug-режим.

    Приоритетно:
    1) Ищем в __main__ атрибуты: DEBUG / debug / debug_mode.
    2) Если в __main__ есть config (ConfigParser) — пробуем найти в нём debug.
    3) Пытаемся прочитать config.ini рядом с base_dir или рядом с этим файлом.
    """
    base_dir = None
    try:
        import __main__
        # Явные флаги в __main__
        for attr in ("DEBUG", "debug", "debug_mode"):
            if hasattr(__main__, attr):
                return bool(getattr(__main__, attr))

        # Конфиг, который, возможно, уже прочитан в main
        cfg = getattr(__main__, "config", None)
        if cfg is not None:
            try:
                for section in cfg.sections():
                    if cfg.has_option(section, "debug"):
                        return cfg.getboolean(section, "debug", fallback=False)
            except Exception:
                pass

        base_dir = getattr(__main__, "base_dir", None)
    except Exception:
        base_dir = None

    parser = configparser.ConfigParser()
    config_paths: list[str] = []

    if base_dir:
        config_paths.append(os.path.join(base_dir, "config.ini"))

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        config_paths.append(os.path.join(here, "config.ini"))
    except Exception:
        pass

    for path in config_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            parser.read(path, encoding="utf-8")
            for section in parser.sections():
                if parser.has_option(section, "debug"):
                    return parser.getboolean(section, "debug", fallback=False)
        except Exception:
            continue

    return False


# ---------- Авторизация ----------

def check_auth() -> bool:
    """
    Считаем, что пользователь авторизован, если в __main__ есть непустой authorized_users.
    """
    try:
        from __main__ import authorized_users  # type: ignore[attr-defined]
        return bool(authorized_users)
    except Exception:
        return False


def wait_for_bot_loop(dp: Dispatcher) -> None:
    """
    Ждём, пока у бота появится loop (актуально при старте).
    """
    while not hasattr(dp.bot, "loop") or dp.bot.loop is None:
        time.sleep(0.5)


# ---------- Работа с модулями из папки moduls ----------

def _ensure_own_dir_in_sys_path() -> str:
    """
    Добавляем папку, где лежит этот файл, и её родителя в sys.path,
    чтобы importlib.import_module мог находить и пакет "moduls.*",
    и соседние файлы как отдельные модули.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        here = os.getcwd()

    parent = os.path.dirname(here) or here

    # Родительский каталог нужен для импорта "moduls.xxx"
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)

    # А сама папка — для прямого импорта "xxx"
    if here not in sys.path:
        sys.path.insert(0, here)

    return here


def _discover_plugin_modules() -> tuple[list[str], list[str]]:
    """
    Находим в папке, где лежит этот файл, все модули:
      startrun*.py   -> запускаются сразу (до авторизации)
      nostartrun*.py -> запускаются после авторизации
    """
    global _startrun_modules, _nostartrun_modules

    if _startrun_modules or _nostartrun_modules:
        return _startrun_modules, _nostartrun_modules

    here = _ensure_own_dir_in_sys_path()

    module_names: list[str] = []
    try:
        # Рекомендуемый паттерн (в том числе для Nuitka):
        # ищем модули в конкретной директории
        for _finder, name, ispkg in pkgutil.iter_modules([here]):
            if ispkg:
                continue
            # Не трогаем сам менеджер
            this_module_name = os.path.splitext(os.path.basename(__file__))[0]
            if name == this_module_name:
                continue
            module_names.append(name)
    except Exception:
        module_names = []

    startrun: list[str] = []
    nostartrun: list[str] = []

    for name in module_names:
        lname = name.lower()
        if lname.startswith("startrun"):
            startrun.append(name)
        elif lname.startswith("nostartrun"):
            nostartrun.append(name)

    _startrun_modules = sorted(startrun)
    _nostartrun_modules = sorted(nostartrun)
    return _startrun_modules, _nostartrun_modules


def _import_module(dp: Dispatcher, short_name: str) -> None:
    """
    Импортирует модуль по имени файла без .py из этой же папки.

    Порядок попыток:
    1) как часть пакета "moduls.<имя>" — так Nuitka включает модули при
       использовании "--include-package=moduls";
    2) как обычный модуль "<имя>" — для запуска исходников без сборки.
    Если в модуле есть register_handlers(dp), вызывает её.
    """
    _ensure_own_dir_in_sys_path()

    candidates: list[str] = [
        f"moduls.{short_name}",
        short_name,
    ]

    last_exc: Exception | None = None
    used_name: str = candidates[-1]

    for module_name in candidates:
        try:
            mod = importlib.import_module(module_name)
            used_name = module_name
            if hasattr(mod, "register_handlers"):
                # Автоматически вызываем register_handlers для найденных модулей
                mod.register_handlers(dp)  # type: ignore[call-arg]
            _log_import_result(used_name, True, None)
            return
        except Exception as e:
            last_exc = e

    # Если ни один вариант не сработал — логируем последнюю ошибку
    _log_import_result(used_name, False, last_exc if last_exc is not None else Exception("Не удалось импортировать модуль"))


def _import_modules_list(dp: Dispatcher, names: list[str]) -> None:
    for name in names:
        _import_module(dp, name)


# ---------- Отправка debug-отчёта ----------

async def _send_debug_report(dp: Dispatcher, results: list[tuple[str, bool, Exception | None]]) -> None:
    """
    Отправляет в Telegram сводку по загрузке модулей, если включён debug.
    В отчёт попадают и startrun*, и nostartrun* модули.
    """
    try:
        if not is_debug_enabled():
            return

        lines: list[str] = ["🧩 Отчёт загрузки модулей из папки moduls (debug):"]

        if not results:
            lines.append("Список модулей пуст.")
        else:
            ok_count = sum(1 for _, ok, _ in results if ok)
            fail_count = sum(1 for _, ok, _ in results if not ok)
            lines.append(f"Успешно: {ok_count}, с ошибками: {fail_count}")
            lines.append("")

            for name, ok, error in results:
                if ok:
                    lines.append(f"✅ {name}")
                else:
                    if error is None:
                        lines.append(f"❌ {name} — ошибка без деталей.")
                    else:
                        err_text = f"{type(error).__name__}: {error}"
                        if len(err_text) > 200:
                            err_text = err_text[:197] + "..."
                        lines.append(f"❌ {name} — {err_text}")

        text = "\n".join(lines)

        # Кому отправлять
        targets: list[int] = []
        try:
            from __main__ import authorized_users  # type: ignore[attr-defined]
            if isinstance(authorized_users, (list, tuple, set)):
                targets = [int(x) for x in authorized_users]
            elif isinstance(authorized_users, dict):
                targets = list(authorized_users.keys())
            elif authorized_users:
                targets = [int(authorized_users)]
        except Exception:
            targets = []

        if not targets:
            # fallback: владелец/один чат из __main__
            try:
                import __main__
                for attr in ("OWNER_ID", "owner_id", "CHAT_ID", "chat_id"):
                    if hasattr(__main__, attr):
                        targets.append(int(getattr(__main__, attr)))
                        break
            except Exception:
                pass

        for chat_id in targets:
            try:
                await dp.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                # Ошибки отправки отчёта какому-то юзеру не фатальны
                pass
    except Exception:
        # Любые ошибки самого отчёта не должны ломать работу бота
        pass


# ---------- Импорт после авторизации ----------

async def _import_after_auth(dp: Dispatcher, nostartrun_modules: list[str]) -> None:
    """
    Импортирует модули nostartrun* после авторизации и шлёт отчёт.
    В отчёт попадают и те модули, которые стартовали сразу (startrun*),
    и те, что загрузились после авторизации (nostartrun*).
    """
    results = _get_import_results_list()
    if results is None:
        results = []
        _set_import_results_list(results)

    if nostartrun_modules:
        _import_modules_list(dp, nostartrun_modules)

    # Больше в этот список не пишем
    _set_import_results_list(None)

    # Отправляем отчёт (если debug включён)
    await _send_debug_report(dp, results)


def _authorization_monitor_thread(dp: Dispatcher, nostartrun_modules: list[str]) -> None:
    """
    Отдельный поток: ждём появления loop, ждём авторизацию и запускаем импорт.
    """
    wait_for_bot_loop(dp)
    while not check_auth():
        time.sleep(1.0)

    # Как только авторизация появилась — импортируем остальное в event loop
    dp.bot.loop.call_soon_threadsafe(
        asyncio.create_task, _import_after_auth(dp, nostartrun_modules)
    )


# ---------- Главная точка входа менеджера ----------

def register_handlers(dp: Dispatcher) -> None:
    """
    Вызывается из основного кода бота.
    Логика:
    1) Находим в папке moduls модули startrun* и nostartrun*.
    2) Создаём общий список для логирования результатов импорта.
    3) Сразу импортируем и запускаем все startrun* (при необходимости вызывая их register_handlers).
    4) Запускаем поток, который ждёт авторизацию и импортирует nostartrun*,
       после чего отправляет в Telegram один общий debug-отчёт.
    """
    startrun_modules, nostartrun_modules = _discover_plugin_modules()

    # Общий список статусов для обоих типов модулей
    results: list[tuple[str, bool, Exception | None]] = []
    _set_import_results_list(results)

    # Модули, которые должны стартовать сразу (до авторизации)
    if startrun_modules:
        _import_modules_list(dp, startrun_modules)

    # Модули, которые запускаются только после авторизации
    threading.Thread(
        target=_authorization_monitor_thread,
        args=(dp, nostartrun_modules),
        daemon=True,
    ).start()
