# Требуемые зависимости:
# pip install aiogram sounddevice soundfile pydub

import os
import sys

# Определяем директорию для поиска внешних ресурсов (ffmpeg):
# если запущено как EXE, берем текущую рабочую директорию,
# иначе — папку, где лежит скрипт.
if getattr(sys, 'frozen', False):
    base_dir = os.getcwd()
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = base_dir

# Поиск ffmpeg.exe рядом со скриптом/EXE
ffmpeg_candidates = [
    os.path.join(script_dir, "ffmpeg.exe"),
    os.path.join(script_dir, "ffmpeg-7.1", "bin", "ffmpeg.exe"),
    os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe")
]
for p in ffmpeg_candidates:
    if os.path.isfile(p):
        FFMPEG_PATH = p
        break
else:
    raise FileNotFoundError(f"ffmpeg.exe не найден ни в {ffmpeg_candidates}")

from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InputFile
from modulsound import get_sound_keyboard
import sounddevice as sd
import soundfile as sf
import threading
import asyncio
from datetime import datetime
import math
from pydub import AudioSegment

# Настройка конвертера pydub
AudioSegment.converter = FFMPEG_PATH

# Папка для сохранения аудиофайлов (относительно текущей рабочей директории)
SOUND_FOLDER = "sound"
os.makedirs(SOUND_FOLDER, exist_ok=True)

# Максимальный размер аудио (50 МБ)
MAX_AUDIO_SIZE = 50 * 1024 * 1024

def get_audio_limit(bot):
    try:
        server = getattr(bot, 'server', None)
        if server:
            base_url = getattr(server, 'base', None) or getattr(server, '_base', None)
            if base_url and not base_url.startswith("https://api.telegram.org"):
                # локальный сервер Telegram API
                return 2 * 1024 * 1024 * 1024
    except Exception:
        pass
    return MAX_AUDIO_SIZE

# Состояния записи по чатам
MIC_STATE = {}

def get_mic_keyboard(mode: str):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if mode == "ready":
        kb.add(KeyboardButton("Начать запись"), KeyboardButton("Отмена"))
    else:
        kb.add(KeyboardButton("Стоп"), KeyboardButton("Отмена"))
    return kb

def find_mic_device():
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            return idx
    return None

def record_audio(chat_id: int):
    state = MIC_STATE.get(chat_id)
    if not state:
        return
    device = state["device"]
    # Метка времени для имен файлов
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_path = os.path.join(SOUND_FOLDER, f"mic_{chat_id}_{timestamp}.wav")
    # Запись WAV
    with sf.SoundFile(wav_path, mode="w", samplerate=44100, channels=1) as file:
        def callback(indata, frames, time_info, status):
            if state.get("stop") or state.get("cancelled"):
                raise sd.CallbackStop()
            file.write(indata)
        with sd.InputStream(samplerate=44100, channels=1, callback=callback, device=device):
            while not state.get("stop") and not state.get("cancelled"):
                sd.sleep(100)
    # Конвертация в MP3 и удаление WAV
    mp3_path = os.path.join(SOUND_FOLDER, f"mic_{chat_id}_{timestamp}.mp3")
    audio = AudioSegment.from_wav(wav_path)
    audio.export(mp3_path, format="mp3")
    os.remove(wav_path)
    state["filepath"] = mp3_path

async def mic_command_handler(message: types.Message):
    chat_id = message.chat.id
    MIC_STATE[chat_id] = {"state": "ready", "stop": False, "cancelled": False}
    limit = get_audio_limit(message.bot)
    text = (
        f"Режим записи активирован.\n"
        f"Лимит отправки: {limit/1024**3:.2f} ГБ.\n"
        f"Нажмите 'Начать запись'."
    )
    await message.answer(text, reply_markup=get_mic_keyboard("ready"))

async def mic_text_handler(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in MIC_STATE:
        return
    state = MIC_STATE[chat_id]
    keyboard = get_sound_keyboard()
    text = message.text
    if state["state"] == "ready":
        if text == "Начать запись":
            device = find_mic_device()
            if device is None:
                await message.answer("Микрофон не найден.", reply_markup=get_mic_keyboard("ready"))
                return
            state.update({"state": "recording", "device": device})
            thread = threading.Thread(target=record_audio, args=(chat_id,), daemon=True)
            state["thread"] = thread
            thread.start()
            await message.answer("Запись началась. Нажмите 'Стоп'.", reply_markup=get_mic_keyboard("recording"))
        else:
            MIC_STATE.pop(chat_id, None)
            await message.answer("Режим записи отменён.", reply_markup=keyboard)
    else:
        if text == "Стоп":
            state["stop"] = True
            # Ждём завершения фонового потока записи вместо фиксированной задержки
            thread = state.get("thread")
            if thread:
                while thread.is_alive():
                    await asyncio.sleep(0.1)
            path = state.get("filepath")
            if path and not state.get("cancelled"):
                size = os.path.getsize(path)
                limit = get_audio_limit(message.bot)
                if size <= limit:
                    abs_path = os.path.abspath(path)
                    size_mb = size / 1024**2
                    await message.answer_audio(InputFile(path), reply_markup=keyboard)
                    # Отправляем полный путь к файлу и его размер в МБ
                    await message.answer(f"Путь к файлу: {abs_path}", reply_markup=keyboard)
                    await message.answer(f"Размер файла: {size_mb:.2f} МБ", reply_markup=keyboard)
                else:
                    # Разбиение на части
                    audio = AudioSegment.from_mp3(path)
                    total = math.ceil(size / limit)
                    ms = len(audio)
                    base, _ = os.path.splitext(path)
                    for i in range(total):
                        start = int(i * ms / total)
                        end = int((i + 1) * ms / total) if i < total - 1 else ms
                        segment = audio[start:end]
                        part_path = f"{base}_part{i+1}.mp3"
                        segment.export(part_path, format="mp3")
                        await message.answer_audio(InputFile(part_path), reply_markup=keyboard)
                        await message.answer(f"Часть {i+1}/{total} отправлена: {part_path}", reply_markup=keyboard)
                await message.answer(f"Файлы сохранены в {os.path.abspath(SOUND_FOLDER)}", reply_markup=keyboard)
            else:
                await message.answer("Запись отменена.", reply_markup=keyboard)
            MIC_STATE.pop(chat_id, None)
        else:
            state["cancelled"] = True
            state["stop"] = True
            await message.answer("Запись отменена.", reply_markup=keyboard)
            MIC_STATE.pop(chat_id, None)

def register_handlers(dp: Dispatcher):
    dp.message_handler(lambda m: m.text == "Звук с микрофона")(mic_command_handler)
    dp.message_handler(lambda m: m.chat.id in MIC_STATE, content_types=["text"])(mic_text_handler)
