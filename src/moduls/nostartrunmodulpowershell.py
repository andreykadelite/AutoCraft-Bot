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
    Регистрирует кнопку 'powershell' в реестре главного меню.

    Кнопка:
    - title:        "powershell" (то, что видит пользователь)
    - trigger_text: "powershell" (то, что обрабатывает handler)
    - group:        "main"
    """
    if register_main_item is None:
        return

    try:
        register_main_item(
            key="powershell_root",
            title="powershell",
            trigger_text="powershell",
            group="main",
            order=40,
            description="Режим PowerShell"
        )
    except Exception:
        # Не роняем модуль, если что-то пошло не так при регистрации.
        pass

powershell_mode = {}
in_powershell_menu = {}

def register_handlers(dp):
    # При регистрации обработчиков регистрируем и кнопку в главном меню.
    _register_mainmenu_item()
    # Вернёмся в главное меню PowerShell-только по авторизации
    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Назад в меню" and
        powershell_mode.get(message.from_user.id, False)
    )
    async def powershell_back_to_main(message: types.Message):
        in_powershell_menu[message.from_user.id] = False
        keyboard = get_main_keyboard()
        await message.answer(
            "Возвращаюсь в главное меню. Режим PowerShell активен.",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "powershell"
    )
    async def powershell_menu(message: types.Message):
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["Запуск PowerShell", "Завершить PowerShell", "Назад в меню"]
        if powershell_mode.get(message.from_user.id, False):
            buttons += [
                "Get-ChildItem",
                "Get-Process",
                "Get-Service",
                "Get-Date",
                "Get-Location",
                "Test-Connection 8.8.8.8"
            ]
        keyboard.add(*buttons)

        if powershell_mode.get(message.from_user.id, False):
            in_powershell_menu[message.from_user.id] = True
            await message.answer("Режим PowerShell активен. Выберите команду.", reply_markup=keyboard)
        else:
            await message.answer(
                "Режим PowerShell не активен. Запустите его кнопкой «Запуск PowerShell».",
                reply_markup=keyboard
            )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Запуск PowerShell"
    )
    async def start_powershell(message: types.Message):
        if powershell_mode.get(message.from_user.id, False):
            await message.answer("Режим PowerShell уже запущен!")
        else:
            powershell_mode[message.from_user.id] = True
            in_powershell_menu[message.from_user.id] = True
            write_bot_log(f"Пользователь {message.from_user.id} запустил режим PowerShell.")
            await message.answer("Режим PowerShell запущен.")
        await powershell_menu(message)

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text in ("Завершить PowerShell", "Закрыть PowerShell")
    )
    async def end_powershell(message: types.Message):
        if not power_mode.get(message.from_user.id, False):
            if not powershell_mode.get(message.from_user.id, False):
                await message.answer("Режим PowerShell не запущен!")
            else:
                powershell_mode[message.from_user.id] = False
                in_powershell_menu[message.from_user.id] = False
                write_bot_log(f"Пользователь {message.from_user.id} завершил режим PowerShell.")
                await message.answer("Режим PowerShell завершён.")
        await powershell_menu(message)

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        powershell_mode.get(message.from_user.id, False) and
        in_powershell_menu.get(message.from_user.id, False) and
        message.text not in ("Запуск PowerShell",
                             "Завершить PowerShell",
                             "Назад в меню",
                             "powershell",
                             "Питание")
    )
    async def execute_powershell(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} выполнил команду PowerShell: {message.text}")
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    message.text
                ],
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
