# -*- coding: utf-8 -*-
"""
menu_registry/utilites.py

Хэндлер раздела «утилиты» для aiogram.

Файл перенесён в папку menu_registry.
Для совместимости:
- get_utilities пробуем импортировать из menu_registry.utilities_registry,
  и только если не вышло — из старого utilities_registry.
- mainmenu_registry.register_main_item — как и раньше, опционально.
"""

from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard

# Реестр утилит (динамический список утилит)
try:
    from menu_registry.utilities_registry import get_utilities
except ImportError:
    try:
        from utilities_registry import get_utilities  # type: ignore
    except ImportError:
        get_utilities = None  # type: ignore

# Реестр главного меню (динамическое "Главное меню").
# Если mainmenu_registry отсутствует, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item
except ImportError:
    register_main_item = None


def _register_mainmenu_item():
    """
    Регистрирует кнопку 'утилиты' в реестре главного меню.

    Кнопка:
    - title:        "утилиты" (то, что видит пользователь)
    - trigger_text: "утилиты" (то, что обрабатывает handler)
    - group:        "main"
    """
    if register_main_item is None:
        return

    try:
        register_main_item(
            key="utilities_root",
            title="утилиты",
            trigger_text="утилиты",
            group="main",
            order=30,
            description="Переход в раздел утилит (главное меню)"
        )
    except Exception:
        # Не роняем модуль из-за реестра.
        pass


def register_handlers(dp: Dispatcher):
    # При регистрации хэндлеров сразу регистрируем кнопку в главном меню.
    _register_mainmenu_item()

    @dp.message_handler(
        lambda message: message.text and message.text.strip().lower() == "утилиты"
    )
    async def handle_utilities(message: types.Message):
        """
        Обработчик кнопки 'утилиты'.
        Показывает клавиатуру для раздела утилит.
        """
        keyboard = get_utilities_keyboard()

        # Определяем, есть ли вообще утилиты в реестре
        utilities_list = []
        if get_utilities is not None:
            try:
                utilities_list = get_utilities(group="utilities")
            except Exception:
                utilities_list = []

        if utilities_list:
            text = "🔧 Выбери нужный раздел утилит:"
        else:
            text = (
                "⚠️ В реестре утилит сейчас ничего нет. "
                "Ни один модуль не зарегистрировался в разделе «утилиты»\n\n"
                "Можешь вернуться назад и позже добавить нужные модули."
            )

        await message.answer(
            text,
            reply_markup=keyboard
        )
