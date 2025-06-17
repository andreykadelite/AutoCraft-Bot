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


# ------------------ Конфигурация логирования ------------------
# Определение базовой директории: поддержка Nuitka Onefile, PyInstaller и обычного режима
if "NUITKA_ONEFILE_PARENT" in os.environ:
    # Для Nuitka Onefile: NUITKA_ONEFILE_PARENT указывает на путь к exe
    base_dir = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
elif getattr(sys, "frozen", False):
    # Для PyInstaller и других фризеров: exe рядом с исполняемым файлом
    base_dir = os.path.dirname(sys.executable)
else:
    # Запуск из исходников
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
# Пытаемся импортировать функцию логирования из основного модуля,
# если не удаётся — используем локальный logger.
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
        return 20 * 1024 * 1024, "20 МБ"

def register_dptools_handlers(dp, base_dir, note_mode, pending_note, file_mode, infiles_mode, power_mode, pending_power_action, get_additional_keyboard):
    @dp.message_handler(lambda message: message.text == "Заметки")
    async def notes_menu(message: types.Message):
        user_id = message.from_user.id
        note_mode[user_id] = True
        pending_note[user_id] = ""
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Сохранить заметку", "Отмена")
        await message.answer("Введите текст заметки. После ввода нажмите «Сохранить заметку» или «Отмена».", reply_markup=keyboard)
        write_bot_log(f"Пользователь {user_id} перешёл в режим заметок.")

    @dp.message_handler(lambda message: message.text == "Сохранить заметку" and note_mode.get(message.from_user.id, False))
    async def save_note_button(message: types.Message):
        user_id = message.from_user.id
        text = pending_note.get(user_id, "")
        if text.strip():
            note_file = os.path.join(base_dir, "notes", f"note_{user_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
            try:
                with open(note_file, "w", encoding="utf-8") as file:
                    file.write(text)
                write_bot_log(f"Пользователь {user_id} сохранил заметку: {text[:50]}...")
                await message.answer("Заметка сохранена!")
            except Exception as e:
                await message.answer(f"Ошибка сохранения заметки: {e}")
        else:
            await message.answer("Текст заметки не введён!")
        note_mode[user_id] = False
        pending_note.pop(user_id, None)
        keyboard = get_additional_keyboard()
        await message.answer("Выберите действие:", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Отмена" and note_mode.get(message.from_user.id, False))
    async def cancel_note_mode(message: types.Message):
        user_id = message.from_user.id
        note_mode[user_id] = False
        pending_note.pop(user_id, None)
        keyboard = get_additional_keyboard()
        await message.answer("Режим заметок отменён.", reply_markup=keyboard)

    @dp.message_handler(lambda message: note_mode.get(message.from_user.id, False) and message.text not in ["Сохранить заметку", "Отмена"])
    async def collect_note_text(message: types.Message):
        user_id = message.from_user.id
        pending_note[user_id] += message.text + "\n"
        await message.answer("Текст заметки получен. Продолжайте ввод или нажмите «Сохранить заметку» для сохранения.")

    @dp.message_handler(lambda message: message.text == "Отправить файлы")
    async def files_menu(message: types.Message):
        user_id = message.from_user.id
        max_bytes, human_readable = get_max_file_size(message)
        file_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Выключить режим отправки файлов")
        await message.answer(
            f"Режим отправки файлов активирован. Отправьте файл (до {human_readable}, можно отправлять любые типы файлов: документы, фото, аудио, видео и т.д.). Для завершения нажмите «Выключить режим отправки файлов».",
            reply_markup=keyboard
        )
        write_bot_log(f"Пользователь {user_id} перешёл в режим отправки файлов. Максимальный размер: {human_readable}.")

    @dp.message_handler(lambda message: message.text == "Выключить режим отправки файлов" and file_mode.get(message.from_user.id, False))
    async def disable_file_mode(message: types.Message):
        user_id = message.from_user.id
        file_mode[user_id] = False
        keyboard = get_additional_keyboard()
        await message.answer("Режим отправки файлов завершён.", reply_markup=keyboard)

    @dp.message_handler(lambda message: file_mode.get(message.from_user.id, False),
                        content_types=[
        types.ContentType.DOCUMENT,
        types.ContentType.PHOTO,
        types.ContentType.AUDIO,
        types.ContentType.VIDEO,
        types.ContentType.VOICE
    ])
    async def handle_file_upload(message: types.Message):
        user_id = message.from_user.id
        if not file_mode.get(user_id, False):
            return

        file_item = None
        file_size = 0
        file_name = None

        if message.document:
            file_item = message.document
            file_size = message.document.file_size
            file_name = message.document.file_name
        elif message.photo:
            photo = message.photo[-1]
            file_item = photo
            file_size = photo.file_size
            file_name = f"photo_{photo.file_unique_id}.jpg"
        elif message.audio:
            file_item = message.audio
            file_size = message.audio.file_size
            file_name = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
        elif message.voice:
            file_item = message.voice
            file_size = message.voice.file_size
            file_name = f"voice_{message.voice.file_unique_id}.ogg"
        elif message.video:
            file_item = message.video
            file_size = message.video.file_size
            file_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
        else:
            await message.answer("Не удалось определить тип файла.")
            return

        max_bytes, human_readable = get_max_file_size(message)
        if file_size > max_bytes:
            await message.answer(f"Файл слишком велик для отправки. Максимальный размер: {human_readable}.")
            logger.warning(f"User {user_id}: attempted to upload file '{file_name}' ({file_size} bytes) exceeding limit {max_bytes} bytes.")
            return

        files_dir = os.path.join(base_dir, "files")
        try:
            os.makedirs(files_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating files directory '{files_dir}': {e}", exc_info=True)
            await message.answer(f"Ошибка при подготовке директории для сохранения файла: {e}")
            return

        save_path = os.path.join(files_dir, file_name)
        try:
            telegram_file = await file_item.get_file()
            file_path_attr = getattr(telegram_file, 'file_path', None)
            if file_path_attr and os.path.isabs(file_path_attr) and os.path.exists(file_path_attr):
                shutil.copy(file_path_attr, save_path)
                write_bot_log(f"Пользователь {user_id}: файл '{file_name}' скопирован напрямую из '{file_path_attr}' в '{save_path}'")
                await message.answer(f"Файл '{file_name}' успешно сохранён напрямую.")
            else:
                await message.bot.download_file(file_path_attr, save_path)
                write_bot_log(f"Пользователь {user_id}: файл '{file_name}' скачан через API и сохранён в '{save_path}'")
                await message.answer(f"Файл '{file_name}' успешно сохранён.")
        except Exception as e:
            logger.error(f"User {user_id}: error saving file '{file_name}' to '{save_path}': {e}", exc_info=True)
            await message.answer(f"Ошибка при сохранении файла: {e}")

    @dp.message_handler(lambda message: message.text == "Прием файлов")
    async def receive_infiles(message: types.Message):
        user_id = message.from_user.id
        max_bytes, human_readable = get_max_file_size(message)
        write_bot_log(f"Пользователь {user_id} активировал режим приёма файлов. Максимальный размер: {human_readable}.")
        infiles_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Завершить прием файлов")
        infiles_dir = os.path.join(base_dir, "infiles")
        if not os.path.exists(infiles_dir):
            await message.answer("Папка «infiles» не найдена.")
            return
        files_to_send = []
        for root, dirs, files in os.walk(infiles_dir):
            for file in files:
                files_to_send.append(os.path.join(root, file))
        if not files_to_send:
            await message.answer("Файлы не найдены в папке «infiles».")
        else:
            for file_path in files_to_send:
                if not infiles_mode.get(user_id, False):
                    break
                file_size = os.path.getsize(file_path)
                max_bytes, human_readable = get_max_file_size(message)
                if file_size > max_bytes:
                    await message.answer(f"Файл «{os.path.basename(file_path)}» слишком велик для отправки (макс {human_readable}).")
                    continue
                try:
                    input_file = types.InputFile(file_path)
                    await message.bot.send_document(message.chat.id, input_file)
                except Exception as e:
                    await message.answer(f"Ошибка отправки файла «{os.path.basename(file_path)}»: {e}")
            await message.answer("Отправка файлов завершена. Для выхода нажмите «Завершить прием файлов».", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Завершить прием файлов" and infiles_mode.get(message.from_user.id, False))
    async def finish_infiles_mode(message: types.Message):
        user_id = message.from_user.id
        infiles_mode[user_id] = False
        keyboard = get_additional_keyboard()
        await message.answer("Режим приёма файлов завершён.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Питание")
    async def power_menu(message: types.Message):
        user_id = message.from_user.id
        write_bot_log(f"Пользователь {user_id} запросил меню «Питание».")
        power_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["Завершить работу", "Перезагрузка", "Назад"]
        keyboard.add(*buttons)
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

    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and message.from_user.id in pending_power_action)
    async def process_power_confirmation(message: types.Message):
        user_id = message.from_user.id
        action = pending_power_action.pop(user_id)
        if message.text == "Да":
            if action == "shutdown":
                await message.answer("Завершаю работу. Ожидайте...")
                try:
                    if os.name == 'nt':
                        subprocess.run("shutdown /s /t 0", shell=True)
                    else:
                        subprocess.run("sudo shutdown -h now", shell=True)
                except Exception as e:
                    await message.answer(f"Ошибка: {e}")
            elif action == "restart":
                await message.answer("Перезагружаю устройство. Ожидайте...")
                try:
                    if os.name == 'nt':
                        subprocess.run("shutdown /r /t 0", shell=True)
                    else:
                        subprocess.run("sudo reboot", shell=True)
                except Exception as e:
                    await message.answer(f"Ошибка: {e}")
        else:
            await message.answer("Операция отменена.")
            if power_mode.get(user_id, False):
                keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
                buttons = ["Завершить работу", "Перезагрузка", "Назад"]
                keyboard.add(*buttons)
                await message.answer("Выберите действие:", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Справка")
    async def send_help(message: types.Message):
        keyboard = get_additional_keyboard()
        max_len = 4096
        text = info.HELP_TEXT
        for start in range(0, len(text), max_len):
            chunk = text[start:start+max_len]
            if start == 0:
                await message.answer(chunk, reply_markup=keyboard)
            else:
                await message.answer(chunk)