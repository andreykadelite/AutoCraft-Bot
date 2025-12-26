# -*- coding: utf-8 -*-
"""
menu_registry/plugins_menu.py

Меню "Плагины" (менеджер плагинов), построенное на реестре plugins_menu_registry.py.

Требование:
- Если в реестре НИЧЕГО не зарегистрировано, меню показывает ТОЛЬКО кнопку "Вернуться".
"""

from __future__ import annotations

from typing import List

try:
    from aiogram import types
    from aiogram.dispatcher import Dispatcher
except Exception as e:
    raise

# --- Реестр пунктов меню ---
try:
    # если menu_registry является пакетом
    from .plugins_menu_registry import get_plugin_menu_items
except Exception:
    try:
        # если импортируют без пакета
        from plugins_menu_registry import get_plugin_menu_items  # type: ignore
    except Exception:
        get_plugin_menu_items = None  # type: ignore

# --- Подключение к меню "Дополнительно" (если доступно) ---
register_additional = None
get_additional_keyboard = None

try:
    from .additional_registry import register_additional  # type: ignore
except Exception:
    try:
        from additional_registry import register_additional  # type: ignore
    except Exception:
        register_additional = None

try:
    # если у тебя есть модуль/функция, выдающая клавиатуру "Дополнительно"
    from .keymenu import get_additional_keyboard  # type: ignore
except Exception:
    try:
        from keymenu import get_additional_keyboard  # type: ignore
    except Exception:
        get_additional_keyboard = None


def _build_plugins_menu_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Собрать клавиатуру из реестра.

    - Если реестр пуст/недоступен -> только "Вернуться".
    - Иначе -> пункты + "Вернуться" в конце.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    items: List[object] = []
    if get_plugin_menu_items is not None:
        try:
            items = get_plugin_menu_items(group="plugins_menu")
        except Exception:
            items = []

    # Реестр пуст? Показываем только "Вернуться".
    if not items:
        kb.add(types.KeyboardButton("Вернуться"))
        return kb

    # Иначе — пункты меню (в порядке реестра).
    for d in items:
        title = getattr(d, "title", "") or ""
        title = title.strip()
        if title:
            kb.insert(types.KeyboardButton(title))

    # "Вернуться" всегда должна быть.
    has_back = any((getattr(d, "title", "") or "").strip().lower() == "вернуться" for d in items)
    if not has_back:
        kb.add(types.KeyboardButton("Вернуться"))

    return kb


async def plugins_root_menu(message: types.Message) -> None:
    """Открыть меню менеджера плагинов."""
    kb = _build_plugins_menu_keyboard()
    await message.answer("🧩 Менеджер плагинов:", reply_markup=kb)


async def plugins_back(message: types.Message) -> None:
    """Fallback: вернуть в меню 'Дополнительно', если оно доступно."""
    if get_additional_keyboard is None:
        await message.answer("↩️ Возврат", reply_markup=types.ReplyKeyboardRemove())
        return
    try:
        await message.answer("📌 Дополнительно:", reply_markup=get_additional_keyboard())
    except Exception:
        await message.answer("↩️ Возврат", reply_markup=types.ReplyKeyboardRemove())


def register_handlers(dp: Dispatcher) -> None:
    """
    Регистрация хэндлеров модуля.

    Также пробует зарегистрировать кнопку "Плагины" в реестр меню "Дополнительно".
    """
    # Регистрируем пункт в "Дополнительно" (если реестр доступен)
    if register_additional is not None:
        try:
            register_additional(
                key="plugins",
                title="Плагины",
                trigger_text="Плагины",
                order=30,
            )
        except Exception:
            pass

    dp.register_message_handler(
        plugins_root_menu,
        lambda m: (m.text or "").strip().lower() == "плагины",
    )

    # fallback на "Вернуться" (если основной менеджер плагинов не перехватил)
    dp.register_message_handler(
        plugins_back,
        lambda m: (m.text or "").strip().lower() == "вернуться",
    )
