
import os
from datetime import datetime
from pathlib import Path

import pyautogui
from PIL import ImageDraw
from aiogram import types
from aiogram.dispatcher import Dispatcher

from __main__ import authorized_users, write_bot_log


# Базовая папка для скриншотов (относительно рабочего каталога бота)
SCREENSHOT_DIR = Path("screenshots")


def ensure_screenshot_dir() -> Path:
    """
    Гарантирует существование папки screenshots.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


async def make_and_send_screenshot(message: types.Message):
    """
    Делает скриншот экрана с выделенным курсором, сохраняет его в папку screenshots
    и отправляет в Telegram, а затем отправляет сообщение с разрешением экрана.
    """
    user_id = message.from_user.id

    try:
        # Гарантируем наличие папки
        screenshots_path = ensure_screenshot_dir()

        # Текущее разрешение экрана и позиция курсора
        screen_width, screen_height = pyautogui.size()
        cursor_x, cursor_y = pyautogui.position()

        # Делаем скриншот
        image = pyautogui.screenshot()

        # Рисуем выделение курсора красным кругом
        draw = ImageDraw.Draw(image)
        radius = 25
        left_up = (cursor_x - radius, cursor_y - radius)
        right_down = (cursor_x + radius, cursor_y + radius)

        # Красный круг с толстой обводкой
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

        # Имя файла: screenshot_YYYYmmdd_HHMMSS_userid.png
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}_{user_id}.png"
        full_path = screenshots_path / filename

        # Сохраняем скриншот
        image.save(full_path)

        write_bot_log(
            f"Пользователь {user_id} сделал скриншот: {full_path}, "
            f"разрешение {screen_width}x{screen_height}, "
            f"курсор ({cursor_x}, {cursor_y})."
        )

        # Отправляем скриншот в Telegram
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

        # Отправляем отдельным сообщением текущее разрешение
        await message.answer(
            f"Текущее разрешение экрана: {screen_width}×{screen_height}."
        )

    except Exception as exc:
        # Логируем и сообщаем об ошибке пользователю
        write_bot_log(f"Ошибка при создании скриншота для пользователя {user_id}: {exc}")
        await message.answer(f"⚠️ Не удалось сделать скриншот: {exc}")


def register_handlers(dp: Dispatcher):
    """
    Регистрация обработчиков для модуля скриншотов.
    """

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users
        and message.text == "Скриншот"
    )
    async def handle_screenshot(message: types.Message):
        """
        Обработчик кнопки/команды «Скриншот».
        Делает скриншот с выделенным курсором и отправляет в чат.
        """
        await message.answer("Делаю скриншот экрана, подожди секунду...")
        await make_and_send_screenshot(message)
