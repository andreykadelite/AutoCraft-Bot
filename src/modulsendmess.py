# send_to_pc.py

from aiogram import types
from aiogram.dispatcher import Dispatcher

def register_handlers(dp: Dispatcher):
    @dp.message_handler(lambda msg: msg.text 
                        and msg.text.strip().lower() == "отправить сообщение на компьютер")
    async def handle_send_to_pc(message: types.Message):
        # Сообщаем, что в разработке
        await message.answer("✉️ Упс, «Отправить сообщение на компьютер» ещё в разработке! 🚧")

