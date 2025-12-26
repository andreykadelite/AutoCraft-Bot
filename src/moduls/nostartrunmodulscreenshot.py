# -*- coding: utf-8 -*-
"""
nostartrunmodulscreenshot_v2.py

Модуль "Скриншот":
- Работает по кнопке/тексту: "Скриншот" (как раньше)
- И работает по команде из Bot Menu: /screenshot  ✅

Остальной функционал сохранён.
"""

from datetime import datetime
from pathlib import Path

import pyautogui
from PIL import ImageDraw
from aiogram import types
from aiogram.dispatcher import Dispatcher

# Авторизация и логирование — как в твоих модулях
try:
    from __main__ import authorized_users, write_bot_log  # type: ignore
except Exception:
    authorized_users = set()

    def write_bot_log(*args, **kwargs):  # type: ignore
        return


# Реестр главного меню (динамическое "Главное меню").
# Если mainmenu_registry отсутствует, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item
except ImportError:
    register_main_item = None


def _register_mainmenu_item() -> None:
    """
    Регистрирует кнопку 'Скриншот' в реестре главного меню.
    """
    if register_main_item is None:
        return

    try:
        register_main_item(
            key="screenshot_root",
            title="Скриншот",
            trigger_text="Скриншот",
            group="main",
            order=15,
            description="Сделать скриншот экрана",
        )
    except Exception:
        # Не роняем модуль, если что-то пошло не так при регистрации.
        pass


# Базовая папка для скриншотов (относительно рабочего каталога бота)
SCREENSHOT_DIR = Path("screenshots")


def ensure_screenshot_dir() -> Path:
    """Гарантирует существование папки screenshots."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


async def make_and_send_screenshot(message: types.Message) -> None:
    """
    Делает скриншот экрана с выделенным курсором, сохраняет его в папку screenshots
    и отправляет в Telegram, а затем отправляет сообщение с разрешением экрана.
    """
    user_id = message.from_user.id

    try:
        screenshots_path = ensure_screenshot_dir()

        screen_width, screen_height = pyautogui.size()
        cursor_x, cursor_y = pyautogui.position()

        image = pyautogui.screenshot()

        # Рисуем выделение курсора красным кругом
        draw = ImageDraw.Draw(image)
        radius = 25
        left_up = (cursor_x - radius, cursor_y - radius)
        right_down = (cursor_x + radius, cursor_y + radius)

        for offset in range(0, 4):
            draw.ellipse(
                (
                    left_up[0] - offset,
                    left_up[1] - offset,
                    right_down[0] + offset,
                    right_down[1] + offset,
                ),
                outline="red",
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}_{user_id}.png"
        full_path = screenshots_path / filename

        image.save(full_path)

        write_bot_log(
            f"Пользователь {user_id} сделал скриншот: {full_path}, "
            f"разрешение {screen_width}x{screen_height}, "
            f"курсор ({cursor_x}, {cursor_y})."
        )

        caption = (
            "📸 Скриншот экрана.\n"
            "Курсор выделен *красным кругом* для наглядности."
        )
        with full_path.open("rb") as photo_file:
            await message.answer_photo(
                photo=photo_file,
                caption=caption,
                parse_mode="Markdown",
            )

        await message.answer(f"Текущее разрешение экрана: {screen_width}×{screen_height}.")

    except Exception as exc:
        write_bot_log(f"Ошибка при создании скриншота для пользователя {user_id}: {exc}")
        await message.answer(f"⚠️ Не удалось сделать скриншот: {exc}")


def register_handlers(dp: Dispatcher) -> None:
    """
    Регистрация обработчиков для модуля скриншотов.
    """
    _register_mainmenu_item()

    # ✅ Команда из Bot Menu: /screenshot
    @dp.message_handler(commands=["screenshot"], state="*")
    async def handle_screenshot_command(message: types.Message):
        if message.from_user.id not in authorized_users:
            await message.answer("⛔ Сначала авторизация.")
            return
        await message.answer("Делаю скриншот экрана, подожди секунду...")
        await make_and_send_screenshot(message)

    # ✅ Кнопка/текст: "Скриншот" (как раньше)
    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users and (message.text or "") == "Скриншот",
        state="*",
    )
    async def handle_screenshot_text(message: types.Message):
        await message.answer("Делаю скриншот экрана, подожди секунду...")
        await make_and_send_screenshot(message)
