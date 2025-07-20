# browser_control.py

from aiogram import types
from aiogram.dispatcher import Dispatcher

def register_handlers(dp: Dispatcher):
    @dp.message_handler(lambda msg: msg.text 
                        and msg.text.strip().lower() == "управление браузером")
    async def handle_browser_control(message: types.Message):
        # Функция ещё в разработке
        await message.answer("🌐 Упс, «Управление браузером» ещё в разработке! ⚙️ Скоро прокачаемся 🚀")
