import asyncio
import glob
import logging
import math
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import winsound
from datetime import datetime

import cv2
import pyttsx3
import sounddevice as sd
import soundfile as sf
from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from gtts import gTTS
from keymenu import get_additional_keyboard, get_main_keyboard

# --- Регистрация кнопки в динамическом меню «Дополнительно» ---
# Делается мягко: если реестр не найден, модуль не падает.
try:
    from additional_registry import register_additional  # type: ignore

    register_additional(
        key="special_functions",
        title="Особые функции",
        trigger_text="Особые функции",
        order=60,
        description="Модуль звука/видео/синтеза речи и прочих спец-возможностей",
    )
except Exception:
    # Важно: не роняем модуль, если additional_registry отсутствует или ещё не инициализирован.
    pass

# Список предупреждений инициализации модуля (например, если не найден ffmpeg)
INIT_ERRORS = []
# --- Дисклеймер безопасности ---
# Показывается пользователю при входе в модуль «Особые функции» (один раз на чат за сессию бота).
DISCLAIMER_TEXT = """Внимание.
Модуль «Особые функции» предоставляет доступ к мультимедиа и системным возможностям (звук, микрофон, камера, запись экрана, синтез речи).
• Используйте функции только на своём устройстве или при наличии явного разрешения владельца.
• Не применяйте модуль для скрытой записи/наблюдения, вмешательства в работу системы или причинения вреда.
• Автор/разработчик не несёт ответственности за последствия использования. Вы действуете законно и на свой риск."""
DISCLAIMER_SHOWN = set()


# Определяем базовую папку: при сборке в exe (Nuitka/pyinstaller) и при запуске из .py
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

FFMPEG_PATH = None
for p in ffmpeg_candidates:
    if os.path.isfile(p):
        FFMPEG_PATH = p
        break

if FFMPEG_PATH is None:
    # Не роняем модуль, а просто записываем предупреждение.
    INIT_ERRORS.append(f"ffmpeg.exe не найден ни в {ffmpeg_candidates}")


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

    # Если формат не WAV — по возможности сконвертируем во временный WAV с помощью ffmpeg.
    if ext != ".wav":
        if FFMPEG_PATH:
            try:
                # Конвертим во временный WAV рядом с исходником
                temp_wav = path + ".__tmp_play__.wav"
                subprocess.run(
                    [FFMPEG_PATH, "-y", "-i", path, temp_wav],
                    check=True,
                    capture_output=True,
                    text=True
                )
                src_for_play = temp_wav
            except subprocess.CalledProcessError as e:
                # Если конвертация не удалась — пробуем отдать как есть, но предупреждаем логом
                logging.exception("modulsound: ошибка ffmpeg при конвертации для воспроизведения")
                src_for_play = path
                temp_wav = None
            except Exception as e:
                logging.exception("modulsound: общая ошибка при подготовке аудио к воспроизведению")
                src_for_play = path
                temp_wav = None
        else:
            # ffmpeg отсутствует — просто попытаемся воспроизвести как есть.
            logging.warning("modulsound: FFMPEG_PATH не задан, воспроизвожу файл без конвертации")

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
    """Определяет лимит размера видео в байтах.

    Если бот подключён к локальному Telegram API серверу (base URL отличается
    от официального https://api.telegram.org), возвращаем лимит 2 ГБ.
    В остальных случаях — стандартный MAX_VIDEO_SIZE.
    """
    try:
        server = getattr(bot, 'server', None)
        if not server:
            return MAX_VIDEO_SIZE

        base_url = getattr(server, 'base', None) or getattr(server, '_base', None)

        # На всякий случай проверяем тип: нас интересует только строковый URL.
        if isinstance(base_url, str):
            # Если base_url не начинается с стандартного API-домена — считаем, что это локальный сервер.
            if not base_url.startswith('https://api.telegram.org'):
                return 2 * 1024 * 1024 * 1024
    except Exception as e:
        # Логируем, но не валим модуль.
        logging.exception("get_video_limit: ошибка при определении типа API-сервера")

    return MAX_VIDEO_SIZE


# Путь к edge-tts (Microsoft TTS CLI), если установлен
EDGE_TTS_PATH = shutil.which("edge-tts")


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


# Опции синтеза речи и состояние TTS
ENGINE_OPTIONS = []
VOICE_OPTIONS = {}
PYTTSX3_VOICE_MAP = {}
EDGE_TTS_VOICE_MAP = {}
EDGE_TTS_MODULE = None
TTS_INIT_DONE = False
TTS_IMPORT_ERRORS = []


def init_tts_engines(force: bool = False):
    """Инициализация доступных движков TTS.
    Работает как в .py-режиме, так и внутри скомпилированного EXE.
    """
    global ENGINE_OPTIONS, VOICE_OPTIONS, PYTTSX3_VOICE_MAP, EDGE_TTS_VOICE_MAP
    global EDGE_TTS_MODULE, TTS_INIT_DONE, TTS_IMPORT_ERRORS

    if TTS_INIT_DONE and not force:
        return

    ENGINE_OPTIONS = []
    VOICE_OPTIONS = {}
    PYTTSX3_VOICE_MAP = {}
    EDGE_TTS_VOICE_MAP = {}
    EDGE_TTS_MODULE = None
    TTS_IMPORT_ERRORS = []

    # --- Google TTS (gTTS) ---
    try:
        if "gTTS" in globals() and gTTS is not None:
            ENGINE_OPTIONS.append("Google")
            VOICE_OPTIONS["Google"] = ["Стандартный голос (ru-RU)"]
        else:
            raise RuntimeError("модуль gTTS недоступен")
    except Exception as e:
        TTS_IMPORT_ERRORS.append(f"Google TTS недоступен: {type(e).__name__}: {e}")

    # --- pyttsx3 (локальные системные голоса) ---
    try:
        if "pyttsx3" not in globals() or pyttsx3 is None:
            raise RuntimeError("модуль pyttsx3 не импортирован")
        tts_engine_tmp = pyttsx3.init()
        voices = tts_engine_tmp.getProperty("voices") or []
        labels = []
        for idx, v in enumerate(voices, start=1):
            lang = None
            try:
                langs = getattr(v, "languages", None)
                if langs:
                    raw = langs[0]
                    if isinstance(raw, bytes):
                        lang = raw.decode(errors="ignore")
                    else:
                        lang = str(raw)
            except Exception:
                lang = None
            if lang:
                label = f"{idx}: {v.name} ({lang})"
            else:
                label = f"{idx}: {v.name}"
            labels.append(label)
            PYTTSX3_VOICE_MAP[label] = v.id
        if labels:
            ENGINE_OPTIONS.append("pyx3")
            VOICE_OPTIONS["pyx3"] = labels
        else:
            TTS_IMPORT_ERRORS.append("pyttsx3 не нашёл ни одного доступного голоса.")
    except Exception as e:
        TTS_IMPORT_ERRORS.append(f"pyttsx3 недоступен: {type(e).__name__}: {e}")

    # --- Edge TTS (Microsoft, через Python-модуль edge_tts) ---
    try:
        import edge_tts as _edge_tts  # type: ignore
        EDGE_TTS_MODULE = _edge_tts

        # В декабре 2025 Microsoft внесла изменения в Edge Read Aloud API (лимиты/аутентификация),
        # из-за чего старые версии edge-tts часто падают с NoAudioReceived.
        # Поэтому мягко предупреждаем, если версия пакета слишком старая.
        try:
            from importlib import metadata as _metadata  # py3.8+
            _edge_ver = _metadata.version("edge-tts")
        except Exception:
            _edge_ver = getattr(_edge_tts, "__version__", "") or ""

        def _ver_tuple(v: str):
            parts = [int(x) for x in re.findall(r"\d+", v)[:3]]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)

        if _edge_ver:
            try:
                if _ver_tuple(_edge_ver) < (7, 2, 4):
                    TTS_IMPORT_ERRORS.append(
                        f"edge-tts версия {_edge_ver} может не работать после изменений Microsoft (декабрь 2025). "
                        f"Рекомендуется обновить минимум до 7.2.4+."
                    )
            except Exception:
                pass

        edge_voices = {
            "ru-RU-SvetlanaNeural (женский)": "ru-RU-SvetlanaNeural",
            "ru-RU-DmitryNeural (мужской)": "ru-RU-DmitryNeural",
            "ru-RU-DariyaNeural (женский)": "ru-RU-DariyaNeural",
        }
        EDGE_TTS_VOICE_MAP.update(edge_voices)
        ENGINE_OPTIONS.append("Edge TTS")
        VOICE_OPTIONS["Edge TTS"] = list(edge_voices.keys())
    except Exception as e:
        TTS_IMPORT_ERRORS.append(f"edge-tts недоступен: {type(e).__name__}: {e}")
        EDGE_TTS_MODULE = None

    # Если вообще ничего не удалось инициализировать — оставим заглушку, чтобы не падать
    if not ENGINE_OPTIONS:
        ENGINE_OPTIONS.append("Google")
        VOICE_OPTIONS["Google"] = ["Стандартный голос (ru-RU)"]
        TTS_IMPORT_ERRORS.append("Не удалось инициализировать ни один TTS-движок. Используется заглушка Google.")

    TTS_INIT_DONE = True


def get_engine_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for opt in ENGINE_OPTIONS:
        kb.add(KeyboardButton(opt))
    kb.add(KeyboardButton("Отмена"))
    return kb


def get_voice_keyboard(engine: str):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    voices = VOICE_OPTIONS.get(engine, [])
    row = []
    for v in voices:
        row.append(KeyboardButton(v))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.add(KeyboardButton("Отмена"))
    return kb


def get_tts_setup_keyboard(has_available_engines: bool):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Установить TTS"))
    if has_available_engines:
        kb.add(KeyboardButton("Продолжить без установки"))
    kb.add(KeyboardButton("Отмена"))
    return kb


def _split_text_utf8(text: str, max_bytes: int) -> list:
    """Режем текст на куски по лимиту UTF-8 байт.
    Пытаемся резать по предложениям/словам, чтобы речь звучала нормально.
    """
    # нормализуем переносы строк, но сохраняем паузы
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\t ]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        return []

    # сначала режем по предложениям
    # NB: это эвристика, но для русского обычно хватает.
    sentences = re.split(r"(?<=[\.!\?…])\s+", cleaned)
    chunks = []
    buf = ""

    def _fits(s: str) -> bool:
        return len(s.encode("utf-8")) <= max_bytes

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if not buf:
            if _fits(s):
                buf = s
            else:
                # очень длинное "предложение" режем по словам
                words = s.split()
                wbuf = ""
                for w in words:
                    cand = (wbuf + " " + w).strip()
                    if _fits(cand):
                        wbuf = cand
                    else:
                        if wbuf:
                            chunks.append(wbuf)
                        wbuf = w
                if wbuf:
                    chunks.append(wbuf)
                buf = ""
            continue

        cand = (buf + " " + s).strip()
        if _fits(cand):
            buf = cand
        else:
            chunks.append(buf)
            if _fits(s):
                buf = s
            else:
                # снова режем по словам
                words = s.split()
                wbuf = ""
                for w in words:
                    cand2 = (wbuf + " " + w).strip()
                    if _fits(cand2):
                        wbuf = cand2
                    else:
                        if wbuf:
                            chunks.append(wbuf)
                        wbuf = w
                if wbuf:
                    chunks.append(wbuf)
                buf = ""

    if buf:
        chunks.append(buf)

    # финальная страховка: убираем пустые
    return [c for c in chunks if c and c.strip()]


def _ffmpeg_concat_mp3(parts: list, out_path: str) -> bool:
    """Склеить mp3-части через ffmpeg concat demuxer. Возвращает True при успехе."""
    if not parts:
        return False
    if len(parts) == 1:
        try:
            if os.path.abspath(parts[0]) != os.path.abspath(out_path):
                shutil.copyfile(parts[0], out_path)
            return True
        except Exception:
            return False

    if not FFMPEG_PATH or not os.path.isfile(FFMPEG_PATH):
        return False

    list_path = out_path + ".__concat__.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in parts:
                p_abs = os.path.abspath(p)
                # экранируем одинарные кавычки для ffmpeg concat list
                p_abs = p_abs.replace("'", "\\'")
                f.write(f"file '{p_abs}'\n")

        cmd = [
            FFMPEG_PATH,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 1024
    finally:
        try:
            if os.path.exists(list_path):
                os.remove(list_path)
        except Exception:
            pass


async def _edge_tts_save(communicate, out_path: str):
    """Сохранить результат edge-tts. Предпочитаем communicate.save(), иначе stream()."""
    if hasattr(communicate, "save"):
        await communicate.save(out_path)
        return

    # fallback
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                f.write(chunk.get("data", b""))


def _edge_tts_create_communicate(text: str, voice_id: str):
    """Создать edge_tts.Communicate с совместимостью по сигнатурам."""
    # В разных версиях edge-tts сигнатура могла отличаться.
    try:
        return EDGE_TTS_MODULE.Communicate(text=text, voice=voice_id)
    except TypeError:
        try:
            return EDGE_TTS_MODULE.Communicate(text, voice=voice_id)
        except TypeError:
            return EDGE_TTS_MODULE.Communicate(text, voice_id)


async def synthesize_edge_tts(text: str, voice_id: str, file_path: str):
    """Синтез через edge_tts в указанный файл.

    После изменений Microsoft (декабрь 2025) появились более жёсткие ограничения:
    - лимит длительности (около 10 минут) на один запрос,
    - более строгая нарезка/размер чанков запроса.
    Из-за этого даже корректные параметры иногда приводят к NoAudioReceived.
    Здесь добавлены: безопасная нарезка текста, повторы, склейка частей.
    """
    if EDGE_TTS_MODULE is None:
        raise RuntimeError("edge-tts не инициализирован")

    text = (text or "").strip()
    if not text:
        raise ValueError("Пустой текст для синтеза")

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

    # С запасом ниже 4096 байт (и учитываем UTF-8 для кириллицы)
    # Слишком большие куски часто приводят к NoAudioReceived после декабрьских изменений.
    primary_limit = 2800
    fallback_limit = 1600

    def _is_noaudio(exc: Exception) -> bool:
        return exc.__class__.__name__ == "NoAudioReceived" or "NoAudioReceived" in repr(exc)

    async def _synth_one_chunk(chunk_text: str, out_path: str, try_voice: str, attempts: int = 3):
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                communicate = _edge_tts_create_communicate(chunk_text, try_voice)
                await _edge_tts_save(communicate, out_path)
                if os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
                    return
                # если файл почти пустой — считаем, что аудио не пришло
                raise RuntimeError("edge-tts вернул пустой/слишком маленький аудиофайл")
            except Exception as e:
                last_exc = e
                # короткая пауза с ростом
                await asyncio.sleep(0.25 * attempt)
        raise last_exc or RuntimeError("edge-tts synthesis failed")

    # режем текст на части (даже если получится 1 часть)
    chunks = _split_text_utf8(text, primary_limit)

    # если почему-то не получилось разрезать — пробуем как есть
    if not chunks:
        chunks = [text]

    parts = []
    try:
        # При ошибке иногда помогает смена голоса, поэтому держим маленький fallback-список
        voice_fallbacks = [voice_id]
        for v in ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-DariyaNeural"):
            if v != voice_id:
                voice_fallbacks.append(v)

        for i, chunk in enumerate(chunks):
            part_path = file_path + f".__part{i:03d}.mp3"
            ok = False
            last_error = None
            for v in voice_fallbacks[:2]:  # не перебираем бесконечно, только 1 fallback
                try:
                    await _synth_one_chunk(chunk, part_path, v, attempts=3)
                    ok = True
                    break
                except Exception as e:
                    last_error = e

            if not ok and last_error and _is_noaudio(last_error):
                # 2-я попытка: режем сильнее и синтезируем уже "подчасти"
                subchunks = _split_text_utf8(chunk, fallback_limit) or [chunk]
                # если первая часть уже что-то создала, чистим
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                # синтезим во временные mp3 и потом сольём в part_path
                subparts = []
                for j, sub in enumerate(subchunks):
                    sub_path = file_path + f".__part{i:03d}_sub{j:03d}.mp3"
                    await _synth_one_chunk(sub, sub_path, voice_id, attempts=3)
                    subparts.append(sub_path)
                # склейка подчастей
                if not _ffmpeg_concat_mp3(subparts, part_path):
                    # fallback: простое склеивание mp3-фреймов (работает не всегда, но лучше чем ничего)
                    with open(part_path, "wb") as out_f:
                        for sp in subparts:
                            with open(sp, "rb") as in_f:
                                out_f.write(in_f.read())
                # чистим subparts
                for sp in subparts:
                    try:
                        if os.path.exists(sp):
                            os.remove(sp)
                    except Exception:
                        pass

                if not os.path.isfile(part_path) or os.path.getsize(part_path) <= 1024:
                    raise last_error

            if not os.path.isfile(part_path) or os.path.getsize(part_path) <= 1024:
                raise RuntimeError("edge-tts: аудио часть не создана")

            parts.append(part_path)

        # Если частей несколько, склеиваем в итоговый файл
        if len(parts) == 1:
            shutil.move(parts[0], file_path)
            parts = []
            return

        if _ffmpeg_concat_mp3(parts, file_path):
            return

        # fallback без ffmpeg
        with open(file_path, "wb") as out_f:
            for p in parts:
                with open(p, "rb") as in_f:
                    out_f.write(in_f.read())
    finally:
        # уборка частей
        for p in parts:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def attempt_tts_installation(message: types.Message):
    """Пытается установить недостающие TTS-библиотеки через pip.

    В EXE-режиме установка недоступна — в этом случае показываем
    понятное сообщение и просто переинициализируем движки.
    """
    # Если мы запущены как EXE (Nuitka/pyinstaller), ставить пакеты внутрь файла нельзя.
    if getattr(sys, "frozen", False):
        await message.answer(
            "Я запущен как EXE-файл.\n"
            "Установить новые пакеты внутрь уже собранного файла нельзя.\n"
            "Будут доступны только те движки, которые были встроены при сборке.",
            reply_markup=get_sound_keyboard()
        )
        init_tts_engines(force=True)
        return False

    await message.answer(
        "Пробую установить недостающие компоненты синтеза речи (pyttsx3, pywin32, edge-tts)...\n"
        "Это может занять некоторое время.",
        reply_markup=get_sound_keyboard()
    )

    commands = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
        ["pip", "install", "--upgrade", "--no-cache-dir", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
        ["python", "-m", "pip", "install", "--upgrade", "--no-cache-dir", "pyttsx3", "pywin32", "edge-tts>=7.2.4"],
    ]
    success = False
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode == 0:
                success = True
                break
        except Exception as e:
            logging.warning("modulsound: ошибка при выполнении команды установки TTS %s: %s", cmd, e)

    if success:
        await message.answer("Установка TTS-библиотек завершена. Перепроверяю доступные движки...")
    else:
        await message.answer(
            "Не удалось автоматически установить дополнительные TTS-движки.\n"
            "Будут доступны только уже встроенные.",
            reply_markup=get_sound_keyboard()
        )

    init_tts_engines(force=True)
    return success


# Клавиатуры для основных функций
def get_sound_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    # Row 1: TTS
    kb.row(KeyboardButton("Синтез речи"), KeyboardButton("Отправить голос"))
    # Row 2: Microphone
    kb.row(KeyboardButton("Звук с микрофона"))
    # Row 3: Camera functions
    kb.row(KeyboardButton("Снимок с камеры"), KeyboardButton("Видео с камеры"), KeyboardButton("Видео с экрана"))
    # Row 4: Cleanup
    kb.row(KeyboardButton("Очистить sound"), KeyboardButton("Очистить videos"))
    # Row 5: Messages and chat
    kb.row(KeyboardButton("Отправить сообщение на компьютер"), KeyboardButton("Создать интерактивный чат"))
    # Bottom: Return
    kb.add(KeyboardButton("Вернуться"))
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
                        f"Ошибка при разделении видео на части:\n```{e.stderr or e.stdout or str(e)}```",
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
        # Инициализируем список доступных движков
        init_tts_engines()
        has_any = len(ENGINE_OPTIONS) > 0
        has_errors = bool(TTS_IMPORT_ERRORS)

        if has_errors:
            # Что-то недоступно — предлагаем установить TTS или продолжить с тем, что есть
            TTS_STATE[chat_id] = {"state": "setup"}
            errors_text = "\n".join(f"- {err}" for err in TTS_IMPORT_ERRORS)
            msg = (
                "Некоторые компоненты синтеза речи сейчас недоступны:\n"
                f"{errors_text}\n\n"
                "Попробовать установить недостающие компоненты автоматически?"
            )
            await message.answer(msg, reply_markup=get_tts_setup_keyboard(has_available_engines=has_any))
        else:
            # Всё ок — сразу переходим к выбору движка
            TTS_STATE[chat_id] = {"state": "engine"}
            await message.answer("Выберите голосовой движок:", reply_markup=get_engine_keyboard())
        return

    # Режим настройки TTS (установка недостающих компонентов)
    if chat_id in TTS_STATE and TTS_STATE[chat_id].get("state") == "setup":
        if text == "Установить TTS":
            await attempt_tts_installation(message)
            # После попытки установки переинициализируем движки
            init_tts_engines(force=True)
            TTS_STATE[chat_id]["state"] = "engine"
            await message.answer("Выберите голосовой движок:", reply_markup=get_engine_keyboard())
        elif text == "Продолжить без установки":
            TTS_STATE[chat_id]["state"] = "engine"
            await message.answer("Выберите голосовой движок:", reply_markup=get_engine_keyboard())
        elif text == "Отмена":
            TTS_STATE.pop(chat_id, None)
            await message.answer("Синтез речи отменён.", reply_markup=get_sound_keyboard())
        else:
            await message.answer(
                "Пожалуйста, выберите действие: установить TTS или отменить.",
                reply_markup=get_tts_setup_keyboard(has_available_engines=len(ENGINE_OPTIONS) > 0),
            )
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

            # Генерация речи в зависимости от выбранного движка
            if engine_choice == "Google":
                if "gTTS" not in globals() or gTTS is None:
                    await message.answer(
                        "Google TTS сейчас недоступен. Попробуйте другой движок.",
                        reply_markup=get_sound_keyboard(),
                    )
                    TTS_STATE.pop(chat_id, None)
                    return
                tts = gTTS(text=text_to_synth, lang="ru")
                tts.save(file_path)
            elif engine_choice == "pyx3":
                if "pyttsx3" not in globals() or pyttsx3 is None:
                    await message.answer(
                        "pyttsx3 сейчас недоступен. Попробуйте другой движок.",
                        reply_markup=get_sound_keyboard(),
                    )
                    TTS_STATE.pop(chat_id, None)
                    return
                tts_engine = pyttsx3.init()
                voice_id = PYTTSX3_VOICE_MAP.get(voice_choice)
                if voice_id:
                    try:
                        tts_engine.setProperty("voice", voice_id)
                    except Exception:
                        # Если не удалось установить голос – используем голос по умолчанию
                        pass
                tts_engine.save_to_file(text_to_synth, file_path)
                tts_engine.runAndWait()
            elif engine_choice == "Edge TTS":
                voice_id = EDGE_TTS_VOICE_MAP.get(voice_choice)
                if not voice_id or EDGE_TTS_MODULE is None:
                    await message.answer(
                        "Edge TTS сейчас недоступен. Попробуйте другой движок.",
                        reply_markup=get_sound_keyboard(),
                    )
                    TTS_STATE.pop(chat_id, None)
                    return
                try:
                    await synthesize_edge_tts(text_to_synth, voice_id, file_path)
                except Exception as e:
                    logging.exception("modulsound: ошибка edge-tts при синтезе речи")
                    await message.answer(
                        f"Edge TTS вернул ошибку при синтезе: {type(e).__name__}: {e}\n"
                        "Попробуйте другой движок или позже.",
                        reply_markup=get_sound_keyboard(),
                    )
                    TTS_STATE.pop(chat_id, None)
                    return
            else:
                await message.answer(
                "Неизвестный движок синтеза речи. Попробуйте ещё раз.",
                reply_markup=get_sound_keyboard()
                )
                TTS_STATE.pop(chat_id, None)
                return

            LAST_TTS[chat_id] = file_path
            LAST_FILE[chat_id] = file_path
            with open(file_path, "rb") as f:
                await message.answer_audio(f)
            await message.answer(
                "Генерация завершена. Можете воспроизвести на компьютере:",
                reply_markup=get_playback_keyboard()
            )
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
            await message.answer("Выберите голос:", reply_markup=get_voice_keyboard(text))
        else:
            await message.answer("Пожалуйста, выберите движок из списка.", reply_markup=get_engine_keyboard())
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
            await message.answer(
                "Пожалуйста, выберите голос из списка.",
                reply_markup=get_voice_keyboard(TTS_STATE[chat_id]["engine"])
            )
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

    # Пытаемся получить информацию о файле голосового сообщения
    try:
        file_info = await message.bot.get_file(message.voice.file_id)
    except Exception as e:
        logging.exception("modulsound: ошибка get_file для голосового сообщения")
        try:
            await message.answer(
                f"❌ Ошибка при получении информации о голосовом сообщении: {type(e).__name__}: {e}",
                reply_markup=get_sound_keyboard()
            )
        except Exception:
            pass
        VOICE_MODE.discard(chat_id)
        return

    file_path_attr = None

    # Вариант 1: локальный API может вернуть dict
    if isinstance(file_info, dict):
        for key in ("file_path", "file_path_absolute", "local_path", "path"):
            val = file_info.get(key)
            if isinstance(val, str):
                file_path_attr = val
                break
    else:
        # Обычный File-объект aiogram
        val = getattr(file_info, "file_path", None)
        if isinstance(val, str):
            file_path_attr = val

    os.makedirs(SOUND_FOLDER, exist_ok=True)
    ogg_path = os.path.join(SOUND_FOLDER, f"voice_{chat_id}_{message.voice.file_unique_id}.ogg")

    # Скачивание/копирование голосового файла
    try:
        if isinstance(file_path_attr, str):
            # Если локальный API вернул абсолютный путь и файл существует — просто копируем
            if os.path.isabs(file_path_attr) and os.path.exists(file_path_attr):
                shutil.copy(file_path_attr, ogg_path)
            else:
                # Стандартный случай: скачиваем по file_path
                await message.bot.download_file(file_path_attr, ogg_path)
        else:
            # Фолбэк: вообще не трогаем file_path, качаем по file_id
            await message.bot.download_file_by_id(message.voice.file_id, ogg_path)
    except Exception as e:
        logging.exception("modulsound: ошибка при загрузке голосового файла")
        try:
            await message.answer(
                f"❌ Ошибка при загрузке голосового сообщения: {type(e).__name__}: {e}",
                reply_markup=get_sound_keyboard()
            )
        except Exception:
            pass
        VOICE_MODE.discard(chat_id)
        if os.path.exists(ogg_path):
            try:
                os.remove(ogg_path)
            except Exception:
                pass
        return

    wav_path = ogg_path.replace(".ogg", ".wav")

    # Если ffmpeg не найден — не пытаемся конвертировать
    if not FFMPEG_PATH:
        msg = "ffmpeg.exe не найден, не могу конвертировать голосовое сообщение в WAV для воспроизведения на компьютере."
        try:
            await message.answer(msg, reply_markup=get_sound_keyboard())
        except Exception:
            pass
        if os.path.exists(ogg_path):
            try:
                os.remove(ogg_path)
            except Exception:
                pass
        VOICE_MODE.discard(chat_id)
        return

    # Конвертация в WAV
    try:
        subprocess.run(
            [FFMPEG_PATH, "-y", "-i", ogg_path, wav_path],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        logging.exception("modulsound: ffmpeg ошибка при конвертации голосового")
        try:
            await message.answer(
                "❌ Ошибка ffmpeg при конвертации голосового сообщения:\n"

                f"{e.stderr or e.stdout or str(e)}",
                reply_markup=get_sound_keyboard()
            )
        except Exception:
            pass
        if os.path.exists(ogg_path):
            try:
                os.remove(ogg_path)
            except Exception:
                pass
        VOICE_MODE.discard(chat_id)
        return
    finally:
        if os.path.exists(ogg_path):
            try:
                os.remove(ogg_path)
            except Exception:
                pass

    LAST_VOICE[chat_id] = wav_path
    LAST_FILE[chat_id] = wav_path
    await message.answer(
        "Голосовое сообщение сохранено. Можете воспроизвести на компьютере:",
        reply_markup=get_playback_keyboard()
    )


# Регистрация хендлеров
def register_handlers(dp: Dispatcher):
    """Регистрация хендлеров модуля звука/особых функций."""

    async def _safe_call(handler, message: types.Message, context: str):
        """Обёртка для любого обработчика этого модуля.

        Любая неожиданная ошибка:
        - логируется;
        - не роняет бота;
        - отправляет в чат понятное сообщение.
        """
        try:
            return await handler(message)
        except Exception as e:
            logging.exception("modulsound: ошибка в обработчике %s", context)
            try:
                await message.answer(
                    f"❌ Ошибка в модуле «Особые функции» ({context}): {type(e).__name__}: {e}",
                    reply_markup=get_sound_keyboard()
                )
            except Exception:
                # Если даже это упало — уже совсем беда, но бота не валим.
                pass

    @dp.message_handler(lambda message: message.text == "Особые функции")
    async def cmd_special_handler(message: types.Message):

        # Дисклеймер безопасности: показываем один раз на чат за сессию бота.
        if message.chat.id not in DISCLAIMER_SHOWN:
            try:
                await message.answer(DISCLAIMER_TEXT)
            except Exception:
                pass
            DISCLAIMER_SHOWN.add(message.chat.id)

        # Если при инициализации были предупреждения (например, нет ffmpeg),
        # сразу покажем их один раз при входе в модуль.
        if INIT_ERRORS:
            try:
                err_text = "\n".join(f"- {err}" for err in INIT_ERRORS)
                await message.answer(
                    "⚠️ Модуль «Особые функции» загрузился с предупреждениями:\n" + err_text
                )
            except Exception:
                pass

        await _safe_call(cmd_special, message, "команда «Особые функции»")

    @dp.message_handler(
        lambda message:
            message.text in [
                "Синтез речи", "Отправить голос", "Очистить sound", "Очистить videos",
                "Снимок с камеры", "Видео с камеры", "Вернуться",
                "Отмена", "Воспроизвести на компьютере"
            ]
            or message.chat.id in TTS_STATE
            or message.chat.id in VOICE_MODE
            or message.chat.id in VIDEO_STATE
            or message.chat.id in SNAPSHOT_STATE
            or message.chat.id in PLAYBACK_STATE,
        content_types=['text']
    )
    async def button_handler_wrapper(message: types.Message):
        await _safe_call(button_handler, message, "кнопки/состояния")

    @dp.message_handler(lambda message: message.chat.id in VOICE_MODE, content_types=['voice'])
    async def voice_handler_wrapper(message: types.Message):
        await _safe_call(voice_handler, message, "голосовое сообщение")

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