import os
import sys
import time
import threading
import importlib
import asyncio
import configparser
from aiogram import Dispatcher
from keymenu import get_additional_keyboard


def _ensure_menu_registry_on_syspath():
    """
    Гарантирует, что папка menu_registry доступна для импортов.

    Зачем:
    - Реестры/меню перенесены в menu_registry.
    - В обычном Python и в Nuitka путь к проекту может отличаться.
    - Добавляем menu_registry в sys.path, чтобы модули внутри были видны
      как top-level (utilities_registry, utilites, plugins_menu_registry и т.п.)
      и чтобы работали fallback-импорты (что особенно важно в EXE).

    Ничего не ломает: просто добавляет существующие пути в начало sys.path.
    """
    possible = []

    # 1) base_dir из __main__ (если есть)
    try:
        import __main__
        base_dir = getattr(__main__, "base_dir", None)
        if base_dir:
            possible.append(os.path.join(base_dir, "menu_registry"))
    except Exception:
        pass

    # 2) рядом с этим файлом
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        possible.append(os.path.join(here, "menu_registry"))
    except Exception:
        pass

    # 3) рядом с exe (Nuitka/обычный запуск)
    try:
        exe_dir = os.path.dirname(os.path.abspath(getattr(sys, "executable", "") or ""))
        if exe_dir:
            possible.append(os.path.join(exe_dir, "menu_registry"))
    except Exception:
        pass

    # 4) текущая рабочая директория
    try:
        possible.append(os.path.join(os.getcwd(), "menu_registry"))
    except Exception:
        pass

    for p in possible:
        try:
            if p and os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        except Exception:
            continue


# Добавляем menu_registry в sys.path как можно раньше
_ensure_menu_registry_on_syspath()

# Подсказка Nuitka: статически "засветить" реестры/меню, чтобы их проще было включать.
# Эти импорты безопасны: если файлов нет — просто игнорируем.
try:
    import utilities_registry  # noqa: F401
except Exception:
    pass
try:
    import plugins_menu_registry  # noqa: F401
except Exception:
    pass



# Глобальное хранилище результатов импорта (для debug-отчёта)
_import_results_lock = threading.Lock()
_import_results = None


def _set_import_results_list(lst):
    global _import_results
    with _import_results_lock:
        _import_results = lst


def _get_import_results_list():
    with _import_results_lock:
        return _import_results


def _log_import_result(module_name: str, success: bool, error: Exception | None = None):
    """Записывает результат загрузки модуля в общий список (если он активен)."""
    results = _get_import_results_list()
    if results is not None:
        results.append((module_name, success, error))


def is_debug_enabled() -> bool:
    """
    Определяем, включён ли debug-режим.

    1) Пробуем взять из __main__ атрибуты DEBUG / debug / debug_mode.
    2) Пробуем взять из __main__.config (если есть) опцию 'debug' в любой секции.
    3) Читаем config.ini рядом с base_dir или рядом с этим модулем и ищем опцию 'debug'.
    Если ничего не нашли / ошибка — считаем, что debug выключен.
    """
    base_dir = None
    try:
        import __main__
        # Прямые флаги в __main__
        for attr in ("DEBUG", "debug", "debug_mode"):
            if hasattr(__main__, attr):
                return bool(getattr(__main__, attr))

        # Конфиг, если main его уже прочитал
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

    # Пробуем сами прочитать config.ini
    parser = configparser.ConfigParser()
    config_paths = []

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


def check_auth():
    try:
        from __main__ import authorized_users
        return bool(authorized_users)
    except Exception:
        return False


def remove_handlers_from_module(dp: Dispatcher, module_name: str):
    """Аккуратно удаляет ранее зарегистрированные handlers конкретного модуля."""
    try:
        def is_from_module(h):
            callback_fn = getattr(h, "callback", None) or getattr(h, "handler", None)
            if not callback_fn:
                return False
            mod_name = getattr(callback_fn, "__module__", "")
            return mod_name == module_name

        dp.message_handlers.handlers[:] = [
            h for h in dp.message_handlers.handlers if not is_from_module(h)
        ]
        dp.callback_query_handlers.handlers[:] = [
            h for h in dp.callback_query_handlers.handlers if not is_from_module(h)
        ]
    except Exception:
        pass


def reorder_plugin_handlers(dp: Dispatcher):
    """Пытаемся поднять modulpsw повыше в цепочке обработчиков."""
    try:
        def is_plugin_handler(h):
            callback_fn = getattr(h, "callback", None) or getattr(h, "handler", None)
            if not callback_fn:
                return False
            mod_name = getattr(callback_fn, "__module__", "")
            return mod_name == "modulpsw"

        plugin_handlers = [h for h in dp.message_handlers.handlers if is_plugin_handler(h)]
        other_handlers = [h for h in dp.message_handlers.handlers if not is_plugin_handler(h)]
        dp.message_handlers.handlers[:] = plugin_handlers + other_handlers
    except Exception:
        pass


def import_modulpsw(dp: Dispatcher):
    module_name = "modulpsw"
    try:
        modulpsw = importlib.import_module(module_name)
        remove_handlers_from_module(dp, module_name)
        if hasattr(modulpsw, "register_handlers"):
            modulpsw.register_handlers(dp)
            reorder_plugin_handlers(dp)
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)


def import_modulset(dp: Dispatcher):
    module_name = "modulset"
    try:
        modulset = importlib.import_module(module_name)
        if hasattr(modulset, "register_handlers"):
            modulset.register_handlers(dp)
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)


def import_modulcon(dp: Dispatcher):
    module_name = "modulcon"
    try:
        modulcon = importlib.import_module(module_name)
        if hasattr(modulcon, "register_handlers"):
            modulcon.register_handlers(dp)
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)


def import_utilites(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков утилит.

    Важно:
    - Файлы утилит/реестра теперь могут лежать в menu_registry.
      Мы заранее добавляем menu_registry в sys.path (см. _ensure_menu_registry_on_syspath),
      поэтому обычный импорт 'utilites' будет работать даже если файл физически в menu_registry.
    - Ничего не ломаем: если по какой-то причине 'utilites' не найден,
      пробуем альтернативный импорт как пакет.
    """
    module_name = "utilites"
    try:
        try:
            utilites = importlib.import_module(module_name)
        except ModuleNotFoundError:
            utilites = importlib.import_module("menu_registry.utilites")
        if hasattr(utilites, "register_handlers"):
            utilites.register_handlers(dp)
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)

def import_moduldptools(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из moduldptools после авторизации.

    Важно: модуль ожидает некоторые переменные/состояния из __main__.
    """
    module_name = "moduldptools"
    try:
        import __main__
        moduldptools = importlib.import_module(module_name)
        if hasattr(moduldptools, "register_dptools_handlers"):
            moduldptools.register_dptools_handlers(
                dp,
                __main__.base_dir,
                __main__.note_mode,
                __main__.pending_note,
                __main__.file_mode,
                __main__.infiles_mode,
                __main__.power_mode,
                __main__.pending_power_action,
                get_additional_keyboard
            )
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)


def import_modulsound(dp: Dispatcher):
    module_name = "modulsound"
    try:
        modulsound = importlib.import_module(module_name)
        if hasattr(modulsound, "register_handlers"):
            modulsound.register_handlers(dp)
        _log_import_result(module_name, True, None)
    except Exception as e:
        _log_import_result(module_name, False, e)


def import_Moduls_manager_sys_ext(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из Moduls_manager_sys_ext (системные расширения).
    Вызывается до авторизации пользователя.

    Логика:
    - пытаемся найти папку "moduls" рядом с base_dir (__main__.base_dir) и рядом с этим файлом;
    - добавляем найденные пути в sys.path;
    - пробуем несколько вариантов импорта, чтобы работало и в обычном Python, и в Nuitka:
        * "Moduls_manager_sys_ext"
        * "moduls.Moduls_manager_sys_ext"
    - если модуль найден и у него есть register_handlers(dp), вызываем её;
    - результат логируем в общий debug-отчёт через _log_import_result.
    """
    module_name_plain = "Moduls_manager_sys_ext"
    module_name_pkg = "moduls.Moduls_manager_sys_ext"

    try:
        import __main__
    except Exception:
        __main__ = None  # type: ignore

    # Кандидаты путей к папке moduls
    possible_paths = []

    # 1) base_dir/moduls
    try:
        base_dir = getattr(__main__, "base_dir", None) if __main__ is not None else None
    except Exception:
        base_dir = None

    if base_dir:
        possible_paths.append(os.path.join(base_dir, "moduls"))

    # 2) moduls рядом с этим файлом
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        possible_paths.append(os.path.join(here, "moduls"))
    except Exception:
        pass

    # Добавляем существующие пути в sys.path
    for p in possible_paths:
        try:
            if p and os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        except Exception:
            continue

    loaded = False
    last_error: Exception | None = None

    for name in (module_name_plain, module_name_pkg):
        try:
            mod_sys_ext = importlib.import_module(name)
            if hasattr(mod_sys_ext, "register_handlers"):
                mod_sys_ext.register_handlers(dp)
            _log_import_result("Moduls_manager_sys_ext", True, None)
            loaded = True
            break
        except ModuleNotFoundError as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            break

    if not loaded:
        _log_import_result("Moduls_manager_sys_ext", False, last_error)


async def _send_debug_report(dp: Dispatcher, results: list[tuple[str, bool, Exception | None]]):
    """Отправляет в Telegram сводку по загрузке модулей, если включён debug."""
    try:
        if not is_debug_enabled():
            return

        lines: list[str] = ["🧩 Отчёт загрузки модулей (debug):"]

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

        # Кому слать: все authorized_users
        try:
            from __main__ import authorized_users
            targets = []
            if isinstance(authorized_users, (list, tuple, set)):
                targets = list(authorized_users)
            elif isinstance(authorized_users, dict):
                targets = list(authorized_users.keys())
            elif authorized_users:
                targets = [authorized_users]
            else:
                targets = []
        except Exception:
            targets = []

        # Подстраховка: если вдруг authorized_users нет / пуст
        if not targets:
            try:
                import __main__
                for attr in ("OWNER_ID", "owner_id", "CHAT_ID", "chat_id"):
                    if hasattr(__main__, attr):
                        targets.append(getattr(__main__, attr))
                        break
            except Exception:
                pass

        for chat_id in targets:
            try:
                await dp.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                pass
    except Exception:
        pass


async def import_all_plugins(dp: Dispatcher):
    """
    Импорт модулей после авторизации пользователя.

    По вашему запросу из менеджера удалены 19 модулей, которые в debug-отчёте
    грузились с ошибками (ModuleNotFoundError). Теперь здесь остаются только те,
    что реально загружаются успешно.
    """
    existing = _get_import_results_list()
    if isinstance(existing, list):
        results = existing
    else:
        results: list[tuple[str, bool, Exception | None]] = []
        _set_import_results_list(results)

    # Список успешных модулей (по вашему debug-отчёту)
    import_modulpsw(dp)
    import_modulset(dp)
    import_modulcon(dp)
    import_utilites(dp)
    import_moduldptools(dp)
    import_modulsound(dp)

    # Отключаем глобальный список, чтобы случайные вызовы не писались в старый отчёт
    _set_import_results_list(None)

    # и если debug включён — шлём отчёт в Telegram
    await _send_debug_report(dp, results)


def wait_for_bot_loop(dp: Dispatcher):
    while not hasattr(dp.bot, "loop") or dp.bot.loop is None:
        time.sleep(0.5)


def authorization_monitor(dp: Dispatcher):
    wait_for_bot_loop(dp)
    while not check_auth():
        time.sleep(1)
    dp.bot.loop.call_soon_threadsafe(asyncio.create_task, import_all_plugins(dp))


def register_handlers(dp: Dispatcher):
    """
    Стартовая точка главного менеджера.

    Здесь:
    - инициализируем общий список результатов импортов (для debug-отчёта);
    - пробуем импортировать системный менеджер из папки moduls до авторизации;
    - запускаем поток, который будет ждать авторизации и импортировать остальные модули.
    """
    # Готовим список результатов для всех импортов (включая системный менеджер)
    try:
        if _get_import_results_list() is None:
            _set_import_results_list([])
    except Exception:
        pass

    # Импортируем менеджер системных расширений из папки moduls до авторизации пользователя
    try:
        import_Moduls_manager_sys_ext(dp)
    except Exception as e:
        try:
            _log_import_result("Moduls_manager_sys_ext", False, e)
        except Exception:
            pass

    threading.Thread(
        target=authorization_monitor,
        args=(dp,),
        daemon=True
    ).start()
