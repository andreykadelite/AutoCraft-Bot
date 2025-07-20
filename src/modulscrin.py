# -*- coding: utf-8 -*-
from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import subprocess
import asyncio
import os
import sys
import threading
import math
import glob
import mss
import numpy as np
import cv2
import time
from datetime import datetime
from modulsound import get_sound_keyboard, get_video_limit

# Определяем директорию запуска (скрипт или exe)
if getattr(sys, 'frozen', False):
    launch_path = sys.executable  # PyInstaller exe
else:
    launch_path = sys.argv[0]      # Скрипт или Nuitka exe
BASE_DIR = os.path.dirname(os.path.abspath(launch_path))
# os.chdir(BASE_DIR)  # при необходимости менять рабочую директорию
RAW_FOLDER = os.path.join(BASE_DIR, 'raw_videos')
OUT_FOLDER = os.path.join(BASE_DIR, 'videos')
# os.makedirs(RAW_FOLDER, exist_ok=True)
os.makedirs(OUT_FOLDER, exist_ok=True)

# Определяем, где искать ffmpeg.exe
if getattr(sys, 'frozen', False):
    script_dir = os.getcwd()
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
# Добавлена поддержка папки ffmpeg-7.1/bin рядом со скриптом
ffmpeg_candidates = [
    os.path.join(script_dir, 'ffmpeg.exe'),
    os.path.join(script_dir, 'ffmpeg-7.1', 'bin', 'ffmpeg.exe'),
    os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe'),
]
for candidate in ffmpeg_candidates:
    if os.path.isfile(candidate):
        FFMPEG = candidate
        break
else:
    raise FileNotFoundError(f"ffmpeg.exe не найден ни в {ffmpeg_candidates}")

# Генерация клавиатур
# Генерация клавиатур
def get_monitor_selection_keyboard(monitors):
    # Убрали one_time_keyboard=True, чтобы клавиатура не скрывалась после нажатия
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    for idx, mon in enumerate(monitors, 1):
        width, height = mon['width'], mon['height']
        keyboard.add(KeyboardButton(f"Экран {idx}: {width}x{height}"))
    keyboard.add(KeyboardButton('Отмена'))
    return keyboard

def get_start_cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Старт'))
    keyboard.add(KeyboardButton('Отмена'))
    return keyboard

def get_stop_cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Стоп'))
    keyboard.add(KeyboardButton('Отмена'))
    return keyboard

# Состояние по chat_id
SCREEN_STATE = {}

# Функция захвата экрана в raw .avi с точным таймингом
def record_screen(stop_event: threading.Event, raw_path: str, monitor: dict, fps: float = 15.0):
    with mss.mss() as sct:
        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        width, height = monitor['width'], monitor['height']
        writer = cv2.VideoWriter(raw_path, fourcc, fps, (width, height))
        frame_interval = 1.0 / fps
        next_time = time.perf_counter()
        start_ts = datetime.now()
        print(f"[{start_ts.isoformat()}] Начало записи: {raw_path} с {fps} FPS")
        while not stop_event.is_set():
            now = time.perf_counter()
            if now < next_time:
                time.sleep(next_time - now)
            img = sct.grab(monitor)
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
            writer.write(frame)
            next_time += frame_interval
        writer.release()
        stop_ts = datetime.now()
        actual_seconds = (stop_ts - start_ts).total_seconds()
        print(f"[{stop_ts.isoformat()}] Остановка записи. Реальная длительность: {actual_seconds:.2f} секунд")

# Команда запуска режима записи экрана
async def cmd_screen_video(message: types.Message):
    chat_id = message.chat.id
    # Поиск доступных экранов
    with mss.mss() as sct:
        monitors = sct.monitors[1:]
    SCREEN_STATE[chat_id] = {'state': 'choosing_monitor', 'monitors': monitors}
    keyboard = get_monitor_selection_keyboard(monitors)
    await message.answer(
        "🎥 Режим записи экрана активирован!\n"
        "Выберите экран для записи или нажмите «Отмена».",
        reply_markup=keyboard
    )

# Обработка кнопок выбора экрана и Старт/Стоп/Отмена
async def screen_button_handler(message: types.Message):
    chat_id = message.chat.id
    state = SCREEN_STATE.get(chat_id)
    if not state:
        return
    text = message.text

    # Выбор экрана
    if state['state'] == 'choosing_monitor':
        if text == 'Отмена':
            SCREEN_STATE.pop(chat_id, None)
            await message.answer('❌ Запись экрана отменена.', reply_markup=get_sound_keyboard())
            return
        try:
            idx = int(text.split()[1].rstrip(':'))
            monitor = state['monitors'][idx-1]
        except Exception:
            await message.answer(
                'Пожалуйста, выберите корректный экран или Отмена.',
                reply_markup=get_monitor_selection_keyboard(state['monitors'])
            )
            return
        state['state'] = 'ready'
        state['monitor'] = monitor
        limit = get_video_limit(message.bot)
        if limit >= 1024**3:
            limit_str = f"{limit/1024**3:.2f} ГБ"
        else:
            limit_str = f"{limit/1024**2:.2f} МБ"
        width, height = monitor['width'], monitor['height']
        await message.answer(
            f"🎥 Выбран экран {idx} ({width}x{height}).\n"
            f"Текущий лимит видео для отправки: {limit_str}\n"
            "Нажмите «Старт» для начала или «Отмена» для выхода.",
            reply_markup=get_start_cancel_keyboard()
        )
        return

    # Запуск захвата
    if state['state'] == 'ready' and text == 'Старт':
        # Создаем папку raw_videos при начале записи
        os.makedirs(RAW_FOLDER, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        raw_path = os.path.join(RAW_FOLDER, f'{chat_id}_{ts}.avi')
        out_path = os.path.join(OUT_FOLDER, f'{chat_id}_{ts}.mp4')
        stop_event = threading.Event()
        thread = threading.Thread(
            target=record_screen,
            args=(stop_event, raw_path, state['monitor'], 15.0),
            daemon=True
        )
        thread.start()
        state.update({
            'state': 'recording',
            'thread': thread,
            'stop_event': stop_event,
            'raw_path': raw_path,
            'out_path': out_path
        })
        await message.answer(
            '🔴 Запись экрана запущена!\n'
            'Нажмите «Стоп» для окончания или «Отмена» для отмены.',
            reply_markup=get_stop_cancel_keyboard()
        )
        return

    # Остановка, конвертация и отправка
    if state['state'] == 'recording' and text == 'Стоп':
        state['stop_event'].set()
        state['thread'].join()
        await message.answer('⌛ Конвертирую и отправляю видео...')
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, '-y', '-i', state['raw_path'],
            '-c:v', 'libx264', '-preset', 'fast', '-movflags', '+faststart',
            state['out_path'],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        try:
            os.remove(state['raw_path'])
        except OSError:
            pass
        raw_folder = os.path.dirname(state['raw_path'])
        if os.path.isdir(raw_folder) and not os.listdir(raw_folder):
            try:
                os.rmdir(raw_folder)
            except OSError:
                pass
        mp4 = state['out_path']
        size = os.path.getsize(mp4)
        limit = get_video_limit(message.bot)
        if size <= limit:
            with open(mp4, 'rb') as f:
                await message.bot.send_video(chat_id, f)
            await message.answer(f'✅ Видео отправлено ({size/1024**2:.2f} МБ).', reply_markup=get_sound_keyboard())
        else:
            num_parts = math.ceil(size / limit)
            cap = cv2.VideoCapture(mp4)
            fps_val = cap.get(cv2.CAP_PROP_FPS) or 1
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            duration = frames / fps_val if fps_val > 0 else 0
            segment_time = max(1, math.ceil(duration / num_parts))
            base, ext = os.path.splitext(mp4)
            pattern = f'{base}_part%03d{ext}'
            subprocess.run([
                FFMPEG, '-y', '-i', mp4,
                '-c', 'copy', '-f', 'segment',
                '-segment_time', str(segment_time),
                '-reset_timestamps', '1',
                pattern
            ], check=True)
            parts = sorted(glob.glob(f'{base}_part*{ext}'))
            for part in parts:
                with open(part, 'rb') as part_file:
                    await message.bot.send_video(chat_id, part_file)
                os.remove(part)
            await message.answer(f'✅ Видео разделено на {len(parts)} частей и отправлено.', reply_markup=get_sound_keyboard())
        SCREEN_STATE.pop(chat_id, None)
        return

    # Отмена записи
    if text == 'Отмена':
        if state.get('state') == 'recording':
            state['stop_event'].set()
            state['thread'].join()
            try:
                os.remove(state['raw_path'])
            except OSError:
                pass
            rf = os.path.dirname(state['raw_path'])
            if os.path.isdir(rf) and not os.listdir(rf):
                try:
                    os.rmdir(rf)
                except OSError:
                    pass
        SCREEN_STATE.pop(chat_id, None)
        await message.answer('❌ Запись экрана отменена.', reply_markup=get_sound_keyboard())
        return

    # Любой другой ввод
    if state['state'] == 'ready':
        await message.answer('Нажмите «Старт» или «Отмена».', reply_markup=get_start_cancel_keyboard())
    elif state['state'] == 'recording':
        await message.answer('Нажмите «Стоп» или «Отмена».', reply_markup=get_stop_cancel_keyboard())

# Регистрация хендлеров
def register_handlers(dp: Dispatcher):
    dp.register_message_handler(
        cmd_screen_video,
        lambda msg: msg.text and msg.text.strip().lower() == 'видео с экрана'
    )
    dp.register_message_handler(
        screen_button_handler,
        lambda msg: msg.chat.id in SCREEN_STATE
    )
