
from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keymenu import get_additional_keyboard, get_main_keyboard
import subprocess
import winsound
import os
import sys
import shutil
from gtts import gTTS
import pyttsx3
from ctypes import POINTER, cast
import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import cv2
import sounddevice as sd
import soundfile as sf
import threading
import asyncio
import time
from datetime import datetime
import glob

# Timelife stream segment duration (seconds)
TIMELIFE_SEGMENT_DURATION = 2  # Changed to 2 seconds for circular video notes  # Changed to 5 seconds for circular video notes

# Максимальный размер видео в байтах (19 МБ)
MAX_VIDEO_SIZE = 19 * 1024 * 1024

# Папки для хранения медиа-файлов
SOUND_FOLDER = "sound"
VIDEO_FOLDER = "videos"
os.makedirs(SOUND_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

# Полный путь до ffmpeg.exe
FFMPEG_PATH = r"E:\vscod\tgbot\test\ffmpeg-7.1\bin\ffmpeg.exe"

# Глобальные состояния
VOICE_MODE = set()
TTS_STATE = {}
LAST_TTS = {}
LAST_VOICE = {}
LAST_FILE = {}
VIDEO_STATE = {}  # состояния для модуля видео
SNAPSHOT_STATE = {}  # состояния для модуля снимка

# Опции синтеза речи
ENGINE_OPTIONS = ["Google", "pyx3"]
VOICE_OPTIONS = {
    "Google": ["ru-RU-Standard-A", "ru-RU-Standard-B"],
    "pyx3": ["Voice1", "Voice2"]
}

# Клавиатуры для TTS
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

# Клавиатуры для основных функций
def get_sound_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Синтез речи"), KeyboardButton("Отправить голос"), KeyboardButton("Очистить sound"), KeyboardButton("Очистить videos"))
    kb.add(KeyboardButton("Громкость"), KeyboardButton("Снимок с камеры"), KeyboardButton("Видео с камеры"))
    kb.add(KeyboardButton("Вернуться"))
    return kb

def get_volume_control_keyboard(is_muted: bool):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Уменьшить громкость"), KeyboardButton("Увеличить громкость"))
    label = "Включить звук" if is_muted else "Выключить звук"
    kb.add(KeyboardButton(label))
    kb.add(KeyboardButton("Вернуться в функции"), KeyboardButton("На главную"))
    return kb

def get_playback_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Воспроизвести на компьютере"))
    kb.add(KeyboardButton("Отмена"))
    return kb

def get_cancel_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Отмена"))
    return kb

def get_video_selection_keyboard(cameras):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for idx, backend in cameras:
        kb.add(KeyboardButton(f"Камера {idx}"))
    kb.add(KeyboardButton("Отмена"))
    return kb


# Состояние для снимков и функция выбора камеры для снимка
def get_snapshot_selection_keyboard(cameras):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for idx, backend in cameras:
        kb.add(KeyboardButton(f"Снимок с камеры - Камера {idx}"))
    kb.add(KeyboardButton("Отмена"))
    return kb

def get_video_control_keyboard(timelife: bool=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if not timelife:
        kb.add(KeyboardButton("Старт"), KeyboardButton("Стоп"), KeyboardButton("Вкл timelife"))
        kb.add(KeyboardButton("Отмена"))
    else:
        # Только кнопка 'Выкл timelife'
        kb.add(KeyboardButton("Выкл timelife"))
    return kb

# Поиск доступных камер (для списка)
def find_camera_indices():
    cameras = []
    backends = []
    if hasattr(cv2, 'CAP_DSHOW'): backends.append(cv2.CAP_DSHOW)
    if hasattr(cv2, 'CAP_MSMF'): backends.append(cv2.CAP_MSMF)
    for backend in backends:
        for idx in range(5):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                cameras.append((idx, backend))
                cap.release()
    return cameras

# Поиск первой камеры (для снимка)
def find_camera_index():
    backends = []
    if hasattr(cv2, 'CAP_DSHOW'): backends.append(cv2.CAP_DSHOW)
    if hasattr(cv2, 'CAP_MSMF'): backends.append(cv2.CAP_MSMF)
    for backend in backends:
        for index in range(5):
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                cap.release()
                return index, backend
    return None, None

# Снимок с камеры
def take_snapshot():
    index, backend = find_camera_index()
    if index is None:
        raise RuntimeError('Камера не найдена.')
    cap = cv2.VideoCapture(index, backend)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise RuntimeError('Не удалось получить кадр с камеры.')
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    screenshot_dir = os.path.join(script_dir, "screenshots")
    os.makedirs(screenshot_dir, exist_ok=True)
    filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    filepath = os.path.join(screenshot_dir, filename)
    cv2.imwrite(filepath, frame)
    return filepath

# Запись видео в фоне и отправка

async def stream_timelife(chat_id, bot):
    # Modified to send circular video notes (2s) instead of plain segments
    state = VIDEO_STATE.get(chat_id)
    if not state:
        return
    index = state["index"]
    backend = state["backend"]
    state["last_stream_msg_id"] = None
    while state.get("timelife"):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        stream_path = os.path.join(VIDEO_FOLDER, f"stream_{chat_id}_{timestamp}.mp4")
        cap = cv2.VideoCapture(index, backend)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        fps = 20.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        # Compute square dimension for circular video note
        min_dim = min(width, height)
        out = cv2.VideoWriter(stream_path, fourcc, fps, (min_dim, min_dim))
        start_time = time.time()
        while time.time() - start_time < TIMELIFE_SEGMENT_DURATION and state.get("timelife"):
            ret, frame = cap.read()
            if not ret:
                break
            # Crop center square for circular shape
            h, w = frame.shape[:2]
            x = (w - min_dim) // 2
            y = (h - min_dim) // 2
            square_frame = frame[y:y+min_dim, x:x+min_dim]
            out.write(square_frame)
            await asyncio.sleep(1/fps)
        cap.release()
        out.release()
        # Send as circular video note (auto-play)
        with open(stream_path, 'rb') as video_file:
            sent = await bot.send_video_note(chat_id, video_file, duration=TIMELIFE_SEGMENT_DURATION, length=min_dim)
        prev_msg = state.get("last_stream_msg_id")
        if prev_msg:
            try:
                await bot.delete_message(chat_id, prev_msg)
            except Exception:
                pass
        state["last_stream_msg_id"] = sent.message_id
        os.remove(stream_path)

async def record_video(chat_id, bot):
    state = VIDEO_STATE.get(chat_id)
    if not state:
        return
    index = state["index"]
    backend = state["backend"]
    duration = state.get("duration")
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    video_dir = os.path.join(script_dir, VIDEO_FOLDER)
    os.makedirs(video_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    video_filepath = os.path.join(video_dir, f"video_{chat_id}_{timestamp}.avi")
    audio_filepath = os.path.join(video_dir, f"audio_{chat_id}_{timestamp}.wav")
    merged_filepath = os.path.join(video_dir, f"video_{chat_id}_{timestamp}.mp4")

    def blocking_record():
        # Видео
        cap = cv2.VideoCapture(index, backend)
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        fps = 20.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = cv2.VideoWriter(video_filepath, fourcc, fps, (width, height))
        # Аудио
        audio_file = sf.SoundFile(audio_filepath, mode='w', samplerate=44100, channels=2)

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(status, file=sys.stderr)
            audio_file.write(indata)

        stream = sd.InputStream(samplerate=44100, channels=2, callback=audio_callback)
        stream.start()

        start_time = time.time()
        while not state.get("stop") and not state.get("cancelled") and (duration is None or time.time() - start_time < duration):
            ret, frame = cap.read()
            if not ret:
                break
            out.write(frame)

        # Остановка
        stream.stop()
        stream.close()
        audio_file.close()
        cap.release()
        out.release()

        # Слияние видео и аудио в mp4 (H.264 + AAC)
        subprocess.run([
            FFMPEG_PATH, '-y',
            '-i', video_filepath,
            '-i', audio_filepath,
            '-c:v', 'libx264', '-preset', 'ultrafast',
            '-c:a', 'aac',
            merged_filepath
        ], check=True)

    await asyncio.to_thread(blocking_record)

    current_state = VIDEO_STATE.get(chat_id, {})
    if current_state.get("cancelled"):
        for path in [video_filepath, audio_filepath, merged_filepath]:
            if os.path.exists(path):
                os.remove(path)
        await bot.send_message(chat_id, "Запись видео отменена.", reply_markup=get_sound_keyboard())
    else:
        file_size = os.path.getsize(merged_filepath)
        if file_size <= MAX_VIDEO_SIZE:
            with open(merged_filepath, 'rb') as video:
                await bot.send_video(chat_id, video)
            await bot.send_message(chat_id, f"Видео отправлено. Сохранено: {merged_filepath}", reply_markup=get_sound_keyboard())
            # Удаляем временные файлы avi и wav
            for path in [video_filepath, audio_filepath]:
                if os.path.exists(path): os.remove(path)
        else:
            await bot.send_message(chat_id, "Видео превышает 19 МБ, разбиваю на части...", reply_markup=get_sound_keyboard())
            base, ext = os.path.splitext(merged_filepath)
            pattern = f"{base}_part%03d{ext}"
            subprocess.run([
                FFMPEG_PATH, "-i", merged_filepath,
                "-c", "copy", "-f", "segment",
                "-segment_time", "60", "-reset_timestamps", "1",
                pattern
            ], check=True)
            parts = sorted(glob.glob(f"{base}_part*{ext}"))
            total = len(parts)
            for idx, part in enumerate(parts, 1):
                with open(part, 'rb') as video:
                    await bot.send_video(chat_id, video)
                await bot.send_message(chat_id, f"Часть {idx}/{total} отправлена: {os.path.basename(part)}", reply_markup=get_sound_keyboard())
            await bot.send_message(chat_id, f"Видео разбито на {total} частей и отправлено.", reply_markup=get_sound_keyboard())
            # Удаляем временные части и файлы avi, wav
            for part in parts:
                if os.path.exists(part): os.remove(part)
            for path in [video_filepath, audio_filepath]:
                if os.path.exists(path): os.remove(path)
    VIDEO_STATE.pop(chat_id, None)
async def cmd_special(message: types.Message):
    await message.answer("Выберите функцию:", reply_markup=get_sound_keyboard())

# Обработчик кнопок
async def button_handler(message: types.Message):
    text = message.text
    chat_id = message.chat.id
    # Обработка состояний снимка
    if chat_id in SNAPSHOT_STATE:
        state = SNAPSHOT_STATE[chat_id].get("state")
        if state == "snapshot_select_camera":
            if text == "Отмена":
                SNAPSHOT_STATE.pop(chat_id, None)
                await message.answer("Отмена снимка.", reply_markup=get_sound_keyboard())
            elif text.startswith("Снимок с камеры - "):
                cam_name = text.replace("Снимок с камеры - ", "")
                for idx, backend in SNAPSHOT_STATE[chat_id]["cameras"]:
                    name = f"Камера {idx}"
                    if name == cam_name:
                        try:
                            cap = cv2.VideoCapture(idx, backend)
                            ret, frame = cap.read()
                            cap.release()
                            if not ret:
                                raise RuntimeError("Не удалось получить кадр с камеры.")
                            script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
                            screenshot_dir = os.path.join(script_dir, "screenshots")
                            os.makedirs(screenshot_dir, exist_ok=True)
                            filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                            filepath = os.path.join(screenshot_dir, filename)
                            cv2.imwrite(filepath, frame)
                            with open(filepath, "rb") as photo:
                                await message.answer_photo(photo)
                            await message.answer(f"Снимок сохранён по пути: {filepath}", reply_markup=get_sound_keyboard())
                            SNAPSHOT_STATE.pop(chat_id, None)
                        except Exception as e:
                            await message.answer(f"Ошибка при съёмке: {e}", reply_markup=get_sound_keyboard())
                            SNAPSHOT_STATE.pop(chat_id, None)
                        break
                else:
                    await message.answer("Пожалуйста, выберите корректную камеру.", reply_markup=get_snapshot_selection_keyboard(SNAPSHOT_STATE[chat_id]["cameras"]))
            else:
                await message.answer("Пожалуйста, выберите кнопку камеры или отмену.", reply_markup=get_snapshot_selection_keyboard(SNAPSHOT_STATE[chat_id]["cameras"]))
        return

    # Обработка состояний видео
    if chat_id in VIDEO_STATE:
        state = VIDEO_STATE[chat_id].get("state")

        # Выбор камеры
        if state == "select_camera":
            if text == "Отмена":
                VIDEO_STATE.pop(chat_id, None)
                await message.answer("Отмена съемки видео.", reply_markup=get_sound_keyboard())
            elif text.startswith("Камера"):
                try:
                    idx = int(text.split()[1])
                    for index, backend in VIDEO_STATE[chat_id]["cameras"]:
                        if index == idx:
                            VIDEO_STATE[chat_id].update({"state": "ready", "index": index, "backend": backend, "timelife": False, "last_stream_msg_id": None})
                            await message.answer("Камера выбрана. Нажмите 'Старт' для начала записи, введите время в секундах для записи с ограничением по времени, или 'Отмена'.", reply_markup=get_video_control_keyboard(False))
                            break
                except Exception:
                    await message.answer("Пожалуйста, выберите корректную камеру.", reply_markup=get_video_selection_keyboard(VIDEO_STATE[chat_id]["cameras"]))
            else:
                await message.answer("Пожалуйста, выберите 'Камера X' или 'Отмена'.", reply_markup=get_video_selection_keyboard(VIDEO_STATE[chat_id]["cameras"]))
            return

        # Готовность к записи
        if state == "ready":
            # Timelife включение/выключение
            if text == "Вкл timelife":
                VIDEO_STATE[chat_id]["timelife"] = True
                await message.answer("Timelife включён. Начинаю трансляцию.", reply_markup=get_video_control_keyboard(True))
                asyncio.create_task(stream_timelife(chat_id, message.bot))
                return
            elif text == "Выкл timelife":
                VIDEO_STATE[chat_id]["timelife"] = False
                await message.answer("Timelife отключён. Трансляция остановлена.", reply_markup=get_video_control_keyboard(False))
                return
            if text == "Отмена":
                VIDEO_STATE.pop(chat_id, None)
                await message.answer("Отмена съемки видео.", reply_markup=get_sound_keyboard())
            elif text == "Старт":
                VIDEO_STATE[chat_id].update({"state": "recording", "duration": None, "stop": False, "cancelled": False})
                await message.answer("Начинаю запись. Нажмите 'Стоп' для остановки или 'Отмена' для отмены.", reply_markup=get_video_control_keyboard(False))
                asyncio.create_task(record_video(chat_id, message.bot))
            elif text == "Стоп":
                await message.answer("Запись не начата. Нажмите 'Старт' или введите время в секундах.", reply_markup=get_video_control_keyboard(False))
            else:
                # возможно введено время
                try:
                    duration = int(text)
                    if duration <= 0:
                        raise ValueError
                    VIDEO_STATE[chat_id].update({"state": "recording", "duration": duration, "stop": False, "cancelled": False})
                    await message.answer(f"Начинаю запись на {duration} секунд. Нажмите 'Стоп' для остановки или 'Отмена' для отмены.", reply_markup=get_video_control_keyboard(False))
                    asyncio.create_task(record_video(chat_id, message.bot))
                except ValueError:
                    await message.answer("Пожалуйста, нажмите 'Старт', 'Стоп', 'Отмена' или введите время в секундах.", reply_markup=get_video_control_keyboard(False))
            return

        # Во время записи
        if state == "recording":
            if text == "Стоп":
                VIDEO_STATE[chat_id]["stop"] = True
                await message.answer("Останавливаю запись...", reply_markup=get_video_control_keyboard(False))
            elif text == "Отмена":
                VIDEO_STATE[chat_id]["cancelled"] = True
                await message.answer("Отмена записи...", reply_markup=get_video_control_keyboard(False))
            else:
                await message.answer("Запись уже идёт. Нажмите 'Стоп' для остановки или 'Отмена' для отмены.", reply_markup=get_video_control_keyboard(False))
            return
# Обработка управления громкостью
    if text == "Громкость":
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        current_vol = int(round(volume.GetMasterVolumeLevelScalar() * 100))
        is_muted = bool(volume.GetMute())
        await message.answer(f"Текущая громкость: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}", reply_markup=get_volume_control_keyboard(is_muted))
        return

    # Обработка изменения громкости и навигации
    if text == "Увеличить громкость":
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctrl = cast(interface, POINTER(IAudioEndpointVolume))
        current = volume_ctrl.GetMasterVolumeLevelScalar()
        new = min(current + 0.1, 1.0)
        volume_ctrl.SetMasterVolumeLevelScalar(new, None)
        current_vol = int(round(new * 100))
        is_muted = bool(volume_ctrl.GetMute())
        await message.answer(f"Громкость увеличена: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}", reply_markup=get_volume_control_keyboard(is_muted))
        return
    elif text == "Уменьшить громкость":
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctrl = cast(interface, POINTER(IAudioEndpointVolume))
        current = volume_ctrl.GetMasterVolumeLevelScalar()
        new = max(current - 0.1, 0.0)
        volume_ctrl.SetMasterVolumeLevelScalar(new, None)
        current_vol = int(round(new * 100))
        is_muted = bool(volume_ctrl.GetMute())
        await message.answer(f"Громкость уменьшена: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}", reply_markup=get_volume_control_keyboard(is_muted))
        return
    elif text == "Включить звук":
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctrl = cast(interface, POINTER(IAudioEndpointVolume))
        volume_ctrl.SetMute(0, None)
        current_vol = int(round(volume_ctrl.GetMasterVolumeLevelScalar() * 100))
        await message.answer(f"Звук включён. Громкость: {current_vol}%", reply_markup=get_volume_control_keyboard(False))
        return
    elif text == "Выключить звук":
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctrl = cast(interface, POINTER(IAudioEndpointVolume))
        volume_ctrl.SetMute(1, None)
        await message.answer("Звук выключен.", reply_markup=get_volume_control_keyboard(True))
        return
    elif text == "Вернуться в функции":
        await message.answer("Возвращаюсь к звуковым функциям.", reply_markup=get_sound_keyboard())
        return
    elif text == "На главную":
        await message.answer("Возвращаюсь на главную.", reply_markup=get_main_keyboard())
        return
    # Обработка динамического выбора камеры для снимка
    if text == "Снимок с камеры":
        cams = find_camera_indices()
        if not cams:
            await message.answer("Камера не найдена.", reply_markup=get_sound_keyboard())
        else:
            SNAPSHOT_STATE[chat_id] = {"state": "snapshot_select_camera", "cameras": cams}
            await message.answer("Выберите камеру для снимка:", reply_markup=get_snapshot_selection_keyboard(cams))
        return

    # Обработка видео с камеры - начало
    if text == "Видео с камеры":
        cams = find_camera_indices()
        if not cams:
            await message.answer("Камера не найдена.", reply_markup=get_sound_keyboard())
        else:
            VIDEO_STATE[chat_id] = {"state": "select_camera", "cameras": cams}
            await message.answer("Выберите камеру для видео:", reply_markup=get_video_selection_keyboard(cams))
        return

    # Синтез речи
    if text == "Синтез речи":
        TTS_STATE[chat_id] = {"state": "engine"}
        await message.answer("Выберите голосовой движок:", reply_markup=ENGINE_KEYBOARD)
        return

    # Очистить sound
    if text == "Очистить sound":
        if os.path.exists(SOUND_FOLDER):
            count = len(os.listdir(SOUND_FOLDER))
            shutil.rmtree(SOUND_FOLDER)
        else:
            count = 0
        os.makedirs(SOUND_FOLDER, exist_ok=True)
        await message.answer(f"Папка sound очищена. Удалено файлов: {count}", reply_markup=get_sound_keyboard())
        return

    # Очистить videos
    if text == "Очистить videos":
        if os.path.exists(VIDEO_FOLDER):
            count = len(os.listdir(VIDEO_FOLDER))
            shutil.rmtree(VIDEO_FOLDER)
        else:
            count = 0
        os.makedirs(VIDEO_FOLDER, exist_ok=True)
        await message.answer(f"Папка videos очищена. Удалено файлов: {count}", reply_markup=get_sound_keyboard())
        return

    # TTS: ввод текста
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
            LAST_FILE[chat_id] = file_path
            with open(file_path, 'rb') as f:
                await message.answer_audio(f)
            await message.answer("Генерация завершена. Можете воспроизвести на компьютере:", reply_markup=get_playback_keyboard())
        else:
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        TTS_STATE.pop(chat_id, None)
        return

    # Выбор движка
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "engine":
        if text in ENGINE_OPTIONS:
            TTS_STATE[chat_id]["engine"] = text
            TTS_STATE[chat_id]["state"] = "voice"
            await message.answer("Выберите голос:", reply_markup=VOICE_KEYBOARDS[text])
        else:
            await message.answer("Пожалуйста, выберите движок из списка.", reply_markup=ENGINE_KEYBOARD)
        return

    # Выбор голоса
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "voice":
        if text in VOICE_OPTIONS.get(TTS_STATE[chat_id]["engine"], []):
            TTS_STATE[chat_id]["voice"] = text
            TTS_STATE[chat_id]["state"] = "text"
            await message.answer("Введите текст для синтеза:", reply_markup=get_cancel_keyboard())
        else:
            await message.answer("Пожалуйста, выберите голос из списка.", reply_markup=VOICE_KEYBOARDS[TTS_STATE[chat_id]["engine"]])
        return

    # Отмена
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

    # Воспроизведение на компьютере
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

    # Отправить голос
    if text == "Отправить голос":
        VOICE_MODE.add(chat_id)
        await message.answer("Режим ожидания голосового сообщения. Отправьте голосовое сообщение или нажмите 'Отмена'", reply_markup=get_cancel_keyboard())
        return

    # Вернуться из общих функций
    if text == "Вернуться":
        await message.answer("Возвращаюсь в меню.", reply_markup=get_additional_keyboard())
        return

# Обработчик голосовых сообщений
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
    LAST_FILE[chat_id] = wav_path
    await message.answer("Голосовое сообщение сохранено. Можете воспроизвести на компьютере:", reply_markup=get_playback_keyboard())

# Регистрация хендлеров
def register_handlers(dp: Dispatcher):
    @dp.message_handler(lambda message: message.text == "Особые функции")
    async def cmd_special_handler(message: types.Message):
        await cmd_special(message)

    @dp.message_handler(
        lambda message:
            message.text in [
                "Синтез речи", "Отправить голос", "Очистить sound", "Очистить videos", "Громкость",
                "Снимок с камеры", "Видео с камеры", "Вернуться",
                "Уменьшить громкость", "Увеличить громкость", "Включить звук", "Выключить звук",
                "Вернуться в функции", "На главную", "Отмена", "Воспроизвести на компьютере"
            ]
            or message.chat.id in TTS_STATE or message.chat.id in VOICE_MODE or message.chat.id in VIDEO_STATE or message.chat.id in SNAPSHOT_STATE,
        content_types=['text']
    )
    async def button_handler_wrapper(message: types.Message):
        await button_handler(message)

    @dp.message_handler(lambda message: message.chat.id in VOICE_MODE, content_types=['voice'])
    async def voice_handler_wrapper(message: types.Message):
        await voice_handler(message)