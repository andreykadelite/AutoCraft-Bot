from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keymenu import get_additional_keyboard, get_sound_keyboard
import subprocess
import winsound
import os
import shutil
from gtts import gTTS
import pyttsx3

# Полный путь до ffmpeg.exe
FFMPEG_PATH = r"E:\vscod\tgbot\test\ffmpeg-7.1\bin\ffmpeg.exe"

# Каталог для хранения аудио
SOUND_FOLDER = "sound"
os.makedirs(SOUND_FOLDER, exist_ok=True)

# Глобальные состояния
VOICE_MODE = set()
TTS_STATE = {}
LAST_TTS = {}
LAST_VOICE = {}
# Новый словарь для отслеживания последнего файла
LAST_FILE = {}

ENGINE_OPTIONS = ["Google", "pyx3"]
VOICE_OPTIONS = {
    "Google": ["ru-RU-Standard-A", "ru-RU-Standard-B"],
    "pyx3": ["Voice1", "Voice2"]
}

ENGINE_KEYBOARD = ReplyKeyboardMarkup(resize_keyboard=True)
ENGINE_KEYBOARD.row(*[KeyboardButton(opt) for opt in ENGINE_OPTIONS])
ENGINE_KEYBOARD.add(KeyboardButton("Отмена"))

VOICE_KEYBOARDS = {}
for engine, voices in VOICE_OPTIONS.items():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    row = []
    for v in voices:
        row.append(KeyboardButton(v))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.add(KeyboardButton("Отмена"))
    VOICE_KEYBOARDS[engine] = kb

def get_cancel_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Отмена"))
    return kb

def get_playback_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Воспроизвести на компьютере"))
    kb.add(KeyboardButton("Отмена"))
    return kb

async def cmd_special(message: types.Message):
    await message.answer("Выберите функцию:", reply_markup=get_sound_keyboard())

async def button_handler(message: types.Message):
    text = message.text
    chat_id = message.chat.id

    # Сразу ловим "Синтез речи"
    if text == "Синтез речи":
        TTS_STATE[chat_id] = {"state": "engine"}
        await message.answer("Выберите голосовой движок:", reply_markup=ENGINE_KEYBOARD)
        return

    # 0. Очистить папку sound
    if text == "Очистить sound":
        if os.path.exists(SOUND_FOLDER):
            count = len(os.listdir(SOUND_FOLDER))
            shutil.rmtree(SOUND_FOLDER)
        else:
            count = 0
        os.makedirs(SOUND_FOLDER, exist_ok=True)
        await message.answer(f"Папка sound очищена. Удалено файлов: {count}", reply_markup=get_sound_keyboard())
        return

    # 1. Обработка TTS: ввод текста
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "text":
        if text != "Отмена":
            text_to_synth = text
            engine_choice = TTS_STATE[chat_id]["engine"]
            voice_choice = TTS_STATE[chat_id]["voice"]
            file_path = os.path.join(SOUND_FOLDER, f"tts_{chat_id}_{message.message_id}.mp3")
            if engine_choice == "Google":
                tts = gTTS(text=text_to_synth, lang="ru", tld="com")
                tts.save(file_path)
            else:
                tts_engine = pyttsx3.init()
                tts_engine.setProperty('voice', voice_choice)
                tts_engine.save_to_file(text_to_synth, file_path)
                tts_engine.runAndWait()
            LAST_TTS[chat_id] = file_path
            # Обновляем последний файл
            LAST_FILE[chat_id] = file_path
            with open(file_path, 'rb') as f:
                await message.answer_audio(f)
            await message.answer(
                "Генерация завершена. Можете воспроизвести на компьютере:",
                reply_markup=get_playback_keyboard()
            )
        else:
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        TTS_STATE.pop(chat_id, None)
        return

    # 2. Выбор движка
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "engine":
        if text in ENGINE_OPTIONS:
            TTS_STATE[chat_id]["engine"] = text
            TTS_STATE[chat_id]["state"] = "voice"
            await message.answer("Выберите голос:", reply_markup=VOICE_KEYBOARDS[text])
        else:
            await message.answer("Пожалуйста, выберите движок из списка.", reply_markup=ENGINE_KEYBOARD)
        return

    # 3. Выбор голоса
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "voice":
        if text in VOICE_OPTIONS.get(TTS_STATE[chat_id]["engine"], []):
            TTS_STATE[chat_id]["voice"] = text
            TTS_STATE[chat_id]["state"] = "text"
            await message.answer("Введите текст для синтеза:", reply_markup=get_cancel_keyboard())
        else:
            await message.answer("Пожалуйста, выберите голос из списка.", reply_markup=VOICE_KEYBOARDS[TTS_STATE[chat_id]["engine"]])
        return

    # 4. Отмена
    if text == "Отмена":
        if chat_id in VOICE_MODE:
            VOICE_MODE.remove(chat_id)
            await message.answer("Режим отправки голоса отменён.", reply_markup=get_sound_keyboard())
        elif chat_id in TTS_STATE:
            TTS_STATE.pop(chat_id, None)
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        else:
            await message.answer("Действие отменено.", reply_markup=get_sound_keyboard())
        return

    # 5. Воспроизведение на компьютере
    if text == "Воспроизвести на компьютере":
        path = LAST_FILE.get(chat_id)
        if path and os.path.exists(path):
            ext = os.path.splitext(path)[1].lower()
            if ext == ".mp3":
                wav_path = path.replace(".mp3", ".wav")
                subprocess.run([FFMPEG_PATH, "-y", "-i", path, wav_path], check=True)
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                os.remove(wav_path)
            else:
                winsound.PlaySound(path, winsound.SND_FILENAME)
            await message.answer("Воспроизводится на компьютере.", reply_markup=get_playback_keyboard())
        else:
            await message.answer("Нет готового аудиофайла для воспроизведения.", reply_markup=get_sound_keyboard())
        return

    # 6. Отправить голос
    if text == "Отправить голос":
        VOICE_MODE.add(chat_id)
        await message.answer("Режим ожидания голосового сообщения. Отправьте голосовое сообщение или нажмите 'Отмена'", reply_markup=get_cancel_keyboard())
        return

    # 7. Вернуться
    if text == "Вернуться":
        await message.answer("Возвращаюсь в меню.", reply_markup=get_additional_keyboard())
        return

async def voice_handler(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in VOICE_MODE:
        return
    file_info = await message.bot.get_file(message.voice.file_id)
    ogg_path = os.path.join(SOUND_FOLDER, f"voice_{chat_id}_{message.voice.file_unique_id}.ogg")
    await message.bot.download_file(file_info.file_path, ogg_path)
    wav_path = ogg_path.replace(".ogg", ".wav")
    subprocess.run([FFMPEG_PATH, "-y", "-i", ogg_path, wav_path], check=True)
    os.remove(ogg_path)
    LAST_VOICE[chat_id] = wav_path
    # Обновляем последний файл
    LAST_FILE[chat_id] = wav_path
    await message.answer("Голосовое сообщение сохранено. Можете воспроизвести на компьютере:", reply_markup=get_playback_keyboard())

def register_handlers(dp: Dispatcher):
    dp.register_message_handler(cmd_special, lambda msg: msg.text == "Особые функции")
    dp.register_message_handler(button_handler, content_types=['text'])
    dp.register_message_handler(voice_handler, content_types=['voice'])
