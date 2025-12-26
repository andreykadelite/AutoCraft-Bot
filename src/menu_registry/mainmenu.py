
# mainmenu.py
from aiogram import types
from aiogram.dispatcher import Dispatcher

# Ожидается, что в keymenu есть функция, аналогичная get_utilities_keyboard,
# но для главного меню. Если она называется иначе - просто поправь импорт и вызов.
from keymenu import get_main_menu_keyboard

try:
    # Реестр главного меню - как utilities_registry, только для корневого меню
    # Скрипты перенесены в папку menu_registry — сначала пробуем новый путь,
    # затем fallback на старый (для совместимости).
    from menu_registry.mainmenu_registry import get_main_items
except ImportError:
    try:
        from mainmenu_registry import get_main_items
    except ImportError:
        get_main_items = None
def register_handlers(dp: Dispatcher):
    @dp.message_handler(
        lambda message: message.text and message.text.strip().lower() == "главное меню"
    )
    async def handle_main_menu(message: types.Message):
        """
        Обработчик кнопки 'Главное меню'.
        Показывает основное меню бота.
        """
        keyboard = get_main_menu_keyboard()

        # Проверяем, есть ли вообще пункты в реестре главного меню
        items_list = []
        if get_main_items is not None:
            try:
                items_list = get_main_items(group="main")
            except Exception:
                items_list = []

        if items_list:
            text = "📋 Главное меню. Выбери нужный раздел:"
        else:
            text = (
                "⚠️ В реестре главного меню сейчас ничего нет.\n"
                "Ни один модуль не зарегистрировался в разделе «главное меню».\n\n"
                "Добавь модули, которые должны появляться здесь."
            )

        await message.answer(
            text,
            reply_markup=keyboard
        )
