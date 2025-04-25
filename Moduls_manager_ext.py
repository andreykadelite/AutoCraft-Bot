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
        dp.message_handlers.handlers[:] = [h for h in dp.message_handlers.handlers if not is_from_module(h)]
        dp.callback_query_handlers.handlers[:] = [h for h in dp.callback_query_handlers.handlers if not is_from_module(h)]
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

def import_utilites(dp: Dispatcher):
    try:
        utilites = importlib.import_module("utilites")
        if hasattr(utilites, "register_handlers"):
            utilites.register_handlers(dp)
    except Exception:
        pass

def import_dptools(dp: Dispatcher):
    """
    Импорт и регистрация обработчиков из dptools после авторизации.
    """
    try:
        import importlib
        import __main__
        dptools = importlib.import_module("dptools")
        if hasattr(dptools, "register_dptools_handlers"):
            dptools.register_dptools_handlers(
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


def wait_for_bot_loop(dp: Dispatcher):
    while not hasattr(dp.bot, "loop") or dp.bot.loop is None:
        time.sleep(0.5)

def authorization_monitor(dp: Dispatcher):
    wait_for_bot_loop(dp)
    while not check_auth():
        time.sleep(1)
    # Импорт модулей плагинов после авторизации
    dp.bot.loop.call_soon_threadsafe(import_modulpsw, dp)
    dp.bot.loop.call_soon_threadsafe(import_modulset, dp)
    dp.bot.loop.call_soon_threadsafe(import_modulcon, dp)
    dp.bot.loop.call_soon_threadsafe(import_dptools, dp)
    dp.bot.loop.call_soon_threadsafe(import_utilites, dp)

def register_handlers(dp: Dispatcher):
    threading.Thread(target=authorization_monitor, args=(dp,), daemon=True).start()
