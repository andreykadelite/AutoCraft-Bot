import asyncio
import logging
import os
import shutil
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import locale
from collections import deque
from datetime import datetime
import configparser
from pathlib import Path
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Deque

from aiogram import types, Dispatcher
from aiohttp import web

from keymenu import get_utilities_keyboard

try:
    from moduls.stream_control_window import (
        close_stream_control_window,
        show_stream_control_window,
    )
except Exception:
    def show_stream_control_window(owner_id: str, title: str, details: str, stop_callback):  # type: ignore
        return False

    def close_stream_control_window(owner_id: str = "") -> None:  # type: ignore
        return None

# OpenCV — опционально (для fallback через pipe)
try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore


# Импортируем опциональные переменные из __main__
try:
    from __main__ import authorized_users  # type: ignore
except Exception:  # pragma: no cover
    authorized_users = []

try:
    from __main__ import write_bot_log  # type: ignore
except Exception:  # pragma: no cover
    def write_bot_log(msg: str) -> None:
        logging.info(msg)


# Поиск базового каталога (учет Nuitka/EXE)
try:
    _MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
except Exception:  # pragma: no cover
    _MODULE_DIR = os.getcwd()

if getattr(sys, "frozen", False):
    # В EXE ориентируемся на рабочую папку (обычно это папка с exe)
    BASE_DIR = os.getcwd()
else:
    # В исходниках: если модуль лежит в папке moduls, то "корень" на уровень выше
    if os.path.basename(_MODULE_DIR).lower() == "moduls":
        BASE_DIR = os.path.dirname(_MODULE_DIR)
    else:
        BASE_DIR = _MODULE_DIR


# ---------------------------
# Конфиг / debug (config.ini)
# ---------------------------
def _get_root_dir() -> str:
    """Папка запуска: рядом с EXE/главным скриптом (важно для EXE/Nuitka)."""
    with suppress(Exception):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return BASE_DIR


def _find_config_path() -> Optional[str]:
    """Ищем config.ini (EXE/py): рядом с запуском, рядом с корнем и рядом с модулем."""
    candidates: List[str] = []
    with suppress(Exception):
        candidates.append(_get_root_dir())
    with suppress(Exception):
        candidates.append(BASE_DIR)
    with suppress(Exception):
        candidates.append(_MODULE_DIR)
    with suppress(Exception):
        candidates.append(os.getcwd())
    if getattr(sys, "frozen", False):
        with suppress(Exception):
            candidates.append(os.path.dirname(sys.executable))

    seen: set = set()
    for base in candidates:
        if not base:
            continue
        base_abs = os.path.abspath(base)
        if base_abs in seen:
            continue
        seen.add(base_abs)
        cfg = os.path.join(base_abs, "config.ini")
        if os.path.isfile(cfg):
            return cfg
    return None


_CFG_PATH = _find_config_path()
_conf = configparser.ConfigParser()
if _CFG_PATH:
    # config.ini в проектах на Windows иногда бывает в cp1251, поэтому читаем с фолбэком
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            _conf.read(_CFG_PATH, encoding=enc)
            break
        except Exception:
            continue

# debug можно держать в [settings] debug=true/false или (старый формат) в [credentials] debug=true/false
DEBUG_ENABLED = False
with suppress(Exception):
    DEBUG_ENABLED = _conf.getboolean(
        "settings",
        "debug",
        fallback=_conf.getboolean("credentials", "debug", fallback=False),
    )

# ---------------------------
# Логирование (в папку log, ОДИН файл)
# ---------------------------
def _get_log_dir() -> str:
    """Создаём/используем папку log рядом с запуском. Если не выходит, падаем в temp."""
    candidates: List[str] = []
    with suppress(Exception):
        candidates.append(_get_root_dir())
    with suppress(Exception):
        candidates.append(BASE_DIR)
    with suppress(Exception):
        candidates.append(os.getcwd())
    if getattr(sys, "frozen", False):
        with suppress(Exception):
            candidates.append(os.path.dirname(sys.executable))
    with suppress(Exception):
        candidates.append(tempfile.gettempdir())

    for base in candidates:
        if not base:
            continue
        d = os.path.join(base, "log")
        try:
            os.makedirs(d, exist_ok=True)
            # проверим запись
            test_path = os.path.join(d, ".write_test_stream")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok")
            with suppress(Exception):
                os.remove(test_path)
            return d
        except Exception:
            continue

    d = os.path.join(tempfile.gettempdir(), "log")
    with suppress(Exception):
        os.makedirs(d, exist_ok=True)
    return d


# ВНИМАНИЕ: файл лога создаём ТОЛЬКО когда включён debug.
LOG_DIR = _get_log_dir() if DEBUG_ENABLED else os.path.join(_get_root_dir(), "log")
STREAM_LOG_PATH = os.path.join(LOG_DIR, "nostartrunmodulstream.log")

_STREAM_LOGGER = logging.getLogger("nostartrunmodulstream")
_STREAM_LOGGER.propagate = False

if DEBUG_ENABLED:
    _STREAM_LOGGER.setLevel(logging.DEBUG)
    # один файл без ротации и без дополнительных дампов в отдельные файлы
    if not any(getattr(h, "baseFilename", "") == STREAM_LOG_PATH for h in _STREAM_LOGGER.handlers):
        try:
            fh = logging.FileHandler(STREAM_LOG_PATH, encoding="utf-8")
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
            fh.setFormatter(fmt)
            fh.setLevel(logging.DEBUG)
            _STREAM_LOGGER.addHandler(fh)
        except Exception:
            # Если файловый лог не удалось создать, не роняем модуль.
            _STREAM_LOGGER.addHandler(logging.NullHandler())
else:
    # debug выключен -> не пишем файловые логи вообще
    _STREAM_LOGGER.setLevel(100)
    if not _STREAM_LOGGER.handlers:
        _STREAM_LOGGER.addHandler(logging.NullHandler())


def _append_debug_block(text_block: str, chat_id: Optional[int] = None) -> None:
    """Добавить большой блок текста прямо в общий лог (только при DEBUG_ENABLED)."""
    if not DEBUG_ENABLED:
        return
    prefix = f"[chat:{chat_id}] " if chat_id is not None else ""
    try:
        with open(STREAM_LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(prefix + text_block)
            if not text_block.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def _slog(level: int, msg: str, chat_id: Optional[int] = None) -> None:
    if not DEBUG_ENABLED:
        return
    prefix = f"[chat:{chat_id}] " if chat_id is not None else ""
    with suppress(Exception):
        _STREAM_LOGGER.log(level, prefix + msg)


_slog(
    logging.INFO,
    f"module loaded | debug={DEBUG_ENABLED} | config={_CFG_PATH or '(не найден)'} | BASE_DIR={BASE_DIR} | MODULE_DIR={_MODULE_DIR} | LOG_DIR={LOG_DIR} | frozen={getattr(sys, 'frozen', False)} | py={sys.version.split()[0]}",
)


# Ищем ffmpeg (поддерживаем варианты: рядом с exe, рядом с модулем, и "рядом с moduls" в корне)
_SEARCH_BASES: List[str] = []
for _p in (BASE_DIR, _MODULE_DIR, os.getcwd(), os.path.dirname(sys.executable)):
    if _p and _p not in _SEARCH_BASES:
        _SEARCH_BASES.append(_p)

FFMPEG_CANDIDATES: List[str] = []
for _base in _SEARCH_BASES:
    FFMPEG_CANDIDATES.extend([
        os.path.join(_base, "ffmpeg.exe"),
        os.path.join(_base, "ffmpeg-7.1", "bin", "ffmpeg.exe"),
    ])

# Последний шанс: PATH системы
FFMPEG_CANDIDATES.append(shutil.which("ffmpeg") or "")

FFMPEG_PATH = next((p for p in FFMPEG_CANDIDATES if p and os.path.isfile(p)), None)

# Тексты/кнопки
ENTRY_TITLE = "Прямая трансляция"
BTN_START = "Включить трансляцию"
BTN_STOP = "Выключить трансляцию"
BTN_PICK_CAMERA = "Выбрать камеру"
BTN_PICK_AUDIO = "Выбрать микрофон"
BTN_REFRESH = "Обновить список устройств"
BTN_BACK_MENU = "Назад к утилитам"
BTN_MINIMIZE = "Свернуть модуль"
BTN_BACK_STREAM = "Назад в трансляцию"

# Предупреждение о законном использовании
LEGAL_NOTICE_TEXT = (
    "⚠️ Предупреждение о законном использовании\n"
    "Этот модуль предназначен только для законного применения.\n"
    "• Запускайте трансляцию только на своём оборудовании и/или при явном согласии всех участников.\n"
    "• Не используйте для скрытой записи, слежки, нарушения приватности или обхода ограничений.\n"
    "• Вы несёте полную ответственность за соблюдение законов и правил вашей организации/страны.\n"
    "Автор(ы) модуля и разработчики проекта не несут ответственности за последствия его использования."
)


# Режимы диалога
stream_mode: Dict[int, bool] = {}
camera_select_mode: Dict[int, bool] = {}
audio_select_mode: Dict[int, bool] = {}
pending_start_after_pick: Dict[int, bool] = {}


@dataclass
class DeviceInfo:
    # Для dshow: name = "Friendly Name", alt_name = "@device_pnp_..."
    # Для opencv: name = индекс (строкой), alt_name = None
    name: str
    label: str
    source: str = "dshow"
    alt_name: Optional[str] = None


@dataclass
class StreamState:
    camera: Optional[DeviceInfo] = None
    audio: Optional[DeviceInfo] = None

    hls_dir: Optional[str] = None
    port: Optional[int] = None
    runner: Optional[web.AppRunner] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    server_thread: Optional[threading.Thread] = None

    ffmpeg: Optional[subprocess.Popen] = None

    # Диагностика ffmpeg
    ffmpeg_cmd: Optional[List[str]] = None
    ffmpeg_stderr_path: Optional[str] = None
    ffmpeg_stderr_tail: Deque[str] = field(default_factory=lambda: deque(maxlen=250))
    ffmpeg_stderr_stop: Optional[threading.Event] = None
    ffmpeg_stderr_thread: Optional[threading.Thread] = None

    # OpenCV->FFmpeg pipe fallback
    opencv_capture: Optional[object] = None
    opencv_thread: Optional[threading.Thread] = None
    opencv_stop: Optional[threading.Event] = None

    status: str = "idle"
    stopping: bool = False
    last_error: Optional[str] = None
    last_audio_error: Optional[str] = None
    last_url: Optional[str] = None


STREAM_STATE: Dict[int, StreamState] = {}
AVAILABLE_DEVICES: Dict[int, List[DeviceInfo]] = {}
AVAILABLE_AUDIO: Dict[int, List[DeviceInfo]] = {}
STATE_LOCK = threading.Lock()


def _stream_window_owner(chat_id: int) -> str:
    return f"tg-live-stream:{chat_id}"


def _reset_stream_modes_after_window_stop(chat_id: int) -> None:
    stream_mode.pop(chat_id, None)
    camera_select_mode.pop(chat_id, None)
    audio_select_mode.pop(chat_id, None)
    pending_start_after_pick.pop(chat_id, None)


def _notify_stream_stopped_via_window(chat_id: int, bot) -> None:
    if not bot:
        return
    with suppress(Exception):
        asyncio.run_coroutine_threadsafe(
            bot.send_message(
                chat_id,
                "Трансляция остановлена через окно управления.",
                reply_markup=get_utilities_keyboard(),
            ),
            bot.loop,
        )


def _stop_stream_from_control_window(chat_id: int, bot) -> dict:
    try:
        stop_stream(chat_id, notify=False)
        _reset_stream_modes_after_window_stop(chat_id)
        _notify_stream_stopped_via_window(chat_id, bot)
        return {"ok": True, "stdout": "Трансляция остановлена.", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def _show_stream_control_window_for_chat(chat_id: int, bot, state: StreamState) -> None:
    camera_label = state.camera.label if state.camera else "не выбрана"
    audio_label = state.audio.label if state.audio else "не выбран"
    details = (
        f"Источник: Telegram (chat_id={chat_id})\n"
        f"Камера: {camera_label}\n"
        f"Микрофон: {audio_label}"
    )
    show_stream_control_window(
        owner_id=_stream_window_owner(chat_id),
        title="Идет прямая трансляция",
        details=details,
        stop_callback=lambda: _stop_stream_from_control_window(chat_id, bot),
    )


# Регистрируем кнопку утилиты
try:
    from utilities_registry import register_utility

    register_utility(
        key="live_stream",
        title=ENTRY_TITLE,
        trigger_text=ENTRY_TITLE,
        group="utilities",
        order=15,
        description="Онлайн-трансляция с камеры/микрофона ПК через ссылку (HLS).",
    )
except Exception:
    pass


def _is_authorized(message: types.Message) -> bool:
    try:
        return not authorized_users or message.from_user.id in authorized_users
    except Exception:
        return True


def _log(msg: str) -> None:
    try:
        write_bot_log(msg)
    except Exception:
        logging.info(msg)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _get_local_ip() -> str:
    ip = "127.0.0.1"
    with suppress(Exception):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
    return ip


def _ensure_state(chat_id: int) -> StreamState:
    with STATE_LOCK:
        state = STREAM_STATE.get(chat_id)
        if not state:
            state = StreamState()
            STREAM_STATE[chat_id] = state
        return state


def _cleanup_hls(hls_dir: Optional[str]) -> None:
    if hls_dir and os.path.isdir(hls_dir):
        with suppress(Exception):
            shutil.rmtree(hls_dir, ignore_errors=True)


def _decode_best_effort(data: bytes) -> str:
    """
    ffmpeg на Windows может писать как в UTF-8, так и в OEM/ANSI.
    Нам важно корректно декодировать имена dshow-устройств, иначе ffmpeg потом их «не найдёт».

    Стратегия:
      1) Сначала пробуем строгий UTF-8. Если получилось — берём его.
      2) Если UTF-8 не зашёл — пробуем набор типичных кодировок в приоритетном порядке.
    """
    if not data:
        return ""

    # 1) UTF-8 first (strict). ASCII тоже сюда попадает.
    with suppress(Exception):
        return data.decode("utf-8", errors="strict")

    # 2) Fallbacks (OEM/ANSI)
    encs: List[str] = []
    with suppress(Exception):
        encs.append(locale.getpreferredencoding(False))
    # Затем OEM/ANSI
    encs.extend(["cp866", "cp1251", "utf-8", "latin-1"])

    candidates: List[Tuple[int, int, str]] = []
    seen: set = set()
    for prio, e in enumerate(encs):
        if not e or e in seen:
            continue
        seen.add(e)
        with suppress(Exception):
            s = data.decode(e, errors="replace")
            repl = s.count("\ufffd")
            candidates.append((repl, prio, s))

    if not candidates:
        return data.decode("utf-8", errors="ignore")

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def _ffmpeg_has_dshow() -> bool:
    if not FFMPEG_PATH:
        return False
    try:
        proc = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-formats"],
            capture_output=True,
            timeout=10,
        )
        out = _decode_best_effort((proc.stdout or b"") + (proc.stderr or b""))
        # В выводе форматов/устройств dshow будет фигурировать как "dshow"
        return " dshow" in out or "\ndshow" in out
    except Exception:
        return False


def _probe_dshow_devices() -> Tuple[List[DeviceInfo], List[DeviceInfo]]:
    """
    Возвращает:
      - список видеоустройств (DeviceInfo: name + alt_name при наличии)
      - список аудиоустройств (friendly/alt_name)
    """
    if not FFMPEG_PATH:
        return [], []

    try:
        proc = subprocess.run(
            [
                FFMPEG_PATH,
                "-hide_banner",
                "-list_devices",
                "true",
                "-f",
                "dshow",
                "-i",
                "dummy",
            ],
            capture_output=True,
            timeout=15,
        )
        output = _decode_best_effort((proc.stdout or b"") + (proc.stderr or b""))
        # Сохраним сырой дамп в ОДИН общий лог (только при debug)
        if DEBUG_ENABLED:
            _append_debug_block(
                "\n=== dshow devices dump | "
                + datetime.now().isoformat(sep=" ", timespec="seconds")
                + " ===\n"
                + output
                + "\n=== /dshow devices dump ===\n"
            )
    except Exception:
        return [], []

    videos: List[DeviceInfo] = []
    audios: List[DeviceInfo] = []
    current: Optional[str] = None
    last_video_index: Optional[int] = None
    last_audio_index: Optional[int] = None
    last_kind: Optional[str] = None

    for line in output.splitlines():
        # Старый формат: есть заголовки секций
        if "DirectShow video devices" in line:
            current = "video"
            continue
        if "DirectShow audio devices" in line:
            current = "audio"
            continue

        # Новый формат (встречается в свежих сборках ffmpeg): девайс уже помечен (video)/(audio),
        # при этом заголовков может не быть.
        m_dev = re.search(r'"([^"]+)"\s*\((video|audio)\)', line)
        if m_dev:
            name = (m_dev.group(1) or "").strip()
            kind = (m_dev.group(2) or "").strip().lower()
            if not name:
                continue
            if kind == "video":
                videos.append(DeviceInfo(name=name, label=name, source="dshow", alt_name=None))
                last_video_index = len(videos) - 1
                last_kind = "video"
            else:
                audios.append(DeviceInfo(name=name, label=name, source="dshow", alt_name=None))
                last_audio_index = len(audios) - 1
                last_kind = "audio"
            continue

        # Альтернативное имя (обычно ASCII, подходит, когда friendly name с юникодом/символами)
        if "Alternative name" in line:
            q_alt = re.search(r'"([^"]+)"', line)
            if q_alt:
                alt = (q_alt.group(1) or "").strip()
                if alt:
                    kind = last_kind or current
                    if kind == "video" and last_video_index is not None:
                        videos[last_video_index].alt_name = alt
                    elif kind == "audio" and last_audio_index is not None:
                        audios[last_audio_index].alt_name = alt
            continue

        # Если секция не определена — пропускаем (старый формат без заголовков мы уже обработали выше)
        if current not in ("video", "audio"):
            continue

        # Старый формат: достаём первое quoted имя
        q = re.search(r'"([^"]+)"', line)
        if not q:
            continue
        name = (q.group(1) or "").strip()
        if not name:
            continue

        if current == "video":
            videos.append(DeviceInfo(name=name, label=name, source="dshow", alt_name=None))
            last_video_index = len(videos) - 1
            last_kind = "video"
        else:
            audios.append(DeviceInfo(name=name, label=name, source="dshow", alt_name=None))
            last_audio_index = len(audios) - 1
            last_kind = "audio"

    return videos, audios


def _probe_opencv_cameras(max_devices: int = 10) -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    if cv2 is None:
        return devices

    # Пробуем DSHOW, если не открылось — MSMF/ANY
    backends = []
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_DSHOW", 0))
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_MSMF", 0))
    backends.append(getattr(cv2, "CAP_ANY", 0))

    for idx in range(max_devices):
        opened = False
        cap = None
        w = h = 0
        for be in backends:
            try:
                cap = cv2.VideoCapture(idx, be)
                if cap and cap.isOpened():
                    opened = True
                    w = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3)) or 0)
                    h = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4)) or 0)
                    break
            except Exception:
                cap = None
        if cap is not None:
            with suppress(Exception):
                cap.release()
        if not opened:
            continue

        label = f"Камера #{idx + 1} (opencv, {w}x{h})"
        devices.append(DeviceInfo(name=str(idx), label=label, source="opencv"))
    return devices


def _probe_audio_devices_powershell() -> List[str]:
    """
    Резервный способ (Windows): получаем имена устройств через PowerShell.

    Почему вообще нужно: иногда ffmpeg/dshow возвращает неполный список или пользователь хочет подсказку.
    Важно: PowerShell 5.1 может печатать текст в OEM-кодировке, и Python получит «кракозябры».
    Поэтому мы принудительно переводим вывод PowerShell в UTF-8 и декодируем его как UTF-8.
    """
    if os.name != "nt":
        return []

    ps_exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"

    # Команды возвращают строки (Name/ProductName), где встречается microphone/микрофон.
    raw_scripts = [
        r"Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '(?i)microphone|микрофон' } | Select-Object -ExpandProperty Name",
        r"Get-CimInstance Win32_SoundDevice | Where-Object { ($_.ProductName -match '(?i)microphone|микрофон') -or ($_.Name -match '(?i)microphone|микрофон') } | ForEach-Object { $_.ProductName; $_.Name }",
    ]

    # Обёртка: переводим stdout в UTF-8 (без BOM), чтобы Python получил нормальные русские буквы.
    # Для Windows PowerShell 5.1 это критично.
    def wrap_utf8(script: str) -> str:
        return (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            + script
        )

    names: List[str] = []
    for script in raw_scripts:
        try:
            proc = subprocess.run(
                [ps_exe, "-NoProfile", "-Command", wrap_utf8(script)],
                capture_output=True,
                text=False,
                timeout=8,
            )
            out = (proc.stdout or b"").decode("utf-8", errors="replace")
            for line in out.splitlines():
                name = (line or "").strip()
                if name:
                    names.append(name)
        except Exception:
            continue

    # Дедуп по lower, сохраняя порядок
    seen = set()
    uniq: List[str] = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(name)
    return uniq

def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    # убираем служебные префиксы из label
    s = re.sub(r"^микрофон\s*#\d+\s*:\s*", "", s)
    s = re.sub(r"^микрофон\s*\(ps\)\s*:\s*", "", s)
    # нормализуем пробелы/кавычки
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.strip(' "')
    return s


def _audio_dev_matches(dev: DeviceInfo, selected: DeviceInfo) -> bool:
    if not dev or not selected:
        return False

    # Сначала самые стабильные идентификаторы
    if dev.alt_name and selected.alt_name and dev.alt_name == selected.alt_name:
        return True
    if dev.alt_name and selected.name and _norm_text(dev.alt_name) == _norm_text(selected.name):
        return True
    if selected.alt_name and dev.name and _norm_text(selected.alt_name) == _norm_text(dev.name):
        return True

    # Затем friendly names
    if dev.name and selected.name and _norm_text(dev.name) == _norm_text(selected.name):
        return True

    # И как последний шанс сравним нормализованные label (они могут меняться из‑за индексов, поэтому только в конце)
    if dev.label and selected.label and _norm_text(dev.label) == _norm_text(selected.label):
        return True

    return False


def _best_dshow_match_for_ps(ps_dev: DeviceInfo, dshow_devices: List[DeviceInfo]) -> Optional[DeviceInfo]:
    """Пытаемся сопоставить PowerShell-имя с реальным dshow устройством (для ffmpeg)."""
    if not ps_dev or not dshow_devices:
        return None

    ps_name = _norm_text(ps_dev.name) or _norm_text(ps_dev.label)
    if not ps_name:
        return None

    # 1) точное совпадение по name/alt_name
    for d in dshow_devices:
        if _norm_text(d.name) == ps_name:
            return d
        if d.alt_name and _norm_text(d.alt_name) == ps_name:
            return d

    # 2) подстрока/токены (PS часто добавляет бренд/суффиксы)
    best: Optional[DeviceInfo] = None
    best_score = 0

    ps_tokens = {t for t in re.split(r"[^a-zа-я0-9]+", ps_name) if t}
    for d in dshow_devices:
        dn = _norm_text(d.name)
        if not dn:
            continue
        d_tokens = {t for t in re.split(r"[^a-zа-я0-9]+", dn) if t}

        score = 0
        if ps_name in dn or dn in ps_name:
            score += 3
        inter = ps_tokens.intersection(d_tokens)
        if inter:
            score += min(3, len(inter))  # 1..3

        if score > best_score:
            best_score = score
            best = d

    # порог: хотя бы какая-то уверенность
    if best and best_score >= 3:
        return best
    return None


def _refresh_devices_for_chat(chat_id: int) -> List[DeviceInfo]:
    # 1) Пытаемся dshow через ffmpeg (самый «правильный» для совместимости/микрофона)
    dshow_videos, _ = _probe_dshow_devices()
    devices: List[DeviceInfo] = []
    for idx, dev in enumerate(dshow_videos, start=1):
        label = f"Камера #{idx}: {dev.name}"
        devices.append(DeviceInfo(name=dev.name, label=label, source="dshow", alt_name=dev.alt_name))

    # 2) Если ffmpeg dshow ничего не выдал (или dshow отсутствует в сборке), fallback: OpenCV индексы
    if not devices:
        devices = _probe_opencv_cameras()

    AVAILABLE_DEVICES[chat_id] = devices
    return devices


def _refresh_audio_devices_for_chat(chat_id: int, dshow_audio: Optional[List[DeviceInfo]] = None) -> List[DeviceInfo]:
    """
    Возвращаем список микрофонов, используя сразу несколько источников:
      - ffmpeg dshow (основной, он же участвует в самом ffmpeg)
      - PowerShell/CIM (резерв, чтобы показать «похожие» устройства пользователю)
      - dshow fallback: audio="default" (важно, когда dshow по каким-то причинам не перечислил аудио, но захват всё равно возможен)

    Критично: для РЕАЛЬНОГО захвата звука ffmpeg-ом нам нужен именно dshow-источник. Поэтому даже если
    PowerShell что-то нашёл, но dshow список пуст, мы добавляем вариант "default".
    """
    if dshow_audio is None:
        _, dshow_audio = _probe_dshow_devices()

    devices: List[DeviceInfo] = []
    seen: set = set()

    def _mark(dev: DeviceInfo) -> bool:
        keys = []
        if dev.name:
            keys.append(dev.name.lower())
        if dev.alt_name:
            keys.append(dev.alt_name.lower())
        if any(k in seen for k in keys if k):
            return False
        for k in keys:
            if k:
                seen.add(k)
        return True

    # 1) DShow через ffmpeg (самое ценное, потому что это те же имена, что нужны ffmpeg)
    for idx, dev in enumerate(dshow_audio or [], start=1):
        device = DeviceInfo(
            name=dev.name,
            label=f"Микрофон #{idx}: {dev.label}",
            source=dev.source,
            alt_name=dev.alt_name,
        )
        if _mark(device):
            devices.append(device)

    has_dshow_entries = any(d.source == "dshow" for d in devices)

    # 2) PowerShell/Win32 резерв (может вернуть другие "человеческие" имена)
    for ps_name in _probe_audio_devices_powershell():
        device = DeviceInfo(name=ps_name, label=f"Микрофон (PS): {ps_name}", source="powershell")
        if _mark(device):
            devices.append(device)

    # 3) ВАЖНО: если dshow ничего не перечислил, но dshow в ffmpeg вообще есть,
    # добавляем "default" (иначе старт всегда уйдёт в video-only, даже если микрофон реально есть).
    if (not has_dshow_entries) and _ffmpeg_has_dshow():
        fallback = DeviceInfo(
            name="default",
            label="Системный микрофон (по умолчанию, dshow: default)",
            source="dshow",
        )
        if _mark(fallback):
            devices.append(fallback)  # последним (экспериментально)м, чтобы было проще выбрать

    AVAILABLE_AUDIO[chat_id] = devices
    return devices

def _render_devices_text(chat_id: int, audio_list: List[DeviceInfo], current_audio: Optional[DeviceInfo] = None) -> str:
    devices = AVAILABLE_DEVICES.get(chat_id) or []
    if not devices:
        hint = "Камеры не найдены."
        if not FFMPEG_PATH:
            hint += " ffmpeg.exe не найден."
        elif not _ffmpeg_has_dshow():
            hint += " Ваша сборка ffmpeg, похоже, без DirectShow (dshow)."
        hint += " Подключите камеру и нажмите «Обновить список устройств»."
        if cv2 is None:
            hint += "\n(А ещё у вас нет OpenCV, поэтому fallback по индексам недоступен.)"
        return hint

    lines = ["Доступные камеры:"]
    for d in devices:
        lines.append(f"- {d.label}")
        if d.source == "dshow" and d.alt_name:
            # Не показываем alt_name по умолчанию (шумно), но это полезно для диагностики
            pass

    if audio_list:
        lines.append("")
        lines.append("Доступные микрофоны:")
        for idx, a in enumerate(audio_list, start=1):
            mark = ""
            if current_audio and (a.name == current_audio.name or a.label == current_audio.label):
                mark = " (выбран)"
            lines.append(f"- {a.label}{mark}")
        lines.append("Выберите микрофон кнопкой ниже. Без выбора микрофона запуск трансляции невозможен.")
    return "\n".join(lines)



def _build_index_html() -> str:
    # Важно:
    # - Без autoplay: воспроизведение только по кнопке "Play" в браузере (user gesture).
    # - Для Chrome/Edge нужен hls.js. Сначала пробуем локальный /static/hls.min.js (если положили рядом),
    #   затем CDN как запасной вариант.
    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Прямая трансляция</title>
  <style>
    body { margin:0; background:#0b1221; color:#e8ecf3; font-family:Arial, sans-serif; display:flex; flex-direction:column; align-items:center; gap:12px; padding:16px; }
    #player { width:100%; max-width:1024px; background:#000; border:1px solid #1f2c45; border-radius:8px; }
    .card { width:100%; max-width:1024px; padding:12px 14px; background:#121a2f; border:1px solid #1f2c45; border-radius:10px; box-shadow:0 10px 30px rgba(0,0,0,0.35); }
    .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    button { padding:10px 12px; border-radius:10px; border:1px solid #1f2c45; background:#0f1a33; color:#e8ecf3; cursor:pointer; }
    button:hover { filter:brightness(1.05); }
    code { background:#0f1a33; padding:2px 6px; border-radius:6px; border:1px solid #1f2c45; }
    .muted { opacity:0.85; font-size:0.95rem; }
  </style>
  <script>
    function loadScript(src) {
      return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.async = true;
        s.onload = () => resolve(true);
        s.onerror = () => reject(new Error('Failed to load: ' + src));
        document.head.appendChild(s);
      });
    }

    document.addEventListener('DOMContentLoaded', async () => {
      const video = document.getElementById('player');
      const status = document.getElementById('status');
      const reloadBtn = document.getElementById('reload');
      const directBtn = document.getElementById('direct');
      const m3u8 = new URL('/hls/stream.m3u8', window.location.href).toString();

      function show(msg) { status.textContent = msg || ''; }

      // Кнопка "открыть m3u8" (для VLC/внешних плееров)
      directBtn.addEventListener('click', () => {
        window.open(m3u8, '_blank');
      });

      let hls = null;

      async function ensureHlsJs() {
        if (window.Hls) return true;

        // 1) локально (если вы положили hls.min.js рядом с exe/проектом, сервер его отдаст)
        try {
          await loadScript('/static/hls.min.js');
          if (window.Hls) return true;
        } catch (_) {}

        // 2) CDN как запасной вариант
        try {
          await loadScript('https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js');
          if (window.Hls) return true;
        } catch (_) {}

        return false;
      }

      function teardown() {
        try {
          if (hls) {
            hls.destroy();
            hls = null;
          }
        } catch (_) {}
      }

      async function attachStream() {
        teardown();

        // Если браузер умеет нативно HLS (Safari/iOS) — просто присваиваем src.
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = m3u8;
          show('Готово. Нажмите ▶ воспроизведение в плеере.');
          return;
        }

        // Для Chrome/Edge нужен hls.js
        const ok = await ensureHlsJs();
        if (!ok) {
          show('Не удалось загрузить hls.js. Откройте поток через VLC (кнопка "Открыть m3u8") или дайте доступ к CDN.');
          return;
        }

        hls = new window.Hls({
          lowLatencyMode: true,
          // живой поток: не копим хвост
          backBufferLength: 10,
          maxBufferLength: 20,
          liveSyncDurationCount: 2,
          liveMaxLatencyDurationCount: 6,
          // иногда помогает на Wi‑Fi с потерями
          enableWorker: true,
        });

        // Держим ссылку, чтобы GC не прибил объект
        window._hls_instance = hls;

        hls.attachMedia(video);

        hls.on(window.Hls.Events.MEDIA_ATTACHED, () => {
          try {
            hls.loadSource(m3u8);
            // не autoplay: пользователь сам жмёт ▶
            show('Подключаю поток… Если зависло — нажмите "Переподключить".');
          } catch (e) {
            show('Ошибка подключения: ' + e);
          }
        });

        hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
          show('Готово. Нажмите ▶ воспроизведение в плеере.');
        });

        hls.on(window.Hls.Events.ERROR, (_, data) => {
          if (!data) return;
          const details = data.details || data.type || 'unknown';
          if (data.fatal) {
            // Фатальные: предлагаем переподключение и пробуем мягкое восстановление
            show('Проблема потока: ' + details + '. Нажмите "Переподключить".');

            try {
              if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
                // переинициализация загрузки
                setTimeout(() => { try { hls.startLoad(); } catch (_) {} }, 800);
              } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
                // восстановление декодера
                try { hls.recoverMediaError(); } catch (_) {}
              }
            } catch (_) {}
          } else {
            // нефатальные просто подсветим, но не будем спамить
            // show('…' + details);
          }
        });
      }

      reloadBtn.addEventListener('click', async () => {
        show('Переподключаю…');
        await attachStream();
      });

      // Готовим источник сразу, но НЕ запускаем воспроизведение.
      // Воспроизведение начнётся только когда пользователь нажмёт ▶ в контролах видео.
      await attachStream();

      // Подсказка, если браузер ругается на воспроизведение (обычно не должно, раз autoplay нет)
      video.addEventListener('error', () => {
        show('Браузер не смог воспроизвести поток. Нажмите "Переподключить" или откройте m3u8 во внешнем плеере.');
      });
    });
  </script>
</head>
<body>
  <div class="card">
    <div class="row">
      <button id="reload">Переподключить</button>
      <button id="direct">Открыть m3u8</button>
      <span class="muted">Воспроизведение стартует только после нажатия ▶ в плеере.</span>
    </div>
    <div id="status" style="margin-top:10px;">Подготавливаю плеер…</div>
  </div>

  <video id="player" controls playsinline preload="none"></video>

  <div class="card muted">
    <div>Если видео не стартует:</div>
    <ol>
      <li>Нажмите <b>Переподключить</b>.</li>
      <li>Проверьте, что страница открыта по <code>http://...</code> и доступ к порту не блокируется брандмауэром.</li>
      <li>Если в сети нет доступа к CDN, положите файл <code>hls.min.js</code> рядом с программой (сервер отдаст его локально).</li>
    </ol>
  </div>
</body>
</html>
"""



def _start_http_server(state: StreamState) -> None:
    """
    Локальный HTTP сервер:
      - /             HTML плеер
      - /hls/...      HLS плейлист/сегменты (правильные MIME + без кэша)
      - /static/...   статические файлы (например, hls.min.js если положили рядом)
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = web.Application()
    hls_dir = state.hls_dir or ""

    # Ищем локальный hls.min.js (опционально). Пользователь может положить файл рядом с EXE/проектом.
    static_candidates = [
        os.path.join(BASE_DIR, "hls.min.js"),
        os.path.join(_MODULE_DIR, "hls.min.js"),
        os.path.join(os.getcwd(), "hls.min.js"),
        os.path.join(os.path.dirname(sys.executable), "hls.min.js"),
    ]
    local_hls_js = next((p for p in static_candidates if p and os.path.isfile(p)), None)

    async def index_handler(_: web.Request) -> web.Response:
        resp = web.Response(text=_build_index_html(), content_type="text/html")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    async def static_handler(request: web.Request) -> web.StreamResponse:
        name = request.match_info.get("name", "")
        if name == "hls.min.js" and local_hls_js:
            resp = web.FileResponse(local_hls_js)
            resp.content_type = "application/javascript"
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            return resp
        raise web.HTTPNotFound()

    def _safe_join(base: str, rel: str) -> str:
        # защита от ../
        rel = rel.lstrip("/\\")
        path = os.path.abspath(os.path.join(base, rel))
        base_abs = os.path.abspath(base)
        if not path.startswith(base_abs):
            raise web.HTTPForbidden()
        return path

    async def hls_handler(request: web.Request) -> web.StreamResponse:
        if not hls_dir:
            raise web.HTTPNotFound()
        name = request.match_info.get("name", "")
        # Ограничим только ожидаемые расширения
        if not (name.endswith(".m3u8") or name.endswith(".m3u") or name.endswith(".ts")):
            raise web.HTTPNotFound()

        path = _safe_join(hls_dir, name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()

        resp = web.FileResponse(path)
        ext = os.path.splitext(name)[1].lower()
        if ext in (".m3u8", ".m3u"):
            resp.content_type = "application/vnd.apple.mpegurl"
        elif ext == ".ts":
            resp.content_type = "video/mp2t"
        else:
            resp.content_type = "application/octet-stream"

        # Для live HLS важно НЕ кэшировать
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        # иногда помогает, если открывают m3u8 не со страницы, а напрямую
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    app.router.add_get("/", index_handler)
    app.router.add_get("/static/{name}", static_handler)
    app.router.add_get("/hls/{name}", hls_handler)

    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", state.port or 0)
    loop.run_until_complete(site.start())

    state.runner = runner
    state.loop = loop
    loop.run_forever()


def _stop_http_server(state: StreamState) -> None:
    loop = state.loop
    runner = state.runner
    if not loop or not runner:
        return

    async def _cleanup():
        with suppress(Exception):
            await runner.cleanup()
        loop.stop()

    try:
        asyncio.run_coroutine_threadsafe(_cleanup(), loop).result(timeout=5)
    except Exception:
        with suppress(Exception):
            loop.stop()


def _safe_hls_dir(base_dir: str) -> str:
    """
    В EXE программа часто лежит в папке без прав на запись.
    Пробуем BASE_DIR, а если нельзя — системный temp.
    """
    try:
        d = tempfile.mkdtemp(prefix="stream_", dir=base_dir)
        return d
    except Exception:
        return tempfile.mkdtemp(prefix="stream_")


def _hls_output_args(hls_dir: str) -> Tuple[str, str, List[str]]:
    os.makedirs(hls_dir, exist_ok=True)
    m3u8_path = os.path.join(hls_dir, "stream.m3u8")
    segment_path = os.path.join(hls_dir, "segment_%03d.ts")
    out = [
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list+independent_segments",
        "-hls_segment_filename", segment_path,
        m3u8_path,
    ]
    return m3u8_path, segment_path, out



def _format_cmd(cmd: List[str]) -> str:
    """Человекочитаемая строка команды (для лога)."""
    def q(a: str) -> str:
        if a is None:
            return ""
        if a == "":
            return '""'
        if re.search(r'[\s"]', a):
            return '"' + a.replace('"', '\"') + '"'
        return a
    return " ".join(q(str(x)) for x in cmd)


def _make_ffmpeg_log_path(chat_id: int, tag: str) -> str:
    """Legacy helper: теперь всё пишем в ОДИН общий лог."""
    return STREAM_LOG_PATH if DEBUG_ENABLED else ""


def _ffmpeg_tail_text(state: StreamState, max_lines: int = 60) -> str:
    try:
        tail = list(state.ffmpeg_stderr_tail)[-max_lines:]
        return "\n".join(tail).strip()
    except Exception:
        return ""


def _dir_snapshot(path: Optional[str], limit: int = 50) -> str:
    if not path:
        return "(нет папки)"
    try:
        if not os.path.isdir(path):
            return "(папки нет)"
        items = sorted(os.listdir(path))[:limit]
        if not items:
            return "(папка пустая)"
        return ", ".join(items)
    except Exception as e:
        return f"(не удалось прочитать папку: {e})"


def _start_ffmpeg_stderr_pump(chat_id: int, state: StreamState, proc: subprocess.Popen, tag: str) -> None:
    """Держим хвост stderr ffmpeg в памяти; в файл пишем ТОЛЬКО при DEBUG_ENABLED (в общий лог)."""
    if not proc.stderr:
        return

    state.ffmpeg_stderr_stop = threading.Event()
    # в debug показываем пользователю путь на общий лог
    if DEBUG_ENABLED:
        state.ffmpeg_stderr_path = STREAM_LOG_PATH

    def _pump() -> None:
        try:
            _slog(logging.DEBUG, f"=== ffmpeg stderr start | tag={tag} ===", chat_id)
            if state.ffmpeg_cmd:
                _slog(logging.DEBUG, "CMD: " + _format_cmd(state.ffmpeg_cmd), chat_id)

            while True:
                if state.ffmpeg_stderr_stop and state.ffmpeg_stderr_stop.is_set():
                    break
                raw = proc.stderr.readline()
                if not raw:
                    break
                line = _decode_best_effort(raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8", "ignore"))
                line = (line or "").rstrip("\r\n")
                if not line:
                    continue
                with suppress(Exception):
                    state.ffmpeg_stderr_tail.append(line)
                _slog(logging.DEBUG, f"[ffmpeg:{tag}] {line}", chat_id)
        except Exception as e:
            _slog(logging.DEBUG, f"ffmpeg stderr pump error: {e}", chat_id)
        finally:
            _slog(logging.DEBUG, f"=== ffmpeg stderr end | tag={tag} ===", chat_id)

    t = threading.Thread(target=_pump, daemon=True)
    state.ffmpeg_stderr_thread = t
    t.start()


def _wait_m3u8_or_fail(state: StreamState, proc: subprocess.Popen, m3u8_path: str, timeout_s: int = 20, chat_id: Optional[int] = None) -> None:
    deadline = time.time() + timeout_s
    _slog(logging.DEBUG, f"wait m3u8: {m3u8_path} (timeout {timeout_s}s)", chat_id)

    while time.time() < deadline:
        if proc.poll() is not None:
            tail = _ffmpeg_tail_text(state)
            more = ""
            if state.ffmpeg_stderr_path:
                more = f"\n(подробный лог: {state.ffmpeg_stderr_path})"
            raise RuntimeError((tail or "ffmpeg завершился во время запуска") + more)

        if os.path.isfile(m3u8_path):
            # Иногда файл появляется пустым на доли секунды, подождём пока станет не нулевым
            with suppress(Exception):
                if os.path.getsize(m3u8_path) > 0:
                    return
            # если размер 0, чуть ждём
        time.sleep(0.35)

    # Timeout
    tail = _ffmpeg_tail_text(state)
    snap = _dir_snapshot(os.path.dirname(m3u8_path))
    more = ""
    if state.ffmpeg_stderr_path:
        more = f"\n(подробный лог: {state.ffmpeg_stderr_path})"
    raise RuntimeError(
        "ffmpeg не успел создать плейлист (stream.m3u8)."
        f"\nПапка HLS: {os.path.dirname(m3u8_path)}"
        f"\nСодержимое папки: {snap}"
        + (f"\n\nПоследние строки ffmpeg:\n{tail}" if tail else "")
        + more
    )


def _start_ffmpeg_dshow(
    chat_id: int,
    state: StreamState,
    video_name: str,
    audio_name: Optional[str],
    extra_input_args: Optional[List[str]] = None,
    video_only: bool = False,
    quote_names: bool = True,
) -> None:
    if not FFMPEG_PATH:
        raise RuntimeError("ffmpeg.exe не найден")

    state.hls_dir = state.hls_dir or _safe_hls_dir(BASE_DIR)
    m3u8_path, _, out_args = _hls_output_args(state.hls_dir)

    # Важно: кавычки в dshow-строке — это НЕ shell quoting, это синтаксис ffmpeg/dshow
    # Но если имя уже содержит кавычки — экранируем.
    def q(s: str) -> str:
        return s.replace('"', r'\"')

    if quote_names:
        input_param = f'video="{q(video_name)}"'
        if (audio_name and not video_only):
            input_param += f':audio="{q(audio_name)}"'
    else:
        input_param = f'video={video_name}'
        if (audio_name and not video_only):
            input_param += f':audio={audio_name}'

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-thread_queue_size", "4096",
        "-f", "dshow",
        "-rtbufsize", "512M",
    ]
    if extra_input_args:
        cmd.extend(extra_input_args)

    cmd.extend(["-i", input_param])

    # кодирование + HLS
    cmd.extend([
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        "-g", "50",
        "-b:v", "2500k",
    ])

    if audio_name and not video_only:
        cmd.extend(["-c:a", "aac", "-ar", "44100", "-b:a", "128k"])
    else:
        cmd.extend(["-an"])

    cmd.extend(out_args)

    state.ffmpeg_cmd = cmd
    state.ffmpeg_stderr_path = STREAM_LOG_PATH if DEBUG_ENABLED else None
    _slog(logging.INFO, f"Starting ffmpeg (dshow). HLS dir: {state.hls_dir}", chat_id)
    _slog(logging.INFO, "CMD: " + _format_cmd(cmd), chat_id)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )
    state.ffmpeg = proc
    _start_ffmpeg_stderr_pump(chat_id, state, proc, "dshow")
    _wait_m3u8_or_fail(state, proc, m3u8_path, chat_id=chat_id)


def _start_ffmpeg_from_opencv_pipe(
    chat_id: int,
    state: StreamState,
    cam_index: int,
    audio_name: Optional[str],
) -> None:
    """
    Fallback: берём кадры OpenCV и льём их в ffmpeg через stdin (rawvideo).
    Это спасает кейсы, когда ffmpeg dshow не умеет/не видит устройства, но камера реально работает в OpenCV.
    """
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) недоступен, fallback невозможен")
    if not FFMPEG_PATH:
        raise RuntimeError("ffmpeg.exe не найден")

    # Открываем камеру. Пробуем DSHOW->MSMF->ANY
    backends = []
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_DSHOW", 0))
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_MSMF", 0))
    backends.append(getattr(cv2, "CAP_ANY", 0))

    cap = None
    for be in backends:
        with suppress(Exception):
            cap = cv2.VideoCapture(cam_index, be)
            if cap and cap.isOpened():
                break
    if not cap or not cap.isOpened():
        with suppress(Exception):
            if cap:
                cap.release()
        raise RuntimeError("Не удалось открыть камеру через OpenCV (индекс)")

    # Вычисляем параметры потока
    w = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3)) or 0) or 1280
    h = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4)) or 0) or 720
    fps = float(cap.get(getattr(cv2, "CAP_PROP_FPS", 5)) or 0.0)
    if fps <= 1 or fps > 120:
        fps = 30.0

    state.hls_dir = state.hls_dir or _safe_hls_dir(BASE_DIR)
    m3u8_path, _, out_args = _hls_output_args(state.hls_dir)

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-thread_queue_size", "4096",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{w}x{h}",
        "-r", f"{fps:.2f}",
        "-i", "pipe:0",
    ]

    # Аудио (если dshow видит микрофон). Отдельным входом.
    # Если не получится, стартанём без звука (ниже fallback по ошибке).
    if audio_name:
        def q(s: str) -> str:
            return s.replace('"', r'\"')
        cmd.extend([
            "-f", "dshow",
            "-rtbufsize", "256M",
            "-i", f'audio="{q(audio_name)}"',
        ])

    # Map: 0:v + (1:a)
    cmd.extend(["-map", "0:v:0"])
    if audio_name:
        cmd.extend(["-map", "1:a:0"])
    else:
        cmd.extend(["-an"])

    cmd.extend([
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        "-g", "50",
        "-b:v", "2500k",
    ])

    if audio_name:
        cmd.extend(["-c:a", "aac", "-ar", "44100", "-b:a", "128k"])
    cmd.extend(out_args)

    state.ffmpeg_cmd = cmd
    state.ffmpeg_stderr_path = STREAM_LOG_PATH if DEBUG_ENABLED else None
    _slog(logging.INFO, f"Starting ffmpeg (opencv pipe). cam_index={cam_index} HLS dir: {state.hls_dir}", chat_id)
    _slog(logging.INFO, "CMD: " + _format_cmd(cmd), chat_id)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
        text=False,
    )
    state.ffmpeg = proc
    _start_ffmpeg_stderr_pump(chat_id, state, proc, "opencv_pipe")

    state.opencv_capture = cap
    state.opencv_stop = threading.Event()

    def _writer():
        try:
            while not state.opencv_stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                # frame: BGR uint8
                try:
                    if proc.stdin:
                        proc.stdin.write(frame.tobytes())
                except Exception:
                    break
        finally:
            with suppress(Exception):
                if proc.stdin:
                    proc.stdin.close()
            with suppress(Exception):
                cap.release()

    t = threading.Thread(target=_writer, daemon=True)
    state.opencv_thread = t
    t.start()

    # Ждём плейлист
    _wait_m3u8_or_fail(state, proc, m3u8_path, chat_id=chat_id)


def _stop_ffmpeg(state: StreamState) -> None:
    # Сначала глушим OpenCV writer, чтобы не писать в закрытый stdin
    if state.opencv_stop:
        state.opencv_stop.set()
    if state.opencv_thread and state.opencv_thread.is_alive():
        state.opencv_thread.join(timeout=2)
    state.opencv_thread = None
    state.opencv_stop = None

    with suppress(Exception):
        if state.opencv_capture is not None:
            # type: ignore[attr-defined]
            state.opencv_capture.release()  # pragma: no cover
    state.opencv_capture = None

    # Останавливаем слив stderr ffmpeg
    if state.ffmpeg_stderr_stop:
        with suppress(Exception):
            state.ffmpeg_stderr_stop.set()
    if state.ffmpeg_stderr_thread and state.ffmpeg_stderr_thread.is_alive():
        with suppress(Exception):
            state.ffmpeg_stderr_thread.join(timeout=1)
    state.ffmpeg_stderr_thread = None
    state.ffmpeg_stderr_stop = None

    proc = state.ffmpeg
    if proc and proc.poll() is None:
        with suppress(Exception):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            with suppress(Exception):
                proc.kill()
    state.ffmpeg = None


def _ffmpeg_watchdog(chat_id: int, bot, state: StreamState) -> None:
    proc = state.ffmpeg
    if not proc:
        return
    rc = proc.wait()
    if state.stopping:
        return
    state.status = "error"
    state.last_error = f"ffmpeg остановился (код {rc})"
    try:
        asyncio.run_coroutine_threadsafe(
            bot.send_message(
                chat_id,
                f"Внимание: трансляция остановлена: {state.last_error}",
                reply_markup=get_stream_keyboard(chat_id),
            ),
            bot.loop,
        )
    except Exception:
        pass
    stop_stream(chat_id, notify=False)


def get_stream_keyboard(chat_id: int) -> types.ReplyKeyboardMarkup:
    """Клавиатура модуля трансляции.

    Требование UX:
      - когда трансляция идёт (running/starting) показываем только «Выключить трансляцию»
        и кнопку «Свернуть модуль» (уйти в утилиты, не останавливая поток).
      - когда трансляция не запущена — полный набор кнопок настройки/запуска.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    state = STREAM_STATE.get(chat_id)
    is_active = bool(state and (state.status in ("running", "starting") or state.stopping))

    if is_active:
        kb.add(types.KeyboardButton(BTN_STOP))
        kb.add(types.KeyboardButton(BTN_MINIMIZE))
        return kb

    kb.add(types.KeyboardButton(BTN_START))
    kb.add(types.KeyboardButton(BTN_PICK_CAMERA))
    kb.add(types.KeyboardButton(BTN_PICK_AUDIO))
    kb.add(types.KeyboardButton(BTN_REFRESH))
    kb.add(types.KeyboardButton(BTN_BACK_MENU))
    return kb


def get_camera_keyboard(chat_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    devices = AVAILABLE_DEVICES.get(chat_id, [])
    for dev in devices:
        kb.add(types.KeyboardButton(dev.label))
    kb.add(types.KeyboardButton(BTN_BACK_STREAM))
    return kb


def get_audio_keyboard(chat_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    devices = AVAILABLE_AUDIO.get(chat_id, [])
    for dev in devices:
        kb.add(types.KeyboardButton(dev.label))
    kb.add(types.KeyboardButton(BTN_BACK_STREAM))
    return kb


def stop_stream(chat_id: int, notify: bool = True) -> None:
    owner_id = _stream_window_owner(chat_id)
    state = STREAM_STATE.get(chat_id)
    if not state:
        close_stream_control_window(owner_id)
        return
    try:
        state.stopping = True
        _stop_ffmpeg(state)
        _stop_http_server(state)
        if state.server_thread and state.server_thread.is_alive():
            state.server_thread.join(timeout=3)
        _cleanup_hls(state.hls_dir)
        state.hls_dir = None
        state.port = None
        state.loop = None
        state.runner = None
        state.server_thread = None
        state.status = "idle"
        state.stopping = False
        if notify:
            # Тут можно добавить уведомление, но сейчас оно отправляется из обработчиков.
            pass
    finally:
        close_stream_control_window(owner_id)


def _start_http_and_watchdog(chat_id: int, bot, state: StreamState) -> None:
    # HTTP сервер поднимаем заранее, чтобы ссылка была доступна сразу,
    # а watchdog стартуем после того как появится state.ffmpeg.
    server_thread = threading.Thread(target=_start_http_server, args=(state,), daemon=True)
    server_thread.start()
    state.server_thread = server_thread
    time.sleep(0.5)

def _start_watchdog(chat_id: int, bot, state: StreamState) -> None:
    if not state.ffmpeg:
        return
    threading.Thread(target=_ffmpeg_watchdog, args=(chat_id, bot, state), daemon=True).start()

def _start_stream_sync(chat_id: int, bot, state: StreamState) -> str:
    if not state.camera:
        raise RuntimeError("Камера не выбрана.")

    # Запуск запрещён, пока не выбран микрофон.
    # Даже если позже ffmpeg не сможет открыть аудио и уйдём в video-only режим,
    # выбор источника звука должен быть сделан пользователем явно.
    if not state.audio:
        raise RuntimeError("Микрофон не выбран.")

    # Список устройств на момент старта (важно: устройства могли измениться/отвалиться)
    dshow_videos, dshow_audio = _probe_dshow_devices()
    audio_devices_all = _refresh_audio_devices_for_chat(chat_id, dshow_audio=dshow_audio)
    # Для фактического захвата звука ffmpeg-ом используем только dshow-устройства.
    audio_devices = [d for d in (audio_devices_all or []) if d.source == "dshow"]

    # Подбираем микрофон: сперва выбранный (с учётом alt_name и PS-маппинга).
    audio_device: Optional[DeviceInfo] = None
    if state.audio:
        # если по каким-то причинам в состоянии осталось PS-устройство, попробуем сопоставить его с dshow
        if state.audio.source not in ("dshow", "opencv") and state.audio.name != "default":
            with suppress(Exception):
                dshow_list: List[DeviceInfo] = [
                    DeviceInfo(name=da.name, label=da.name, source="dshow", alt_name=da.alt_name)
                    for da in (dshow_audio or [])
                ]
                m = _best_dshow_match_for_ps(state.audio, dshow_list)
                if m:
                    state.audio = DeviceInfo(name=m.name, label=state.audio.label, source="dshow", alt_name=m.alt_name)

        for dev in audio_devices:
            if _audio_dev_matches(dev, state.audio):
                audio_device = dev
                break

    # Важно: без явного выбора микрофона НЕ подставляем первый попавшийся.
    # Если выбранный микрофон не удалось сопоставить с dshow (или dshow пустой),
    # дальше попробуем стартовать, а при проблемах уйдём в video-only режим (план B ниже).
    # Подготовка временной папки и порта
    state.hls_dir = _safe_hls_dir(BASE_DIR)
    state.port = _pick_free_port()
    state.status = "starting"
    state.stopping = False
    # state.audio хранит выбранный пользователем микрофон. Фактически использованный
    # микрофон (если открылся) будет привязан ниже через _bind_audio().

    def _bind_audio(dev: Optional[DeviceInfo], used_name: Optional[str]) -> Optional[DeviceInfo]:
        if not dev or not used_name:
            return None
        if dev.name == used_name:
            return dev
        return DeviceInfo(name=used_name, label=dev.label, source=dev.source, alt_name=dev.alt_name)

    audio_devices_to_try: List[Optional[DeviceInfo]] = []
    if audio_device:
        audio_devices_to_try.append(audio_device)
    for dev in audio_devices:
        if not audio_device or dev.name != audio_device.name:
            audio_devices_to_try.append(dev)
    if not audio_devices_to_try:
        audio_devices_to_try.append(None)

    audio_variants: List[Tuple[Optional[DeviceInfo], List[Optional[str]]]] = []
    for dev in audio_devices_to_try:
        names: List[Optional[str]] = []
        if dev and dev.name:
            names.append(dev.name)
        if dev and dev.alt_name and dev.alt_name != dev.name:
            names.append(dev.alt_name)
        if not names:
            names.append(None)
        audio_variants.append((dev, names))

    # Финальный «план Б»: попробовать запуститься вообще без аудио (чтобы поток не умер),
    # если ни один микрофон не откроется. Это особенно важно на системах, где dshow-список пустой
    # или Windows запрещает доступ к микрофону.
    audio_variants.append((None, [None]))

    audio_names_flat: List[str] = []
    for _, names in audio_variants:
        for nm in names:
            if nm:
                audio_names_flat.append(nm)

    audio_fail_reason: Optional[str] = None

# Старт HTTP (он нужен независимо от способа захвата)
    _start_http_and_watchdog(chat_id, bot, state)

    try:
        # =========================
        # ПУТЬ 1: dshow (стандарт)
        # =========================
        if state.camera.source == "dshow":
            # Находим alt_name для выбранного friendly name (если есть)
            alt = None
            for dv in dshow_videos:
                if dv.name == state.camera.name:
                    alt = dv.alt_name
                    break

            # Набор вариантов запуска (на разных ноутбуках/камерах могут быть капризы)
            # 1) без принудительных параметров
            # 2) типовые режимы 720p@30 / 480p@25
            variants: List[List[str]] = [
                [],
                ["-framerate", "30", "-video_size", "1280x720"],
                ["-framerate", "25", "-video_size", "640x480"],
            ]

            # сначала пробуем friendly name, потом alt_name (если есть)
            name_candidates = [state.camera.name]
            if alt:
                name_candidates.append(alt)

            last_exc: Optional[Exception] = None
            for nm in name_candidates:
                for extra in variants:
                    for quote_names in (True, False):
                        for audio_dev, audio_names in audio_variants:
                            for audio_name in audio_names:
                                try:
                                    _log(
                                        f"[stream] ffmpeg(dshow) start: video={nm} audio={'yes' if audio_name else 'no'} extra={extra} quote={quote_names}"
                                    )
                                    _start_ffmpeg_dshow(
                                        chat_id,
                                        state,
                                        nm,
                                        audio_name,
                                        extra_input_args=extra,
                                        video_only=not bool(audio_name),
                                        quote_names=quote_names,
                                    )
                                    state.audio = _bind_audio(audio_dev, audio_name)
                                    state.last_audio_error = (None if audio_name else audio_fail_reason)
                                    state.status = "running"
                                    url = f"http://{_get_local_ip()}:{state.port}/"
                                    state.last_url = url
                                    _start_watchdog(chat_id, bot, state)
                                    return url
                                except Exception as e:
                                    last_exc = e
                                    try:
                                        err_txt = str(e).lower()
                                    except Exception:
                                        err_txt = ""
                                    audio_related = any(
                                        k in err_txt for k in [
                                            "audio", "could not find audio", "audio only", "invalid audio", "was not found"
                                        ]
                                    )
                                    if audio_related:
                                        # Запоминаем последнюю «аудио-причину» (полезно, если в итоге уйдём в video-only).
                                        if audio_name:
                                            audio_fail_reason = str(e)
                                        # Пробуем следующий вариант аудио (alt_name/PS/без звука)
                                        continue
                                    # Если ошибка не связана с аудио - пробуем следующую комбинацию камеры/параметров
                                    continue

            # Если dshow провалился - отдаём максимально полезную ошибку
            raise RuntimeError(
                "ffmpeg не смог открыть выбранную камеру через DirectShow.\n"
                "Варианты причин: камера занята другим приложением, имя содержит спец-символы/юникод, "
                "или для камеры нужен другой режим (разрешение/частота).\n"
                f"Деталь: {last_exc}"
            )

        # ============================================
        # ПУТЬ 2: OpenCV -> FFmpeg pipe (fallback)
        # ============================================
        if state.camera.source == "opencv":
            if not FFMPEG_PATH:
                raise RuntimeError("ffmpeg.exe не найден.")
            if cv2 is None:
                raise RuntimeError("OpenCV (cv2) недоступен, fallback невозможен.")
            try:
                cam_index = int(state.camera.name)
            except Exception:
                cam_index = 0

                        # Для opencv-pipe нам нужен один конкретный аудио-идентификатор.
            # Предпочитаем dshow alternative name (ASCII, стабильнее и не ломается на кодировках),
            # затем dshow friendly-name, затем PS-имя, и только в самом конце — экспериментальный 'default'.
            audio_name_for_pipe: Optional[str] = None
            dshow_devs = [d for d in audio_devices_to_try if d and d.source == "dshow"]

            # 1) dshow alt_name
            for d in dshow_devs:
                if d.alt_name:
                    audio_name_for_pipe = d.alt_name
                    break

            # 2) dshow friendly (кроме default)
            if not audio_name_for_pipe:
                for d in dshow_devs:
                    if d.name and d.name.lower() != "default":
                        audio_name_for_pipe = d.name
                        break

            # 3) любое не-default имя (в т.ч. из PowerShell)
            if not audio_name_for_pipe:
                for n in audio_names_flat:
                    if n and n.lower() != "default":
                        audio_name_for_pipe = n
                        break

            # 4) default (последним)
            if (not audio_name_for_pipe) and dshow_devs:
                for d in dshow_devs:
                    if d.name and d.name.lower() == "default":
                        audio_name_for_pipe = d.name
                        break


            # Если ffmpeg dshow всё-таки видит микрофон - добавим звук, иначе без.
            # (тут важно: даже если dshow видео не видит, аудио иногда видит, а иногда нет)
            _log(f"[stream] ffmpeg(opencv-pipe) start: cam_index={cam_index} audio={'yes' if audio_name_for_pipe else 'no'}")
            try:
                _start_ffmpeg_from_opencv_pipe(
                    chat_id,
                    state,
                    cam_index=cam_index,
                    audio_name=audio_name_for_pipe,
                )
                state.audio = _bind_audio(audio_device, audio_name_for_pipe)
                state.last_audio_error = None if audio_name_for_pipe else audio_fail_reason
            except Exception as e:
                # Если упало из-за аудио, повторяем без звука
                _log(f"[stream] opencv-pipe failed with audio, retry video-only: {e}")
                _start_ffmpeg_from_opencv_pipe(chat_id, state, cam_index=cam_index, audio_name=None)
                state.audio = None
                audio_fail_reason = str(e)

            state.last_audio_error = audio_fail_reason
            state.status = "running"
            url = f"http://{_get_local_ip()}:{state.port}/"
            state.last_url = url
            _start_watchdog(chat_id, bot, state)
            return url

        raise RuntimeError("Неизвестный источник камеры.")
    except Exception:
        # На любой ошибке подчистим то, что уже подняли
        with suppress(Exception):
            _stop_ffmpeg(state)
        with suppress(Exception):
            _stop_http_server(state)
        if state.server_thread and state.server_thread.is_alive():
            state.server_thread.join(timeout=1)
        _cleanup_hls(state.hls_dir)
        state.hls_dir = None
        state.port = None
        state.status = "idle"
        state.stopping = False
        raise


async def start_stream(message: types.Message) -> None:
    chat_id = message.chat.id
    state = _ensure_state(chat_id)

    # Если кнопку «Включить трансляцию» нажали вне меню, всё равно покажем предупреждение.
    if not stream_mode.get(chat_id, False):
        await message.answer(LEGAL_NOTICE_TEXT, reply_markup=get_stream_keyboard(chat_id))

    devices_now = _refresh_devices_for_chat(chat_id)
    if state.camera and all(d.label != state.camera.label for d in devices_now):
        state.camera = None
        await message.answer(
            "Ранее выбранная камера недоступна. Выберите камеру заново.",
            reply_markup=get_stream_keyboard(chat_id),
        )
        pending_start_after_pick[chat_id] = True
        await prompt_camera_selection(message, auto_start=True)
        return

    if state.status == "running":
        await message.answer(
            f"Трансляция уже запущена.\nСсылка: {state.last_url}",
            reply_markup=get_stream_keyboard(chat_id),
        )
        return

    if not state.camera:
        pending_start_after_pick[chat_id] = True
        await prompt_camera_selection(message, auto_start=True)
        return

    # Требование: запуск запрещён, пока НЕ выбраны камера И микрофон.
    # Поэтому:
    # - если микрофонов нет -> не запускаем
    # - если микрофон не выбран -> отправляем в меню выбора
    # - если выбранный микрофон исчез -> просим выбрать заново
    audio_now = _refresh_audio_devices_for_chat(chat_id)
    if not audio_now:
        await message.answer(
            "Микрофоны не найдены. Подключите микрофон и нажмите «Обновить список устройств».\n"
            "Запуск трансляции без микрофона запрещён.",
            reply_markup=get_stream_keyboard(chat_id),
        )
        return

    if not state.audio:
        pending_start_after_pick[chat_id] = True
        await prompt_audio_selection(message)
        return

    if state.audio and all(not _audio_dev_matches(dev, state.audio) for dev in audio_now):
        state.audio = None
        await message.answer(
            "Ранее выбранный микрофон недоступен. Выберите микрофон заново.",
            reply_markup=get_stream_keyboard(chat_id),
        )
        pending_start_after_pick[chat_id] = True
        await prompt_audio_selection(message)
        return

    # мягкая диагностика: если ffmpeg не найден или без dshow, скажем заранее, но не блокируем (opencv fallback может выручить)
    if not FFMPEG_PATH:
        await message.answer(
            "Внимание: ffmpeg.exe не найден. Положите ffmpeg.exe рядом с EXE/проектом (или в ffmpeg-7.1/bin) и повторите.",
            reply_markup=get_stream_keyboard(chat_id),
        )
        return

    try:
        _log(f"Запуск трансляции: chat_id={chat_id}, камера={state.camera.label if state.camera else 'нет'}")
        url = await asyncio.to_thread(_start_stream_sync, chat_id, message.bot, state)
        audio_note = (
            f"\nМикрофон: {state.audio.label}"
            if state.audio
            else ("\nВнимание: звук не удалось включить, поток идёт без микрофона."
                  + (f"\nПричина: {state.last_audio_error}" if state.last_audio_error else ""))
        )
        _show_stream_control_window_for_chat(chat_id, message.bot, state)
        await message.answer(
            f"Трансляция запущена.\nСсылка: {url}\nОткройте её в браузере, чтобы увидеть поток.{audio_note}",
            reply_markup=get_stream_keyboard(chat_id),
        )
    except Exception as e:
        state.status = "idle"
        state.last_error = str(e)
        _cleanup_hls(state.hls_dir)
        state.hls_dir = None
        await message.answer(
            f"Не удалось запустить трансляцию: {e}",
            reply_markup=get_stream_keyboard(chat_id),
        )


async def prompt_camera_selection(message: types.Message, auto_start: bool = False) -> None:
    chat_id = message.chat.id
    state = _ensure_state(chat_id)
    # режимы выбора не должны конфликтовать
    audio_select_mode[chat_id] = False
    devices = _refresh_devices_for_chat(chat_id)
    audio_list = _refresh_audio_devices_for_chat(chat_id)
    text = _render_devices_text(chat_id, audio_list, state.audio)
    if auto_start:
        text += f"\n\nВы выбираете камеру для запуска трансляции.\nВажно: автозапуска НЕТ. После выбора камеры/микрофона нажмите «{BTN_START}»."
    if not devices:
        text += "\n\nНажмите «Обновить список устройств» после подключения камеры."
    camera_select_mode[chat_id] = True
    await message.answer(text, reply_markup=get_camera_keyboard(chat_id))


async def prompt_audio_selection(message: types.Message) -> None:
    chat_id = message.chat.id
    state = _ensure_state(chat_id)
    audio_devices = _refresh_audio_devices_for_chat(chat_id)
    audio_select_mode[chat_id] = True
    camera_select_mode[chat_id] = False

    if not audio_devices:
        await message.answer(
            "Микрофоны не найдены. Подключите устройство и нажмите «Обновить список устройств».",
            reply_markup=get_audio_keyboard(chat_id),
        )
        return

    lines = [
        "Выберите микрофон из списка ниже (без микрофона запуск трансляции невозможен):",
        f"Важно: после выбора устройств трансляция НЕ стартует автоматически, запуск только по кнопке «{BTN_START}».",
        "Подсказка: лучше выбирать пункты вида «Микрофон #...». Строки «(PS)» это резерв и могут не совпадать с тем, что открывает ffmpeg.",
    ]
    for dev in audio_devices:
        mark = ""
        if state.audio and (state.audio.name == dev.name or state.audio.label == dev.label):
            mark = " (выбран)"
        lines.append(f"- {dev.label}{mark}")
    lines.append("\nНажмите на нужный вариант.")
    await message.answer("\n".join(lines), reply_markup=get_audio_keyboard(chat_id))


async def handle_camera_pick(message: types.Message) -> None:
    chat_id = message.chat.id

    devices = AVAILABLE_DEVICES.get(chat_id, [])
    picked = None
    for dev in devices:
        if message.text == dev.label:
            picked = dev
            break

    if not picked:
        await message.answer(
            "Камера не распознана. Выберите из списка или обновите устройства.",
            reply_markup=get_camera_keyboard(chat_id),
        )
        return

    state = _ensure_state(chat_id)
    state.camera = picked
    camera_select_mode[chat_id] = False
    audio_select_mode[chat_id] = False

    # Если выбор камеры был частью сценария "Нажал Включить трансляцию" — НЕ запускаем автоматически.
    if pending_start_after_pick.get(chat_id, False):
        # Если микрофон ещё не выбран — продолжаем выбор микрофона (без запуска).
        if not state.audio:
            await message.answer(
                f"Выбрана камера: {picked.label}\nТеперь выберите микрофон. Автозапуска нет 🙂",
                reply_markup=get_stream_keyboard(chat_id),
            )
            await prompt_audio_selection(message)
            return

        # Камера + микрофон уже выбраны -> сообщаем, что можно запускать вручную
        pending_start_after_pick.pop(chat_id, None)
        await message.answer(
            f"Выбрана камера: {picked.label}\n\n✅ Камера и микрофон выбраны. Трансляция готова к запуску.\n"
            f"Нажмите «{BTN_START}».",
            reply_markup=get_stream_keyboard(chat_id),
        )
        return

    # Обычный ручной выбор камеры
    await message.answer(
        f"Выбрана камера: {picked.label}",
        reply_markup=get_stream_keyboard(chat_id),
    )



async def handle_audio_pick(message: types.Message) -> None:
    chat_id = message.chat.id
    devices = AVAILABLE_AUDIO.get(chat_id, [])
    picked: Optional[DeviceInfo] = None
    for dev in devices:
        if message.text == dev.label:
            picked = dev
            break
    if not picked:
        await message.answer(
            "Микрофон не распознан. Выберите из списка или обновите устройства.",
            reply_markup=get_audio_keyboard(chat_id),
        )
        return

    # Режимы должны быть взаимоисключающими
    audio_select_mode[chat_id] = False
    camera_select_mode[chat_id] = False

    resolved = picked
    extra_hint = ""

    # Если пользователь выбрал PS-строку, попробуем сопоставить её с реальным dshow-устройством,
    # иначе ffmpeg может не открыть микрофон (PS имена не всегда совпадают с dshow).
    if picked.source not in ("dshow", "opencv") and picked.name != "default":
        with suppress(Exception):
            _, dshow_audio = _probe_dshow_devices()
            dshow_list: List[DeviceInfo] = [
                DeviceInfo(name=da.name, label=da.name, source="dshow", alt_name=da.alt_name)
                for da in (dshow_audio or [])
            ]
            m = _best_dshow_match_for_ps(picked, dshow_list)
            if m:
                # оставляем «человеческую» метку из списка, но используем реальное имя для ffmpeg
                resolved = DeviceInfo(name=m.name, label=picked.label, source="dshow", alt_name=m.alt_name)
            else:
                # Если dshow ничего не дал, но dshow в ffmpeg есть, лучше ставить default, иначе будет video-only.
                if _ffmpeg_has_dshow():
                    resolved = DeviceInfo(
                        name="default",
                        label="Системный микрофон (по умолчанию, dshow: default)",
                        source="dshow",
                    )
                    extra_hint = "\nPS-имя не удалось сопоставить с dshow, поэтому поставил микрофон по умолчанию."

    state = _ensure_state(chat_id)
    state.audio = resolved

    note = ""
    if state.status == "running":
        note = "\nТрансляция уже идёт: остановите и запустите заново, чтобы применить новый микрофон."

    msg = f"Выбран микрофон: {resolved.label}.{note}{extra_hint}"

    # Если выбор микрофона был частью сценария "Нажал Включить трансляцию" — НЕ запускаем автоматически.
    if pending_start_after_pick.get(chat_id, False):
        if not state.camera:
            await message.answer(
                msg + "\nТеперь выберите камеру. Автозапуска нет 🙂",
                reply_markup=get_stream_keyboard(chat_id),
            )
            await prompt_camera_selection(message, auto_start=True)
            return

        pending_start_after_pick.pop(chat_id, None)
        await message.answer(
            msg + f"\n\n✅ Камера: {state.camera.label}\n"
                  f"Трансляция готова к запуску. Нажмите «{BTN_START}».",
            reply_markup=get_stream_keyboard(chat_id),
        )
        return

    await message.answer(
        msg,
        reply_markup=get_stream_keyboard(chat_id),
    )

async def handle_stop_stream(message: types.Message) -> None:
    chat_id = message.chat.id
    state = STREAM_STATE.get(chat_id)
    if not state or state.status == "idle":
        await message.answer("Трансляция уже остановлена.", reply_markup=get_stream_keyboard(chat_id))
        return
    _log(f"Остановка трансляции: chat_id={chat_id}")
    stop_stream(chat_id, notify=False)
    await message.answer("Трансляция остановлена.", reply_markup=get_stream_keyboard(chat_id))


async def handle_refresh_devices(message: types.Message) -> None:
    chat_id = message.chat.id
    state = _ensure_state(chat_id)
    # после обновления выводим основное меню трансляции, поэтому сбрасываем режимы выбора
    camera_select_mode[chat_id] = False
    audio_select_mode[chat_id] = False
    devices = _refresh_devices_for_chat(chat_id)
    audio_list = _refresh_audio_devices_for_chat(chat_id)
    text = _render_devices_text(chat_id, audio_list, state.audio)
    if devices:
        text += "\n\nВыберите камеру и микрофон, затем включите трансляцию."
    if audio_list:
        text += "\nКнопка «Выбрать микрофон» переключает источник звука."
    await message.answer(text, reply_markup=get_stream_keyboard(chat_id))


async def enter_stream_menu(message: types.Message) -> None:
    chat_id = message.chat.id
    stream_mode[chat_id] = True
    camera_select_mode[chat_id] = False
    audio_select_mode[chat_id] = False
    pending_start_after_pick.pop(chat_id, None)
    state = _ensure_state(chat_id)

    status_text = "Готов к запуску."
    if not FFMPEG_PATH:
        status_text = (
            "ffmpeg.exe не найден. Положите ffmpeg.exe рядом с исполняемым файлом (или рядом с папкой moduls) "
            "либо в ffmpeg-7.1/bin, затем обновите список устройств."
        )
    else:
        if not _ffmpeg_has_dshow():
            status_text += " (Внимание: ваша сборка ffmpeg может быть без DirectShow/dshow. Тогда будет работать только OpenCV-fallback, если он установлен.)"

    if state.status == "running":
        status_text = f"Трансляция активна: {state.last_url}"
    elif state.status == "starting":
        status_text = "Трансляция подготавливается..."
    elif state.last_error:
        status_text = f"Были ошибки: {state.last_error}"

    if state.status in ("running", "starting"):
        buttons_hint = f"Кнопки: «{BTN_STOP}» и «{BTN_MINIMIZE}» (остановить или свернуть)."
    else:
        buttons_hint = (
            f"Кнопки: «{BTN_START}», «{BTN_PICK_CAMERA}», «{BTN_PICK_AUDIO}», «{BTN_REFRESH}», «{BTN_BACK_MENU}»."
        )

    await message.answer(
        f"{LEGAL_NOTICE_TEXT}\n\nМеню трансляции.\n{status_text}\n\n{buttons_hint}",
        reply_markup=get_stream_keyboard(chat_id),
    )



def register_handlers(dp: Dispatcher) -> None:
    @dp.message_handler(lambda m: m.text == ENTRY_TITLE and _is_authorized(m))
    async def _entry(message: types.Message):
        await enter_stream_menu(message)

    @dp.message_handler(lambda m: m.text == BTN_BACK_MENU and _is_authorized(m))
    async def _back(message: types.Message):
        chat_id = message.chat.id
        stream_mode.pop(chat_id, None)
        camera_select_mode.pop(chat_id, None)
        audio_select_mode.pop(chat_id, None)
        pending_start_after_pick.pop(chat_id, None)

        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                "Модуль свёрнут. Трансляция продолжает работать 🟢",
                reply_markup=get_utilities_keyboard(),
            )
        else:
            await message.answer("Возврат в утилиты.", reply_markup=get_utilities_keyboard())

    @dp.message_handler(lambda m: m.text == BTN_MINIMIZE and _is_authorized(m))
    async def _minimize(message: types.Message):
        chat_id = message.chat.id
        # «Сворачивание» = выйти в утилиты, но трансляцию НЕ трогаем
        stream_mode.pop(chat_id, None)
        camera_select_mode.pop(chat_id, None)
        audio_select_mode.pop(chat_id, None)
        pending_start_after_pick.pop(chat_id, None)

        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                "Модуль свёрнут. Трансляция продолжает работать 🟢",
                reply_markup=get_utilities_keyboard(),
            )
        else:
            await message.answer("Модуль свёрнут.", reply_markup=get_utilities_keyboard())

    @dp.message_handler(lambda m: m.text == BTN_START and _is_authorized(m))
    async def _start(message: types.Message):
        await start_stream(message)

    @dp.message_handler(lambda m: m.text == BTN_STOP and _is_authorized(m))
    async def _stop(message: types.Message):
        await handle_stop_stream(message)

    @dp.message_handler(lambda m: m.text == BTN_PICK_CAMERA and _is_authorized(m))
    async def _pick(message: types.Message):
        chat_id = message.chat.id
        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                "Трансляция уже идёт. Чтобы сменить камеру, сначала остановите поток.",
                reply_markup=get_stream_keyboard(chat_id),
            )
            return
        await prompt_camera_selection(message, auto_start=False)

    @dp.message_handler(lambda m: m.text == BTN_PICK_AUDIO and _is_authorized(m))
    async def _pick_audio(message: types.Message):
        chat_id = message.chat.id
        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                "Трансляция уже идёт. Чтобы сменить микрофон, сначала остановите поток.",
                reply_markup=get_stream_keyboard(chat_id),
            )
            return
        await prompt_audio_selection(message)

    @dp.message_handler(lambda m: m.text == BTN_REFRESH and _is_authorized(m))
    async def _refresh(message: types.Message):
        chat_id = message.chat.id
        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                "Трансляция уже идёт. Обновление устройств доступно после остановки потока.",
                reply_markup=get_stream_keyboard(chat_id),
            )
            return
        await handle_refresh_devices(message)

    @dp.message_handler(lambda m: m.text == BTN_BACK_STREAM and _is_authorized(m))
    async def _back_from_pick(message: types.Message):
        chat_id = message.chat.id
        camera_select_mode[chat_id] = False
        audio_select_mode[chat_id] = False
        pending_start_after_pick.pop(chat_id, None)
        await message.answer("Возвращаю меню трансляции.", reply_markup=get_stream_keyboard(chat_id))

    @dp.message_handler(lambda m: _is_authorized(m) and camera_select_mode.get(m.chat.id, False))
    async def _handle_pick(message: types.Message):
        await handle_camera_pick(message)

    @dp.message_handler(lambda m: _is_authorized(m) and audio_select_mode.get(m.chat.id, False))
    async def _handle_audio_pick(message: types.Message):
        await handle_audio_pick(message)

    @dp.message_handler(lambda m: _is_authorized(m) and stream_mode.get(m.chat.id, False), content_types=["text"])
    async def _fallback(message: types.Message):
        chat_id = message.chat.id
        state = STREAM_STATE.get(chat_id)
        if state and state.status in ("running", "starting"):
            await message.answer(
                f"Сейчас поток активен. Доступно: «{BTN_STOP}» или «{BTN_MINIMIZE}».",
                reply_markup=get_stream_keyboard(chat_id),
            )
            return

        await message.answer(
            "Используйте кнопки выбора устройств, запуск/остановку, обновление списка или выход.",
            reply_markup=get_stream_keyboard(chat_id),
        )
