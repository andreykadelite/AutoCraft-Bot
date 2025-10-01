import os

import subprocess

import asyncio

import shutil

from datetime import datetime

from aiogram import types

import logging

import info

import sys

import configparser

# ------------------ РљРѕРЅС„РёРіСѓСЂР°С†РёСЏ Р»РѕРіРёСЂРѕРІР°РЅРёСЏ ------------------

# РћРїСЂРµРґРµР»РµРЅРёРµ Р±Р°Р·РѕРІРѕР№ РґРёСЂРµРєС‚РѕСЂРёРё: РїРѕРґРґРµСЂР¶РєР° Nuitka Onefile, PyInstaller Рё РѕР±С‹С‡РЅРѕРіРѕ СЂРµР¶РёРјР°

if "NUITKA_ONEFILE_PARENT" in os.environ:

    # Р”Р»СЏ Nuitka Onefile: NUITKA_ONEFILE_PARENT СѓРєР°Р·С‹РІР°РµС‚ РЅР° РїСѓС‚СЊ Рє exe

    base_dir = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))

elif getattr(sys, "frozen", False):

    # Р”Р»СЏ PyInstaller Рё РґСЂСѓРіРёС… С„СЂРёР·РµСЂРѕРІ: exe СЂСЏРґРѕРј СЃ РёСЃРїРѕР»РЅСЏРµРјС‹Рј С„Р°Р№Р»РѕРј

    base_dir = os.path.dirname(sys.executable)

else:

    # Р—Р°РїСѓСЃРє РёР· РёСЃС…РѕРґРЅРёРєРѕРІ

    base_dir = os.path.dirname(os.path.abspath(__file__))

log_dir = os.path.join(base_dir, 'лог')

os.makedirs(log_dir, exist_ok=True)

log_file = os.path.join(log_dir, 'moduldptools.log')

# Load debug flag for logging

config = configparser.ConfigParser()

CONFIG_FILE = os.path.join(base_dir, 'config.ini')

CONFIG_SECTION = 'settings'

try:

    config.read(CONFIG_FILE, encoding='utf-8')

    debug_enabled = config.getboolean(CONFIG_SECTION, 'debug', fallback=False)

except Exception:

    debug_enabled = False

# Adjust logging level based on debug flag

level = logging.DEBUG if debug_enabled else logging.CRITICAL

logging.basicConfig(

    level=level,

    format='%(asctime)s [%(levelname)s] %(message)s',

    handlers=[

        logging.FileHandler(log_file, encoding='utf-8'),

        logging.StreamHandler()

    ], force=True

)

logger = logging.getLogger(__name__)

# РџС‹С‚Р°РµРјСЃСЏ РёРјРїРѕСЂС‚РёСЂРѕРІР°С‚СЊ С„СѓРЅРєС†РёСЋ Р»РѕРіРёСЂРѕРІР°РЅРёСЏ РёР· РѕСЃРЅРѕРІРЅРѕРіРѕ РјРѕРґСѓР»СЏ,

# РµСЃР»Рё РЅРµ СѓРґР°С‘С‚СЃСЏ вЂ” РёСЃРїРѕР»СЊР·СѓРµРј Р»РѕРєР°Р»СЊРЅС‹Р№ logger.

try:

    from __main__ import write_bot_log

except ImportError:

    def write_bot_log(msg):

        logger.info(msg)

def get_max_file_size(message):

    server = getattr(message.bot, 'server', None)

    base = None

    if server:

        base = getattr(server, 'base', None) or getattr(server, '_base_url', None)

    if base and not base.startswith("https://api.telegram.org"):

        return 2 * 1024 * 1024 * 1024, "2 ГБ"

    else:

        return 50 * 1024 * 1024, "50 МБ"

def register_dptools_handlers(dp, base_dir, note_mode, pending_note, file_mode, infiles_mode, power_mode, pending_power_action, get_additional_keyboard):

    infiles_tasks = {}

    note_read_mode = {}

    note_view_state = {}

    note_button_map = {}

    note_menu_active = {}

    NOTE_WRITE_BUTTON = "Написать заметку"

    NOTE_READ_BUTTON = "Прочитать заметки"

    NOTE_MAIN_BACK_BUTTON = "Назад бота"

    NOTE_BACK_TO_MENU_BUTTON = "Назад в меню заметок"

    NEXT_PAGE_BUTTON = "Следующая страница"

    PREV_PAGE_BUTTON = "Предыдущая страница"

    NOTES_PER_PAGE = 10

    FILE_SEND_CANCEL_BUTTON = "Завершить отправку файлов"

    FILE_RECEIVE_CANCEL_BUTTON = "Завершить прием файлов"

    async def send_text_chunks(message, text, reply_markup=None):

        if not text:

            return

        max_len = 4000

        markup = reply_markup

        for start in range(0, len(text), max_len):

            chunk = text[start:start + max_len]

            await message.answer(chunk, reply_markup=markup)

            if markup is not None:

                markup = None

    async def cancel_infiles_task(user_id):

        task = infiles_tasks.pop(user_id, None)

        if task:

            task.cancel()

            try:

                await task

            except asyncio.CancelledError:

                pass

    def human_readable_size(num_bytes):

        units = [

            (1024 ** 3, "ГБ"),

            (1024 ** 2, "МБ"),

            (1024, "КБ"),

        ]

        for factor, suffix in units:

            if num_bytes >= factor:

                value = num_bytes / factor

                if value.is_integer():

                    value_str = f"{int(value)}"

                else:

                    value_str = f"{value:.2f}".replace(".", ",")

                return f"{value_str} {suffix}"

        return f"{num_bytes} Б"

    def get_limit_warning_text(max_bytes, context):

        if max_bytes <= 50 * 1024 * 1024:

            if context == "send":

                return "Внимание: ограничение Telegram для бота — 50 МБ. Файлы больше этого размера не будут сохранены."

            return "Внимание: ограничение Telegram для бота — 50 МБ. Будут отправлены только файлы, не превышающие этот размер."

        if max_bytes >= 2 * 1024 * 1024 * 1024:

            if context == "send":

                return "Лимит сервера позволяет принимать файлы до 2 ГБ."

            return "Лимит сервера позволяет отправлять файлы до 2 ГБ. Файлы больше лимита будут пропущены."

        return None

    def get_notes_menu_keyboard():

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add(NOTE_WRITE_BUTTON)

        keyboard.add(NOTE_READ_BUTTON)

        keyboard.add(NOTE_MAIN_BACK_BUTTON)

        return keyboard

    def reset_note_inputs(user_id):

        note_mode[user_id] = False

        pending_note.pop(user_id, None)

        note_read_mode.pop(user_id, None)

        note_view_state.pop(user_id, None)

        note_button_map.pop(user_id, None)

    def get_note_files():

        notes_dir = os.path.join(base_dir, "notes")

        if not os.path.isdir(notes_dir):

            return []

        files = []

        for entry in os.listdir(notes_dir):

            file_path = os.path.join(notes_dir, entry)

            if os.path.isfile(file_path) and entry.lower().endswith(".txt"):

                files.append(file_path)

        files.sort(key=lambda path: os.path.getmtime(path), reverse=True)

        return files

    def build_note_button_label(index, file_path):

        title = ""

        try:

            with open(file_path, "r", encoding="utf-8") as note_file:

                title = note_file.readline().strip()

        except Exception:

            title = ""

        if not title:

            title = os.path.basename(file_path)

        if len(title) > 32:

            title = f"{title[:29]}..."

        return f"{index}. {title}"

    async def present_notes_page(message, user_id):

        state = note_view_state.get(user_id)

        if not state:

            await message.answer("Заметки не найдены.", reply_markup=get_notes_menu_keyboard())

            note_read_mode.pop(user_id, None)

            return

        files = state.get("files", [])

        if not files:

            await message.answer("Заметки не найдены.", reply_markup=get_notes_menu_keyboard())

            note_read_mode.pop(user_id, None)

            return

        total_pages = (len(files) - 1) // NOTES_PER_PAGE + 1

        page = max(0, min(state.get("page", 0), total_pages - 1))

        state["page"] = page

        start_index = page * NOTES_PER_PAGE

        end_index = start_index + NOTES_PER_PAGE

        visible = list(enumerate(files[start_index:end_index], start=start_index + 1))

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add(NOTE_BACK_TO_MENU_BUTTON)

        mapping = {}

        for idx, file_path in visible:

            label = build_note_button_label(idx, file_path)

            keyboard.add(label)

            mapping[label] = file_path

        if total_pages > 1:

            if page > 0 and page < total_pages - 1:

                keyboard.row(PREV_PAGE_BUTTON, NEXT_PAGE_BUTTON)

            elif page > 0:

                keyboard.add(PREV_PAGE_BUTTON)

            else:

                keyboard.add(NEXT_PAGE_BUTTON)

        note_button_map[user_id] = mapping

        note_read_mode[user_id] = True

        await message.answer("Выберите заметку для чтения.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Заметки")

    async def notes_menu(message: types.Message):

        user_id = message.from_user.id

        note_menu_active[user_id] = True

        reset_note_inputs(user_id)

        keyboard = get_notes_menu_keyboard()

        await message.answer("Раздел заметок. Выберите действие.", reply_markup=keyboard)

        write_bot_log(f"Пользователь {user_id} открыл раздел заметок.")

    @dp.message_handler(lambda message: message.text == NOTE_MAIN_BACK_BUTTON and note_menu_active.get(message.from_user.id))

    async def notes_back_to_main(message: types.Message):

        user_id = message.from_user.id

        reset_note_inputs(user_id)

        note_menu_active.pop(user_id, None)

        keyboard = get_additional_keyboard()

        await message.answer("Возвращаюсь в главное меню.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == NOTE_WRITE_BUTTON and note_menu_active.get(message.from_user.id))

    async def note_write_menu(message: types.Message):

        user_id = message.from_user.id

        reset_note_inputs(user_id)

        note_mode[user_id] = True

        pending_note[user_id] = ""

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Сохранить заметку", "Отмена")

        await message.answer("Введите текст заметки. После ввода нажмите «Сохранить заметку» или «Отмена».", reply_markup=keyboard)

        write_bot_log(f"Пользователь {user_id} перешёл в режим создания заметок.")

    @dp.message_handler(lambda message: message.text == "Сохранить заметку" and note_mode.get(message.from_user.id, False))

    async def save_note_button(message: types.Message):

        user_id = message.from_user.id

        text = pending_note.get(user_id, "")

        if text.strip():

            notes_dir = os.path.join(base_dir, "notes")

            os.makedirs(notes_dir, exist_ok=True)

            note_file = os.path.join(notes_dir, f"note_{user_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")

            try:

                with open(note_file, "w", encoding="utf-8") as file:

                    file.write(text)

                write_bot_log(f"Пользователь {user_id} сохранил заметку: {text[:50]}...")

                keyboard = get_notes_menu_keyboard()

                await message.answer("Заметка сохранена! Что дальше?", reply_markup=keyboard)

            except Exception as e:

                await message.answer(f"Ошибка сохранения заметки: {e}")

        else:

            await message.answer("Текст заметки не введён!")

        note_mode[user_id] = False

        pending_note.pop(user_id, None)

    @dp.message_handler(lambda message: message.text == "Отмена" and note_mode.get(message.from_user.id, False))

    async def cancel_note_mode(message: types.Message):

        user_id = message.from_user.id

        note_mode[user_id] = False

        pending_note.pop(user_id, None)

        keyboard = get_notes_menu_keyboard() if note_menu_active.get(user_id) else get_additional_keyboard()

        await message.answer("Режим заметок отменён.", reply_markup=keyboard)

    @dp.message_handler(lambda message: note_mode.get(message.from_user.id, False) and message.text not in ["Сохранить заметку", "Отмена"])

    async def collect_note_text(message: types.Message):

        user_id = message.from_user.id

        pending_note[user_id] += message.text + "\n"

        await message.answer("Текст заметки получен. Продолжайте ввод или нажмите «Сохранить заметку» для сохранения.")

    @dp.message_handler(lambda message: message.text == NOTE_READ_BUTTON and note_menu_active.get(message.from_user.id))

    async def note_read_menu(message: types.Message):

        user_id = message.from_user.id

        reset_note_inputs(user_id)

        files = get_note_files()

        if not files:

            note_menu_active[user_id] = True

            await message.answer("Заметок пока нет.", reply_markup=get_notes_menu_keyboard())

            return

        note_view_state[user_id] = {"files": files, "page": 0}

        await present_notes_page(message, user_id)

        write_bot_log(f"Пользователь {user_id} открыл список заметок для чтения.")

    @dp.message_handler(lambda message: message.text == NOTE_BACK_TO_MENU_BUTTON and note_read_mode.get(message.from_user.id))

    async def note_read_back(message: types.Message):

        user_id = message.from_user.id

        note_read_mode.pop(user_id, None)

        note_view_state.pop(user_id, None)

        note_button_map.pop(user_id, None)

        keyboard = get_notes_menu_keyboard()

        await message.answer("Вы вернулись в меню заметок.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == NEXT_PAGE_BUTTON and note_read_mode.get(message.from_user.id))

    async def notes_next_page(message: types.Message):

        user_id = message.from_user.id

        state = note_view_state.get(user_id)

        if not state:

            await message.answer("Список заметок недоступен. Вернитесь в меню заметок.", reply_markup=get_notes_menu_keyboard())

            return

        files = state.get("files", [])

        if not files:

            await message.answer("Заметок пока нет.", reply_markup=get_notes_menu_keyboard())

            return

        total_pages = (len(files) - 1) // NOTES_PER_PAGE + 1

        if state["page"] < total_pages - 1:

            state["page"] += 1

        await present_notes_page(message, user_id)

    @dp.message_handler(lambda message: message.text == PREV_PAGE_BUTTON and note_read_mode.get(message.from_user.id))

    async def notes_prev_page(message: types.Message):

        user_id = message.from_user.id

        state = note_view_state.get(user_id)

        if not state:

            await message.answer("Список заметок недоступен. Вернитесь в меню заметок.", reply_markup=get_notes_menu_keyboard())

            return

        if state.get("page", 0) > 0:

            state["page"] -= 1

        await present_notes_page(message, user_id)

    @dp.message_handler(lambda message: note_read_mode.get(message.from_user.id) and note_button_map.get(message.from_user.id) and message.text in note_button_map.get(message.from_user.id, {}))

    async def send_note_content(message: types.Message):

        user_id = message.from_user.id

        file_path = note_button_map.get(user_id, {}).get(message.text)

        if not file_path:

            await present_notes_page(message, user_id)

            return

        try:

            with open(file_path, "r", encoding="utf-8") as note_file:

                content = note_file.read().strip()

        except FileNotFoundError as error:

            logger.warning(f"Заметка '{file_path}' недоступна: {error}", exc_info=True)

            state = note_view_state.get(user_id)

            if state and file_path in state.get("files", []):

                state["files"].remove(file_path)

            await message.answer("Заметка недоступна. Возможно, файл был удалён.")

            await present_notes_page(message, user_id)

            return

        except Exception as error:

            logger.error(f"Ошибка чтения заметки '{file_path}': {error}", exc_info=True)

            await message.answer(f"Ошибка чтения заметки: {error}")

        else:

            if content:

                await send_text_chunks(message, content)

            else:

                await message.answer("Заметка пуста.")

            write_bot_log(f"Пользователь {user_id} прочитал заметку '{os.path.basename(file_path)}'.")

        await present_notes_page(message, user_id)

    @dp.message_handler(lambda message: message.text == "Отправить файлы")

    async def files_menu(message: types.Message):

        user_id = message.from_user.id

        max_bytes, human_readable = get_max_file_size(message)

        file_mode[user_id] = True

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add(FILE_SEND_CANCEL_BUTTON)

        info_lines = [

            "Режим отправки файлов активирован.",

            f"Лимит размера файла: {human_readable}.",

            "Отправьте файл в чат (документы, фото, аудио, видео и т.д.).",

            "Когда закончите, нажмите «Завершить отправку файлов».",

        ]

        warning_text = get_limit_warning_text(max_bytes, "send")

        if warning_text:

            info_lines.insert(1, warning_text)

        await message.answer("\n".join(info_lines), reply_markup=keyboard)

        write_bot_log(f"Пользователь {user_id} перешёл в режим отправки файлов. Максимальный размер: {human_readable}.")

    @dp.message_handler(lambda message: message.text == FILE_SEND_CANCEL_BUTTON and file_mode.get(message.from_user.id, False))

    async def disable_file_mode(message: types.Message):

        user_id = message.from_user.id

        file_mode[user_id] = False

        keyboard = get_additional_keyboard()

        await message.answer("Режим отправки файлов завершён.", reply_markup=keyboard)

        write_bot_log(f"Пользователь {user_id} завершил режим отправки файлов.")

    @dp.message_handler(lambda message: file_mode.get(message.from_user.id, False), content_types=types.ContentType.ANY)

    async def handle_file_upload(message: types.Message):

        user_id = message.from_user.id

        if not file_mode.get(user_id, False):

            return

        if message.text:

            if message.text == FILE_SEND_CANCEL_BUTTON:

                return

            await message.answer("Режим отправки файлов активен. Отправьте файл или нажмите «Завершить отправку файлов».")

            return

        file_item = None

        file_size = 0

        file_name = None

        if message.document:

            file_item = message.document

            file_size = message.document.file_size or 0

            file_name = message.document.file_name or f"document_{message.document.file_unique_id}"

        elif message.photo:

            file_item = message.photo[-1]

            file_size = file_item.file_size or 0

            file_name = f"photo_{file_item.file_unique_id}.jpg"

        elif message.audio:

            file_item = message.audio

            file_size = message.audio.file_size or 0

            file_name = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"

        elif message.voice:

            file_item = message.voice

            file_size = message.voice.file_size or 0

            file_name = f"voice_{message.voice.file_unique_id}.ogg"

        elif message.video:

            file_item = message.video

            file_size = message.video.file_size or 0

            file_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"

        elif message.video_note:

            file_item = message.video_note

            file_size = message.video_note.file_size or 0

            file_name = f"video_note_{message.video_note.file_unique_id}.mp4"

        elif message.animation:

            file_item = message.animation

            file_size = message.animation.file_size or 0

            file_name = message.animation.file_name or f"animation_{message.animation.file_unique_id}.mp4"

        else:

            await message.answer("Не удалось определить тип файла. Отправьте документ, фото, аудио или видео.")

            return

        max_bytes, human_readable = get_max_file_size(message)

        if file_size and file_size > max_bytes:

            await message.answer(f"Файл слишком велик для сохранения. Максимальный размер: {human_readable}.")

            logger.warning(f"User {user_id}: attempted to upload file '{file_name}' ({file_size} bytes) exceeding limit {max_bytes} bytes.")

            return

        files_dir = os.path.join(base_dir, "files")

        try:

            os.makedirs(files_dir, exist_ok=True)

        except Exception as error:

            logger.error(f"Error creating files directory '{files_dir}': {error}", exc_info=True)

            await message.answer(f"Ошибка при подготовке директории для сохранения файла: {error}")

            return

        save_path = os.path.join(files_dir, file_name)

        try:

            telegram_file = await file_item.get_file()

            file_path_attr = getattr(telegram_file, "file_path", None)

            target_path = file_path_attr or telegram_file.file_path

            if target_path and os.path.isabs(target_path) and os.path.exists(target_path):

                shutil.copy(target_path, save_path)

                write_bot_log(f"Пользователь {user_id}: файл '{file_name}' скопирован напрямую из '{target_path}' в '{save_path}'")

            else:

                await message.bot.download_file(target_path, save_path)

                write_bot_log(f"Пользователь {user_id}: файл '{file_name}' скачан через API и сохранён в '{save_path}'")

            await message.answer(f"Файл '{file_name}' успешно сохранён.")

        except Exception as error:

            logger.error(f"User {user_id}: error saving file '{file_name}' to '{save_path}': {error}", exc_info=True)

            await message.answer(f"Ошибка при сохранении файла: {error}")

    @dp.message_handler(lambda message: message.text == "Прием файлов")

    async def receive_infiles(message: types.Message):

        user_id = message.from_user.id

        max_bytes, human_readable_limit = get_max_file_size(message)

        await cancel_infiles_task(user_id)

        infiles_mode[user_id] = True

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add(FILE_RECEIVE_CANCEL_BUTTON)

        write_bot_log(f"Пользователь {user_id} активировал режим приёма файлов. Максимальный размер: {human_readable_limit}.")

        infiles_dir = os.path.join(base_dir, "infiles")

        if not os.path.isdir(infiles_dir):

            infiles_mode[user_id] = False

            await message.answer("Папка «infiles» не найдена.", reply_markup=get_additional_keyboard())

            return

        files_found = []

        for root, _, files in os.walk(infiles_dir):

            for file_name in sorted(files):

                full_path = os.path.join(root, file_name)

                try:

                    size = os.path.getsize(full_path)

                except OSError as error:

                    logger.error(f"Не удалось получить размер файла '{full_path}': {error}", exc_info=True)

                    await message.answer(f"Не удалось получить размер файла «{os.path.relpath(full_path, infiles_dir)}»: {error}")

                    continue

                relative_path = os.path.relpath(full_path, infiles_dir)

                files_found.append((full_path, relative_path, size))

        if not files_found:

            infiles_mode[user_id] = False

            await message.answer("Файлы не найдены в папке «infiles».", reply_markup=get_additional_keyboard())

            return

        listing_lines = ["Список файлов, доступных для отправки:"]

        for index, (_, relative_path, size) in enumerate(files_found, start=1):

            listing_lines.append(f"{index}. {relative_path} — {human_readable_size(size)}")

        await send_text_chunks(message, "\n".join(listing_lines), reply_markup=keyboard)

        warning_text = get_limit_warning_text(max_bytes, "receive")

        if warning_text:

            await message.answer(warning_text)

        eligible_files = [item for item in files_found if item[2] <= max_bytes]

        skipped_files = [item for item in files_found if item[2] > max_bytes]

        if not eligible_files:

            infiles_mode[user_id] = False

            await message.answer("Нет файлов, удовлетворяющих текущему лимиту. Добавьте файлы меньшего размера.", reply_markup=get_additional_keyboard())

            return

        if skipped_files:

            skipped_lines = ["Пропущены из-за ограничения по размеру:"]

            for _, relative_path, size in skipped_files:

                skipped_lines.append(f"- {relative_path} — {human_readable_size(size)}")

            await send_text_chunks(message, "\n".join(skipped_lines))

        async def stream_files():

            try:

                for full_path, relative_path, size in eligible_files:

                    if not infiles_mode.get(user_id, False):

                        break

                    if not os.path.exists(full_path):

                        await message.answer(f"Файл «{relative_path}» недоступен.")

                        continue

                    try:

                        input_file = types.InputFile(full_path)

                        caption = f"{relative_path} ({human_readable_size(size)})"

                        await message.bot.send_document(message.chat.id, input_file, caption=caption)

                        write_bot_log(f"Пользователь {user_id}: файл '{relative_path}' отправлен из «infiles».")

                        await asyncio.sleep(0)

                    except asyncio.CancelledError:

                        raise

                    except Exception as send_error:

                        logger.error(f"Ошибка отправки файла '{full_path}': {send_error}", exc_info=True)

                        await message.answer(f"Ошибка отправки файла «{relative_path}»: {send_error}")

                if infiles_mode.get(user_id, False):

                    await message.answer("Отправка файлов завершена. Для выхода нажмите «Завершить прием файлов».")

            finally:

                infiles_tasks.pop(user_id, None)

        infiles_tasks[user_id] = asyncio.create_task(stream_files())

    @dp.message_handler(lambda message: message.text == FILE_RECEIVE_CANCEL_BUTTON and infiles_mode.get(message.from_user.id, False))

    async def finish_infiles_mode(message: types.Message):

        user_id = message.from_user.id

        infiles_mode[user_id] = False

        await cancel_infiles_task(user_id)

        keyboard = get_additional_keyboard()

        await message.answer("Режим приёма файлов завершён.", reply_markup=keyboard)

        write_bot_log(f"Пользователь {user_id} завершил режим приёма файлов.")

    @dp.message_handler(lambda message: message.text == "Питание")

    async def power_menu(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил меню «Питание».")

        power_mode[user_id] = True

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.row("Завершить работу", "Перезагрузка")

        keyboard.row("Спящий режим", "Гибернация")

        keyboard.row("Выход из системы", "Назад")

        await message.answer("Выберите действие:", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Назад" and power_mode.get(message.from_user.id, False))

    async def back_from_power(message: types.Message):

        user_id = message.from_user.id

        power_mode[user_id] = False

        await message.answer("Возвращаюсь в главное меню.", reply_markup=get_additional_keyboard())

    @dp.message_handler(lambda message: message.text == "Завершить работу" and power_mode.get(message.from_user.id, False))

    async def confirm_shutdown(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил завершение работы.")

        pending_power_action[user_id] = "shutdown"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Да", "Нет")

        await message.answer("Вы действительно хотите завершить работу?", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Перезагрузка" and power_mode.get(message.from_user.id, False))

    async def confirm_restart(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил перезагрузку.")

        pending_power_action[user_id] = "restart"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Да", "Нет")

        await message.answer("Вы действительно хотите перезагрузить устройство?", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Спящий режим" and power_mode.get(message.from_user.id, False))

    async def confirm_sleep(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил переход в спящий режим.")

        pending_power_action[user_id] = "sleep"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Да", "Нет")

        await message.answer("Перевести компьютер в спящий режим?", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Гибернация" and power_mode.get(message.from_user.id, False))

    async def confirm_hibernate(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил гибернацию.")

        pending_power_action[user_id] = "hibernate"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Да", "Нет")

        await message.answer("Перевести компьютер в режим гибернации?", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Выход из системы" and power_mode.get(message.from_user.id, False))

    async def confirm_logoff(message: types.Message):

        user_id = message.from_user.id

        write_bot_log(f"Пользователь {user_id} запросил выход из учетной записи.")

        pending_power_action[user_id] = "logoff"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

        keyboard.add("Да", "Нет")

        await message.answer("Выполнить выход из системы?", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and message.from_user.id in pending_power_action)

    async def process_power_confirmation(message: types.Message):

        user_id = message.from_user.id

        action = pending_power_action.pop(user_id)

        if message.text == "Да":

            if action == "shutdown":

                write_bot_log(f"Пользователь {user_id} подтвердил завершение работы.")

                await message.answer("Завершение работы. Пожалуйста, подождите...")

                try:

                    if os.name == 'nt':

                        subprocess.run("shutdown /s /t 0", shell=True)

                    else:

                        subprocess.run("sudo shutdown -h now", shell=True)

                except Exception as e:

                    await message.answer(f"Ошибка: {e}")

            elif action == "restart":

                write_bot_log(f"Пользователь {user_id} подтвердил перезагрузку.")

                await message.answer("Перезагрузка запускается. Пожалуйста, подождите...")

                try:

                    if os.name == 'nt':

                        subprocess.run("shutdown /r /t 0", shell=True)

                    else:

                        subprocess.run("sudo reboot", shell=True)

                except Exception as e:

                    await message.answer(f"Ошибка: {e}")

            elif action == "sleep":

                write_bot_log(f"Пользователь {user_id} подтверждает переход в спящий режим.")

                await message.answer("Перевожу компьютер в спящий режим...")

                try:

                    if os.name == 'nt':

                        subprocess.run("powershell -Command \"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)\"", shell=True)

                    else:

                        subprocess.run("systemctl suspend", shell=True)

                except Exception as e:

                    await message.answer(f"Ошибка: {e}")

            elif action == "hibernate":

                write_bot_log(f"Пользователь {user_id} подтверждает гибернацию.")

                await message.answer("Перевожу компьютер в режим гибернации...")

                try:

                    if os.name == 'nt':

                        subprocess.run("shutdown /h", shell=True)

                    else:

                        subprocess.run("systemctl hibernate", shell=True)

                except Exception as e:

                    await message.answer(f"Ошибка: {e}")

            elif action == "logoff":

                write_bot_log(f"Пользователь {user_id} подтверждает выход из системы.")

                await message.answer("Выполняю выход из системы...")

                try:

                    if os.name == 'nt':

                        subprocess.run("shutdown /l", shell=True)

                    else:

                        subprocess.run("logout", shell=True)

                except Exception as e:

                    await message.answer(f"Ошибка: {e}")

        else:

            await message.answer("Действие отменено.")

            if power_mode.get(user_id, False):

                keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

                keyboard.row("Завершить работу", "Перезагрузка")

                keyboard.row("Спящий режим", "Гибернация")

                keyboard.row("Выход из системы", "Назад")

                await message.answer("Выберите действие:", reply_markup=keyboard)

    # ------------------ РћР±СЂР°Р±РѕС‚С‡РёРє РґР»СЏ СЃРїСЂР°РІРєРё ------------------

    @dp.message_handler(lambda message: message.text == "Справка")

    async def send_help(message: types.Message):

        keyboard = get_additional_keyboard()

        max_len = 4096  # Telegram message character limit

        text = info.HELP_TEXT

        for start in range(0, len(text), max_len):

            chunk = text[start:start+max_len]

            # Send keyboard only with the last chunk

            if start + max_len >= len(text):

                await message.answer(chunk, reply_markup=keyboard)

            else:

                await message.answer(chunk)

        write_bot_log(f"Пользователь {message.from_user.id} запросил справку.")

    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "связь с разработчиком")

    async def contact_developer(message: types.Message):

        keyboard = get_additional_keyboard()

        await message.answer(info.CONTACT_TEXT, reply_markup=keyboard)

        write_bot_log(f"Пользователь {message.from_user.id} запросил связь с разработчиком.")

    # Р”РѕР±Р°РІР»СЏРµРј С‚Р°РєР¶Рµ РѕР±СЂР°Р±РѕС‚С‡РёРє РґР»СЏ РєРѕРјР°РЅРґС‹ /contact

    @dp.message_handler(commands=["contact"])

    async def contact_developer_cmd(message: types.Message):

        keyboard = get_additional_keyboard()

        await message.answer(info.CONTACT_TEXT, reply_markup=keyboard)

        write_bot_log(f"Пользователь {message.from_user.id} запросил связь с разработчиком командой /contact.")

        keyboard = get_additional_keyboard()

        await message.answer(info.CONTACT_TEXT, reply_markup=keyboard)

        write_bot_log(f"Пользователь {message.from_user.id} запросил связь с разработчиком.")

