import subprocess
from aiogram import types
from keymenu import get_main_keyboard
from __main__ import power_mode, write_com_log, write_bot_log, authorized_users

# Реестр главного меню (динамическое "Главное меню").
# Если mainmenu_registry отсутствует, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item
except ImportError:
    register_main_item = None


def _register_mainmenu_item():
    """
    Регистрирует кнопку 'cmd' в реестре главного меню.

    Кнопка:
    - title:        "cmd" (то, что видит пользователь)
    - trigger_text: "cmd" (то, что обрабатывает handler)
    - group:        "main"
    """
    if register_main_item is None:
        return

    try:
        register_main_item(
            key="cmd_root",
            title="cmd",
            trigger_text="cmd",
            group="main",
            order=32,
            description="Режим CMD"
        )
    except Exception:
        # Не роняем модуль, если что-то пошло не так при регистрации.
        pass


cmd_mode = {}
in_cmd_menu = {}

def register_handlers(dp):
    # При регистрации обработчиков регистрируем кнопку в главном меню.
    _register_mainmenu_item()
    # Вернёмся в главное меню CMD-только по авторизации
    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Назад в меню" and
        cmd_mode.get(message.from_user.id, False)
    )
    async def cmd_back_to_main(message: types.Message):
        in_cmd_menu[message.from_user.id] = False
        keyboard = get_main_keyboard()
        await message.answer(
            "Возвращаюсь в главное меню. Режим CMD активен.",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "cmd"
    )
    async def cmd_menu(message: types.Message):
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["Запуск CMD", "Завершить CMD", "Назад в меню"]
        if cmd_mode.get(message.from_user.id, False):
            buttons += ["dir", "ipconfig", "tasklist", "ping 8.8.8.8", "netstat", "tracert 8.8.8.8"]
        keyboard.add(*buttons)

        if cmd_mode.get(message.from_user.id, False):
            in_cmd_menu[message.from_user.id] = True
            await message.answer("Режим CMD активен. Выберите команду.", reply_markup=keyboard)
        else:
            await message.answer(
                "Режим CMD не активен. Запустите его кнопкой «Запуск CMD».",
                reply_markup=keyboard
            )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Запуск CMD"
    )
    async def start_cmd(message: types.Message):
        if cmd_mode.get(message.from_user.id, False):
            await message.answer("Режим CMD уже запущен!")
        else:
            cmd_mode[message.from_user.id] = True
            in_cmd_menu[message.from_user.id] = True
            write_bot_log(f"Пользователь {message.from_user.id} запустил режим CMD.")
            await message.answer("Режим CMD запущен.")
        await cmd_menu(message)

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text in ("Завершить CMD", "Закрыть CMD")
    )
    async def end_cmd(message: types.Message):
        if not power_mode.get(message.from_user.id, False):
            if not cmd_mode.get(message.from_user.id, False):
                await message.answer("Режим CMD не запущен!")
            else:
                cmd_mode[message.from_user.id] = False
                in_cmd_menu[message.from_user.id] = False
                write_bot_log(f"Пользователь {message.from_user.id} завершил режим CMD.")
                await message.answer("Режим CMD завершён.")
        await cmd_menu(message)

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        cmd_mode.get(message.from_user.id, False) and
        in_cmd_menu.get(message.from_user.id, False) and
        message.text not in ("Запуск CMD",
                             "Завершить CMD",
                             "Назад в меню",
                             "cmd",
                             "Питание")
    )
    async def execute_cmd(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} выполнил команду CMD: {message.text}")
        try:
            result = subprocess.run(
                message.text,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            output = result.stdout.strip() or result.stderr.strip() or "Команда выполнена без вывода."
            if len(output) > 4000:
                for chunk in (output[i:i+4000] for i in range(0, len(output), 4000)):
                    await message.answer(chunk)
            else:
                await message.answer(output)
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
