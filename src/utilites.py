from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard


def register_handlers(dp: Dispatcher):
    @dp.message_handler(
        lambda message: message.text and message.text.strip().lower() == "утилиты"
    )
    async def handle_utilities(message: types.Message):
        """
        Обработчик кнопки 'утилиты'.
        Показывает клавиатуру с разделами:
        - Просмотр логов Windows
        - Работа с процессами
        - Работа с BAT
        """
        keyboard = get_utilities_keyboard()
        await message.answer(
            "🔧 Выбери нужный раздел утилит:",
            reply_markup=keyboard
        )
