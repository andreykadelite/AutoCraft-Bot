from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keymenu import get_additional_keyboard, get_main_keyboard
import subprocess
import math
import winsound
import os
import os, sys
if getattr(sys, 'frozen', False):
    base_dir = os.getcwd()
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
# Добавлена поддержка папки ffmpeg-7.1/bin рядом со скриптом
script_dir = base_dir
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
TIMELIFE_SEGMENT_DURATION = 2  # сегменты кружков 2 сек

# Максимальный размер видео в байтах (49 МБ)
MAX_VIDEO_SIZE = 49 * 1024 * 1024

# ---------- NEW: состояние воспроизведения (для мгновенной Отмены) ----------
# PLAYBACK_STATE[chat_id] = {
#   "playing": bool,
#   "temp_wav": str|None,
#   "cleanup_timer": threading.Timer|None
# }
PLAYBACK_STATE = {}

# ---------- NEW: выбор устройства воспроизведения для управления громкостью ----------
# ВНИМАНИЕ: мы НЕ меняем системное устройство по умолчанию глобально.
# Мы даём выбрать ЦЕЛЕВОЙ аудио-эндпоинт, над которым бот будет выполнять
# операции громкости/мута. В списке помечаем текущее системное "по умолчанию".
AUDIO_OUTPUT_STATE = {}   # {chat_id: {"state": "select_output", "devices": [ {id, name}, ... ]}}
CURRENT_OUTPUT_DEVICE = {}  # {chat_id: device_id}

def _stop_playback(chat_id: int, silent: bool = True):
    """Остановить текущее воспроизведение, прибрать temp-файл и таймер."""
    state = PLAYBACK_STATE.get(chat_id)
    try:
        # Останавливаем winsound (работает для SND_ASYNC)
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass
    if state:
        # Отключаем отложенную очистку
        t = state.get("cleanup_timer")
        if t and isinstance(t, threading.Timer):
            try:
                t.cancel()
            except Exception:
                pass
        # Удаляем temp wav, если есть
        tmp = state.get("temp_wav")
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        # Сбрасываем состояние
        PLAYBACK_STATE.pop(chat_id, None)

def _schedule_cleanup(chat_id: int, temp_wav: str, seconds: float):
    """Плановая уборка после окончания воспроизведения (если отмены не было)."""
    def _cleanup():
        # Если к этому моменту воспроизведение не отменяли — уберём temp и состояние
        state = PLAYBACK_STATE.get(chat_id)
        if not state:
            # уже очищено
            return
        # Переинициализируем, чтобы не зависало состояние
        tmp = state.get("temp_wav")
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        PLAYBACK_STATE.pop(chat_id, None)

    timer = threading.Timer(max(0.2, seconds + 0.5), _cleanup)
    timer.daemon = True
    timer.start()
    PLAYBACK_STATE.setdefault(chat_id, {})["cleanup_timer"] = timer

def _start_playback(chat_id: int, path: str):
    """Запускает проигрывание файла асинхронно и позволяет прерывать по 'Отмена'."""
    # Перед стартом снесём возможное предыдущее проигрывание
    _stop_playback(chat_id)

    # Подготовка файла: в winsound корректнее скармливать WAV
    ext = os.path.splitext(path)[1].lower()
    temp_wav = None
    src_for_play = path
    try:
        if ext != ".wav":
            # Конвертим во временный WAV рядом с исходником
            temp_wav = path + ".__tmp_play__.wav"
            subprocess.run([FFMPEG_PATH, "-y", "-i", path, temp_wav], check=True, capture_output=True, text=True)
            src_for_play = temp_wav
    except subprocess.CalledProcessError as e:
        # Если конвертация не удалась — пробуем отдать как есть, но предупреждаем логом
        src_for_play = path
        temp_wav = None

    # Оценка длительности для отложенной уборки (только если WAV)
    duration = 0.0
    try:
        if os.path.splitext(src_for_play)[1].lower() == ".wav" and os.path.exists(src_for_play):
            with sf.SoundFile(src_for_play, 'r') as f:
                frames = len(f)
                sr = f.samplerate or 44100
                duration = frames / float(sr) if sr else 0.0
    except Exception:
        duration = 0.0

    # Сохраняем состояние
    PLAYBACK_STATE[chat_id] = {"playing": True, "temp_wav": temp_wav, "cleanup_timer": None}

    # Воспроизводим асинхронно: так обработчик кнопок НЕ блокируется
    winsound.PlaySound(src_for_play, winsound.SND_FILENAME | winsound.SND_ASYNC)

    # Поставим отложенную уборку: удалим temp_wav и очистим состояние
    if duration > 0:
        _schedule_cleanup(chat_id, temp_wav, duration)


# Dynamic video size limit based on API server type
def get_video_limit(bot):
    """
    Return maximum video size in bytes: 2GB when connected to local Telegram API server,
    otherwise use standard MAX_VIDEO_SIZE.
    """
    try:
        server = getattr(bot, 'server', None)
        if server:
            base_url = getattr(server, 'base', None) or getattr(server, '_base', None)
            # If base_url does not start with standard API domain, assume local
            if base_url and not base_url.startswith('https://api.telegram.org'):
                return 2 * 1024 * 1024 * 1024
    except Exception:
        pass
    return MAX_VIDEO_SIZE



# Папки для хранения медиа-файлов
SOUND_FOLDER = "sound"
VIDEO_FOLDER = "videos"
os.makedirs(SOUND_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)


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
    # Row 1: TTS
    kb.row(KeyboardButton("Синтез речи"), KeyboardButton("Отправить голос"))
    # Row 2: Microphone
    kb.row(KeyboardButton("Звук с микрофона"), KeyboardButton("Управление браузером"))
    # Row 3: Camera functions
    kb.row(KeyboardButton("Снимок с камеры"), KeyboardButton("Видео с камеры"), KeyboardButton("Видео с экрана"))
    # Row 4: Cleanup and volume
    kb.row(KeyboardButton("Очистить sound"), KeyboardButton("Очистить videos"), KeyboardButton("Громкость"))
    # Row 5: Messages and chat
    kb.row(KeyboardButton("Отправить сообщение на компьютер"), KeyboardButton("Создать интерактивный чат"))
    # Bottom: Return
    kb.add(KeyboardButton("Вернуться"))
    return kb

def get_volume_control_keyboard(is_muted: bool, current_device_name: str = None, is_default: bool = False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Уменьшить громкость"), KeyboardButton("Увеличить громкость"))
    label = "Включить звук" if is_muted else "Выключить звук"
    kb.add(KeyboardButton(label))
    # NEW: кнопка смены устройства
    kb.add(KeyboardButton("Сменить устройство воспроизведения"))
    # Навигация
    kb.add(KeyboardButton("Вернуться в функции"), KeyboardButton("На главную"))
    return kb

def get_output_devices_keyboard(devices, default_id):
    """Сформировать клавиатуру устройств вывода. Помечаем системное по умолчанию."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for i, dev in enumerate(devices, 1):
        name = dev["name"]
        if dev["id"] == default_id:
            name = f"{name} (по умолчанию)"
        kb.add(KeyboardButton(f"{i}. {name}"))
    kb.add(KeyboardButton("Отмена"))
    return kb

def _get_default_playback_device_id():
    try:
        spk = AudioUtilities.GetSpeakers()
        return spk.GetId()
    except Exception:
        return None

def _list_playback_devices():
    """Вернуть список активных устройств ВЫВОДА: [{id, name}, ...]."""
    result = []
    try:
        devices = AudioUtilities.GetAllDevices()
    except Exception:
        devices = []
    # Попробуем отфильтровать по data_flow == 0 (Render). Если свойства нет — просто возьмём всё, что даёт IAudioEndpointVolume.
    for d in devices:
        dev_id = None
        name = None
        try:
            dev_id = d.GetId()
            name = getattr(d, "FriendlyName", None) or "Аудио устройство"
            data_flow = getattr(d, "DataFlow", getattr(d, "data_flow", None))
            state = getattr(d, "State", getattr(d, "state", None))
            # 0 = Render, 1 = Capture; 1 (DEVICE_STATE_ACTIVE) = активно
            if data_flow is not None and data_flow != 0:
                continue
            if state is not None and state != 1:
                continue
            # Проверим, что устройство поддерживает IAudioEndpointVolume
            try:
                interface = d.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                cast(interface, POINTER(IAudioEndpointVolume))
            except Exception:
                continue
            result.append({"id": dev_id, "name": name})
        except Exception:
            continue
    # Фолбэк: если ничего не нашли — добавим системное по умолчанию (если доступно)
    if not result:
        try:
            spk = AudioUtilities.GetSpeakers()
            result.append({"id": spk.GetId(), "name": getattr(spk, "FriendlyName", "Динамики")})
        except Exception:
            pass
    return result

def _find_device_by_id(dev_id):
    try:
        for d in AudioUtilities.GetAllDevices():
            try:
                if d.GetId() == dev_id:
                    return d
            except Exception:
                continue
    except Exception:
        pass
    return None

def _get_volume_interface_for_chat(chat_id):
    """Получить интерфейс IAudioEndpointVolume для выбранного устройства (или системного по умолчанию)."""
    # Сначала пробуем выбранное пользователем устройство
    dev_id = CURRENT_OUTPUT_DEVICE.get(chat_id)
    if dev_id:
        d = _find_device_by_id(dev_id)
        if d is not None:
            try:
                interface = d.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                return cast(interface, POINTER(IAudioEndpointVolume)), d
            except Exception:
                # Если что-то не так — убираем выбор
                CURRENT_OUTPUT_DEVICE.pop(chat_id, None)
    # Фолбэк на системное по умолчанию
    spk = AudioUtilities.GetSpeakers()
    interface = spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume)), spk

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
def find_camera_indices(max_index: int = 10):
    """Возвращает список уникальных камер без дублей кнопок.
    Для каждого индекса выбирается первый рабочий backend (MSMF, затем DSHOW).
    Это устраняет случаи, когда один и тот же индекс открывается разными бэкендами
    и в клавиатуре появлялись "Камера 0", "Камера 0".
    """
    cameras = []
    # Предпочитаем современные бэкенды Windows: сначала MSMF, потом DSHOW
    backends = []
    if hasattr(cv2, 'CAP_MSMF'):
        backends.append(cv2.CAP_MSMF)
    if hasattr(cv2, 'CAP_DSHOW'):
        backends.append(cv2.CAP_DSHOW)
    # Фолбэк, если по какой-то причине нет этих констант
    if not backends and hasattr(cv2, 'CAP_ANY'):
        backends.append(cv2.CAP_ANY)

    for idx in range(max_index):
        chosen_backend = None
        for backend in backends:
            cap = cv2.VideoCapture(idx, backend)
            opened = cap.isOpened()
            cap.release()
            if opened:
                chosen_backend = backend
                break
        if chosen_backend is not None:
            cameras.append((idx, chosen_backend))
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

async def _stream_timelife(chat_id, bot):
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

async def _record_video(chat_id, bot):
    try:
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
            try:
                result = subprocess.run(
                    [
                        FFMPEG_PATH, '-y',
                        '-i', video_filepath,
                        '-i', audio_filepath,
                        '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-c:a', 'aac',
                        merged_filepath
                    ], capture_output=True, text=True, check=True
                )
            except subprocess.CalledProcessError as e:
                state["error"] = e.stderr or e.stdout or str(e)
                return
    
        await asyncio.to_thread(blocking_record)
        current_state = VIDEO_STATE.get(chat_id, {})
        error = current_state.get("error")
        if error:
            await bot.send_message(
                chat_id,
                f"""Ошибка конвертации:
```{error}```
Конвертер: {FFMPEG_PATH}""",\
                parse_mode='Markdown',
                reply_markup=get_sound_keyboard()
            )
            return
    
        current_state = VIDEO_STATE.get(chat_id, {})
        if current_state.get("cancelled"):
            for path in [video_filepath, audio_filepath, merged_filepath]:
                if os.path.exists(path):
                    os.remove(path)
            await bot.send_message(chat_id, "Запись видео отменена.", reply_markup=get_sound_keyboard())
        else:
            
            file_size = os.path.getsize(merged_filepath)
            limit = get_video_limit(bot)
            limit_text = f"{limit/1024**3:.2f} ГБ" if limit>=1024**3 else f"{limit/1024**2:.2f} МБ"
            if file_size <= limit:
                with open(merged_filepath, 'rb') as video:
                    await bot.send_video(chat_id, video)
                await bot.send_message(
                    chat_id,
                    f"Видео отправлено. Размер: {file_size/1024**2:.2f} МБ. Лимит видео: {limit_text}",
                    reply_markup=get_sound_keyboard()
                )
                # Удаляем временные файлы avi и wav
                for path in [video_filepath, audio_filepath]:
                    if os.path.exists(path):
                        os.remove(path)
            else:
                await bot.send_message(
                    chat_id,
                    f"Видео превышает {limit_text}, разбиваю на части по {limit_text}...",
                    reply_markup=get_sound_keyboard()
                )
                base, ext = os.path.splitext(merged_filepath)
                pattern = f"{base}_part%03d{ext}"
                # Split video into parts based on duration and target size
                file_size = os.path.getsize(merged_filepath)
                num_parts = math.ceil(file_size / limit)
                # Get video duration using OpenCV
                cap_split = cv2.VideoCapture(merged_filepath)
                fps_split = cap_split.get(cv2.CAP_PROP_FPS) or 1
                frame_count = cap_split.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                cap_split.release()
                duration = frame_count / fps_split if fps_split > 0 else 0
                segment_time = int(math.ceil(duration / num_parts)) if num_parts > 0 else int(duration)
                if segment_time < 1:
                    segment_time = 1
                pattern = f"{base}_part%03d{ext}"
                split_cmd = [
                    FFMPEG_PATH, "-i", merged_filepath,
                    "-c", "copy", "-f", "segment",
                    "-segment_time", str(segment_time),
                    "-reset_timestamps", "1",
                    pattern
                ]
                try:
                    subprocess.run(split_cmd, check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as e:
                    await bot.send_message(
                        chat_id,
                        f"Ошибка при разделении видео на части:\\n```{e.stderr or e.stdout or str(e)}```",
                        parse_mode='Markdown',
                        reply_markup=get_sound_keyboard()
                    )
                    return
                parts = sorted(glob.glob(f"{base}_part*{ext}"))
                total = len(parts)
                for idx, part in enumerate(parts, 1):
                    with open(part, 'rb') as video:
                        await bot.send_video(chat_id, video)
                    await bot.send_message(
                        chat_id,
                        f"Часть {idx}/{total} отправлена: {os.path.basename(part)}",
                        reply_markup=get_sound_keyboard()
                    )
                await bot.send_message(
                    chat_id,
                    f"Видео разбито на {total} частей и отправлено.",
                    reply_markup=get_sound_keyboard()
                )
                # Удаляем временные файлы, оставляем только целый mp4
                for path in [video_filepath, audio_filepath]:
                    if os.path.exists(path):
                        os.remove(path)
                for part in parts:
                    if os.path.exists(part):
                        os.remove(part)
    finally:
        VIDEO_STATE.pop(chat_id, None)
async def cmd_special(message: types.Message):
    await message.answer("Выберите функцию:", reply_markup=get_sound_keyboard())

# Обработчик кнопок
async def button_handler(message: types.Message):
    text = message.text
    chat_id = message.chat.id

    # -------- NEW: выбор устройства воспроизведения --------
    if chat_id in AUDIO_OUTPUT_STATE:
        st = AUDIO_OUTPUT_STATE.get(chat_id, {})
        if st.get("state") == "select_output":
            if text == "Отмена":
                AUDIO_OUTPUT_STATE.pop(chat_id, None)
                # Вернёмся в меню громкости
                try:
                    vol_iface, dev = _get_volume_interface_for_chat(chat_id)
                    current_vol = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
                    is_muted = bool(vol_iface.GetMute())
                except Exception:
                    current_vol = 0
                    is_muted = False
                await message.answer("Отмена выбора устройства.", reply_markup=get_volume_control_keyboard(is_muted))
                return
            # Пытаемся распарсить "N. Название"
            try:
                if "." in text:
                    num_str = text.split(".", 1)[0].strip()
                    idx = int(num_str) - 1
                    devices = st.get("devices", [])
                    if idx < 0 or idx >= len(devices):
                        raise ValueError
                    chosen = devices[idx]
                    CURRENT_OUTPUT_DEVICE[chat_id] = chosen["id"]
                    AUDIO_OUTPUT_STATE.pop(chat_id, None)
                    # Обновим меню громкости
                    try:
                        vol_iface, dev = _get_volume_interface_for_chat(chat_id)
                        current_vol = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
                        is_muted = bool(vol_iface.GetMute())
                        dev_name = getattr(dev, "FriendlyName", None) or "Аудио устройство"
                    except Exception:
                        current_vol = 0
                        is_muted = False
                        dev_name = "Аудио устройство"
                    await message.answer(
                        f"Выбрано устройство для управления громкостью: {dev_name}",
                        reply_markup=get_volume_control_keyboard(is_muted)
                    )
                    return
            except Exception:
                pass
            # Если не распарсили — повторяем клавиатуру
            devices = st.get("devices", [])
            default_id = _get_default_playback_device_id()
            await message.answer("Пожалуйста, выберите пункт списком ниже:", reply_markup=get_output_devices_keyboard(devices, default_id))
            return

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
                            limit = get_video_limit(message.bot)
                            limit_text = f"{limit/1024**3:.2f} ГБ" if limit>=1024**3 else f"{limit/1024**2:.2f} МБ"
                            await message.answer(
                                f"Камера выбрана. Лимит видео: {limit_text}. Нажмите 'Старт' для начала записи, введите время в секундах для записи с ограничением по времени, или 'Отмена'.",
                                reply_markup=get_video_control_keyboard(False)
                            )
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
        # Определим текущее устройство, громкость и статус мута
        try:
            vol_iface, dev_obj = _get_volume_interface_for_chat(chat_id)
            current_vol = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
            is_muted = bool(vol_iface.GetMute())
            dev_name = getattr(dev_obj, "FriendlyName", None) or "Аудио устройство"
        except Exception:
            current_vol = 0
            is_muted = False
            dev_name = "Аудио устройство"
        # Выведем состояние
        await message.answer(
            f"Текущая громкость: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}\\nУстройство: {dev_name}",
            reply_markup=get_volume_control_keyboard(is_muted, dev_name)
        )
        return

    # Обработка изменения громкости и навигации
    if text == "Увеличить громкость":
        vol_iface, _ = _get_volume_interface_for_chat(chat_id)
        current = vol_iface.GetMasterVolumeLevelScalar()
        new = min(current + 0.1, 1.0)
        vol_iface.SetMasterVolumeLevelScalar(new, None)
        current_vol = int(round(new * 100))
        is_muted = bool(vol_iface.GetMute())
        await message.answer(f"Громкость увеличена: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}", reply_markup=get_volume_control_keyboard(is_muted))
        return
    elif text == "Уменьшить громкость":
        vol_iface, _ = _get_volume_interface_for_chat(chat_id)
        current = vol_iface.GetMasterVolumeLevelScalar()
        new = max(current - 0.1, 0.0)
        vol_iface.SetMasterVolumeLevelScalar(new, None)
        current_vol = int(round(new * 100))
        is_muted = bool(vol_iface.GetMute())
        await message.answer(f"Громкость уменьшена: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}", reply_markup=get_volume_control_keyboard(is_muted))
        return
    elif text == "Включить звук":
        vol_iface, _ = _get_volume_interface_for_chat(chat_id)
        vol_iface.SetMute(0, None)
        current_vol = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
        await message.answer(f"Звук включён. Громкость: {current_vol}%", reply_markup=get_volume_control_keyboard(False))
        return
    elif text == "Выключить звук":
        vol_iface, _ = _get_volume_interface_for_chat(chat_id)
        vol_iface.SetMute(1, None)
        await message.answer("Звук выключен.", reply_markup=get_volume_control_keyboard(True))
        return
    elif text == "Сменить устройство воспроизведения":
        # Собираем список устройств вывода
        devices = _list_playback_devices()
        if not devices:
            await message.answer("Не удалось получить список устройств воспроизведения.", reply_markup=get_volume_control_keyboard(False))
            return
        AUDIO_OUTPUT_STATE[chat_id] = {"state": "select_output", "devices": devices}
        default_id = _get_default_playback_device_id()
        await message.answer("Выберите устройство для управления громкостью:", reply_markup=get_output_devices_keyboard(devices, default_id))
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
        if text == "Отмена":
            TTS_STATE.pop(chat_id, None)
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        elif text in ENGINE_OPTIONS:
            TTS_STATE[chat_id]["engine"] = text
            TTS_STATE[chat_id]["state"] = "voice"
            await message.answer("Выберите голос:", reply_markup=VOICE_KEYBOARDS[text])
        else:
            await message.answer("Пожалуйста, выберите движок из списка.", reply_markup=ENGINE_KEYBOARD)
        return

    # Выбор голоса
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "voice":
        if text == "Отмена":
            TTS_STATE.pop(chat_id, None)
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        elif text in VOICE_OPTIONS.get(TTS_STATE[chat_id]["engine"], []):
            TTS_STATE[chat_id]["voice"] = text
            TTS_STATE[chat_id]["state"] = "text"
            await message.answer("Введите текст для синтеза:", reply_markup=get_cancel_keyboard())
        else:
            await message.answer("Пожалуйста, выберите голос из списка.", reply_markup=VOICE_KEYBOARDS[TTS_STATE[chat_id]["engine"]])
        return

    # Отмена (ПЕРВЫМ ДЕЛОМ проверяем активное воспроизведение)
    if text == "Отмена":
        if chat_id in PLAYBACK_STATE:
            _stop_playback(chat_id)
            # Выходим из всех режимов, чтобы "режим" закрылся целиком
            if chat_id in VOICE_MODE:
                VOICE_MODE.remove(chat_id)
            if chat_id in TTS_STATE:
                TTS_STATE.pop(chat_id, None)
            if chat_id in AUDIO_OUTPUT_STATE:
                AUDIO_OUTPUT_STATE.pop(chat_id, None)
            await message.answer("Воспроизведение остановлено. Режим закрыт.", reply_markup=get_sound_keyboard())
            return
        if chat_id in VOICE_MODE:
            VOICE_MODE.remove(chat_id)
            await message.answer("Режим отправки голоса отменён.", reply_markup=get_sound_keyboard())
            return
        if chat_id in TTS_STATE:
            TTS_STATE.pop(chat_id, None)
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
            return
        if chat_id in AUDIO_OUTPUT_STATE:
            AUDIO_OUTPUT_STATE.pop(chat_id, None)
            await message.answer("Выбор устройства отменён.", reply_markup=get_sound_keyboard())
            return
        await message.answer("Действие отменено.", reply_markup=get_sound_keyboard())
        return

    # Воспроизведение на компьютере
    if text == "Воспроизвести на компьютере":
        path = LAST_FILE.get(chat_id)
        if path and os.path.exists(path):
            # Стартуем асинхронное воспроизведение с возможностью мгновенной отмены
            _start_playback(chat_id, path)
            await message.answer("Воспроизводится на компьютере. Нажмите «Отмена», чтобы прервать и выйти из режима.", reply_markup=get_playback_keyboard())
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
    file_path_attr = getattr(file_info, 'file_path', None)
    ogg_path = os.path.join(SOUND_FOLDER, f"voice_{chat_id}_{message.voice.file_unique_id}.ogg")
    # Save voice file: copy if local Telegram API server, else download
    if file_path_attr and os.path.isabs(file_path_attr) and os.path.exists(file_path_attr):
        shutil.copy(file_path_attr, ogg_path)
    else:
        await message.bot.download_file(file_path_attr, ogg_path)
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
                "Вернуться в функции", "На главную", "Отмена", "Воспроизвести на компьютере",
                "Сменить устройство воспроизведения"
            ]
            or message.chat.id in TTS_STATE or message.chat.id in VOICE_MODE or message.chat.id in VIDEO_STATE or message.chat.id in SNAPSHOT_STATE or message.chat.id in PLAYBACK_STATE or message.chat.id in AUDIO_OUTPUT_STATE,
        content_types=['text']
    )
    async def button_handler_wrapper(message: types.Message):
        await button_handler(message)

    @dp.message_handler(lambda message: message.chat.id in VOICE_MODE, content_types=['voice'])
    async def voice_handler_wrapper(message: types.Message):
        await voice_handler(message)
# Обёртки с обработкой ошибок для видео-функций
async def stream_timelife(chat_id, bot):
    try:
        await _stream_timelife(chat_id, bot)
    except Exception as e:
        try:
            await bot.send_message(chat_id, f"❌ Ошибка в процессе live-стрима видео: {e}", reply_markup=get_sound_keyboard())
        except Exception:
            pass

async def record_video(chat_id, bot):
    try:
        await _record_video(chat_id, bot)
    except Exception as e:
        try:
            await bot.send_message(chat_id, f"❌ Ошибка в процессе записи видео: {e}", reply_markup=get_sound_keyboard())
        except Exception:
            pass
