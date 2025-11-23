import os
import time
import threading
import importlib
import asyncio
from aiogram import Dispatcher
from keymenu import get_additional_keyboard


def check_auth():
    try:
        from __main__ import authorized_users
        return bool(authorized_users)
    except Exception:
        return False


def remove_handlers_from_module(dp: Dispatcher, module_name: str):
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
    try:
        modulpsw = importlib.import_module("modulpsw")
        remove_handlers_from_module(dp, "modulpsw")
        if hasattr(modulpsw, "register_handlers"):
            modulpsw.register_handlers(dp)
            reorder_plugin_handlers(dp)
    except Exception:
        pass


def import_modulset(dp: Dispatcher):
    try:
        modulset = importlib.import_module("modulset")
        if hasattr(modulset, "register_handlers"):
            modulset.register_handlers(dp)
    except Exception:
        pass


def import_modulcon(dp: Dispatcher):
    try:
        modulcon = importlib.import_module("modulcon")
        if hasattr(modulcon, "register_handlers"):
            modulcon.register_handlers(dp)
    except Exception:
        pass


def import_modulpowershell(dp: Dispatcher):
    try:
        modulpowershell = importlib.import_module("modulpowershell")
        if hasattr(modulpowershell, "register_handlers"):
            modulpowershell.register_handlers(dp)
    except Exception:
        pass


def import_utilites(dp: Dispatcher):
    try:
        utilites = importlib.import_module("utilites")
        if hasattr(utilites, "register_handlers"):
            utilites.register_handlers(dp)
    except Exception:
        pass


def import_modulwinlogs(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulwinlogs после авторизации.
    """
    try:
        modulwinlogs = importlib.import_module("modulwinlogs")
        if hasattr(modulwinlogs, "register_handlers"):
            modulwinlogs.register_handlers(dp)
    except Exception:
        pass


def import_modulbatrun(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulbatrun после авторизации.
    """
    try:
        mod_batrun = importlib.import_module("modulbatrun")
        if hasattr(mod_batrun, "register_handlers"):
            mod_batrun.register_handlers(dp)
    except Exception:
        pass


def import_modulwinrun(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulwinrun после авторизации.
    """
    try:
        modulwinrun = importlib.import_module("modulwinrun")
        if hasattr(modulwinrun, "register_handlers"):
            modulwinrun.register_handlers(dp)
    except Exception:
        pass


def import_moduldptools(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из moduldptools после авторизации.
    """
    try:
        import __main__
        moduldptools = importlib.import_module("moduldptools")
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
    except Exception:
        pass


def import_modulscrin(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulscrin после авторизации.
    """
    try:
        modulescrin = importlib.import_module("modulscrin")
        if hasattr(modulescrin, "register_handlers"):
            modulescrin.register_handlers(dp)
    except Exception:
        pass


def import_modulscreenshot(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulscreenshot после авторизации.
    """
    try:
        modulscreenshot = importlib.import_module("modulscreenshot")
        if hasattr(modulscreenshot, "register_handlers"):
            modulscreenshot.register_handlers(dp)
    except Exception:
        pass


def import_modulsound(dp: Dispatcher):
    try:
        modulsound = importlib.import_module("modulsound")
        if hasattr(modulsound, "register_handlers"):
            modulsound.register_handlers(dp)
    except Exception:
        pass


def import_modulmicrosendsound(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulmicrosendsound после авторизации.
    """
    try:
        micros = importlib.import_module("modulmicrosendsound")
        if hasattr(micros, "register_handlers"):
            micros.register_handlers(dp)
    except Exception:
        pass


def import_modulvolume_menu(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulvolume_menu после авторизации.
    """
    try:
        volume_menu = importlib.import_module("modulvolume_menu")
        if hasattr(volume_menu, "register_handlers"):
            volume_menu.register_handlers(dp)
    except Exception:
        pass


# --- Новые функции для ваших модулей ---
def import_modulopenchat(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulopenchat после авторизации.
    """
    try:
        openchat = importlib.import_module("modulopenchat")
        if hasattr(openchat, "register_handlers"):
            openchat.register_handlers(dp)
    except Exception:
        pass


def import_modulsendmess(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulsendmess после авторизации.
    """
    try:
        sendmess = importlib.import_module("modulsendmess")
        if hasattr(sendmess, "register_handlers"):
            sendmess.register_handlers(dp)
    except Exception:
        pass


def import_modulbrowsrem(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulbrowsrem после авторизации.
    """
    try:
        browsrem = importlib.import_module("modulbrowsrem")
        if hasattr(browsrem, "register_handlers"):
            browsrem.register_handlers(dp)
    except Exception:
        pass


def import_modulprocesses(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulprocesses после авторизации.
    """
    try:
        modulprocesses = importlib.import_module("modulprocesses")
        if hasattr(modulprocesses, "register_handlers"):
            modulprocesses.register_handlers(dp)
    except Exception:
        pass


def import_modulservices(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulservices после авторизации.
    """
    try:
        modulservices = importlib.import_module("modulservices")
        if hasattr(modulservices, "register_handlers"):
            modulservices.register_handlers(dp)
    except Exception:
        pass


def import_modulfilemanager(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulfmtg (файловый менеджер) после авторизации.
    """
    try:
        modulfilemanager = importlib.import_module("modulfmtg")
        if hasattr(modulfilemanager, "register_handlers"):
            modulfilemanager.register_handlers(dp)
    except Exception:
        pass


def import_modulnetwork(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из modulnetwork (модуль работы с сетью) после авторизации.
    """
    try:
        modulnetwork = importlib.import_module("modulnetwork")
        if hasattr(modulnetwork, "register_handlers"):
            modulnetwork.register_handlers(dp)
    except Exception:
        pass
# --- Конец новых функций ---


async def import_all_plugins(dp: Dispatcher):
    import_modulpsw(dp)             # 1. psw
    import_modulset(dp)             # 2. set
    import_modulcon(dp)             # 3. con
    import_modulpowershell(dp)      # 4. powershell
    import_utilites(dp)             # 5. утилиты
    import_modulwinlogs(dp)         # 6. просмотр логов Windows
    import_modulbatrun(dp)          # 7. работа с BAT
    import_modulwinrun(dp)          # 8. режим Win+R
    import_moduldptools(dp)         # 9. dptools
    import_modulscrin(dp)           # 10. scrin (старый модуль, если есть)
    import_modulscreenshot(dp)      # 11. новый модуль скриншотов
    import_modulsound(dp)           # 12. звук
    import_modulmicrosendsound(dp)  # 13. микрозвук
    import_modulvolume_menu(dp)     # 14. меню громкости
    # Подключаем ваши новые модули:
    import_modulopenchat(dp)        # 15. openchat
    import_modulsendmess(dp)        # 16. sendmess
    import_modulbrowsrem(dp)        # 17. browsrem
    import_modulprocesses(dp)       # 18. процессы
    import_modulservices(dp)        # 19. службы
    import_modulfilemanager(dp)     # 20. файловый менеджер (modulfmtg)
    import_modulnetwork(dp)         # 21. модуль работы с сетью


def wait_for_bot_loop(dp: Dispatcher):
    while not hasattr(dp.bot, "loop") or dp.bot.loop is None:
        time.sleep(0.5)


def authorization_monitor(dp: Dispatcher):
    wait_for_bot_loop(dp)
    while not check_auth():
        time.sleep(1)
    dp.bot.loop.call_soon_threadsafe(asyncio.create_task, import_all_plugins(dp))


def register_handlers(dp: Dispatcher):
    threading.Thread(
        target=authorization_monitor,
        args=(dp,),
        daemon=True
    ).start()
