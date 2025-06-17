# file_manager.py

import os
import shutil
import zipfile
import logging
import chardet
import string
from datetime import datetime
import math

from aiogram import Dispatcher, types
import keymenu

# Глобальное состояние плагина по user_id
file_manager_mode = {}  # { user_id: { "active": True, "state": <state>, "current_dir": <path>, ... } }

# Оригинальные состояния
STATE_MAIN = "MAIN"
STATE_DOWNLOAD_FILE = "DOWNLOAD_FILE"
STATE_UPLOAD_FILE = "UPLOAD_FILE"
STATE_DELETE_ITEM = "DELETE_ITEM"
STATE_DELETE_CONFIRM = "DELETE_CONFIRM"
STATE_RENAME_SELECT = "RENAME_SELECT"
STATE_RENAME_NEW_NAME = "RENAME_NEW_NAME"
STATE_CREATE_FOLDER = "CREATE_FOLDER"
STATE_COPY_SELECT = "COPY_SELECT"
STATE_COPY_TARGET = "COPY_TARGET"
STATE_DISK_TARGET = "DISK_TARGET"
STATE_MOVE_SELECT = "MOVE_SELECT"
STATE_MOVE_TARGET = "MOVE_TARGET"
STATE_FILE_INFO_SELECT = "FILE_INFO_SELECT"
STATE_SEARCH_PATTERN = "SEARCH_PATTERN"
STATE_PREVIEW_SELECT = "PREVIEW_SELECT"
STATE_ARCHIVE_SELECT = "ARCHIVE_SELECT"
STATE_ARCHIVE_NAME = "ARCHIVE_NAME"
STATE_EXTRACT_SELECT = "EXTRACT_SELECT"
STATE_DISK_SELECT = "DISK_SELECT"

# Новые режимы работы плагина
MODE_NAV = "NAV"       # Режим навигации по папкам (пользователь выбирает папки, нажимая на их имена)
MODE_ACTION = "ACTION" # Режим меню действий (оригинальное меню без кнопок "Выбрать диск" и "Назад")

# Размер страницы для навигации (количество папок на странице)
NAV_PAGE_SIZE = 5

logger = logging.getLogger(__name__)

# -----------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# -----------------------------

def is_plugin_active(message: types.Message) -> bool:
    user_id = message.from_user.id
    return file_manager_mode.get(user_id, {}).get("active", False)

def get_state(message: types.Message) -> str:
    user_id = message.from_user.id
    return file_manager_mode.get(user_id, {}).get("state", STATE_MAIN)

def set_state(message: types.Message, new_state: str):
    user_id = message.from_user.id
    if user_id not in file_manager_mode:
        file_manager_mode[user_id] = {}
    file_manager_mode[user_id]["state"] = new_state

def get_data(message: types.Message, key: str):
    user_id = message.from_user.id
    return file_manager_mode.get(user_id, {}).get(key)

def set_data(message: types.Message, key: str, value):
    user_id = message.from_user.id
    if user_id not in file_manager_mode:
        file_manager_mode[user_id] = {}
    file_manager_mode[user_id][key] = value

def get_current_dir(message: types.Message) -> str:
    cd = get_data(message, "current_dir")
    if not cd:
        return os.getcwd()
    return cd

def set_current_dir(message: types.Message, path: str):
    set_data(message, "current_dir", path)

def action_log(message: types.Message, action: str):
    user_id = message.from_user.id
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"{timestamp} - {action}"
    if "action_history" not in file_manager_mode.get(user_id, {}):
        file_manager_mode[user_id]["action_history"] = []
    file_manager_mode[user_id]["action_history"].append(entry)
    logger.info(f"[User {user_id}] {entry}")

def get_action_history(message: types.Message):
    user_id = message.from_user.id
    return file_manager_mode.get(user_id, {}).get("action_history", [])

def get_undo_stack(message: types.Message):
    user_id = message.from_user.id
    if "undo_stack" not in file_manager_mode.get(user_id, {}):
        file_manager_mode[user_id]["undo_stack"] = []
    return file_manager_mode[user_id]["undo_stack"]

def main_menu_keyboard() -> types.ReplyKeyboardMarkup:
    # Удалены кнопки "Выбрать диск" и "Назад"
    keyboard = [
        ["Список файлов", "Текущая директория", "Обновить"],
        ["Отменить операцию"],
        ["Скачать файл", "Загрузить файл"],
        ["Удалить", "Переименовать", "Создать папку"],
        ["Копировать", "Переместить"],
        ["Информация", "Поиск", "Просмотр файла"],
        ["Архивировать", "Распаковать"],
        ["Помощь", "История"],
        ["Выбрать диск", "Возврат", "Закрыть плагин"]
    ]
    return types.ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def close_plugin_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Отмена", "Закрыть плагин")
    return kb

def read_file_content(file_path, max_chars=500):
    try:
        with open(file_path, "rb") as f:
            raw = f.read(max_chars * 4)
        detection = chardet.detect(raw)
        encoding = detection.get("encoding", "utf-8")
        try:
            text = raw.decode(encoding, errors="replace")
        except Exception:
            text = "Ошибка: не удалось декодировать файл."
        return text[:max_chars]
    except Exception:
        return "Ошибка: не удалось прочитать файл."

# -----------------------------
# НОВАЯ НАВИГАЦИЯ (MODE_NAV)
# -----------------------------

def get_folder_navigation_keyboard(message: types.Message) -> types.ReplyKeyboardMarkup:
    current_dir = get_current_dir(message)
    try:
        items = os.listdir(current_dir)
        folders = [item for item in items if os.path.isdir(os.path.join(current_dir, item))]
    except Exception:
        folders = []
    page = file_manager_mode[message.from_user.id].get("nav_page", 0)
    total_pages = math.ceil(len(folders) / NAV_PAGE_SIZE) if folders else 1
    start = page * NAV_PAGE_SIZE
    end = start + NAV_PAGE_SIZE
    page_folders = folders[start:end]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for folder in page_folders:
        kb.add(folder)
    nav_buttons = []
    if page > 0:
        nav_buttons.append("Предыдущая страница")
    if page < total_pages - 1:
        nav_buttons.append("Следующая страница")
    if nav_buttons:
        kb.row(*nav_buttons)
    kb.row("Вверх", "Действия", "Закрыть плагин")
    return kb

async def send_folder_navigation_menu(message: types.Message):
    current_dir = get_current_dir(message)
    page = file_manager_mode[message.from_user.id].get("nav_page", 0)
    try:
        items = os.listdir(current_dir)
        folders = [item for item in items if os.path.isdir(os.path.join(current_dir, item))]
    except Exception:
        folders = []
    total_pages = math.ceil(len(folders) / NAV_PAGE_SIZE) if folders else 1
    text = f"Текущая директория:\n{current_dir}\nСтраница {page+1} из {total_pages}\nВыберите папку:"
    kb = get_folder_navigation_keyboard(message)
    await message.answer(text, reply_markup=kb)

async def send_action_menu(message: types.Message):
    kb = main_menu_keyboard()
    await message.answer("Меню действий:", reply_markup=kb)

# -----------------------------
# Функция run_plugin (точка входа)
# -----------------------------

async def run_plugin(message: types.Message):
    user_id = message.from_user.id
    file_manager_mode[user_id] = {
        "active": True,
        "state": STATE_DISK_SELECT,
        "current_dir": os.getcwd(),
        "action_history": [],
        "undo_stack": [],
        "nav_page": 0,
        "mode": MODE_NAV
    }
    action_log(message, "Файловый менеджер запущен (выбор диска)")
    await disk_menu_entry(message)

async def close_plugin(message: types.Message):
    user_id = message.from_user.id
    file_manager_mode.pop(user_id, None)
    kb = keymenu.get_main_keyboard()
    await message.answer("Плагин завершён. Возвращаемся в главное меню.", reply_markup=kb)

# -----------------------------
# Основной обработчик плагина
# -----------------------------

def init_plugin(dp: Dispatcher):
    @dp.message_handler(lambda m: m.text == "Запустить файловый менеджер")
    async def start_handler(message: types.Message):
        await run_plugin(message)

    @dp.message_handler(lambda m: is_plugin_active(m))
    async def file_manager_handler(message: types.Message):
        user_id = message.from_user.id
        state = file_manager_mode.get(user_id)
        if not state or not state.get("active"):
            return

        # Если операционный state не равен STATE_MAIN, делегируем обработку соответствующей функции
        op_state = state.get("state", STATE_MAIN)
        if op_state != STATE_MAIN:
            if op_state == STATE_DOWNLOAD_FILE:
                await handle_file_download(message)
                return
            elif op_state == STATE_UPLOAD_FILE:
                await handle_file_upload(message)
                return
            elif op_state == STATE_DELETE_ITEM:
                await delete_item_confirm(message)
                return
            elif op_state == STATE_DELETE_CONFIRM:
                await handle_delete_confirm(message)
                return
            elif op_state == STATE_RENAME_SELECT:
                await rename_item_get_new_name(message)
                return
            elif op_state == STATE_RENAME_NEW_NAME:
                await handle_rename_item(message)
                return
            elif op_state == STATE_CREATE_FOLDER:
                await handle_create_folder(message)
                return
            elif op_state == STATE_COPY_SELECT:
                await copy_item_select_destination(message)
                return
            elif op_state == STATE_COPY_TARGET:
                await copy_target_handler(message)
                return
            elif op_state == STATE_MOVE_SELECT:
                await move_item_select_destination(message)
                return
            elif op_state == STATE_MOVE_TARGET:
                await move_target_handler(message)
                return
            elif op_state == STATE_DISK_TARGET:
                await target_disk_handler(message)
                return
            elif op_state == STATE_FILE_INFO_SELECT:
                await handle_file_info(message)
                return
            elif op_state == STATE_SEARCH_PATTERN:
                await handle_search_file(message)
                return
            elif op_state == STATE_PREVIEW_SELECT:
                await handle_preview_file(message)
                return
            elif op_state == STATE_ARCHIVE_SELECT:
                await archive_item_get_name(message)
                return
            elif op_state == STATE_ARCHIVE_NAME:
                await handle_archive_item(message)
                return
            elif op_state == STATE_EXTRACT_SELECT:
                await handle_extract_archive(message)
                return
            elif op_state == STATE_DISK_SELECT:
                choice = message.text
                if choice.lower() == "отмена":
                    await close_plugin(message)
                else:
                    set_current_dir(message, choice)
                    state["nav_page"] = 0
                    state["mode"] = MODE_NAV
                    state["state"] = STATE_MAIN
                    await send_folder_navigation_menu(message)
                return
                return

        # Если операционный state равен STATE_MAIN, действуем в зависимости от режима
        mode = state.get("mode", MODE_NAV)
        text = message.text.strip()
        if mode == MODE_NAV:
            if text.lower() == "закрыть плагин":
                await close_plugin(message)
                return
            elif text.lower() == "вверх":
                current = get_current_dir(message)
                parent = os.path.dirname(current)
                if parent and parent != current:
                    set_current_dir(message, parent)
                    state["nav_page"] = 0
                await send_folder_navigation_menu(message)
                return
            elif text.lower() == "предыдущая страница":
                if state.get("nav_page", 0) > 0:
                    state["nav_page"] -= 1
                await send_folder_navigation_menu(message)
                return
            elif text.lower() == "следующая страница":
                try:
                    items = os.listdir(get_current_dir(message))
                    folders = [item for item in items if os.path.isdir(os.path.join(get_current_dir(message), item))]
                except Exception:
                    folders = []
                total_pages = math.ceil(len(folders) / NAV_PAGE_SIZE) if folders else 1
                if state.get("nav_page", 0) < total_pages - 1:
                    state["nav_page"] += 1
                await send_folder_navigation_menu(message)
                return
            elif text.lower() == "действия":
                state["mode"] = MODE_ACTION
                await send_action_menu(message)
                return
            else:
                # При выборе папки в режиме навигации
                current = get_current_dir(message)
                new_path = os.path.join(current, text)
                if os.path.isdir(new_path):
                    set_current_dir(message, new_path)
                    state["nav_page"] = 0
                await send_folder_navigation_menu(message)
                return
        elif mode == MODE_ACTION:
            if text.lower() == "закрыть плагин":
                await close_plugin(message)
            elif text.lower() == "возврат":
                state["mode"] = MODE_NAV
                await send_folder_navigation_menu(message)
            elif text.lower() == "выбрать диск":
                await disk_menu_entry(message)
                return
            elif text.lower() == "список файлов":
                await list_files(message)
            elif text.lower() == "текущая директория":
                await current_directory(message)
            elif text.lower() == "обновить":
                await list_files(message)
            elif text.lower() == "помощь":
                await help_command(message)
            elif text.lower() == "история":
                await show_history(message)
            elif text.lower() == "отменить операцию":
                await undo_last_operation(message)
            elif text.lower() == "скачать файл":
                await download_file_entry(message)
            elif text.lower() == "загрузить файл":
                await upload_file_entry(message)
            elif text.lower() == "удалить":
                await delete_item_entry(message)
            elif text.lower() == "переименовать":
                await rename_item_entry(message)
            elif text.lower() == "создать папку":
                await create_folder_entry(message)
            elif text.lower() == "копировать":
                await copy_item_entry(message)
            elif text.lower() == "переместить":
                await move_item_entry(message)
            elif text.lower() == "информация":
                await file_info_entry(message)
            elif text.lower() == "поиск":
                await search_file_entry(message)
            elif text.lower() == "просмотр файла":
                await preview_file_entry(message)
            elif text.lower() == "архивировать":
                await archive_item_entry(message)
            elif text.lower() == "распаковать":
                await extract_archive_entry(message)
            else:
                await message.answer("Неверная команда в меню действий. Используйте кнопки.", reply_markup=main_menu_keyboard())
        else:
            await message.answer("Неверное состояние плагина.", reply_markup=main_menu_keyboard())

# -----------------------------
# ОРИГИНАЛЬНЫЕ ФУНКЦИИ ПЛАГИНА
# (Функции скачивания, загрузки, удаления, переименования, создания папок, копирования, перемещения,
# информации, поиска, предпросмотра, архивирования, распаковки, отмены операций остаются без изменений)
# -----------------------------

async def list_files(message: types.Message):
    current_dir = get_current_dir(message)
    try:
        items = os.listdir(current_dir)
        if not items:
            text = "В текущей директории нет файлов или папок."
        else:
            text = f"Содержимое папки: {current_dir}\n" + "\n".join(items)
    except Exception as e:
        text = f"Ошибка: {e}"
    await message.answer(text, reply_markup=main_menu_keyboard())
    action_log(message, f"Просмотр списка файлов в {current_dir}")

async def current_directory(message: types.Message):
    cd = get_current_dir(message)
    await message.answer(f"Текущая директория: {cd}", reply_markup=main_menu_keyboard())
    action_log(message, f"Просмотр текущей директории: {cd}")

async def help_command(message: types.Message):
    text = (
        "Доступные действия:\n"
        "• Список файлов, Текущая директория, Обновить\n"
        "• Отменить операцию\n"
        "• Скачать файл, Загрузить файл\n"
        "• Удалить, Переименовать, Создать папку\n"
        "• Копировать, Переместить\n"
        "• Информация, Поиск, Просмотр файла\n"
        "• Архивировать, Распаковать\n"
        "• Помощь, История\n\n"
        "Кнопки «Выбрать диск» и «Назад» удалены из меню действий."
    )
    await message.answer(text, reply_markup=main_menu_keyboard())

async def show_history(message: types.Message):
    hist = get_action_history(message)
    if hist:
        txt = "История действий:\n" + "\n".join(hist[-20:])
    else:
        txt = "История пуста."
    await message.answer(txt, reply_markup=main_menu_keyboard())

async def disk_menu_entry(message: types.Message):
    # Функция выбора диска используется в других режимах, оставляем без изменений
    if os.name == 'nt':
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        drives = ["/"]
    if not drives:
        await message.answer("Нет доступных дисков.", reply_markup=main_menu_keyboard())
        return
    set_state(message, STATE_DISK_SELECT)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for d in drives:
        kb.add(d)
    kb.add("Отменить выбор")
    await message.answer("Выберите диск:", reply_markup=kb)

async def download_file_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        files = [f for f in items if os.path.isfile(os.path.join(current, f))]
        if not files:
            await message.answer("Нет файлов для скачивания.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_DOWNLOAD_FILE)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for f in files:
            kb.add(f)
        kb.add("Отмена")
        await message.answer("Выберите файл для скачивания:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def handle_file_download(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    path = os.path.join(current, choice)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                await message.answer_document(f, caption=f"Файл: {choice}")
            set_state(message, STATE_MAIN)
            await message.answer("Файл отправлен.", reply_markup=main_menu_keyboard())
            action_log(message, f"Скачивание файла: {path}")
        except Exception as e:
            await message.answer(f"Ошибка при отправке файла: {e}", reply_markup=main_menu_keyboard())
    else:
        await message.answer("Неверный выбор. Попробуйте снова или Отмена.")

async def upload_file_entry(message: types.Message):
    set_state(message, STATE_UPLOAD_FILE)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Отмена")
    await message.answer("Отправьте файл (документ) для загрузки или нажмите Отмена.", reply_markup=kb)

async def handle_file_upload(message: types.Message):
    if message.text and message.text.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    if message.document:
        doc = message.document
        file_id = doc.file_id
        file_name = doc.file_name
        current = get_current_dir(message)
        new_path = os.path.join(current, file_name)
        try:
            file_obj = await message.bot.get_file(file_id)
            await file_obj.download(destination=new_path)
            stack = get_undo_stack(message)
            stack.append({"action": "upload", "path": new_path})
            set_state(message, STATE_MAIN)
            await message.answer(f"Файл загружен: {new_path}\nДля отмены операции нажмите «Отменить операцию».", reply_markup=main_menu_keyboard())
            action_log(message, f"Загрузка файла: {new_path}")
        except Exception as e:
            await message.answer(f"Ошибка при загрузке: {e}", reply_markup=main_menu_keyboard())
    else:
        await message.answer("Пожалуйста, отправьте документ или Отмена.")

async def delete_item_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет элементов для удаления.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_DELETE_ITEM)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите файл или папку для удаления:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def delete_item_confirm(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    set_data(message, "delete_item", choice)
    set_state(message, STATE_DELETE_CONFIRM)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Да", "Нет")
    await message.answer(f"Уверены, что хотите удалить '{choice}'? (Да/Нет)", reply_markup=kb)

async def handle_delete_confirm(message: types.Message):
    resp = message.text.strip().lower()
    if resp != "да":
        set_state(message, STATE_MAIN)
        await message.answer("Удаление отменено.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    item = get_data(message, "delete_item")
    target = os.path.join(current, item)
    try:
        backup_dir = os.path.join(current, ".undo_temp")
        if not os.path.exists(backup_dir):
            os.mkdir(backup_dir)
        backup_name = item + "_" + datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = os.path.join(backup_dir, backup_name)
        shutil.move(target, backup_path)
        stack = get_undo_stack(message)
        stack.append({"action": "delete", "original_path": target, "backup_path": backup_path})
        set_state(message, STATE_MAIN)
        await message.answer(f"'{item}' удалён(а). Для отмены нажмите «Отменить операцию».", reply_markup=main_menu_keyboard())
        action_log(message, f"Удаление: {target}")
    except Exception as e:
        await message.answer(f"Ошибка при удалении: {e}", reply_markup=main_menu_keyboard())

async def rename_item_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет объектов для переименования.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_RENAME_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите файл или папку для переименования:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def rename_item_get_new_name(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    set_data(message, "rename_item", choice)
    set_state(message, STATE_RENAME_NEW_NAME)
    await message.answer(f"Введите новое имя для '{choice}':", reply_markup=types.ReplyKeyboardRemove())

async def handle_rename_item(message: types.Message):
    new_name = message.text
    current = get_current_dir(message)
    old_name = get_data(message, "rename_item")
    old_path = os.path.join(current, old_name)
    new_path = os.path.join(current, new_name)
    try:
        os.rename(old_path, new_path)
        stack = get_undo_stack(message)
        stack.append({"action": "rename", "old": old_path, "new": new_path})
        set_state(message, STATE_MAIN)
        await message.answer(f"Переименовано в '{new_name}'. Для отмены — «Отменить операцию».", reply_markup=main_menu_keyboard())
        action_log(message, f"Переименование: {old_path} -> {new_path}")
    except Exception as e:
        await message.answer(f"Ошибка при переименовании: {e}", reply_markup=main_menu_keyboard())

async def create_folder_entry(message: types.Message):
    set_state(message, STATE_CREATE_FOLDER)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Отмена")
    await message.answer("Введите имя новой папки или Отмена:", reply_markup=kb)

async def handle_create_folder(message: types.Message):
    name = message.text
    if name.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    new_folder = os.path.join(current, name)
    try:
        os.mkdir(new_folder)
        stack = get_undo_stack(message)
        stack.append({"action": "create_folder", "path": new_folder})
        set_state(message, STATE_MAIN)
        await message.answer(f"Папка '{name}' создана. Для отмены — «Отменить операцию».", reply_markup=main_menu_keyboard())
        action_log(message, f"Создание папки: {new_folder}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def copy_item_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет объектов для копирования.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_COPY_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите элемент для копирования:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def copy_item_select_destination(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    if os.path.exists(os.path.join(current, choice)):
        set_data(message, "copy_item", choice)
        set_data(message, "copy_source_dir", current)
        set_data(message, "operation", "copy")
        set_state(message, STATE_COPY_TARGET)
        await show_copy_target_keyboard(message)
    else:
        await message.answer("Неверный выбор, попробуйте ещё раз или Отмена.")

async def show_copy_target_keyboard(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        dirs = [d for d in items if os.path.isdir(os.path.join(current, d))]
    except:
        dirs = []
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for d in dirs:
        kb.add(d)
    kb.row("Вверх", "Выбрать диск")
    kb.row("Копировать сюда", "Отмена копирования")
    await message.answer(f"Текущая папка назначения: {current}\nВыберите папку или нажмите «Копировать сюда».", reply_markup=kb)

async def copy_target_handler(message: types.Message):
    text = message.text.strip().lower()
    current = get_current_dir(message)
    if text == "вверх":
        parent = os.path.dirname(current)
        set_current_dir(message, parent)
        await show_copy_target_keyboard(message)
    elif text == "выбрать диск":
        set_state(message, STATE_DISK_TARGET)
        await show_target_disk_keyboard(message)
    elif text == "копировать сюда":
        src_dir = get_data(message, "copy_source_dir")
        item = get_data(message, "copy_item")
        src_path = os.path.join(src_dir, item)
        dest_path = os.path.join(current, item)
        try:
            if os.path.isfile(src_path):
                shutil.copy(src_path, dest_path)
            else:
                shutil.copytree(src_path, dest_path)
            stack = get_undo_stack(message)
            stack.append({"action": "copy", "path": dest_path})
            set_state(message, STATE_MAIN)
            await message.answer(f"Скопировано в '{dest_path}'. Для отмены — «Отменить операцию».", reply_markup=main_menu_keyboard())
            action_log(message, f"Копирование: {src_path} -> {dest_path}")
        except Exception as e:
            await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())
    elif text == "отмена копирования":
        set_state(message, STATE_MAIN)
        await message.answer("Операция копирования отменена.", reply_markup=main_menu_keyboard())
    else:
        new_path = os.path.join(current, message.text)
        if os.path.isdir(new_path):
            set_current_dir(message, new_path)
            await show_copy_target_keyboard(message)
        else:
            await message.answer("Неверный выбор, попробуйте снова.")
            await show_copy_target_keyboard(message)

async def move_item_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет объектов для перемещения.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_MOVE_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите элемент для перемещения:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def move_item_select_destination(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    if os.path.exists(os.path.join(current, choice)):
        set_data(message, "move_item", choice)
        set_data(message, "move_source_dir", current)
        set_data(message, "operation", "move")
        set_state(message, STATE_MOVE_TARGET)
        await show_move_target_keyboard(message)
    else:
        await message.answer("Неверный выбор, попробуйте ещё раз или Отмена.")

async def show_move_target_keyboard(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        dirs = [d for d in items if os.path.isdir(os.path.join(current, d))]
    except:
        dirs = []
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for d in dirs:
        kb.add(d)
    kb.row("Вверх", "Выбрать диск")
    kb.row("Переместить сюда", "Отмена перемещения")
    await message.answer(f"Текущая папка назначения: {current}\nВыберите папку или нажмите «Переместить сюда».", reply_markup=kb)

async def move_target_handler(message: types.Message):
    text = message.text.strip().lower()
    current = get_current_dir(message)
    if text == "вверх":
        parent = os.path.dirname(current)
        set_current_dir(message, parent)
        await show_move_target_keyboard(message)
    elif text == "выбрать диск":
        set_state(message, STATE_DISK_TARGET)
        await show_target_disk_keyboard(message)
    elif text == "переместить сюда":
        src_dir = get_data(message, "move_source_dir")
        item = get_data(message, "move_item")
        src_path = os.path.join(src_dir, item)
        dest_path = os.path.join(current, item)
        try:
            shutil.move(src_path, dest_path)
            stack = get_undo_stack(message)
            stack.append({"action": "move", "src": src_path, "dest": dest_path})
            set_state(message, STATE_MAIN)
            await message.answer(f"Перемещено в '{dest_path}'. Для отмены — «Отменить операцию».", reply_markup=main_menu_keyboard())
            action_log(message, f"Перемещение: {src_path} -> {dest_path}")
        except Exception as e:
            await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())
    elif text == "отмена перемещения":
        set_state(message, STATE_MAIN)
        await message.answer("Операция перемещения отменена.", reply_markup=main_menu_keyboard())
    else:
        new_path = os.path.join(current, message.text)
        if os.path.isdir(new_path):
            set_current_dir(message, new_path)
            await show_move_target_keyboard(message)
        else:
            await message.answer("Неверный выбор. Попробуйте снова.")
            await show_move_target_keyboard(message)

async def show_target_disk_keyboard(message: types.Message):
    if os.name == 'nt':
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        drives = ["/"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for d in drives:
        kb.add(d)
    kb.add("Отмена")
    await message.answer("Выберите диск:", reply_markup=kb)

async def target_disk_handler(message: types.Message):
    text = message.text
    if text.lower() == "отмена":
        op = get_data(message, "operation")
        if op == "copy":
            set_state(message, STATE_COPY_TARGET)
            await show_copy_target_keyboard(message)
        elif op == "move":
            set_state(message, STATE_MOVE_TARGET)
            await show_move_target_keyboard(message)
        else:
            set_state(message, STATE_MAIN)
            await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    if os.path.exists(text):
        set_current_dir(message, text)
        op = get_data(message, "operation")
        if op == "copy":
            set_state(message, STATE_COPY_TARGET)
            await show_copy_target_keyboard(message)
        elif op == "move":
            set_state(message, STATE_MOVE_TARGET)
            await show_move_target_keyboard(message)
        else:
            set_state(message, STATE_MAIN)
            await message.answer("Непредвиденная операция.", reply_markup=main_menu_keyboard())
    else:
        await message.answer("Недоступный диск. Попробуйте снова.")
        await show_target_disk_keyboard(message)

async def file_info_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет объектов для просмотра информации.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_FILE_INFO_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите файл или папку:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def handle_file_info(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    path = os.path.join(current, choice)
    try:
        st = os.stat(path)
        info = (
            f"Информация о '{choice}':\n"
            f"Размер: {st.st_size} байт\n"
            f"Изменён: {datetime.fromtimestamp(st.st_mtime)}\n"
            f"Создан: {datetime.fromtimestamp(st.st_ctime)}"
        )
        set_state(message, STATE_MAIN)
        await message.answer(info, reply_markup=main_menu_keyboard())
        action_log(message, f"Информация о: {path}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def search_file_entry(message: types.Message):
    set_state(message, STATE_SEARCH_PATTERN)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Отмена")
    await message.answer("Введите часть имени файла для поиска или Отмена:", reply_markup=kb)

async def handle_search_file(message: types.Message):
    pattern = message.text
    if pattern.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    found = []
    for root, dirs, files in os.walk(current):
        for f in files:
            if pattern.lower() in f.lower():
                found.append(os.path.join(root, f))
    txt = "Найденные файлы:\n" + "\n".join(found) if found else "Ничего не найдено."
    set_state(message, STATE_MAIN)
    await message.answer(txt, reply_markup=main_menu_keyboard())
    action_log(message, f"Поиск файлов по шаблону: {pattern}")

async def preview_file_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        txt_files = [f for f in items if os.path.isfile(os.path.join(current, f)) and f.lower().endswith((".txt", ".py", ".log", ".md"))]
        if not txt_files:
            await message.answer("Нет текстовых файлов для предпросмотра.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_PREVIEW_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for f in txt_files:
            kb.add(f)
        kb.add("Отмена")
        await message.answer("Выберите файл для предпросмотра:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def handle_preview_file(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    path = os.path.join(current, choice)
    try:
        content = read_file_content(path, max_chars=500)
        safe_content = types.utils.escape_md(content)
        safe_filename = types.utils.escape_md(choice)
        msg = f"*Предпросмотр «{safe_filename}» (первые 500 символов):*\n```\n{safe_content}\n```"
        set_state(message, STATE_MAIN)
        await message.answer(msg, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        action_log(message, f"Предпросмотр файла: {path}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def archive_item_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        if not items:
            await message.answer("Нет объектов для архивирования.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_ARCHIVE_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for it in items:
            kb.add(it)
        kb.add("Отмена")
        await message.answer("Выберите файл или папку для архивирования:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def archive_item_get_name(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    set_data(message, "archive_item", choice)
    set_state(message, STATE_ARCHIVE_NAME)
    await message.answer("Введите имя архива (без .zip):", reply_markup=types.ReplyKeyboardRemove())

async def handle_archive_item(message: types.Message):
    archive_name = message.text
    current = get_current_dir(message)
    item = get_data(message, "archive_item")
    src_path = os.path.join(current, item)
    zip_path = os.path.join(current, archive_name + ".zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.isfile(src_path):
                zf.write(src_path, arcname=item)
            else:
                for root, dirs, files in os.walk(src_path):
                    for f in files:
                        fp = os.path.join(root, f)
                        arcname = os.path.relpath(fp, current)
                        zf.write(fp, arcname=arcname)
        set_state(message, STATE_MAIN)
        await message.answer(f"Архив создан: {zip_path}", reply_markup=main_menu_keyboard())
        action_log(message, f"Архив: {src_path} -> {zip_path}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def extract_archive_entry(message: types.Message):
    current = get_current_dir(message)
    try:
        items = os.listdir(current)
        archives = [i for i in items if i.lower().endswith(".zip") and os.path.isfile(os.path.join(current, i))]
        if not archives:
            await message.answer("Нет ZIP-архивов для распаковки.", reply_markup=main_menu_keyboard())
            return
        set_state(message, STATE_EXTRACT_SELECT)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for a in archives:
            kb.add(a)
        kb.add("Отмена")
        await message.answer("Выберите архив для распаковки:", reply_markup=kb)
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def handle_extract_archive(message: types.Message):
    choice = message.text
    if choice.lower() == "отмена":
        set_state(message, STATE_MAIN)
        await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())
        return
    current = get_current_dir(message)
    archive_path = os.path.join(current, choice)
    extract_dir = os.path.join(current, choice + "_extracted")
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)
        set_state(message, STATE_MAIN)
        await message.answer(f"Архив распакован: {extract_dir}", reply_markup=main_menu_keyboard())
        action_log(message, f"Распаковка: {archive_path} -> {extract_dir}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=main_menu_keyboard())

async def undo_last_operation(message: types.Message):
    stack = get_undo_stack(message)
    if not stack:
        await message.answer("Нет операций для отмены.", reply_markup=main_menu_keyboard())
        return
    op = stack.pop()
    try:
        if op["action"] == "delete":
            if os.path.exists(op["backup_path"]):
                shutil.move(op["backup_path"], op["original_path"])
                await message.answer(f"Отмена удаления: '{op['original_path']}' восстановлен(а).", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Файл для восстановления не найден.", reply_markup=main_menu_keyboard())
        elif op["action"] == "rename":
            if os.path.exists(op["new"]):
                os.rename(op["new"], op["old"])
                await message.answer(f"Отмена переименования: '{op['old']}' восстановлен(а).", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Новый файл не найден для отмены.", reply_markup=main_menu_keyboard())
        elif op["action"] == "copy":
            if os.path.exists(op["path"]):
                if os.path.isfile(op["path"]):
                    os.remove(op["path"])
                else:
                    shutil.rmtree(op["path"])
                await message.answer(f"Отмена копирования: '{op['path']}' удалён(а).", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Скопированный элемент не найден.", reply_markup=main_menu_keyboard())
        elif op["action"] == "move":
            if os.path.exists(op["dest"]):
                shutil.move(op["dest"], op["src"])
                await message.answer(f"Отмена перемещения: '{op['src']}' восстановлен(а).", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Не найден перемещённый элемент для возврата.", reply_markup=main_menu_keyboard())
        elif op["action"] == "upload":
            if os.path.exists(op["path"]):
                if os.path.isfile(op["path"]):
                    os.remove(op["path"])
                else:
                    shutil.rmtree(op["path"])
                await message.answer(f"Отмена загрузки: '{op['path']}' удалён(а).", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Загруженный файл не найден.", reply_markup=main_menu_keyboard())
        elif op["action"] == "create_folder":
            if os.path.exists(op["path"]):
                shutil.rmtree(op["path"])
                await message.answer(f"Отмена создания папки: '{op['path']}' удалена.", reply_markup=main_menu_keyboard())
            else:
                await message.answer("Папка не найдена для отмены.", reply_markup=main_menu_keyboard())
        else:
            await message.answer("Неизвестная операция для undo.", reply_markup=main_menu_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка при отмене: {e}", reply_markup=main_menu_keyboard())
