# -*- coding: utf-8 -*-
"""
nostartrunmodulscreenshot_fixed.py

Модуль "Скриншот":
- Работает по кнопке/тексту: "Скриншот" (как раньше)
- И работает по команде из Bot Menu: /screenshot ✅

Доработки:
- Скриншот ВСЕГДА сохраняется на диск (screenshots/), даже если Telegram не отправил медиа.
- Понятные сообщения об ошибках отправки (таймауты/сеть/флуд-контроль/прочее).
- Фолбэк: если отправка как фото не прошла, пробуем отправить как документ.
- В ответе всегда есть путь к сохранённому файлу.
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pyautogui
from PIL import ImageDraw

from aiogram import types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import exceptions as tg_exc

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
    """Регистрирует кнопку 'Скриншот' в реестре главного меню."""
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

# Сколько ждать ответа Telegram при отправке медиа (если сеть/ТГ «тормозит»)
SEND_TIMEOUT_SEC = 35

# Если отправка как фото не прошла, пробовать отправлять как документ
FALLBACK_SEND_AS_DOCUMENT = True


def ensure_screenshot_dir() -> Path:
    """Гарантирует существование папки screenshots."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


def _format_local_path(p: Path) -> str:
    """Аккуратный абсолютный путь для сообщения пользователю."""
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def _classify_send_error(exc: Exception) -> Tuple[str, str]:
    """Возвращает (заголовок, человеческое пояснение)."""
    if isinstance(exc, tg_exc.RetryAfter):
        return (
            "⏳ Флуд-контроль Telegram",
            f"Telegram попросил подождать ~{getattr(exc, 'timeout', 'несколько')} сек и повторить отправку.",
        )

    if isinstance(exc, (tg_exc.NetworkError, asyncio.TimeoutError)):
        return (
            "🌐 Проблема сети/таймаут",
            "Соединение с Telegram нестабильно или медиа отправляется слишком долго (таймаут). "
            "Такое бывает при блокировках/фильтрации трафика, VPN/прокси или перегрузе сети.",
        )

    if isinstance(exc, tg_exc.TelegramAPIError):
        return (
            "⚠️ Ошибка Telegram API",
            f"Telegram вернул ошибку при отправке медиа: {exc}",
        )

    return (
        "⚠️ Не удалось отправить медиа",
        f"Ошибка: {exc}",
    )


async def _safe_send_photo(
    message: types.Message,
    full_path: Path,
    caption: str,
    parse_mode: Optional[str] = None,
) -> None:
    with full_path.open("rb") as photo_file:
        coro = message.answer_photo(photo=photo_file, caption=caption, parse_mode=parse_mode)
        await asyncio.wait_for(coro, timeout=SEND_TIMEOUT_SEC)


async def _safe_send_document(
    message: types.Message,
    full_path: Path,
    caption: Optional[str] = None,
) -> None:
    with full_path.open("rb") as doc_file:
        input_file = types.InputFile(doc_file, filename=full_path.name)
        coro = message.answer_document(document=input_file, caption=caption)
        await asyncio.wait_for(coro, timeout=SEND_TIMEOUT_SEC)


async def make_and_send_screenshot(message: types.Message) -> None:
    """
    Делает скриншот экрана с выделенным курсором, сохраняет его в папку screenshots
    и пытается отправить в Telegram. Если отправка не удалась, сообщает причину и путь к файлу.
    """
    user_id = message.from_user.id

    full_path: Optional[Path] = None
    screen_width = screen_height = 0
    cursor_x = cursor_y = 0

    # 1) Сохраняем скрин на диск (это приоритет)
    try:
        screenshots_path = ensure_screenshot_dir()

        screen_width, screen_height = pyautogui.size()
        cursor_x, cursor_y = pyautogui.position()

        image = pyautogui.screenshot()

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
            f"[SCREENSHOT] user={user_id} saved={_format_local_path(full_path)} "
            f"res={screen_width}x{screen_height} cursor=({cursor_x},{cursor_y})"
        )

    except Exception as exc:
        write_bot_log(f"[SCREENSHOT] user={user_id} CAPTURE_ERROR: {exc}\n{traceback.format_exc()}")
        await message.answer(f"⚠️ Не удалось сделать скриншот: {exc}")
        return

    local_path_text = _format_local_path(full_path)
    caption = "📸 Скриншот экрана.\nКурсор выделен *красным кругом* для наглядности."

    # 2) Пытаемся отправить в Telegram
    try:
        await _safe_send_photo(message, full_path, caption=caption, parse_mode="Markdown")
        await message.answer(f"Текущее разрешение экрана: {screen_width}×{screen_height}.")
        return

    except Exception as exc_photo:
        title, human = _classify_send_error(exc_photo)
        write_bot_log(f"[SCREENSHOT] user={user_id} SEND_PHOTO_ERROR: {exc_photo}\n{traceback.format_exc()}")

        if FALLBACK_SEND_AS_DOCUMENT:
            try:
                await _safe_send_document(
                    message,
                    full_path,
                    caption="📎 Скриншот отправлен файлом (документ).",
                )
                await message.answer(f"Текущее разрешение экрана: {screen_width}×{screen_height}.")
                await message.answer(
                    "ℹ️ Фото-отправка не прошла, поэтому отправил как документ. "
                    "При проблемах с медиа это часто работает стабильнее."
                )
                return
            except Exception as exc_doc:
                title2, human2 = _classify_send_error(exc_doc)
                write_bot_log(f"[SCREENSHOT] user={user_id} SEND_DOC_ERROR: {exc_doc}\n{traceback.format_exc()}")

                await message.answer(
                    f"{title2}\n"
                    f"{human2}\n\n"
                    f"✅ Но скриншот сохранён на диске:\n{local_path_text}\n\n"
                    f"Текущее разрешение экрана: {screen_width}×{screen_height}."
                )
                return

        await message.answer(
            f"{title}\n"
            f"{human}\n\n"
            f"✅ Но скриншот сохранён на диске:\n{local_path_text}\n\n"
            f"Текущее разрешение экрана: {screen_width}×{screen_height}."
        )


def register_handlers(dp: Dispatcher) -> None:
    """Регистрация обработчиков для модуля скриншотов."""
    _register_mainmenu_item()

    @dp.message_handler(commands=["screenshot"], state="*")
    async def handle_screenshot_command(message: types.Message):
        if message.from_user.id not in authorized_users:
            await message.answer("⛔ Сначала авторизация.")
            return
        await message.answer("Делаю скриншот экрана, подожди секунду...")
        await make_and_send_screenshot(message)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users and (message.text or "") == "Скриншот",
        state="*",
    )
    async def handle_screenshot_text(message: types.Message):
        await message.answer("Делаю скриншот экрана, подожди секунду...")
        await make_and_send_screenshot(message)
