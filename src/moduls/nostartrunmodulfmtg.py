import asyncio
import io
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from aiogram import types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import exceptions as tg_exc

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility  # регистрация в реестре утилит

# --- Состояние файлового менеджера по пользователям ---
fileman_mode: Dict[int, bool] = {}  # user_id -> bool (активен ли модуль)
fileman_current_path: Dict[int, Optional[str]] = {}  # user_id -> str | None (текущий каталог или None, если список дисков)
fileman_page: Dict[int, int] = {}  # user_id -> int (номер страницы каталога)

# Контекстное меню одиночного файла
fileman_in_context_menu: Dict[int, bool] = {}  # user_id -> bool (открыто ли контекстное меню файла)
fileman_selected_file: Dict[int, Optional[str]] = {}  # user_id -> str | None (полный путь к выбранному файлу)

# Режим выделения объектов
fileman_selection_mode: Dict[int, bool] = {}  # user_id -> bool (включён ли режим «Выбор объектов»)
fileman_selected_entries: Dict[int, Set[str]] = {}  # user_id -> set(full_path) выделенных объектов

# Буфер обмена для операций копирования/вырезания
fileman_clipboard: Dict[int, Dict[str, object]] = {}  # user_id -> {"paths": List[str], "cut": bool}

# Буфер подтверждения удаления объектов
fileman_delete_confirm: Dict[int, Dict[str, object]] = {}  # user_id -> {"paths": List[str]}

# Контекстное меню для группы объектов
fileman_in_multi_context_menu: Dict[int, bool] = {}  # user_id -> bool
fileman_rename_target: Dict[int, Optional[str]] = {}  # user_id -> full_path объекта для переименования

PAGE_SIZE = 20
SELECTION_PREFIX = "выд. "

# Тексты служебных кнопок
BTN_BACK_UTILS = "Назад в утилиты"
BTN_UP_DIR = "⬅️ В предыдущую папку"
BTN_PREV_PAGE = "⬅️ Предыдущая страница"
BTN_NEXT_PAGE = "➡️ Следующая страница"

BTN_FILE_DOWNLOAD = "Скачать в телеграмм"
BTN_FILE_OPEN = "Открыть на компьютере"
BTN_FILE_INFO = "Информация о файле"
BTN_FILE_RENAME = "Переименовать объект"
BTN_FILE_DELETE = "Удалить объект"
BTN_FILE_CLOSE_MENU = "Закрыть меню"

# Кнопки режима выбора / групповых действий
BTN_SELECT_OBJECTS = "Выбор объектов"
BTN_CANCEL_SELECT_OBJECTS = "Отменить выбор объектов"
BTN_OBJECTS_ACTIONS_MENU = "Меню действий с объектами"
BTN_PASTE_OBJECTS = "Вставить объекты"

# Кнопки контекстного меню для нескольких объектов
BTN_MULTI_COPY = "Копировать"
BTN_MULTI_CUT = "Вырезать"
BTN_MULTI_RENAME = "Переименовать"
BTN_MULTI_DELETE = "Удалить объекты"
BTN_MULTI_CLOSE_MENU = "Закрыть контекстное меню"

BTN_CONFIRM_DELETE = "Да, удалить"
BTN_CANCEL_DELETE = "Отмена удаления"

# Параметры лимитов Telegram API
LOCAL_API_FILE_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
STANDARD_API_SEND_LIMIT_BYTES = 50 * 1024 * 1024
STANDARD_API_RECEIVE_LIMIT_BYTES = 20 * 1024 * 1024

# Параметры отслеживания отправки файла
UPLOAD_PROGRESS_INTERVAL_SEC = 10
UPLOAD_STALL_TIMEOUT_SEC = 120
UPLOAD_TIMEOUT_MIN_SEC = 120
UPLOAD_TIMEOUT_MAX_SEC = 4 * 60 * 60
UPLOAD_MIN_SPEED_BPS_FOR_TIMEOUT = 256 * 1024  # 256 КБ/с
UPLOAD_CANCEL_CALLBACK_PREFIX = "fm_upload_cancel:"

# Активные отправки файлов (по пользователю)
fileman_upload_transfers: Dict[int, Dict[str, Any]] = {}


class UploadCancelledError(Exception):
    """Отправка файла была отменена пользователем или сторожем таймаута."""


class UploadProgressTracker:
    """
    Потокобезопасный трекер прогресса отправки файла.
    Отдельный lock нужен, потому что чтение файла может происходить вне основного потока.
    """

    def __init__(self, total_bytes: int):
        now = time.monotonic()
        self._lock = threading.Lock()
        self.total_bytes = max(total_bytes, 0)
        self.sent_bytes = 0
        self.started_at = now
        self.last_progress_at = now
        self.finished = False
        self.success = False
        self.cancel_requested = False
        self.cancel_reason = ""
        self.error_text = ""

    def advance(self, delta: int) -> None:
        if delta <= 0:
            return
        with self._lock:
            self.sent_bytes += delta
            if self.total_bytes > 0 and self.sent_bytes > self.total_bytes:
                self.sent_bytes = self.total_bytes
            self.last_progress_at = time.monotonic()

    def request_cancel(self, reason: str) -> bool:
        with self._lock:
            if self.finished or self.cancel_requested:
                return False
            self.cancel_requested = True
            self.cancel_reason = reason
            return True

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def get_cancel_reason(self) -> str:
        with self._lock:
            return self.cancel_reason

    def finish(self, success: bool, error_text: str = "") -> None:
        with self._lock:
            self.finished = True
            self.success = success
            self.error_text = error_text
            self.last_progress_at = time.monotonic()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            elapsed = max(now - self.started_at, 0.001)
            sent = max(self.sent_bytes, 0)
            total = max(self.total_bytes, 0)
            remaining = max(total - sent, 0)
            speed_bps = sent / elapsed if elapsed > 0 else 0.0
            eta_seconds: Optional[float] = None
            if speed_bps > 0 and remaining > 0:
                eta_seconds = remaining / speed_bps
            percent = 100.0 if total == 0 else min((sent / total) * 100.0, 100.0)
            return {
                "sent_bytes": sent,
                "total_bytes": total,
                "remaining_bytes": remaining,
                "elapsed_seconds": elapsed,
                "speed_bps": speed_bps,
                "eta_seconds": eta_seconds,
                "percent": percent,
                "finished": self.finished,
                "success": self.success,
                "cancel_requested": self.cancel_requested,
                "cancel_reason": self.cancel_reason,
                "error_text": self.error_text,
                "last_progress_at": self.last_progress_at,
            }


class TrackedFileReader(io.BufferedReader):
    """Файловый объект с отслеживанием прогресса и поддержкой принудительной отмены."""

    def __init__(self, path: str, tracker: UploadProgressTracker):
        self._raw_file = open(path, "rb")
        super().__init__(self._raw_file)
        self._tracker = tracker

    def read(self, size: int = -1) -> bytes:
        if self._tracker.is_cancel_requested():
            raise UploadCancelledError(
                self._tracker.get_cancel_reason() or "Отправка была отменена."
            )
        data = super().read(size)
        if data:
            self._tracker.advance(len(data))
        return data


def get_telegram_api_limits(bot) -> Tuple[bool, str, int, int]:
    """
    Возвращает (is_local_api, api_label, send_limit_bytes, receive_limit_bytes).
    """
    base_url: Optional[str] = None
    try:
        server = getattr(bot, "server", None)
        if server:
            base_url = (
                getattr(server, "base", None)
                or getattr(server, "_base", None)
                or getattr(server, "_base_url", None)
            )
    except Exception:
        base_url = None

    if isinstance(base_url, str) and base_url and not base_url.startswith(
        "https://api.telegram.org"
    ):
        return (
            True,
            f"локальный Telegram API ({base_url})",
            LOCAL_API_FILE_LIMIT_BYTES,
            LOCAL_API_FILE_LIMIT_BYTES,
        )

    return (
        False,
        "стандартный Telegram API (api.telegram.org)",
        STANDARD_API_SEND_LIMIT_BYTES,
        STANDARD_API_RECEIVE_LIMIT_BYTES,
    )


def format_megabytes(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.2f} МБ"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "неизвестно"
    sec = max(int(seconds), 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h} ч {m:02d} мин {s:02d} сек"
    if m > 0:
        return f"{m} мин {s:02d} сек"
    return f"{s} сек"


def calc_upload_timeout(total_bytes: int) -> int:
    dynamic = int(total_bytes / UPLOAD_MIN_SPEED_BPS_FOR_TIMEOUT) + 60
    return max(UPLOAD_TIMEOUT_MIN_SEC, min(dynamic, UPLOAD_TIMEOUT_MAX_SEC))


def build_upload_cancel_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "Отмена отправки",
            callback_data=f"{UPLOAD_CANCEL_CALLBACK_PREFIX}{user_id}",
        )
    )
    return kb


def build_upload_progress_text(
    file_name: str,
    api_label: str,
    send_limit_bytes: int,
    receive_limit_bytes: int,
    snapshot: Dict[str, Any],
) -> str:
    sent = int(snapshot.get("sent_bytes", 0))
    total = int(snapshot.get("total_bytes", 0))
    remaining = int(snapshot.get("remaining_bytes", 0))
    speed_bps = float(snapshot.get("speed_bps", 0.0))
    percent = float(snapshot.get("percent", 0.0))
    eta_seconds = snapshot.get("eta_seconds")

    lines = [
        "📤 Загрузка файла в Telegram",
        f"Файл: {file_name}",
        f"Подключение: {api_label}",
        (
            "Лимиты: "
            f"отправка {human_readable_size(send_limit_bytes)}, "
            f"получение {human_readable_size(receive_limit_bytes)}"
        ),
        "",
        f"Процент: {percent:.2f}%",
        f"Скорость: {speed_bps / (1024 * 1024):.2f} МБ/с",
        f"Загружено: {format_megabytes(sent)}",
        f"Осталось: {format_megabytes(remaining)}",
        f"Общий размер: {format_megabytes(total)}",
    ]

    if remaining > 0:
        lines.append(f"Осталось по времени: {format_duration(eta_seconds)}")

    if snapshot.get("finished") and snapshot.get("success"):
        lines.append("Статус: ✅ Отправка завершена.")
    elif snapshot.get("cancel_requested"):
        reason = snapshot.get("cancel_reason") or "Отправка отменена."
        lines.append(f"Статус: 🛑 {reason}")
    elif snapshot.get("finished") and not snapshot.get("success"):
        err = snapshot.get("error_text") or "Не удалось отправить файл."
        lines.append(f"Статус: ❌ {err}")
    else:
        lines.append("Статус: ⏳ Идёт отправка... Нажми «Отмена отправки», если нужно остановить.")

    return "\n".join(lines)


def classify_upload_error(exc: Exception) -> Tuple[str, str]:
    if isinstance(exc, UploadCancelledError):
        return (
            "🛑 Отправка файла остановлена",
            str(exc),
        )

    if isinstance(exc, tg_exc.RetryAfter):
        timeout = getattr(exc, "timeout", None)
        wait_text = f"{timeout} сек" if timeout else "несколько секунд"
        return (
            "⏳ Ограничение Telegram (flood control)",
            f"Telegram попросил подождать {wait_text} и повторить отправку.",
        )

    if isinstance(exc, (tg_exc.NetworkError, asyncio.TimeoutError)):
        return (
            "🌐 Проблема сети или таймаут",
            "Отправка зависла по сети. Возможна блокировка медиа-трафика, "
            "нестабильный интернет, VPN/прокси или недоступность API.",
        )

    if isinstance(exc, tg_exc.BadRequest):
        text = str(exc)
        text_l = text.lower()
        if "file is too big" in text_l:
            return (
                "⚠️ Файл слишком большой для выбранного Telegram API",
                text,
            )
        if "wrong file identifier" in text_l:
            return (
                "⚠️ Telegram отклонил файл",
                "Telegram не принял файл. Попробуй переоткрыть файл и отправить снова.",
            )
        return (
            "⚠️ Telegram отклонил отправку",
            text,
        )

    if isinstance(exc, tg_exc.TelegramAPIError):
        return (
            "⚠️ Ошибка Telegram API",
            str(exc),
        )

    return (
        "⚠️ Не удалось отправить файл",
        f"{type(exc).__name__}: {exc}",
    )


async def safe_edit_message_text(
    bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except tg_exc.MessageNotModified:
        return
    except tg_exc.MessageToEditNotFound:
        return
    except Exception as exc:
        write_bot_log(f"[fileman] Не удалось обновить сообщение прогресса: {exc}")


async def monitor_upload_progress(
    bot,
    user_id: int,
    transfer_state: Dict[str, Any],
) -> None:
    """
    Фоновый монитор:
    - обновляет прогресс каждые 10 сек;
    - если долго нет прогресса, отменяет отправку, чтобы не висела бесконечно.
    """
    tracker = transfer_state.get("tracker")
    if not isinstance(tracker, UploadProgressTracker):
        return

    chat_id = transfer_state.get("chat_id")
    message_id = transfer_state.get("progress_message_id")
    file_name = transfer_state.get("file_name", "file")
    api_label = transfer_state.get("api_label", "Telegram API")
    send_limit_bytes = int(transfer_state.get("send_limit_bytes", 0))
    receive_limit_bytes = int(transfer_state.get("receive_limit_bytes", 0))

    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return

    last_text = ""

    while True:
        await asyncio.sleep(UPLOAD_PROGRESS_INTERVAL_SEC)
        snap = tracker.snapshot()

        # Если отправка зависла без прогресса — отменяем.
        if not snap["finished"] and not snap["cancel_requested"]:
            stuck_sec = time.monotonic() - float(snap["last_progress_at"])
            if (
                snap["sent_bytes"] < snap["total_bytes"]
                and stuck_sec >= UPLOAD_STALL_TIMEOUT_SEC
            ):
                reason = (
                    "Отправка остановлена: нет прогресса более "
                    f"{UPLOAD_STALL_TIMEOUT_SEC} сек."
                )
                tracker.request_cancel(reason)
                upload_task = transfer_state.get("upload_task")
                if isinstance(upload_task, asyncio.Task) and not upload_task.done():
                    upload_task.cancel()
                write_bot_log(f"[fileman] user={user_id} send stalled, cancel requested.")
                snap = tracker.snapshot()

        text = build_upload_progress_text(
            file_name=file_name,
            api_label=api_label,
            send_limit_bytes=send_limit_bytes,
            receive_limit_bytes=receive_limit_bytes,
            snapshot=snap,
        )
        if text != last_text:
            markup = (
                None
                if snap["finished"] or snap["cancel_requested"]
                else build_upload_cancel_keyboard(user_id)
            )
            await safe_edit_message_text(
                bot=bot,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
            )
            last_text = text

        if snap["finished"] or snap["cancel_requested"]:
            break


def request_cancel_upload(user_id: int, reason: str) -> bool:
    transfer_state = fileman_upload_transfers.get(user_id)
    if not transfer_state:
        return False

    tracker = transfer_state.get("tracker")
    if not isinstance(tracker, UploadProgressTracker):
        return False

    changed = tracker.request_cancel(reason)
    upload_task = transfer_state.get("upload_task")
    if isinstance(upload_task, asyncio.Task) and not upload_task.done():
        upload_task.cancel()
    return changed


def list_drives() -> List[str]:
    """
    Возвращает список доступных дисков в виде 'C:\\', 'D:\\' и т.д.
    """
    drives: List[str] = []
    for letter in range(ord("A"), ord("Z") + 1):
        drive = f"{chr(letter)}:\\"
        if os.path.isdir(drive):
            drives.append(drive)
    if not drives:
        # На всякий случай берём диск, где сейчас запущен скрипт
        current_drive = os.path.splitdrive(os.getcwd())[0] or "C:"
        drives.append(current_drive + "\\")
    return sorted(drives, key=str.upper)


def get_disks_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура выбора дисков.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for drive in list_drives():
        kb.add(drive)
    kb.add(BTN_BACK_UTILS)
    return kb


def is_root_path(path: str) -> bool:
    """
    Проверяет, является ли путь корнем диска (C:\\ и т.п.).
    """
    if not path:
        return False
    abs_path = os.path.abspath(path)
    drive, tail = os.path.splitdrive(abs_path)
    tail = tail.replace("/", "\\")
    return bool(drive) and tail in ("\\", "")


def get_sorted_entries(path: str) -> List[str]:
    """
    Возвращает список объектов в каталоге: сначала папки, потом файлы.
    """
    try:
        entries = os.listdir(path)
    except Exception:
        return []

    dirs: List[str] = []
    files: List[str] = []

    for name in entries:
        full = os.path.join(path, name)
        # Игнорируем объекты, которые не удаётся прочитать
        try:
            if os.path.isdir(full):
                dirs.append(name)
            else:
                files.append(name)
        except Exception:
            continue

    dirs.sort(key=str.lower)
    files.sort(key=str.lower)
    return dirs + files


def build_directory_keyboard(
    user_id: int, path: str, entries: List[str], page: int
) -> types.ReplyKeyboardMarkup:
    """
    Собирает клавиатуру для просмотра каталога с постраничной навигацией,
    режимом выбора объектов и буфером обмена.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    total = len(entries)
    selection_mode = fileman_selection_mode.get(user_id, False)
    selected_paths = fileman_selected_entries.get(user_id) or set()
    clipboard = fileman_clipboard.get(user_id)
    has_clipboard = bool(clipboard and clipboard.get("paths"))

    if total > 0:
        if page < 0:
            page = 0
        max_page = (total - 1) // PAGE_SIZE
        if page > max_page:
            page = max_page
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE
        for name in entries[start:end]:
            display_name = name
            if selection_mode:
                full = os.path.join(path, name)
                if full in selected_paths:
                    display_name = SELECTION_PREFIX + name
            kb.add(display_name)

    # Навигация по каталогам / страницам
    has_up_button = bool(path)

    if has_up_button:
        kb.add(BTN_UP_DIR)

    if total > PAGE_SIZE:
        # Добавляем кнопки страниц отдельной строкой
        page_nav: List[str] = []
        if page > 0:
            page_nav.append(BTN_PREV_PAGE)
        if (page + 1) * PAGE_SIZE < total:
            page_nav.append(BTN_NEXT_PAGE)
        if page_nav:
            kb.row(*page_nav)

    # Кнопки выбора / вставки объектов
    if has_clipboard:
        # Если в буфере что-то есть — вместо «Выбор объектов» показываем «Вставить объекты»
        kb.add(BTN_PASTE_OBJECTS)
    else:
        if selection_mode:
            kb.add(BTN_CANCEL_SELECT_OBJECTS)
            if selected_paths:
                kb.add(BTN_OBJECTS_ACTIONS_MENU)
        else:
            kb.add(BTN_SELECT_OBJECTS)

    # Кнопка выхода в утилиты
    kb.add(BTN_BACK_UTILS)
    return kb


def get_file_context_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура контекстного меню для выбранного файла.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(BTN_FILE_DOWNLOAD)
    kb.add(BTN_FILE_OPEN)
    kb.add(BTN_FILE_INFO)
    kb.add(BTN_FILE_RENAME)
    kb.add(BTN_FILE_DELETE)
    kb.add(BTN_FILE_CLOSE_MENU)
    kb.add(BTN_BACK_UTILS)
    return kb


def get_multi_context_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура контекстного меню для нескольких выделенных объектов.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(BTN_MULTI_COPY)
    kb.add(BTN_MULTI_CUT)
    kb.add(BTN_MULTI_RENAME)
    kb.add(BTN_MULTI_DELETE)
    kb.add(BTN_MULTI_CLOSE_MENU)
    kb.add(BTN_BACK_UTILS)
    return kb


def human_readable_size(num_bytes: int) -> str:
    """
    Читабельный размер файла.
    """
    step = 1024.0
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num_bytes)
    unit = 0
    while size >= step and unit < len(units) - 1:
        size /= step
        unit += 1
    return f"{size:.2f} {units[unit]}"


async def send_directory_listing(message: types.Message, user_id: int):
    """
    Отправляет пользователю список объектов текущего каталога с клавиатурой.
    """
    path = fileman_current_path.get(user_id)

    if not path or not os.path.isdir(path):
        # Возврат к списку дисков
        fileman_current_path[user_id] = None
        fileman_page[user_id] = 0
        # При показе дисков режим выделения неактивен
        fileman_selection_mode[user_id] = False
        fileman_selected_entries[user_id] = set()

        kb = get_disks_keyboard()
        await message.answer(
            "Список дисков.\nВыбери диск для просмотра содержимого:",
            reply_markup=kb,
        )
        return

    entries = get_sorted_entries(path)
    page = fileman_page.get(user_id, 0)

    total = len(entries)
    if total > 0:
        max_page = (total - 1) // PAGE_SIZE
        if page < 0:
            page = 0
        if page > max_page:
            page = max_page
        fileman_page[user_id] = page
        pages_text = f"Страница {page + 1} из {max_page + 1}."
    else:
        fileman_page[user_id] = 0
        pages_text = "Папка пуста."

    kb = build_directory_keyboard(user_id, path, entries, fileman_page[user_id])

    header = f"📁 Текущая папка:\n{path}\n\n"
    if total > 0:
        info = (
            f"Объектов: {total}. {pages_text}\n"
            "Выбери папку или файл.\n"
            "Чтобы выделять объекты, используй кнопку «Выбор объектов»."
        )
    else:
        info = (
            "В этой папке пока нет объектов.\n"
            "Используй «⬅️ В предыдущую папку» или «Назад в утилиты»."
        )
    await message.answer(header + info, reply_markup=kb)


async def send_file_to_telegram_with_progress(
    message: types.Message,
    user_id: int,
    path: str,
) -> None:
    """
    Отправляет файл в Telegram с периодическим обновлением прогресса и кнопкой отмены.
    """
    if not os.path.isfile(path):
        await message.answer("Файл недоступен или был перемещён.")
        return

    # Не допускаем параллельную отправку для одного пользователя.
    existing_transfer = fileman_upload_transfers.get(user_id)
    if existing_transfer:
        existing_tracker = existing_transfer.get("tracker")
        if isinstance(existing_tracker, UploadProgressTracker):
            snap = existing_tracker.snapshot()
            if not snap["finished"] and not snap["cancel_requested"]:
                await message.answer(
                    "Уже выполняется отправка другого файла.\n"
                    "Дождись завершения или нажми «Отмена отправки» в сообщении прогресса."
                )
                return
        fileman_upload_transfers.pop(user_id, None)

    file_name = os.path.basename(path)
    try:
        file_size = os.path.getsize(path)
    except Exception as exc:
        await message.answer(f"Не удалось определить размер файла: {exc}")
        return

    is_local_api, api_label, send_limit_bytes, receive_limit_bytes = (
        get_telegram_api_limits(message.bot)
    )
    if file_size > send_limit_bytes:
        await message.answer(
            "Файл слишком большой для текущего типа Telegram API.\n"
            f"Подключение: {api_label}\n"
            f"Размер файла: {human_readable_size(file_size)}\n"
            f"Лимит отправки: {human_readable_size(send_limit_bytes)}"
        )
        write_bot_log(
            "[fileman] user="
            f"{user_id} file={path} exceeds send limit "
            f"{file_size}>{send_limit_bytes} (local_api={is_local_api})"
        )
        return

    tracker = UploadProgressTracker(file_size)
    initial_snapshot = tracker.snapshot()

    progress_message = await message.answer(
        build_upload_progress_text(
            file_name=file_name,
            api_label=api_label,
            send_limit_bytes=send_limit_bytes,
            receive_limit_bytes=receive_limit_bytes,
            snapshot=initial_snapshot,
        ),
        reply_markup=build_upload_cancel_keyboard(user_id),
    )

    transfer_state: Dict[str, Any] = {
        "tracker": tracker,
        "chat_id": progress_message.chat.id,
        "progress_message_id": progress_message.message_id,
        "file_name": file_name,
        "file_path": path,
        "api_label": api_label,
        "send_limit_bytes": send_limit_bytes,
        "receive_limit_bytes": receive_limit_bytes,
        "upload_task": asyncio.current_task(),
    }
    fileman_upload_transfers[user_id] = transfer_state

    monitor_task = asyncio.create_task(
        monitor_upload_progress(message.bot, user_id, transfer_state)
    )
    transfer_state["monitor_task"] = monitor_task

    timeout_sec = calc_upload_timeout(file_size)
    write_bot_log(
        "[fileman] user="
        f"{user_id} send-start file={path} size={file_size} "
        f"api='{api_label}' timeout={timeout_sec}s"
    )

    try:
        try:
            with TrackedFileReader(path, tracker) as tracked_file:
                input_file = types.InputFile(tracked_file, filename=file_name)
                send_coro = message.answer_document(document=input_file, caption=file_name)
                await asyncio.wait_for(send_coro, timeout=timeout_sec)
        except TypeError as exc:
            # Защитный путь: в некоторых сборках/обёртках InputFile может
            # отвергать кастомный file-like объект. Тогда повторяем отправку
            # через обычный open(), как в старой рабочей реализации.
            if "Not supported file type" not in str(exc):
                raise

            write_bot_log(
                f"[fileman] user={user_id} tracked InputFile rejected, fallback to plain file: {exc}"
            )
            with open(path, "rb") as plain_file:
                send_coro = message.answer_document(document=plain_file, caption=file_name)
                await asyncio.wait_for(send_coro, timeout=timeout_sec)
            # В fallback-пути телеметрия чтения недоступна — отмечаем итог как полностью отправленный.
            tracker.advance(file_size)

        tracker.finish(success=True)
        await message.answer(f"✅ Файл «{file_name}» успешно отправлен в Telegram.")
        write_bot_log(f"[fileman] user={user_id} send-success file={path}")

    except asyncio.CancelledError:
        cancel_reason = tracker.get_cancel_reason() or "Отправка отменена."
        tracker.finish(success=False, error_text=cancel_reason)
        await message.answer(
            f"🛑 Отправка файла «{file_name}» отменена.\nПричина: {cancel_reason}"
        )
        write_bot_log(f"[fileman] user={user_id} send-cancelled file={path}: {cancel_reason}")

    except Exception as exc:
        title, details = classify_upload_error(exc)
        tracker.finish(success=False, error_text=f"{type(exc).__name__}: {exc}")
        await message.answer(
            f"{title}\n"
            f"{details}\n\n"
            "Файл остался на компьютере. Можешь повторить отправку позже."
        )
        write_bot_log(
            f"[fileman] user={user_id} send-error file={path}: "
            f"{type(exc).__name__}: {exc}"
        )

    finally:
        final_snapshot = tracker.snapshot()
        final_text = build_upload_progress_text(
            file_name=file_name,
            api_label=api_label,
            send_limit_bytes=send_limit_bytes,
            receive_limit_bytes=receive_limit_bytes,
            snapshot=final_snapshot,
        )
        await safe_edit_message_text(
            bot=message.bot,
            chat_id=progress_message.chat.id,
            message_id=progress_message.message_id,
            text=final_text,
            reply_markup=None,
        )

        monitor_task_obj = transfer_state.get("monitor_task")
        if isinstance(monitor_task_obj, asyncio.Task):
            monitor_task_obj.cancel()
            try:
                await monitor_task_obj
            except asyncio.CancelledError:
                pass
            except Exception as monitor_exc:
                write_bot_log(
                    f"[fileman] user={user_id} progress-monitor-stop-error: {monitor_exc}"
                )

        fileman_upload_transfers.pop(user_id, None)


def register_handlers(dp: Dispatcher):
    """
    Регистрация хендлеров файлового менеджера.
    """

    # Регистрируем утилиту в общем реестре, чтобы она появилась в меню "Утилиты"
    register_utility(
        key="filemanager",
        title="Файловый менеджер",
        trigger_text="Файловый менеджер",
        group="utilities",
        order=25,
        description="Файловый менеджер: просмотр дисков, каталогов, файлов, контекстное меню, копирование, перемещение и удаление",
    )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and message.text == "Файловый менеджер"
    )
    async def fileman_entry(message: types.Message):
        """
        Точка входа в файловый менеджер.
        """
        user_id = message.from_user.id

        request_cancel_upload(
            user_id,
            "Отправка остановлена: повторный вход в файловый менеджер.",
        )

        fileman_mode[user_id] = True
        fileman_current_path[user_id] = None  # показываем список дисков
        fileman_page[user_id] = 0

        fileman_in_context_menu[user_id] = False
        fileman_selected_file.pop(user_id, None)

        fileman_selection_mode[user_id] = False
        fileman_selected_entries[user_id] = set()

        fileman_clipboard.pop(user_id, None)
        fileman_delete_confirm.pop(user_id, None)
        fileman_in_multi_context_menu[user_id] = False
        fileman_rename_target.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Файловый менеджер'.")

        _, api_label, send_limit_bytes, receive_limit_bytes = get_telegram_api_limits(
            message.bot
        )

        kb = get_disks_keyboard()
        await message.answer(
            "📂 Файловый менеджер.\n"
            f"Подключение: {api_label}.\n"
            f"Лимит отправки файлов (ПК → Telegram): {human_readable_size(send_limit_bytes)}.\n"
            f"Лимит получения файлов (Telegram → бот): {human_readable_size(receive_limit_bytes)}.\n"
            "Сначала выбери диск, затем папку или файл.\n"
            "Для выхода используй кнопку «Назад в утилиты».",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and message.text == BTN_BACK_UTILS
    )
    async def fileman_back_to_utilities(message: types.Message):
        """
        Выход из файлового менеджера обратно в раздел утилит.
        """
        user_id = message.from_user.id

        upload_was_cancelled = request_cancel_upload(
            user_id,
            "Отправка остановлена: выход из файлового менеджера.",
        )

        fileman_mode[user_id] = False
        fileman_current_path.pop(user_id, None)
        fileman_page.pop(user_id, None)

        fileman_in_context_menu.pop(user_id, None)
        fileman_selected_file.pop(user_id, None)

        fileman_selection_mode.pop(user_id, None)
        fileman_selected_entries.pop(user_id, None)

        fileman_clipboard.pop(user_id, None)
        fileman_delete_confirm.pop(user_id, None)
        fileman_in_multi_context_menu.pop(user_id, None)
        fileman_rename_target.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} закрыл модуль 'Файловый менеджер'.")

        kb = get_utilities_keyboard()
        if upload_was_cancelled:
            text = "Возвращаюсь в раздел утилит. Активная отправка файла остановлена."
        else:
            text = "Возвращаюсь в раздел утилит."
        await message.answer(text, reply_markup=kb)

    @dp.callback_query_handler(
        lambda callback_query: callback_query.from_user.id in authorized_users
        and (callback_query.data or "").startswith(UPLOAD_CANCEL_CALLBACK_PREFIX)
    )
    async def fileman_cancel_upload(callback_query: types.CallbackQuery):
        """
        Отмена активной отправки файла по inline-кнопке в сообщении прогресса.
        """
        data = callback_query.data or ""
        try:
            target_user_id = int(data[len(UPLOAD_CANCEL_CALLBACK_PREFIX) :])
        except Exception:
            await callback_query.answer("Некорректные данные отмены.", show_alert=True)
            return

        caller_id = callback_query.from_user.id
        if caller_id != target_user_id:
            await callback_query.answer(
                "Эта кнопка относится к отправке другого пользователя.",
                show_alert=True,
            )
            return

        changed = request_cancel_upload(
            target_user_id,
            "Отправка отменена пользователем.",
        )
        if changed:
            await callback_query.answer("Останавливаю отправку файла...")
        else:
            await callback_query.answer("Отправка уже завершена или останавливается.")

    # --- Контекстное меню одиночного файла ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and fileman_in_context_menu.get(message.from_user.id, False)
        and message.text
        in {
            BTN_FILE_DOWNLOAD,
            BTN_FILE_OPEN,
            BTN_FILE_INFO,
            BTN_FILE_RENAME,
            BTN_FILE_DELETE,
            BTN_FILE_CLOSE_MENU,
        }
    )
    async def fileman_file_context_actions(message: types.Message):
        """
        Обработка действий контекстного меню файла.
        """
        user_id = message.from_user.id
        action = message.text
        path = fileman_selected_file.get(user_id)

        if not path or not os.path.isfile(path):
            fileman_in_context_menu[user_id] = False
            fileman_selected_file.pop(user_id, None)
            await message.answer(
                "Выбранный файл больше недоступен или был перемещён.\n"
                "Возвращаюсь к содержимому папки."
            )
            await send_directory_listing(message, user_id)
            return

        if action == BTN_FILE_DOWNLOAD:
            write_bot_log(
                f"Пользователь {user_id} запрашивает отправку файла в Telegram: {path}"
            )
            await send_file_to_telegram_with_progress(
                message=message,
                user_id=user_id,
                path=path,
            )
            return

        if action == BTN_FILE_OPEN:
            write_bot_log(
                f"Пользователь {user_id} открывает файл на компьютере: {path}"
            )
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                await message.answer("Команда на открытие файла отправлена на компьютер.")
            except Exception as e:
                await message.answer(f"Не удалось открыть файл: {e}")
            return

        if action == BTN_FILE_INFO:
            try:
                size = os.path.getsize(path)
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                ctime = datetime.fromtimestamp(os.path.getctime(path))
                info_lines = [
                    "Информация о файле:",
                    f"Полный путь: {path}",
                    f"Имя: {os.path.basename(path)}",
                    f"Размер: {human_readable_size(size)} ({size} байт)",
                    f"Создан: {ctime:%Y-%m-%d %H:%M:%S}",
                    f"Изменён: {mtime:%Y-%m-%d %H:%M:%S}",
                ]
                await message.answer("\n".join(info_lines))
            except Exception as e:
                await message.answer(f"Не удалось получить информацию о файле: {e}")
            return

        if action == BTN_FILE_RENAME:
            fileman_rename_target[user_id] = path
            fileman_in_context_menu[user_id] = False
            await message.answer(
                "Отправь новое имя для объекта (без пути):\n"
                f"{os.path.basename(path)}"
            )
            return

        if action == BTN_FILE_DELETE:
            # Подготовка к удалению с подтверждением
            fileman_in_context_menu[user_id] = False
            fileman_selected_file.pop(user_id, None)

            fileman_delete_confirm[user_id] = {"paths": [path]}

            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add(BTN_CONFIRM_DELETE)
            kb.add(BTN_CANCEL_DELETE)
            kb.add(BTN_BACK_UTILS)

            await message.answer(
                "Внимание! Ты собираешься удалить объект:\n"
                f"{path}\n\n"
                "Это действие необратимо. Подтверди удаление.",
                reply_markup=kb,
            )
            return

        if action == BTN_FILE_CLOSE_MENU:
            fileman_in_context_menu[user_id] = False
            fileman_selected_file.pop(user_id, None)
            await send_directory_listing(message, user_id)
            return

    # --- Контекстное меню для нескольких объектов ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and fileman_in_multi_context_menu.get(message.from_user.id, False)
        and message.text
        in {
            BTN_MULTI_COPY,
            BTN_MULTI_CUT,
            BTN_MULTI_RENAME,
            BTN_MULTI_DELETE,
            BTN_MULTI_CLOSE_MENU,
        }
    )
    async def fileman_multi_context_actions(message: types.Message):
        """
        Обработка действий контекстного меню для выделенных объектов.
        """
        user_id = message.from_user.id
        action = message.text
        selected_paths = fileman_selected_entries.get(user_id) or set()

        if action == BTN_MULTI_CLOSE_MENU:
            fileman_in_multi_context_menu[user_id] = False
            await send_directory_listing(message, user_id)
            return

        if not selected_paths:
            await message.answer("Список выделенных объектов пуст. Сначала выдели объекты.")
            fileman_in_multi_context_menu[user_id] = False
            await send_directory_listing(message, user_id)
            return

        if action in {BTN_MULTI_COPY, BTN_MULTI_CUT}:
            # Подготовка буфера обмена
            paths_list = list(selected_paths)
            fileman_clipboard[user_id] = {
                "paths": paths_list,
                "cut": action == BTN_MULTI_CUT,
            }
            write_bot_log(
                f"Пользователь {user_id} подготовил {len(paths_list)} объектов для "
                f"{'перемещения' if action == BTN_MULTI_CUT else 'копирования'}."
            )

            # Сброс режима выделения и закрытие меню
            fileman_selection_mode[user_id] = False
            fileman_selected_entries[user_id] = set()
            fileman_in_multi_context_menu[user_id] = False

            if action == BTN_MULTI_COPY:
                await message.answer(
                    "Объекты скопированы в буфер. Открой нужную папку и нажми «Вставить объекты»."
                )
            else:
                await message.answer(
                    "Объекты подготовлены к перемещению. Открой нужную папку и нажми «Вставить объекты»."
                )

            await send_directory_listing(message, user_id)
            return

        if action == BTN_MULTI_DELETE:
            paths_list = list(selected_paths)
            fileman_in_multi_context_menu[user_id] = False

            if not paths_list:
                await message.answer("Список выделенных объектов пуст.")
                await send_directory_listing(message, user_id)
                return

            fileman_delete_confirm[user_id] = {"paths": paths_list}

            names = [os.path.basename(p) for p in paths_list]
            preview = "\n".join(names[:10])
            more_count = max(0, len(names) - 10)

            text_lines = [
                f"Выбрано для удаления объектов: {len(names)}.",
            ]

            if preview:
                text_lines.append("Список (первые объекты):")
                text_lines.append(preview)
            if more_count:
                text_lines.append(f"… и ещё {more_count}.")

            text_lines.append(
                "\nВнимание! Удаление объектов необратимо. Подтверди действие."
            )

            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add(BTN_CONFIRM_DELETE)
            kb.add(BTN_CANCEL_DELETE)
            kb.add(BTN_BACK_UTILS)

            await message.answer("\n".join(text_lines), reply_markup=kb)
            return

        if action == BTN_MULTI_RENAME:
            if len(selected_paths) != 1:
                await message.answer(
                    "Переименование доступно только при выборе одного объекта."
                )
                return

            target_path = next(iter(selected_paths))
            if not os.path.exists(target_path):
                await message.answer(
                    "Выбранный объект для переименования больше не существует."
                )
                fileman_selected_entries[user_id] = set()
                fileman_selection_mode[user_id] = False
                fileman_in_multi_context_menu[user_id] = False
                await send_directory_listing(message, user_id)
                return

            fileman_rename_target[user_id] = target_path
            fileman_in_multi_context_menu[user_id] = False

            await message.answer(
                "Отправь новое имя для объекта (без пути):\n"
                f"{os.path.basename(target_path)}"
            )
            return

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and fileman_in_multi_context_menu.get(message.from_user.id, False)
    )
    async def fileman_multi_context_fallback(message: types.Message):
        """
        Фолбэк, пока открыто контекстное меню для нескольких объектов.
        """
        user_id = message.from_user.id
        kb = get_multi_context_keyboard()
        await message.answer(
            "Сейчас открыто контекстное меню для выбранных объектов.\n"
            "Используй кнопки для выбора действия или нажми "
            f"«{BTN_MULTI_CLOSE_MENU}» для возврата к папке.",
            reply_markup=kb,
        )

    # --- Обработка ввода нового имени при переименовании ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and fileman_rename_target.get(message.from_user.id)
    )
    async def fileman_rename_handler(message: types.Message):
        """
        Обработка нового имени объекта при переименовании.
        """
        user_id = message.from_user.id
        target_path = fileman_rename_target.get(user_id)
        if not target_path or not os.path.exists(target_path):
            fileman_rename_target.pop(user_id, None)
            await message.answer(
                "Объект для переименования недоступен. Возвращаюсь к папке."
            )
            await send_directory_listing(message, user_id)
            return

        new_name = (message.text or "").strip()
        if not new_name:
            await message.answer("Имя не может быть пустым. Введи другое имя.")
            return

        # Запрещённые символы для имён файлов в Windows
        forbidden = '\\/:*?"<>|'
        if any(ch in forbidden for ch in new_name):
            await message.answer(
                "Имя содержит недопустимые символы. "
                "Недопустимы: \\\\/:*?\"<>|"
            )
            return

        parent_dir = os.path.dirname(target_path)
        new_path = os.path.join(parent_dir, new_name)

        if os.path.exists(new_path):
            await message.answer(
                "Файл или папка с таким именем уже существует. Введи другое имя."
            )
            return

        try:
            os.rename(target_path, new_path)
            write_bot_log(
                f"Пользователь {user_id} переименовал объект: {target_path} -> {new_path}"
            )

            # Обновляем выделения, если объект был выделен
            selected = fileman_selected_entries.get(user_id)
            if selected and target_path in selected:
                selected.remove(target_path)
                selected.add(new_path)

            fileman_rename_target.pop(user_id, None)

            await message.answer(f"Объект переименован в: {new_name}")
            await send_directory_listing(message, user_id)
        except Exception as e:
            await message.answer(f"Не удалось переименовать объект: {e}")

        # --- Подтверждение удаления объектов ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and fileman_delete_confirm.get(message.from_user.id)
        and message.text in {BTN_CONFIRM_DELETE, BTN_CANCEL_DELETE}
    )
    async def fileman_delete_confirm_handler(message: types.Message):
        """
        Подтверждение или отмена удаления объектов.
        """
        user_id = message.from_user.id
        action = message.text
        data = fileman_delete_confirm.get(user_id)

        if not data or not data.get("paths"):
            fileman_delete_confirm.pop(user_id, None)
            await message.answer("Нет объектов для удаления.")
            await send_directory_listing(message, user_id)
            return

        paths: List[str] = list(data.get("paths", []))

        if action == BTN_CANCEL_DELETE:
            fileman_delete_confirm.pop(user_id, None)
            await message.answer("Удаление отменено.")
            await send_directory_listing(message, user_id)
            return

        # Подтверждено удаление
        success_count = 0
        error_lines: List[str] = []

        for p in paths:
            name = os.path.basename(p)
            if not os.path.exists(p):
                error_lines.append(f"⚠ '{name}' — объект не найден. Пропускаю.")
                continue
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                success_count += 1
            except Exception as e:
                error_lines.append(f"❌ '{name}': {e}")

        # Сброс состояний, связанных с этими объектами
        fileman_delete_confirm.pop(user_id, None)
        fileman_selected_file.pop(user_id, None)
        fileman_in_context_menu[user_id] = False

        # Очистка выделения
        selected = fileman_selected_entries.get(user_id)
        if selected:
            for p in paths:
                selected.discard(p)
        fileman_selection_mode[user_id] = False

        write_bot_log(
            f"Пользователь {user_id} удалил объектов: успешно={success_count}, ошибок={len(error_lines)}."
        )

        summary_lines = [f"Удаление выполнено. Успешно удалено объектов: {success_count}."]
        if error_lines:
            summary_lines.append("Сообщения об ошибках:")
            summary_lines.extend(error_lines[:20])

        await message.answer("\n".join(summary_lines))
        await send_directory_listing(message, user_id)


# --- Режим выбора / вставки объектов ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and not fileman_in_context_menu.get(message.from_user.id, False)
        and not fileman_in_multi_context_menu.get(message.from_user.id, False)
        and message.text
        in {
            BTN_SELECT_OBJECTS,
            BTN_CANCEL_SELECT_OBJECTS,
            BTN_OBJECTS_ACTIONS_MENU,
            BTN_PASTE_OBJECTS,
        }
    )
    async def fileman_selection_controls(message: types.Message):
        """
        Управление режимом выбора объектов, открытие меню действий и вставка.
        """
        user_id = message.from_user.id
        text = message.text
        current_path = fileman_current_path.get(user_id)

        # Включить режим выбора объектов
        if text == BTN_SELECT_OBJECTS:
            if not current_path or not os.path.isdir(current_path):
                await message.answer(
                    "Режим выбора объектов доступен только внутри папки.\n"
                    "Сначала выбери диск и открой нужную папку."
                )
                return

            fileman_selection_mode[user_id] = True
            fileman_selected_entries[user_id] = set()
            await message.answer(
                "Режим выбора объектов включён.\n"
                "Нажимай на имена файлов и папок, чтобы выделять или снимать выделение."
            )
            await send_directory_listing(message, user_id)
            return

        # Выключить режим выбора объектов
        if text == BTN_CANCEL_SELECT_OBJECTS:
            fileman_selection_mode[user_id] = False
            fileman_selected_entries[user_id] = set()
            fileman_in_multi_context_menu[user_id] = False
            await message.answer("Режим выбора объектов выключен.")
            await send_directory_listing(message, user_id)
            return

        # Открыть меню действий с объектами
        if text == BTN_OBJECTS_ACTIONS_MENU:
            selected_paths = fileman_selected_entries.get(user_id) or set()
            if not selected_paths:
                await message.answer("Сначала выдели хотя бы один объект.")
                return

            fileman_in_multi_context_menu[user_id] = True
            kb = get_multi_context_keyboard()
            await message.answer(
                f"Выбрано объектов: {len(selected_paths)}.\n"
                "Выбери действие для выделенных объектов:",
                reply_markup=kb,
            )
            return

        # Вставка объектов из буфера
        if text == BTN_PASTE_OBJECTS:
            clipboard = fileman_clipboard.get(user_id)
            if not clipboard or not clipboard.get("paths"):
                fileman_clipboard.pop(user_id, None)
                await message.answer("Нет сохранённых объектов для вставки.")
                await send_directory_listing(message, user_id)
                return

            if not current_path or not os.path.isdir(current_path):
                await message.answer(
                    "Сначала открой папку, в которую нужно вставить объекты."
                )
                return

            paths: List[str] = clipboard.get("paths", [])
            cut_mode: bool = bool(clipboard.get("cut", False))

            success_count = 0
            error_lines: List[str] = []

            for src_path in paths:
                name = os.path.basename(src_path)
                if not os.path.exists(src_path):
                    error_lines.append(f"❌ '{name}' — исходный объект не найден.")
                    continue

                dest_path = os.path.join(current_path, name)

                # Если источник и назначение совпадают — пропускаем
                if os.path.abspath(dest_path) == os.path.abspath(src_path):
                    error_lines.append(
                        f"⚠ '{name}' уже находится в этой папке. Пропускаю."
                    )
                    continue

                if os.path.exists(dest_path):
                    error_lines.append(
                        f"⚠ '{name}' уже существует в папке назначения. Пропускаю."
                    )
                    continue

                try:
                    if os.path.isdir(src_path):
                        if cut_mode:
                            shutil.move(src_path, dest_path)
                        else:
                            shutil.copytree(src_path, dest_path)
                    else:
                        if cut_mode:
                            shutil.move(src_path, dest_path)
                        else:
                            shutil.copy2(src_path, dest_path)
                    success_count += 1
                except Exception as e:
                    error_lines.append(f"❌ '{name}': {e}")

            # После вставки сбрасываем буфер и режимы
            fileman_clipboard.pop(user_id, None)
            fileman_selection_mode[user_id] = False
            fileman_selected_entries[user_id] = set()

            if cut_mode:
                op_text = "перемещено"
            else:
                op_text = "скопировано"

            summary_lines = [f"Готово. Успешно {op_text} объектов: {success_count}."]
            if error_lines:
                summary_lines.append("Сообщения об ошибках:")
                # Чтобы не заспамить чат, ограничим количество строк
                summary_lines.extend(error_lines[:20])

            write_bot_log(
                f"Пользователь {user_id} выполнил вставку объектов "
                f"(успешно: {success_count}, ошибок: {len(error_lines)})."
            )

            await message.answer("\n".join(summary_lines))
            await send_directory_listing(message, user_id)
            return

    # --- Навигация по каталогу / выбор объектов ---

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
        and not fileman_in_context_menu.get(message.from_user.id, False)
        and not fileman_in_multi_context_menu.get(message.from_user.id, False)
        and message.text not in {BTN_BACK_UTILS, "Файловый менеджер"}
    )
    async def fileman_navigation(message: types.Message):
        """
        Навигация по дискам и каталогам, выбор файлов / объектов.
        """
        user_id = message.from_user.id
        text = message.text
        current_path = fileman_current_path.get(user_id)

        # Если путь не выбран — показываем / ждём выбор диска
        if not current_path:
            if os.path.isdir(text):
                # Пользователь выбрал диск
                path = os.path.abspath(text)
                fileman_current_path[user_id] = path
                fileman_page[user_id] = 0

                # Сброс выделения при смене корня
                fileman_selection_mode[user_id] = False
                fileman_selected_entries[user_id] = set()

                write_bot_log(f"Пользователь {user_id} выбрал диск: {path}")
                await send_directory_listing(message, user_id)
            else:
                kb = get_disks_keyboard()
                await message.answer(
                    "Сейчас активен файловый менеджер.\n"
                    "Выбери диск с помощью кнопок ниже.",
                    reply_markup=kb,
                )
            return

        # Ниже работа уже внутри выбранного каталога

        # Подъём на уровень выше
        if text == BTN_UP_DIR:
            try:
                if is_root_path(current_path):
                    # Возврат к списку дисков
                    fileman_current_path[user_id] = None
                    fileman_page[user_id] = 0
                    fileman_selected_entries[user_id] = set()
                    kb = get_disks_keyboard()
                    await message.answer("Возврат к списку дисков.", reply_markup=kb)
                else:
                    parent = os.path.dirname(os.path.abspath(current_path.rstrip("\\/")))
                    fileman_current_path[user_id] = parent
                    fileman_page[user_id] = 0
                    # При смене папки сбрасываем выделение
                    fileman_selected_entries[user_id] = set()
                    await send_directory_listing(message, user_id)
            except Exception as e:
                await message.answer(f"Не удалось перейти в предыдущую папку: {e}")
            return

        # Постраничная навигация
        if text == BTN_NEXT_PAGE:
            entries = get_sorted_entries(current_path)
            total = len(entries)
            if not total:
                await message.answer("В этой папке пока нет объектов.")
                return
            page = fileman_page.get(user_id, 0)
            max_page = (total - 1) // PAGE_SIZE
            if page >= max_page:
                await message.answer("Это последняя страница этого каталога.")
            else:
                fileman_page[user_id] = page + 1
                await send_directory_listing(message, user_id)
            return

        if text == BTN_PREV_PAGE:
            entries = get_sorted_entries(current_path)
            total = len(entries)
            if not total:
                await message.answer("В этой папке пока нет объектов.")
                return
            page = fileman_page.get(user_id, 0)
            if page <= 0:
                await message.answer("Это первая страница этого каталога.")
            else:
                fileman_page[user_id] = page - 1
                await send_directory_listing(message, user_id)
            return

        # Если включён режим выбора объектов — клик по объекту переключает его выделение
        if fileman_selection_mode.get(user_id, False):
            entries = get_sorted_entries(current_path)
            entry_name = text.strip()

            # Убираем префикс выделения, если он есть
            if entry_name.startswith(SELECTION_PREFIX):
                entry_name = entry_name[len(SELECTION_PREFIX) :].lstrip()

            if entry_name not in entries:
                await message.answer(
                    "Не удалось найти такой объект в текущей папке. "
                    "Используй кнопки списка, чтобы выделять объекты."
                )
                await send_directory_listing(message, user_id)
                return

            full_path = os.path.join(current_path, entry_name)
            selected = fileman_selected_entries.get(user_id)
            if selected is None:
                selected = set()
                fileman_selected_entries[user_id] = selected

            if full_path in selected:
                selected.remove(full_path)
            else:
                selected.add(full_path)

            await send_directory_listing(message, user_id)
            return

        # В обычном режиме интерпретируем текст как имя файла/папки
        entry_name = text.strip()
        full_path = os.path.join(current_path, entry_name)

        if os.path.isdir(full_path):
            fileman_current_path[user_id] = full_path
            fileman_page[user_id] = 0
            # При смене папки сбрасываем выделение
            fileman_selected_entries[user_id] = set()
            write_bot_log(f"Пользователь {user_id} открыл папку: {full_path}")
            await send_directory_listing(message, user_id)
            return

        if os.path.isfile(full_path):
            fileman_selected_file[user_id] = full_path
            fileman_in_context_menu[user_id] = True
            kb = get_file_context_keyboard()
            write_bot_log(
                f"Пользователь {user_id} открыл контекстное меню файла: {full_path}"
            )
            await message.answer(
                f"Файл:\n{os.path.basename(full_path)}\n\nВыбери действие:",
                reply_markup=kb,
            )
            return

        # Если ничего не подошло — просто ещё раз показываем текущий каталог
        await send_directory_listing(message, user_id)
        await message.answer(
            "Не удалось распознать команду или объект.\n"
            "Используй кнопки списка, чтобы выбирать файлы и папки.",
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and fileman_mode.get(message.from_user.id, False)
    )
    async def fileman_fallback(message: types.Message):
        """
        Обработчик любых остальных сообщений, пока активен файловый менеджер.
        """
        user_id = message.from_user.id

        # Если ожидается подтверждение удаления — напоминаем об этом
        if fileman_delete_confirm.get(user_id):
            data = fileman_delete_confirm.get(user_id) or {}
            paths = data.get("paths") or []
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add(BTN_CONFIRM_DELETE)
            kb.add(BTN_CANCEL_DELETE)
            kb.add(BTN_BACK_UTILS)

            if paths:
                if len(paths) == 1:
                    msg = (
                        "Ожидается подтверждение удаления объекта.\n"
                        f"{paths[0]}\n\n"
                        "Нажми «Да, удалить» или «Отмена удаления»."
                    )
                else:
                    msg = (
                        f"Ожидается подтверждение удаления {len(paths)} объектов.\n"
                        "Нажми «Да, удалить» или «Отмена удаления»."
                    )
            else:
                msg = (
                    "Ожидается подтверждение удаления.\n"
                    "Нажми «Да, удалить» или «Отмена удаления»."
                )

            await message.answer(msg, reply_markup=kb)
            return

        if fileman_in_context_menu.get(user_id, False):
            kb = get_file_context_keyboard()
            await message.answer(
                "Сейчас открыто контекстное меню файла.\n"
                f"Используй кнопки для выбора действия или нажми «{BTN_FILE_CLOSE_MENU}» "
                "для возврата к папке.",
                reply_markup=kb,
            )
        elif fileman_in_multi_context_menu.get(user_id, False):
            kb = get_multi_context_keyboard()
            await message.answer(
                "Сейчас открыто контекстное меню для выбранных объектов.\n"
                f"Используй кнопки или нажми «{BTN_MULTI_CLOSE_MENU}» для возврата к папке.",
                reply_markup=kb,
            )
        else:
            current_path = fileman_current_path.get(user_id)
            if not current_path:
                kb = get_disks_keyboard()
                await message.answer(
                    "Сейчас активен файловый менеджер.\n"
                    f"Выбери диск с помощью кнопок ниже или нажми «{BTN_BACK_UTILS}» "
                    "для выхода.",
                    reply_markup=kb,
                )
            else:
                entries = get_sorted_entries(current_path)
                kb = build_directory_keyboard(
                    user_id, current_path, entries, fileman_page.get(user_id, 0)
                )
                await message.answer(
                    "Сейчас активен файловый менеджер.\n"
                    f"Используй кнопки для навигации по папкам и выбора файлов, или нажми "
                    f"«{BTN_BACK_UTILS}» для выхода.",
                    reply_markup=kb,
                )
