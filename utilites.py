from aiogram import types
from aiogram.dispatcher import Dispatcher

def register_handlers(dp: Dispatcher):
    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "утилиты")
    async def handle_utilities(message: types.Message):
        # Клавиатура с кнопкой "Назад"
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Назад")
        await message.answer(
            "🔧 Наши утилиты сейчас в разработке, но мы трудимся на полную мощность! "
            "В следующих обновлениях ожидай:\n"
            "• Функцию удобного резервного копирования данных;\n"
            "• Инструменты для глубокого анализа логов;\n"
            "• Генератор отчётов в пару кликов;\n"
            "и многое другое! Спасибо за терпение — оставайся с нами 🚀",
            reply_markup=keyboard
        )
