# -*- coding: utf-8 -*-
"""
modulscrin.py — захват экрана с «анти-1КБ» фиксом и мягкой обработкой ошибок.

Что сделано:
• Больше не молчим: все критичные шаги завернуты в try/except, ошибки не валят бота.
• Отправка ошибок в ТГ: пользователю прилетит понятное сообщение, а также «техничка» с деталями.
• Фикс 1 КБ: гарантируем запись хотя бы одного кадра; проверяем, что VideoWriter открылся; при
  невозможности XVID — пробуем MJPG; при нулевой длительности — корректно сообщаем.
• Конвертация и разрезание через FFmpeg теперь проверяются на код возврата; stderr собирается.
• Без смены логики UX: те же команды/кнопки/поведение, только стабильнее.
"""

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
from typing import Dict, Any, Optional, Tuple

from modulsound import get_sound_keyboard, get_video_limit

# === ПУТИ И ПАПКИ =============================================================

# Определяем директорию запуска (скрипт или exe)
if getattr(sys, 'frozen', False):
    launch_path = sys.executable  # PyInstaller/Nuitka onefile exe
else:
    launch_path = sys.argv[0]      # Скрипт/обычный запуск

BASE_DIR = os.path.dirname(os.path.abspath(launch_path))

RAW_FOLDER = os.path.join(BASE_DIR, 'raw_videos')
OUT_FOLDER = os.path.join(BASE_DIR, 'videos')
os.makedirs(OUT_FOLDER, exist_ok=True)  # RAW создаём по факту старта

# === ПОИСК FFmpeg =============================================================

def _detect_ffmpeg_path() -> str:
    """Ищем ffmpeg рядом со скриптом / exe или в PATH. Не бросаем исключение на импорте."""
    if getattr(sys, 'frozen', False):
        script_dir = os.getcwd()
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        os.path.join(script_dir, 'ffmpeg.exe'),
        os.path.join(script_dir, 'ffmpeg-7.1', 'bin', 'ffmpeg.exe'),
        os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe') if getattr(sys, 'frozen', False) else None,
        'ffmpeg',  # на случай, если в PATH
    ]
    for c in candidates:
        if not c:
            continue
        if os.name == 'nt':
            if c.endswith('.exe'):
                if os.path.isfile(c):
                    return c
            else:
                # 'ffmpeg' в PATH — пусть попробует
                return c
        else:
            # на *nix достаточно, чтобы команда существовала в PATH
            return c
    return 'ffmpeg'

FFMPEG = _detect_ffmpeg_path()

# === ВСПОМОГАТЕЛЬНОЕ ==========================================================

def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')

async def _send_error(message: types.Message, headline: str, details: str) -> None:
    """Аккуратно сообщает об ошибке пользователю в чат + даёт техподробности."""
    # кратко
    try:
        await message.answer(f"⚠️ {headline}\nЯ отправлю подробности ниже.")
    except Exception:
        pass
    # техподробности отдельным сообщением, чтобы не терять
    details_trim = details.strip()
    if not details_trim:
        details_trim = "(нет подробностей)"
    # Оборачиваем детали в код-блок, чтобы не поломать формат
    try:
        await message.answer(f"```text\n{details_trim[:3900]}\n```", parse_mode='Markdown')
    except Exception:
        # на всякий случай без форматирования
        try:
            await message.answer(details_trim[:4000])
        except Exception:
            pass

def _format_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n/1024**3:.2f} ГБ"
    if n >= 1024**2:
        return f"{n/1024**2:.2f} МБ"
    if n >= 1024:
        return f"{n/1024:.2f} КБ"
    return f"{n} Б"

# Состояние по chat_id
SCREEN_STATE: Dict[int, Dict[str, Any]] = {}

# === ЗАПИСЬ ЭКРАНА ===========================================================

def _open_writer(path: str, size: Tuple[int, int], fps: float) -> Tuple[Optional[cv2.VideoWriter], str]:
    """Пытаемся открыть VideoWriter с XVID, иначе — MJPG. Возвращаем writer и строку кодека."""
    width, height = size
    # Попытка XVID
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    if writer is not None and writer.isOpened():
        return writer, 'XVID'
    # Фоллбек MJPG
    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    if writer is not None and writer.isOpened():
        return writer, 'MJPG'
    return None, ''

def record_screen(stop_event: threading.Event, raw_path: str, monitor: dict, fps: float, err_box: Dict[str, str]) -> None:
    """
    Захват экрана в .avi с точным таймингом.
    Все исключения ловим и шлём наружу через err_box['error'].
    """
    try:
        with mss.mss() as sct:
            width, height = monitor['width'], monitor['height']
            writer, codec = _open_writer(raw_path, (width, height), fps)
            if writer is None:
                raise RuntimeError("Не удалось открыть VideoWriter ни с XVID, ни с MJPG. "
                                   "Возможные причины: отсутствуют кодеки OpenCV / неподдерживаемое разрешение.")

            frame_interval = 1.0 / fps
            next_time = time.perf_counter()
            start_ts = datetime.now()

            # Гарантируем хотя бы один кадр ДО цикла — анти-1КБ
            img0 = sct.grab(monitor)
            frame0 = cv2.cvtColor(np.array(img0), cv2.COLOR_BGRA2BGR)
            writer.write(frame0)

            # Основной цикл
            while not stop_event.is_set():
                now = time.perf_counter()
                if now < next_time:
                    time.sleep(next_time - now)
                img = sct.grab(monitor)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
                writer.write(frame)
                next_time += frame_interval

            writer.release()
            # Для диагностики: печать в stdout (попадёт в лог процесса, если есть)
            stop_ts = datetime.now()
            actual_seconds = (stop_ts - start_ts).total_seconds()
            print(f"[{stop_ts.isoformat()}] Остановка записи ({codec}). Длительность: {actual_seconds:.2f} сек")
    except Exception as e:
        err_box['error'] = f"{type(e).__name__}: {e}"

# === КЛАВИАТУРЫ ==============================================================

def get_monitor_selection_keyboard(monitors):
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

# === КОМАНДЫ И ХЕНДЛЕРЫ ======================================================

async def cmd_screen_video(message: types.Message):
    try:
        chat_id = message.chat.id
        # Поиск доступных экранов
        with mss.mss() as sct:
            monitors = sct.monitors[1:]
        if not monitors:
            await _send_error(message, "Нет доступных мониторов", "mss не вернул ни одного монитора.")
            return
        SCREEN_STATE[chat_id] = {'state': 'choosing_monitor', 'monitors': monitors}
        keyboard = get_monitor_selection_keyboard(monitors)
        await message.answer(
            "🎥 Режим записи экрана активирован!\n"
            "Выберите экран для записи или нажмите «Отмена».",
            reply_markup=keyboard
        )
    except Exception as e:
        await _send_error(message, "Не удалось запустить режим записи экрана", f"{type(e).__name__}: {e}")

async def screen_button_handler(message: types.Message):
    chat_id = message.chat.id
    state = SCREEN_STATE.get(chat_id)
    if not state:
        return
    text = (message.text or "").strip()

    # Вспомогательная функция безопасной конвертации через FFmpeg
    async def _ffmpeg_convert(inp: str, outp: str) -> Tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                FFMPEG, '-y', '-i', inp,
                '-c:v', 'libx264', '-preset', 'fast', '-movflags', '+faststart',
                outp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            ok = proc.returncode == 0
            details = (stdout or b'').decode(errors='ignore') + "\n" + (stderr or b'').decode(errors='ignore')
            return ok, details
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    # Вспомогательная функция безопасного разрезания
    def _ffmpeg_split_sync(mp4: str, limit: int) -> Tuple[bool, str, list]:
        try:
            cap = cv2.VideoCapture(mp4)
            fps_val = cap.get(cv2.CAP_PROP_FPS) or 1
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            duration = frames / fps_val if fps_val > 0 else 0

            size = os.path.getsize(mp4)
            num_parts = max(1, math.ceil(size / limit))
            if num_parts <= 1:
                return True, "Разбиение не требуется", [mp4]

            segment_time = max(1, math.ceil(duration / num_parts))
            base, ext = os.path.splitext(mp4)
            pattern = f'{base}_part%03d{ext}'

            proc = subprocess.run(
                [FFMPEG, '-y', '-i', mp4, '-c', 'copy', '-f', 'segment',
                 '-segment_time', str(segment_time), '-reset_timestamps', '1', pattern],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if proc.returncode != 0:
                return False, f"FFmpeg segment error:\n{proc.stderr}", []
            parts = sorted(glob.glob(f'{base}_part*{ext}'))
            if not parts:
                return False, "FFmpeg не создал части (не найдено файлов *_partXXX.mp4).", []
            return True, "", parts
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", []

    try:
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
            width, height = monitor['width'], monitor['height']
            limit_str = _format_bytes(limit)
            await message.answer(
                f"🎥 Выбран экран {idx} ({width}x{height}).\n"
                f"Текущий лимит видео для отправки: {limit_str}\n"
                "Нажмите «Старт» для начала или «Отмена» для выхода.",
                reply_markup=get_start_cancel_keyboard()
            )
            return

        # Запуск захвата
        if state['state'] == 'ready' and text == 'Старт':
            try:
                os.makedirs(RAW_FOLDER, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                raw_path = os.path.join(RAW_FOLDER, f'{chat_id}_{ts}.avi')
                out_path = os.path.join(OUT_FOLDER, f'{chat_id}_{ts}.mp4')
                stop_event = threading.Event()
                err_box: Dict[str, str] = {}

                thread = threading.Thread(
                    target=record_screen,
                    args=(stop_event, raw_path, state['monitor'], 15.0, err_box),
                    daemon=True
                )
                thread.start()
                state.update({
                    'state': 'recording',
                    'thread': thread,
                    'stop_event': stop_event,
                    'raw_path': raw_path,
                    'out_path': out_path,
                    'err_box': err_box,
                })
                await message.answer(
                    '🔴 Запись экрана запущена!\n'
                    'Нажмите «Стоп» для окончания или «Отмена» для отмены.',
                    reply_markup=get_stop_cancel_keyboard()
                )
            except Exception as e:
                await _send_error(message, "Не удалось запустить запись экрана", f"{type(e).__name__}: {e}")
            return

        # Остановка, конвертация и отправка
        if state['state'] == 'recording' and text == 'Стоп':
            state['stop_event'].set()
            state['thread'].join()

            # Проверка ошибок из потока захвата
            err_box = state.get('err_box') or {}
            if 'error' in err_box:
                # удаляем «пустой» файл, если он создался
                try:
                    if os.path.isfile(state['raw_path']):
                        size = os.path.getsize(state['raw_path'])
                        if size <= 2048:
                            os.remove(state['raw_path'])
                except Exception:
                    pass
                await _send_error(message, "Ошибка при записи экрана", err_box['error'])
                SCREEN_STATE.pop(chat_id, None)
                return

            await message.answer('⌛ Конвертирую видео (FFmpeg)...')
            ok, details = await _ffmpeg_convert(state['raw_path'], state['out_path'])

            # Уберём RAW, даже если конверт удалось/не удалось — но осторожно
            try:
                if os.path.isfile(state['raw_path']):
                    os.remove(state['raw_path'])
                raw_folder = os.path.dirname(state['raw_path'])
                if os.path.isdir(raw_folder) and not os.listdir(raw_folder):
                    os.rmdir(raw_folder)
            except Exception:
                pass

            if not ok:
                await _send_error(message, "FFmpeg не смог сконвертировать видео", details)
                SCREEN_STATE.pop(chat_id, None)
                return

            mp4 = state['out_path']
            try:
                size = os.path.getsize(mp4)
            except Exception as e:
                await _send_error(message, "Не удалось определить размер выходного файла", f"{type(e).__name__}: {e}")
                SCREEN_STATE.pop(chat_id, None)
                return

            # «Анти‑1КБ»: проверяем, что есть хотя бы ~ несколько килобайт
            if size <= 4096:
                await _send_error(
                    message,
                    "Похоже, видео пустое или повреждено (очень маленький размер)",
                    f"Размер файла {mp4} = {_format_bytes(size)}. Возможно, не удалось захватить ни одного кадра."
                )
                SCREEN_STATE.pop(chat_id, None)
                return

            limit = get_video_limit(message.bot)
            if size <= limit:
                try:
                    with open(mp4, 'rb') as f:
                        await message.bot.send_video(chat_id, f)
                    await message.answer(f'✅ Видео отправлено ({_format_bytes(size)}).', reply_markup=get_sound_keyboard())
                except Exception as e:
                    await _send_error(message, "Не удалось отправить видео", f"{type(e).__name__}: {e}")
            else:
                # Разбиваем и шлём частями
                ok_split, split_details, parts = _ffmpeg_split_sync(mp4, limit)
                if not ok_split:
                    await _send_error(message, "Не удалось разрезать видео под лимит", split_details)
                    SCREEN_STATE.pop(chat_id, None)
                    return
                sent = 0
                for part in parts:
                    try:
                        with open(part, 'rb') as pf:
                            await message.bot.send_video(chat_id, pf)
                        sent += 1
                    except Exception as e:
                        await _send_error(message, f"Не удалось отправить часть {os.path.basename(part)}",
                                          f"{type(e).__name__}: {e}")
                    finally:
                        try:
                            os.remove(part)
                        except Exception:
                            pass
                await message.answer(f'✅ Видео разделено на {sent} частей и отправлено.', reply_markup=get_sound_keyboard())

            SCREEN_STATE.pop(chat_id, None)
            return

        # Отмена записи
        if text == 'Отмена':
            try:
                if state.get('state') == 'recording':
                    state['stop_event'].set()
                    state['thread'].join()
                    try:
                        if os.path.isfile(state['raw_path']):
                            os.remove(state['raw_path'])
                    except Exception:
                        pass
                    rf = os.path.dirname(state['raw_path'])
                    try:
                        if os.path.isdir(rf) and not os.listdir(rf):
                            os.rmdir(rf)
                    except Exception:
                        pass
            finally:
                SCREEN_STATE.pop(chat_id, None)
            await message.answer('❌ Запись экрана отменена.', reply_markup=get_sound_keyboard())
            return

        # Любой другой ввод
        if state['state'] == 'ready':
            await message.answer('Нажмите «Старт» или «Отмена».', reply_markup=get_start_cancel_keyboard())
        elif state['state'] == 'recording':
            await message.answer('Нажмите «Стоп» или «Отмена».', reply_markup=get_stop_cancel_keyboard())

    except Exception as e:
        # Глобальный страховочный пояс
        try:
            await _send_error(message, "Непредвиденная ошибка в обработчике экрана", f"{type(e).__name__}: {e}")
        finally:
            # Пытаемся корректно очистить состояние
            try:
                if state and state.get('state') == 'recording':
                    state['stop_event'].set()
                    state['thread'].join(timeout=2.0)
            except Exception:
                pass
            SCREEN_STATE.pop(chat_id, None)

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
