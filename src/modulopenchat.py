# create_interactive_chat.py

from aiogram import types
from aiogram.dispatcher import Dispatcher

def register_handlers(dp: Dispatcher):
    @dp.message_handler(lambda msg: msg.text 
                        and msg.text.strip().lower() == "создать интерактивный чат")
    async def handle_create_interactive_chat(message: types.Message):
        # Пока в прокачке
        await message.answer("🤖 Упс… «Создать интерактивный чат» ещё в разработке! 🚧 Скоро будет 🔥")
