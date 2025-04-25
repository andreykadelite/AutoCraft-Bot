import os
import subprocess
import asyncio
from datetime import datetime
from aiogram import types
import info

# Максимальный размер файла: 50 МБ
MAX_FILE_SIZE = 50 * 1024 * 1024

# Пытаемся импортировать функцию логирования из основного модуля,
# если не удаётся – определяем её как простую печать в консоль.
try:
    from __main__ import write_bot_log
except ImportError:
    def write_bot_log(msg):
        print(msg)

def register_dptools_handlers(dp, base_dir, note_mode, pending_note, file_mode, infiles_mode, power_mode, pending_power_action, get_additional_keyboard):
    # ------------------ Обработчики для заметок ------------------
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

    # ------------------ Обработчики для отправки файлов ------------------
    @dp.message_handler(lambda message: message.text == "Отправить файлы")
    async def files_menu(message: types.Message):
        user_id = message.from_user.id
        file_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Выключить режим отправки файлов")
        await message.answer(
            "Режим отправки файлов активирован. Отправьте файл (до 50 МБ, можно отправлять любые типы файлов: голосовые, музыку, и т.д.). Для завершения нажмите «Выключить режим отправки файлов».",
            reply_markup=keyboard
        )
        write_bot_log(f"Пользователь {user_id} перешёл в режим отправки файлов.")

    @dp.message_handler(lambda message: message.text == "Выключить режим отправки файлов" and file_mode.get(message.from_user.id, False))
    async def disable_file_mode(message: types.Message):
        user_id = message.from_user.id
        file_mode[user_id] = False
        keyboard = get_additional_keyboard()
        await message.answer("Режим отправки файлов завершён.", reply_markup=keyboard)

    @dp.message_handler(content_types=[types.ContentType.DOCUMENT, types.ContentType.PHOTO, types.ContentType.AUDIO, types.ContentType.VOICE, types.ContentType.VIDEO])
    async def handle_file_upload(message: types.Message):
        user_id = message.from_user.id
        if not file_mode.get(user_id, False):
            return
        file_info = None
        file_size = 0
        file_name = ""
        if message.document:
            file_info = await message.document.get_file()
            file_size = message.document.file_size
            file_name = message.document.file_name
        elif message.photo:
            photo = message.photo[-1]
            file_info = await photo.get_file()
            file_size = photo.file_size
            file_name = f"photo_{photo.file_unique_id}.jpg"
        elif message.audio:
            file_info = await message.audio.get_file()
            file_size = message.audio.file_size
            file_name = message.audio.file_name or f"audio_{message.audio.file_unique_id}.mp3"
        elif message.voice:
            file_info = await message.voice.get_file()
            file_size = message.voice.file_size
            file_name = f"voice_{message.voice.file_unique_id}.ogg"
        elif message.video:
            file_info = await message.video.get_file()
            file_size = message.video.file_size
            file_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
        else:
            await message.answer("Не удалось определить тип файла.")
            return
        if file_size > MAX_FILE_SIZE:
            await message.answer("Файл слишком велик для отправки. Максимальный размер: 50 МБ.")
            return
        save_path = os.path.join(base_dir, "files", file_name)
        try:
            await message.bot.download_file(file_info.file_path, save_path)
            await message.answer(f"Файл '{file_name}' получен и сохранён.")
        except Exception as e:
            await message.answer(f"Ошибка при сохранении файла: {e}")

    # ------------------ Обработчики для приёма файлов ------------------
    @dp.message_handler(lambda message: message.text == "Прием файлов")
    async def receive_infiles(message: types.Message):
        user_id = message.from_user.id
        write_bot_log(f"Пользователь {user_id} активировал режим приёма файлов.")
        infiles_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Завершить прием файлов")
        await message.answer("Режим приёма файлов активирован. Начинаю отправку файлов из «infiles»...", reply_markup=keyboard)
        infiles_dir = os.path.join(base_dir, "infiles")
        if not os.path.exists(infiles_dir):
            await message.answer("Папка «infiles» не найдена.")
            return
        files_to_send = []
        for root, dirs, files in os.walk(infiles_dir):
            for file in files:
                file_path = os.path.join(root, file)
                files_to_send.append(file_path)
        if not files_to_send:
            await message.answer("Файлы не найдены в папке «infiles».")
        else:
            for file_path in files_to_send:
                if not infiles_mode.get(user_id, False):
                    break
                try:
                    file_size = os.path.getsize(file_path)
                except Exception as e:
                    await message.answer(f"Ошибка получения размера файла «{os.path.basename(file_path)}»: {str(e)}")
                    continue
                if file_size > MAX_FILE_SIZE:
                    await message.answer(f"Файл «{os.path.basename(file_path)}» слишком велик для отправки.")
                    continue
                try:
                    input_file = types.InputFile(file_path)
                    await message.bot.send_document(message.chat.id, input_file)
                except Exception as e:
                    await message.answer(f"Ошибка отправки файла «{os.path.basename(file_path)}»: {str(e)}")
            await message.answer("Отправка файлов завершена. Для выхода нажмите «Завершить прием файлов».", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Завершить прием файлов" and infiles_mode.get(message.from_user.id, False))
    async def finish_infiles_mode(message: types.Message):
        user_id = message.from_user.id
        infiles_mode[user_id] = False
        keyboard = get_additional_keyboard()
        await message.answer("Режим приёма файлов завершён.", reply_markup=keyboard)

    # ------------------ Обработчики для функций питания ------------------
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
                    await message.answer(f"Ошибка: {str(e)}")
            elif action == "restart":
                await message.answer("Перезагружаю устройство. Ожидайте...")
                try:
                    if os.name == 'nt':
                        subprocess.run("shutdown /r /t 0", shell=True)
                    else:
                        subprocess.run("sudo reboot", shell=True)
                except Exception as e:
                    await message.answer(f"Ошибка: {str(e)}")
        else:
            await message.answer("Операция отменена.")
            if power_mode.get(user_id, False):
                keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
                buttons = ["Завершить работу", "Перезагрузка", "Назад"]
                keyboard.add(*buttons)
                await message.answer("Выберите действие:", reply_markup=keyboard)

    # ------------------ Обработчик для справки ------------------
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

    # Добавляем также обработчик для команды /contact
    @dp.message_handler(commands=["contact"])
    async def contact_developer_cmd(message: types.Message):
        keyboard = get_additional_keyboard()
        await message.answer(info.CONTACT_TEXT, reply_markup=keyboard)
        write_bot_log(f"Пользователь {message.from_user.id} запросил связь с разработчиком командой /contact.")

        keyboard = get_additional_keyboard()
        await message.answer(info.CONTACT_TEXT, reply_markup=keyboard)
        write_bot_log(f"Пользователь {message.from_user.id} запросил связь с разработчиком.")

