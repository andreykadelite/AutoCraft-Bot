# -*- coding: utf-8 -*-
# Требуемые зависимости:
# pip install aiogram sounddevice soundfile pydub
#
# Обновления (этот релиз):
# • Полностью удалена кнопка «Отмена» и любая логика/состояния, связанные с отменой.
# • Во время записи любые посторонние сообщения игнорируются — бот мягко подсказывает нажать «Стоп».
# • Сообщения/подсказки очищены от упоминаний кнопки «Отмена».
# • Сохранён функционал: выбор «реальных» микрофонов, пагинация, фильтр «Только рабочие / Все устройства»,
#   запись → (опциональная) конвертация в MP3 → отправка / разбиение по лимиту.
#
# Важно: модуль ожидает, что в проекте есть modulsound.get_sound_keyboard().

import os
import sys
import threading
import asyncio
import math
from datetime import datetime
from typing import List, Dict, Any, Tuple

from textwrap import shorten
from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InputFile

import sounddevice as sd
import soundfile as sf
from pydub import AudioSegment

from modulsound import get_sound_keyboard

# ---------------------------
# ПОДГОТОВКА ПУТЕЙ / FFMPEG
# ---------------------------

# Определяем директорию для поиска внешних ресурсов (ffmpeg):
# если запущено как EXE, берем текущую рабочую директорию,
# иначе — папку, где лежит скрипт.
if getattr(sys, 'frozen', False):
    base_dir = os.getcwd()
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = base_dir

# Поиск ffmpeg.exe рядом со скриптом/EXE (без падения, если не найден)
FFMPEG_PATH = None
ffmpeg_candidates = [
    os.path.join(script_dir, "ffmpeg.exe"),
    os.path.join(script_dir, "ffmpeg-7.1", "bin", "ffmpeg.exe"),
    os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
]
for _p in ffmpeg_candidates:
    if os.path.isfile(_p):
        FFMPEG_PATH = _p
        break

# Настройка конвертера pydub (если нашли ffmpeg)
if FFMPEG_PATH:
    AudioSegment.converter = FFMPEG_PATH


# ---------------------------
# ФИЛЬТРЫ И ПРЕДПОЧТЕНИЯ
# ---------------------------

# Предпочтительные Host API (для равных устройств выберем "лучший")
PREFERRED_APIS = [
    "Windows WASAPI",
    "MME",
    "Windows DirectSound",
    "Windows WDM-KS",
    "ALSA",
    "PulseAudio",
    "Core Audio",
    "ASIO",
]

def _normalize_title(name: str) -> str:
    # Нормализуем имя для грубого дедупа: срезаем всё после первой скобки
    # и приводим к нижнему регистру
    base = name.split(' (', 1)[0].strip().lower()
    return base

def _check_device_works(device_id: int, samplerate: int = 44100, channels: int = 1) -> bool:
    try:
        sd.check_input_settings(device=device_id, samplerate=samplerate, channels=channels)
        return True
    except Exception:
        return False

# ---------------------------
# КОНСТАНТЫ И СОСТОЯНИЯ
# ---------------------------

SOUND_FOLDER = "sound"       # Папка для сохранения аудиофайлов
os.makedirs(SOUND_FOLDER, exist_ok=True)

# Максимальный размер аудио (50 МБ, если обычный сервер; 2 ГБ при локальном API)
MAX_AUDIO_SIZE = 50 * 1024 * 1024

# Пагинация для списка микрофонов
PAGE_SIZE = 8

# Состояния записи по чатам
# Пример:
# MIC_STATE[chat_id] = {
#   "state": "choose_device" | "recording",
#   "page": 0,
#   "devices": List[Dict],
#   "btn_map": Dict[str, int],  # соответствие текста кнопки -> device_id
#   "device": int,
#   "stop": bool,
#   "thread": threading.Thread,
#   "filepath": str
# }
MIC_STATE: Dict[int, Dict[str, Any]] = {}


# ---------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------

def get_audio_limit(bot) -> int:
    """
    Определяем лимит отправки аудио:
    • 2 ГБ для локального Telegram API;
    • 50 МБ — для обычного API Telegram.
    """
    try:
        server = getattr(bot, 'server', None)
        if server:
            base_url = getattr(server, 'base', None) or getattr(server, '_base', None)
            if base_url and not base_url.startswith("https://api.telegram.org"):
                return 2 * 1024 * 1024 * 1024
    except Exception:
        pass
    return MAX_AUDIO_SIZE


def format_err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def safe_tg_notify(loop: asyncio.AbstractEventLoop, bot, chat_id: int, text: str) -> None:
    """
    Безопасно отправляем сообщение из стороннего потока.
    """
    try:
        asyncio.run_coroutine_threadsafe(bot.send_message(chat_id, text), loop)
    except Exception:
        # Последняя линия обороны — ничего не делаем, чтобы не уронить поток.
        pass



def enumerate_input_devices(advanced: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает список «реальных» входных устройств (по умолчанию только проверенные).
    Если advanced=True — вернёт все устройства с входными каналами (даже если проверка не прошла).
    Выполняется дедуп по «базовому» имени (до первой скобки) с приоритетом PREFERRED_APIS.
    Каждый элемент: {"id": int, "name": str, "api": str, "channels": int, "works": bool, "default": bool}
    """
    result: List[Dict[str, Any]] = []
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        default_input_id = None
        try:
            dd = sd.default.device
            if isinstance(dd, (list, tuple)) and dd and dd[0] is not None and int(dd[0]) >= 0:
                default_input_id = int(dd[0])
        except Exception:
            default_input_id = None
    except Exception:
        return result

    candidates: List[Dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        channels = int(dev.get("max_input_channels", 0) or 0)
        if channels <= 0:
            continue

        api_idx = dev.get("hostapi", None)
        api_name = ""
        if isinstance(api_idx, int):
            try:
                api_name = hostapis[api_idx].get("name", "") or ""
            except Exception:
                api_name = ""
        name = str(dev.get("name", f"Device {idx}"))
        works = _check_device_works(idx, samplerate=44100, channels=min(1, channels) or 1)

        candidates.append({
            "id": idx,
            "name": name,
            "api": api_name,
            "channels": channels,
            "works": works,
            "default": (default_input_id == idx),
        })

    # Если не advanced — оставляем только реально работающие
    if not advanced:
        candidates = [c for c in candidates if c["works"]]

    # Дедуп по базовому названию с приоритетом API и "работоспособности"/каналов/дефолтности
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        key = _normalize_title(c["name"])
        grouped.setdefault(key, []).append(c)

    def _score(c: Dict[str, Any]) -> Tuple[int, int, int, str]:
        # Выше — лучше
        api_rank = len(PREFERRED_APIS) - (PREFERRED_APIS.index(c["api"]) if c["api"] in PREFERRED_APIS else -1)
        return (
            1 if c["default"] else 0,   # дефолт — приоритетнее
            1 if c["works"] else 0,     # рабочее — приоритетнее
            c["channels"],              # больше каналов — приоритетнее
            api_rank,                   # предпочтительное API — приоритетнее
        )

    deduped: List[Dict[str, Any]] = []
    for key, items in grouped.items():
        best = sorted(items, key=_score, reverse=True)[0]
        deduped.append(best)

    # Сортируем: дефолт сначала, потом по API приоритету и имени
    def _sort_key(c: Dict[str, Any]):
        api_rank = PREFERRED_APIS.index(c["api"]) if c["api"] in PREFERRED_APIS else 999
        return (0 if c["default"] else 1, api_rank, _normalize_title(c["name"]))

    deduped.sort(key=_sort_key)
    return deduped



def make_mic_caption(dev: Dict[str, Any], ordinal: int) -> str:
    """
    Короткий и понятный заголовок кнопки для микрофона.
    Показываем дефолтность и «проверено».
    """
    name = dev["name"]
    api = dev.get("api", "")
    marks = []
    if dev.get("default"):
        marks.append("⭐")
    if dev.get("works", True):
        marks.append("✔")
    base = f"{' '.join(marks)} №{ordinal}: {name}".strip()
    if api:
        base += f" · {api}"
    return shorten(base, width=60, placeholder="…")



def build_mic_select_keyboard(chat_id: int) -> ReplyKeyboardMarkup:
    """
    Формирует клавиатуру выбора микрофона + системные кнопки.
    """
    state = MIC_STATE.get(chat_id, {})
    page = int(state.get("page", 0))
    devices: List[Dict[str, Any]] = state.get("devices", [])
    advanced = bool(state.get("advanced", False))

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    state["btn_map"] = {}

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_devices = devices[start:end]

    for i, dev in enumerate(page_devices, start=1):
        ordinal = start + i
        text = make_mic_caption(dev, ordinal)
        state["btn_map"][text] = dev["id"]
        kb.add(KeyboardButton(text))

    nav_row: List[KeyboardButton] = []
    if page > 0:
        nav_row.append(KeyboardButton("⬅️ Назад"))
    if end < len(devices):
        nav_row.append(KeyboardButton("➡️ Далее"))
    if nav_row:
        kb.row(*nav_row)

    # Переключатель фильтра
    kb.add(KeyboardButton("Показать все устройства" if not advanced else "Только рабочие"))

    kb.add(KeyboardButton("Обновить список"))
    MIC_STATE[chat_id] = state
    return kb


def get_mic_keyboard(mode: str) -> ReplyKeyboardMarkup:
    """
    Клавиатура управления записью.
    """
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if mode == "ready":
        # Сейчас «ready» не используется (мы сразу показываем выбор микрофона),
        # оставлено для совместимости.
        kb.add(KeyboardButton("Начать запись"))
    else:
        kb.add(KeyboardButton("Стоп"))
    return kb


# ---------------------------
# ЗАПИСЬ АУДИО (В ПОТОКЕ)
# ---------------------------

def record_audio(chat_id: int, bot, loop: asyncio.AbstractEventLoop) -> None:
    """
    Запись аудио в отдельном потоке. Все исключения ловим и пересылаем в TG.
    """
    state = MIC_STATE.get(chat_id)
    if not state:
        return

    device = state.get("device")
    if device is None:
        safe_tg_notify(loop, bot, chat_id, "❌ Ошибка: устройство не выбрано.")
        return

    # Метка времени для имен файлов
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_path = os.path.join(SOUND_FOLDER, f"mic_{chat_id}_{timestamp}.wav")

    try:
        # Запись WAV
        with sf.SoundFile(wav_path, mode="w", samplerate=44100, channels=1) as file:
            def callback(indata, frames, time_info, status):
                if state.get("stop"):
                    raise sd.CallbackStop()
                file.write(indata)

            with sd.InputStream(samplerate=44100, channels=1, callback=callback, device=device):
                while not state.get("stop"):
                    sd.sleep(100)

        # Конвертация в MP3 (если возможно), удаление WAV при успехе
        mp3_path = os.path.join(SOUND_FOLDER, f"mic_{chat_id}_{timestamp}.mp3")
        final_path = wav_path  # по умолчанию шлём WAV

        converted_ok = False
        try:
            # Если ffmpeg найден — явно выставляем, иначе pydub попробует PATH
            if FFMPEG_PATH:
                AudioSegment.converter = FFMPEG_PATH
            audio = AudioSegment.from_file(wav_path, format="wav")
            audio.export(mp3_path, format="mp3")
            os.remove(wav_path)
            final_path = mp3_path
            converted_ok = True
        except Exception as conv_err:
            # Сообщим, но не упадём — шлём WAV
            safe_tg_notify(loop, bot, chat_id, f"⚠️ Не удалось конвертировать в MP3. Отправляю WAV.\n{format_err(conv_err)}")

        state["filepath"] = final_path

    except Exception as e:
        # Любая ошибка — оповещаем
        safe_tg_notify(loop, bot, chat_id, f"❌ Ошибка записи: {format_err(e)}")
        # Попробуем почистить незавершённый файл
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception:
            pass
        # Останавливаем запись
        try:
            state["stop"] = True
        except Exception:
            pass


# ---------------------------
# ХЭНДЛЕРЫ
# ---------------------------

async def mic_command_handler(message: types.Message):
    chat_id = message.chat.id
    # Сразу готовим состояние выбора устройства
    try:
        MIC_STATE[chat_id] = {
            "state": "choose_device",
            "page": 0,
            "devices": enumerate_input_devices(False),
            "advanced": False,
            "stop": False,
        }

        limit = get_audio_limit(message.bot)
        intro = (
            "🎚 Режим записи микрофона активирован.\n"
            f"Лимит отправки: {limit/1024**3:.2f} ГБ.\n"
            "Выберите устройство из списка ниже."
        )

        # Пустой список — сообщаем
        if not MIC_STATE[chat_id]["devices"]:
            await message.answer(intro + "\n\n❗️ Микрофоны не найдены. Нажмите «Обновить список».",
                                 reply_markup=build_mic_select_keyboard(chat_id))
            return

        await message.answer(intro, reply_markup=build_mic_select_keyboard(chat_id))
    except Exception as e:
        await message.answer(f"❌ Ошибка при активации режима: {format_err(e)}")


async def mic_text_handler(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in MIC_STATE:
        return

    state = MIC_STATE[chat_id]
    keyboard_main = get_sound_keyboard()
    text_raw = message.text or ""
    text = text_raw.strip()

    try:
        # ------ ЭТАП ВЫБОРА УСТРОЙСТВА ------
        if state.get("state") == "choose_device":
            lower = text.lower()

            if lower == "показать все устройства":
                state["advanced"] = True
                state["devices"] = enumerate_input_devices(True)
                state["page"] = 0
                if not state["devices"]:
                    await message.answer("Устройств не найдено.", reply_markup=build_mic_select_keyboard(chat_id))
                    return
                await message.answer("Показаны все устройства.", reply_markup=build_mic_select_keyboard(chat_id))
                return

            if lower == "только рабочие":
                state["advanced"] = False
                state["devices"] = enumerate_input_devices(False)
                state["page"] = 0
                if not state["devices"]:
                    await message.answer("Рабочие микрофоны не найдены. Попробуйте «Показать все устройства».",
                                         reply_markup=build_mic_select_keyboard(chat_id))
                    return
                await message.answer("Показаны только проверенные микрофоны.", reply_markup=build_mic_select_keyboard(chat_id))
                return

            if lower == "обновить список":
                state["devices"] = enumerate_input_devices(state.get("advanced", False))
                state["page"] = 0
                if not state["devices"]:
                    await message.answer("Микрофоны не найдены. Попробуйте ещё раз.",
                                         reply_markup=build_mic_select_keyboard(chat_id))
                    return
                await message.answer("Список устройств обновлён.", reply_markup=build_mic_select_keyboard(chat_id))
                return

            if lower == "⬅️ назад":
                state["page"] = max(0, int(state.get("page", 0)) - 1)
                await message.answer("Страница назад.", reply_markup=build_mic_select_keyboard(chat_id))
                return

            if lower == "➡️ далее":
                total = len(state.get("devices", []))
                page = int(state.get("page", 0))
                if (page + 1) * PAGE_SIZE < total:
                    state["page"] = page + 1
                await message.answer("Страница вперёд.", reply_markup=build_mic_select_keyboard(chat_id))
                return

            # Попытка сопоставить нажатую кнопку с устройством
            btn_map: Dict[str, int] = state.get("btn_map", {})
            if text in btn_map:
                device_id = btn_map[text]
            else:
                # Дополнительно пробуем распознать короткий ввод числом (№ в списке)
                device_id = None
                if text.isdigit():
                    # Номер в общем списке (1..N), не индекс устройства
                    ordinal = int(text)
                    devices = state.get("devices", [])
                    if 1 <= ordinal <= len(devices):
                        device_id = devices[ordinal - 1]["id"]

            if device_id is None:
                await message.answer(
                    "Не понял выбор. Нажмите кнопку с устройством или «Обновить список».",
                    reply_markup=build_mic_select_keyboard(chat_id)
                )
                return

            # Проверим, что выбранное устройство всё ещё существует и является входным
            try:
                dev = sd.query_devices(device_id)
                if int(dev.get("max_input_channels", 0) or 0) <= 0 or not _check_device_works(device_id, samplerate=44100, channels=1):
                    await message.answer("Выбранное устройство не является входным. Выберите другой микрофон.",
                                         reply_markup=build_mic_select_keyboard(chat_id))
                    return
            except Exception as e:
                await message.answer(f"Не удалось получить информацию об устройстве. Попробуйте обновить список.\n{format_err(e)}",
                                     reply_markup=build_mic_select_keyboard(chat_id))
                return

            # Старт записи
            state.update({"state": "recording", "device": device_id, "stop": False})
            th = threading.Thread(target=record_audio, args=(chat_id, message.bot, message.bot.loop), daemon=True)
            state["thread"] = th
            th.start()
            await message.answer("🔴 Запись началась. Нажмите «Стоп» для завершения.", reply_markup=get_mic_keyboard("recording"))
            return

        # ------ ЭТАП ЗАПИСИ ------
        else:
            lower = text.lower()
            if lower == "стоп":
                state["stop"] = True
                # Ждём завершения фонового потока записи
                thread: threading.Thread = state.get("thread")
                if thread:
                    while thread.is_alive():
                        await asyncio.sleep(0.1)

                # Отправка файла/частей
                path = state.get("filepath")
                if path:
                    try:
                        size = os.path.getsize(path)
                    except Exception as e:
                        await message.answer(f"⚠️ Файл не найден/повреждён: {format_err(e)}", reply_markup=keyboard_main)
                        MIC_STATE.pop(chat_id, None)
                        return

                    limit = get_audio_limit(message.bot)
                    abs_path = os.path.abspath(path)
                    size_mb = size / 1024**2

                    if size <= limit:
                        # Отправляем как аудио
                        try:
                            await message.answer_audio(InputFile(path), reply_markup=keyboard_main)
                        except Exception as send_err:
                            # Если как аудио не уходит — отправим документом
                            await message.answer_document(InputFile(path), reply_markup=keyboard_main)
                            await message.answer(f"Примечание: аудио отправлено как документ из-за ошибки: {format_err(send_err)}")

                        await message.answer(f"Путь к файлу: {abs_path}", reply_markup=keyboard_main)
                        await message.answer(f"Размер файла: {size_mb:.2f} МБ", reply_markup=keyboard_main)

                    else:
                        # Разбиение на части (требуется pydub). Пытаемся открыть исходный файл по расширению.
                        try:
                            ext = os.path.splitext(path)[1].lower().lstrip(".")
                            if ext not in ("mp3", "wav"):
                                # fallback — попытаемся по содержимому
                                audio = AudioSegment.from_file(path)
                            else:
                                audio = AudioSegment.from_file(path, format=ext)

                            total = math.ceil(size / limit)
                            ms = len(audio)
                            base, _ = os.path.splitext(path)
                            for i in range(total):
                                start = int(i * ms / total)
                                end = int((i + 1) * ms / total) if i < total - 1 else ms
                                segment = audio[start:end]
                                part_path = f"{base}_part{i+1}.{ext}"
                                segment.export(part_path, format=ext)
                                try:
                                    await message.answer_audio(InputFile(part_path), reply_markup=keyboard_main)
                                except Exception:
                                    await message.answer_document(InputFile(part_path), reply_markup=keyboard_main)
                                await message.answer(f"Часть {i+1}/{total} отправлена: {os.path.abspath(part_path)}", reply_markup=keyboard_main)

                            await message.answer(f"Файлы сохранены в {os.path.abspath(SOUND_FOLDER)}", reply_markup=keyboard_main)

                        except Exception as split_err:
                            await message.answer(f"⚠️ Не удалось разбить файл на части: {format_err(split_err)}\n"
                                                 f"Пробую отправить исходный файл документом…", reply_markup=keyboard_main)
                            try:
                                await message.answer_document(InputFile(path), reply_markup=keyboard_main)
                            except Exception as doc_err:
                                await message.answer(f"❌ Не удалось отправить файл: {format_err(doc_err)}", reply_markup=keyboard_main)

                else:
                    await message.answer("Файл записи отсутствует.", reply_markup=keyboard_main)

                MIC_STATE.pop(chat_id, None)
                return

            # Любые другие сообщения во время записи не прерывают процесс.
            await message.answer("Идёт запись. Используйте «Стоп» для завершения.", reply_markup=get_mic_keyboard("recording"))

    except Exception as e:
        # Глобальная защита хэндлера
        MIC_STATE.pop(chat_id, None)
        await message.answer(f"❌ Ошибка: {format_err(e)}", reply_markup=keyboard_main)


# ---------------------------
# РЕГИСТРАЦИЯ ХЭНДЛЕРОВ
# ---------------------------

def register_handlers(dp: Dispatcher):
    dp.message_handler(lambda m: m.text == "Звук с микрофона")(mic_command_handler)
    dp.message_handler(lambda m: m.chat.id in MIC_STATE, content_types=["text"])(mic_text_handler)
