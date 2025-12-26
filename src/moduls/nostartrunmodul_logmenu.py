# -*- coding: utf-8 -*-
"""
nostartrunmodul_logmenu.py

"""

from __future__ import annotations

from typing import Optional, Iterable, List

import os

from aiogram import types


# --------- helpers ---------

def _main():
    import __main__ as main  # главный скрипт
    return main


def _safe_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def _get_attr(name: str, default=None):
    return getattr(_main(), name, default)


def _write_bot(msg: str):
    wb = _get_attr("write_bot_log")
    if callable(wb):
        _safe_call(wb, msg)


def _write_com(msg: str):
    wc = _get_attr("write_com_log")
    if callable(wc):
        _safe_call(wc, msg)


def _read_text_file(path: str) -> str:
    if not path:
        return ""
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _split_for_telegram(text: str, max_len: int = 3900) -> List[str]:
    """
    Режем аккуратно по строкам, чтобы телега не обижалась на лимит,
    и чтобы скринридерам было проще.
    """
    if not text:
        return []

    lines = text.splitlines(True)  # keep \n
    chunks: List[str] = []
    buf = ""
    for line in lines:
        # если одна строка очень длинная, режем её кусками
        if len(line) > max_len:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(line), max_len):
                chunks.append(line[i:i + max_len])
            continue

        if len(buf) + len(line) > max_len:
            chunks.append(buf)
            buf = line
        else:
            buf += line

    if buf:
        chunks.append(buf)

    # страховка: если текст без \n и очень большой
    if not chunks:
        chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
    return chunks


async def _send_log_text(message: types.Message, title: str, path: str):
    raw = _read_text_file(path)
    if not raw.strip():
        raw = f"{title}: логов нет."
    for part in _split_for_telegram(raw):
        await message.answer(part)


def _set_debug_enabled(value: bool) -> bool:
    """
    Применяет debug-флаг:
    - main.debug_enabled
    - logsys.set_debug_enabled
    - config.ini (CONFIG_SECTION.debug)
    """
    main = _main()

    try:
        setattr(main, "debug_enabled", bool(value))
    except Exception:
        pass

    logsys = getattr(main, "logsys", None)
    if logsys is not None and hasattr(logsys, "set_debug_enabled"):
        _safe_call(logsys.set_debug_enabled, bool(value))

    # Сохраняем в config.ini (если доступно)
    cfg = getattr(main, "config", None)
    section = getattr(main, "CONFIG_SECTION", "credentials")
    saver = getattr(main, "_save_config", None)
    try:
        if cfg is not None:
            if section not in cfg:
                cfg[section] = {}
            cfg[section]["debug"] = "True" if value else "False"
            if callable(saver):
                saver()
    except Exception as e:
        _write_bot(f"[ОШИБКА] Не удалось сохранить debug в config.ini: {e}")
        return False

    return True


def _try_register_in_settings_registry() -> bool:
    """
    Регистрирует кнопку «Лог» в реестре настроек (settings_registry).

    Важно: импорт делаем мягко, чтобы модуль не падал, если реестр отсутствует
    (например, при запуске в урезанной конфигурации).
    """
    register_setting = None
    try:
        from menu_registry.settings_registry import register_setting as _rs  # новый путь
        register_setting = _rs
    except Exception:
        try:
            from settings_registry import register_setting as _rs  # совместимость со старым импортом
            register_setting = _rs
        except Exception:
            register_setting = None

    if not callable(register_setting):
        return False

    try:
        register_setting(
            key="log_menu",
            title="Лог",
            trigger_text="Лог",
            group="settings",
            order=40,
            description="Меню логов и управление дебагом",
        )
        return True
    except Exception as e:
        # логгер может быть ещё не инициализирован, поэтому используем мягкий вызов
        try:
            _write_bot(f"[ОШИБКА] Не удалось зарегистрировать пункт настроек «Лог»: {e}")
        except Exception:
            pass
        return False


# --------- public API ---------

def register_handlers(dp):
    """
    Подключается извне: register_handlers(dp)
    """
    _try_register_in_settings_registry()
    # ленивые импорты внутри, чтобы модуль был максимально "безопасный"
    from keymenu import get_additional_keyboard

    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "лог")
    async def log_menu(message: types.Message):
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(
            "лог устройства",
            "лог бота",
            "лог менеджера плагинов",
            "лог ошибок",
            "дебаг",
            "назад бота",
        )
        await message.answer("Выберите тип лога:", reply_markup=kb)
        _write_bot(f"Пользователь {message.from_user.id} открыл меню логов.")

    @dp.message_handler(lambda message: message.text == "лог устройства")
    async def device_log(message: types.Message):
        _write_bot(f"Пользователь {message.from_user.id} запросил лог устройства.")
        await _send_log_text(message, "Лог устройства", _get_attr("com_log_file", ""))

    @dp.message_handler(lambda message: message.text == "лог бота")
    async def bot_log_handler(message: types.Message):
        _write_bot(f"Пользователь {message.from_user.id} запросил лог бота.")
        await _send_log_text(message, "Лог бота", _get_attr("bot_log_file", ""))

    @dp.message_handler(lambda message: message.text == "лог менеджера плагинов")
    async def plugin_log_handler(message: types.Message):
        _write_bot(f"Пользователь {message.from_user.id} запросил лог менеджера плагинов.")
        await _send_log_text(message, "Лог менеджера плагинов", _get_attr("plugin_log_file", ""))

    @dp.message_handler(lambda message: message.text == "лог ошибок")
    async def error_log_handler(message: types.Message):
        _write_bot(f"Пользователь {message.from_user.id} запросил лог ошибок.")
        path = _get_attr("error_log_file", "")
        raw = _read_text_file(path)
        if not raw.strip():
            raw = "Ошибок нет."
        for part in _split_for_telegram(raw):
            await message.answer(part)

    # -------------------- Debug submenu --------------------

    @dp.message_handler(lambda message: message.text == "дебаг")
    async def debug_menu(message: types.Message):
        dbg = bool(_get_attr("debug_enabled", False))
        status_str = "включен" if dbg else "выключен"
        _write_com(f"Пользователь {message.from_user.id} открыл меню дебага. Статус: {status_str}.")
        await message.answer(f"Статус дебага: {status_str}.")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Вкл дебаг", "Выкл дебаг")
        kb.add("Прочитать лог дебага", "Назад в меню логов")
        await message.answer("Меню дебага:", reply_markup=kb)

    @dp.message_handler(lambda message: message.text == "Вкл дебаг")
    async def enable_debug(message: types.Message):
        if bool(_get_attr("debug_enabled", False)):
            await message.answer("Дебаг уже включен.")
            return
        ok = _set_debug_enabled(True)
        if ok:
            _write_com(f"Пользователь {message.from_user.id} включил дебаг.")
            await message.answer("Дебаг включен.")
        else:
            await message.answer("Не удалось включить дебаг (ошибка сохранения config.ini).")
        await debug_menu(message)

    @dp.message_handler(lambda message: message.text == "Выкл дебаг")
    async def disable_debug(message: types.Message):
        if not bool(_get_attr("debug_enabled", False)):
            await message.answer("Дебаг уже выключен.")
            return
        ok = _set_debug_enabled(False)
        if ok:
            _write_com(f"Пользователь {message.from_user.id} выключил дебаг.")
            await message.answer("Дебаг выключен.")
        else:
            await message.answer("Не удалось выключить дебаг (ошибка сохранения config.ini).")
        await debug_menu(message)

    @dp.message_handler(lambda message: message.text == "Прочитать лог дебага")
    async def read_debug_log(message: types.Message):
        _write_com(f"Пользователь {message.from_user.id} запросил лог дебага.")
        await _send_log_text(message, "Лог дебага", _get_attr("debug_log_file", ""))

    @dp.message_handler(lambda message: message.text == "Назад в меню логов")
    async def back_from_debug_menu(message: types.Message):
        _write_com(f"Пользователь {message.from_user.id} вернулся в меню логов из дебага.")
        await log_menu(message)

    @dp.message_handler(lambda message: message.text == "назад бота")
    async def back_from_log_menu(message: types.Message):
        user_id = message.from_user.id

        # Сбрасываем флаги режимов ровно как в original additional_menu
        power_mode = _get_attr("power_mode")
        plugins_mode = _get_attr("plugins_mode")
        if isinstance(power_mode, dict):
            power_mode[user_id] = False
        if isinstance(plugins_mode, dict):
            plugins_mode[user_id] = False

        kb = get_additional_keyboard()
        await message.answer("Выберите действие:", reply_markup=kb)
        _write_bot(f"Пользователь {user_id} вышел из меню логов (назад в «Дополнительно»).")

    _write_bot("Меню логов (nostartrunmodul_logmenu.py) зарегистрировано.")
