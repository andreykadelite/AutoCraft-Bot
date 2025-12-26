import os
import shutil
from datetime import datetime
from typing import List, Dict, Set, Optional

from aiogram import types
from aiogram.dispatcher import Dispatcher

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

        kb = get_disks_keyboard()
        await message.answer(
            "📂 Файловый менеджер.\n"
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
        await message.answer("Возвращаюсь в раздел утилит.", reply_markup=kb)

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
            try:
                with open(path, "rb") as f:
                    await message.answer_document(f, caption=os.path.basename(path))
            except Exception as e:
                await message.answer(f"Не удалось отправить файл: {e}")
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
