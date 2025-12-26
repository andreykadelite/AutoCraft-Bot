#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import configparser
from pathlib import Path

import asyncio
try:
    from qasync import QEventLoop  # type: ignore
    _HAS_QASYNC = True
except Exception:
    _HAS_QASYNC = False
    class QEventLoop(asyncio.AbstractEventLoop):  # type: ignore
        pass

import socket
import random
import argparse
import platform
import threading

from aiohttp import web
import aiohttp
import winsound

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QStyleFactory,
    QPlainTextEdit, QShortcut, QSizePolicy, QStyle
)
from PyQt5.QtGui import QPalette, QColor, QFont, QKeySequence, QIcon
from PyQt5.QtCore import Qt, QObject, pyqtSignal, pyqtSlot

from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# === Конфигурация / пути ===
base_dir = Path(__file__).parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))
parent_dir = str(base_dir.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

conf = configparser.ConfigParser()
conf.read(base_dir / 'config.ini', encoding='utf-8')
TOKEN = conf.get('credentials', 'token', fallback=None)
debug_enabled = conf.getboolean('credentials', 'debug', fallback=False)

# === Логирование ===
# Логи всегда рядом с основным скриптом/EXE:
# берём папку, из которой запущена программа (sys.argv[0]),
# а если вдруг не получилось — падаем обратно к каталогу модуля.
try:
    root_dir = Path(os.path.abspath(sys.argv[0])).parent
except Exception:
    root_dir = base_dir

log_dir = root_dir / 'log'
log_dir.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("modulsendmess")
logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

fh = logging.FileHandler(str(log_dir / 'modulsendmess.log'), encoding='utf-8')
fh.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
fh.setFormatter(formatter)
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    logger.addHandler(fh)

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.INFO)
sh.setFormatter(formatter)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    logger.addHandler(sh)

logger.info("=== modulsendmess start ===")
logger.info(f"Python={sys.version.split()[0]} | OS={platform.system()} {platform.release()} | PID={os.getpid()} | Thread={threading.current_thread().name}")
logger.info(f"qasync_available={_HAS_QASYNC}")

# === Импорты клавиатур с фолбэками ===
def _fallback_get_sound_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Главное меню"))
    return kb

def _fallback_get_main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Главное меню"))
    return kb

try:
    from modulsound import get_sound_keyboard as _get_sound_keyboard_impl
except Exception as e1:
    try:
        from .modulsound import get_sound_keyboard as _get_sound_keyboard_impl  # type: ignore
    except Exception as e2:
        logger.warning("Не удалось импортировать modulsound: %r | %r. Использую фолбэк.", e1, e2)
        _get_sound_keyboard_impl = _fallback_get_sound_keyboard

try:
    from keymenu import get_main_keyboard as _get_main_keyboard_impl
except Exception as e1:
    try:
        from .keymenu import get_main_keyboard as _get_main_keyboard_impl  # type: ignore
    except Exception as e2:
        logger.warning("Не удалось импортировать keymenu: %r | %r. Использую фолбэк.", e1, e2)
        _get_main_keyboard_impl = _fallback_get_main_keyboard

def get_sound_keyboard():
    return _get_sound_keyboard_impl()

def get_main_keyboard():
    return _get_main_keyboard_impl()

def _is_chat_mode_active(cid: int) -> bool:
    """Проверка, активен ли сейчас интерактивный чат для данного chat_id.
    Импортируем модуль лениво, чтобы не создавать циклические импорты на старте.
    """
    try:
        # Прямая попытка
        from modulopenchat import CHAT_MODE as _CHAT_MODE, CHAT_WINDOW_OPEN as _CHAT_WIN
    except Exception:
        try:
            # Относительный импорт, если модули в одном пакете
            from .modulopenchat import CHAT_MODE as _CHAT_MODE, CHAT_WINDOW_OPEN as _CHAT_WIN  # type: ignore
        except Exception:
            return False
    try:
        return bool(_CHAT_MODE.get(cid) or _CHAT_WIN.get(cid))
    except Exception:
        return False

# === Exception hook ===
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
sys.excepthook = handle_exception

# === Args ===
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, help='Specify port number')
args, _ = parser.parse_known_args()

# === Сеть ===
def find_free_port():
    for _ in range(100):
        p = random.randint(1025, 65535)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', p))
                return p
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('0.0.0.0', 0))
        return s.getsockname()[1]

_LISTEN_PORT = args.port if args.port else find_free_port()

def detect_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip
    except Exception:
        logger.exception("Failed to detect local IP, defaulting to 127.0.0.1")
        return "127.0.0.1"
    finally:
        try: s.close()
        except: pass

PC_SERVER_URL = f"http://{detect_local_ip()}:{_LISTEN_PORT}"

# === Глобальные состояния ===
_bot: Bot | None = None
_loop: asyncio.AbstractEventLoop | None = None   # цикл aiogram
SEND_MODE: dict[int, bool] = {}
WINDOW_OPEN: dict[int, bool] = {}
MINIMIZED: dict[int, bool] = {}
WINDOW_OPEN_EVENTS: dict[int, asyncio.Event] = {}

# Флаг "закрыто по команде из Telegram", чтобы корректно формировать уведомление и избежать дублей
CLOSED_BY_TELEGRAM: dict[int, bool] = {}

WINDOWS: dict[int, "MessageWindow"] = {}

_server_started = False
_runner: web.AppRunner | None = None

def _norm_chat_id(cid) -> int:
    try:
        return int(cid)
    except Exception:
        try:
            return int(str(cid))
        except Exception:
            logger.error("Не удалось нормализовать chat_id=%r", cid)
            return 0

# === GUI объекты ===

def _apply_window_theme(widget: QWidget):
    """
    Стилизация окна как в gui.py, но *только* на уровне этого виджета,
    без изменения глобального QApplication — чтобы не ловить конфликты и вылеты.
    """
    try:
        # Шрифт как в gui.py
        try:
            widget.setFont(QFont('Segoe UI', 10))
        except Exception:
            pass

        # Стиль-скин на уровне окна
        widget.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: #dddddd;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-size: 10pt;
            }
            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: none;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QLineEdit, QPlainTextEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
                color: #ffffff;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #dddddd;
            }
            QMenu::item:selected {
                background-color: #555555;
            }
        """)
    except Exception:
        logger.exception("Failed to apply window-local theme")

class MessageWindow(QWidget):
    def __init__(self, text: str, chat_id: int):
        super().__init__()
        self.chat_id = _norm_chat_id(chat_id)
        self._closed = False

        # Локальная стилизация окна (как в gui.py), без изменений глобального QApplication
        _apply_window_theme(self)

        # Заголовок и флаги
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Сообщение от Telegram")
        self.setAccessibleName("Окно сообщения от Telegram")
        self.setAccessibleDescription("Диалоговое окно с текстом сообщения и кнопкой закрытия.")

        # Иконка как в gui.py (если icon.png рядом), иначе системная
        try:
            icon_path = str((base_dir / "icon.png").resolve())
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
            else:
                self.setWindowIcon(self.style().standardIcon(QStyle.SP_MessageBoxInformation))
        except Exception:
            pass

        # Макет
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        # Доступный для чтения скринридером текст: фокусируемый, табаемый, копируемый
        self.text_area = QPlainTextEdit(self)
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText(text or "")
        self.text_area.setFocusPolicy(Qt.StrongFocus)  # чтобы получать фокус по Tab
        self.text_area.setAccessibleName("Текст сообщения")
        self.text_area.setAccessibleDescription("Содержимое сообщения, пришедшего из Telegram. Текст доступен для чтения скринридером и копирования.")
        # Минимальный размер, чтобы текст не обрезался
        self.text_area.setMinimumHeight(120)
        layout.addWidget(self.text_area)

        # Кнопка закрытия с хоткеями
        self.btn_close = QPushButton("&Закрыть", self)  # Alt+C по умолчанию
        self.btn_close.clicked.connect(self._on_close)
        self.btn_close.setAutoDefault(False)  # не перехватывать Enter у текстового поля
        self.btn_close.setDefault(False)
        self.btn_close.setAccessibleName("Кнопка закрыть")
        self.btn_close.setAccessibleDescription("Закрывает окно сообщения.")
        layout.addWidget(self.btn_close)

        # Порядок таба: сначала текст, потом кнопка
        self.setTabOrder(self.text_area, self.btn_close)

        # Горячие клавиши: Esc — закрыть
        QShortcut(QKeySequence("Escape"), self, activated=self._on_close)

        # Размер и начальный фокус (адаптивно)
        try:
            screen = QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail:
                w = max(500, int(avail.width() * 0.38))
                h = max(280, int(avail.height() * 0.32))
            else:
                w, h = 520, 260
        except Exception:
            w, h = 520, 260
        self.setMinimumSize(420, 240)
        self.resize(w, h)

        # Политики размера, чтобы текст тянулся
        try:
            self.text_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        except Exception:
            pass

        self.show()
        self.text_area.setFocus(Qt.OtherFocusReason)

        # Звук уведомления — асинхронно, чтобы не блокировать UI
        def _play():
            for alias in ("SystemNotification", "SystemAsterisk", "SystemDefault", "SystemExclamation"):
                try:
                    winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
                    return
                except Exception:
                    continue
        threading.Thread(target=_play, daemon=True).start()

    def bring_to_front(self):
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            logger.exception('bring_to_front failed')

    def _on_close(self):
        if not self._closed:
            self._closed = True
        self.close()

    def closeEvent(self, event):
        cid = getattr(self, "chat_id", None)
        logger.debug("MessageWindow.closeEvent: window closed, chat_id=%s", cid)
        try:
            if cid in WINDOWS:
                WINDOWS.pop(cid, None)
            WINDOW_OPEN.pop(cid, None)

            # Сохраним состояние "был ли режим свернут" ДО сброса,
            # чтобы правильно решить, показывать ли клавиатуру.
            was_minimized = MINIMIZED.get(cid, False)

            # Сброс режима при закрытии окна
            SEND_MODE.pop(cid, None)
            MINIMIZED.pop(cid, None)

            ev = WINDOW_OPEN_EVENTS.pop(cid, None)
            if ev and not ev.is_set():
                ev.set()

            if _bot and _loop and isinstance(cid, int) and cid != 0:
                try:
                    # Кем закрыто: из Telegram или вручную
                    closed_by_tg = CLOSED_BY_TELEGRAM.pop(cid, False)

                    if closed_by_tg:
                        note = "🛑 Окно закрыто по команде из Telegram. Режим отправки завершён."
                        kb = get_sound_keyboard()
                    else:
                        note = "❌ Окно закрыто пользователем. Режим отправки завершён."
                        # Если режим был свернут, клавиатуру НЕ показываем
                        kb = None if was_minimized else get_sound_keyboard()

                    asyncio.run_coroutine_threadsafe(
                        _bot.send_message(cid, note, reply_markup=kb),
                        _loop
                    )
                except Exception:
                    logger.exception("closeEvent: notify send_message failed")
        except Exception:
            logger.exception("closeEvent state cleanup error")
        super().closeEvent(event)

class GuiInvoker(QObject):
    @pyqtSlot("PyQt_PyObject")
    def bring_to_front(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            win = WINDOWS.get(chat_id)
            if win:
                win.bring_to_front()
        except Exception:
            logger.exception("GuiInvoker.bring_to_front error")

    
    """Все операции с окнами — строго в GUI-потоке. chat_id передаём как PyObject, чтобы не терять 64‑бит."""
    @pyqtSlot(str, "PyQt_PyObject")
    def do_show(self, text, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            logger.debug("GuiInvoker.do_show: creating window (chat_id=%r -> %d)", chat_id_obj, chat_id)
            app = QApplication.instance()
            if app is None:
                logger.error("GuiInvoker.do_show: QApplication.instance() is None — окно не будет создано")
                return
            # Закрыть старое окно этого чата, если было
            old = WINDOWS.get(chat_id)
            if old:
                try: old.close()
                except: pass
            win = MessageWindow(text, chat_id)
            WINDOWS[chat_id] = win
            WINDOW_OPEN[chat_id] = True
            ev = WINDOW_OPEN_EVENTS.get(chat_id)
            if ev and not ev.is_set():
                ev.set()
            logger.debug("GuiInvoker.do_show: window created OK (chat_id=%d)", chat_id)
        except Exception:
            logger.exception("GuiInvoker.do_show error")

    @pyqtSlot("PyQt_PyObject")
    def do_close(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            ids = list(WINDOWS.keys()) if chat_id == 0 else [chat_id]
            for cid in ids:
                w = WINDOWS.pop(cid, None)
                if w:
                    w.close()
            logger.debug("GuiInvoker.do_close: closed ids=%s", ids)
        except Exception:
            logger.exception("GuiInvoker.do_close error")

class Communicator(QObject):
    # ВАЖНО: используем object вместо int, иначе большие chat_id (>= 2^31) ломаются (становятся отрицательными)
    show_message = pyqtSignal(str, object)  # text, chat_id(PyObject)
    close_message = pyqtSignal(object)      # chat_id(PyObject, 0 -> все)
    bring_to_front = pyqtSignal(object)     # chat_id(PyObject)

comm: Communicator | None = None
gui_invoker: GuiInvoker | None = None

def _ensure_qapp_and_gui_objects() -> bool:
    """Гарантируем, что QApp создан и мосты живут в GUI-потоке."""
    global comm, gui_invoker
    app = QApplication.instance()
    if app is None:
        logger.error("QApplication.instance() is None. Откройте основное окно (gui.py) прежде чем слать сообщения.")
        return False

    if gui_invoker is None:
        gui_invoker = GuiInvoker()
        gui_invoker.moveToThread(app.thread())
    if comm is None:
        comm = Communicator()
        comm.moveToThread(app.thread())
        comm.show_message.connect(gui_invoker.do_show, type=Qt.QueuedConnection)
        comm.close_message.connect(gui_invoker.do_close, type=Qt.QueuedConnection)
        comm.bring_to_front.connect(gui_invoker.bring_to_front, type=Qt.QueuedConnection)
        logger.debug("Communicator & GuiInvoker are ready (affinity=GUI thread).")
    return True

# === HTTP-обработчики ===
async def handle_show(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        text = request.rel_url.query.get("text", "")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.show_message.emit(text, cid)  # chat_id как PyObject
        logger.debug("handle_show: queued show_message (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_show")
        return web.Response(status=500, text="Internal Server Error")

async def handle_close_message(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.close_message.emit(cid if cid else 0)
        logger.debug("handle_close_message: queued close_message (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_close_message")
        return web.Response(status=500, text="Internal Server Error")

async def handle_window_closed(request):
    try:
        cid = int(request.rel_url.query.get("chat_id", "0"))
        WINDOWS.pop(cid, None)
        WINDOW_OPEN.pop(cid, None)

        # Смотрим, был ли режим свернут
        was_minimized = MINIMIZED.get(cid, False)

        # Сброс состояния режима
        SEND_MODE.pop(cid, None)
        MINIMIZED.pop(cid, None)

        logger.debug("handle_window_closed: cleaned state for chat_id=%s (was_minimized=%s)", cid, was_minimized)
        if _bot and _loop and cid:
            kb = None if was_minimized else get_sound_keyboard()
            await _bot.send_message(
                cid,
                "❌ Окно закрыто пользователем. Режим отправки завершён.",
                reply_markup=kb
            )
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_window_closed")
        return web.Response(status=500, text="Internal Server Error")

async def handle_health(request):
    app = QApplication.instance()
    data = {
        "has_qapp": app is not None,
        "gui_thread": str(app.thread()) if app else None,
        "current_thread": threading.current_thread().name,
        "server_started": _server_started,
        "qasync_available": _HAS_QASYNC,
        "loop_cls": asyncio.get_event_loop().__class__.__name__,
        "window_open_keys": list(WINDOW_OPEN.keys()),
    }
    return web.json_response(data)

async def _start_server():
    global _server_started, _runner
    if _server_started:
        return
    try:
        app = web.Application()
        app.router.add_get("/show", handle_show)
        app.router.add_get("/close_message", handle_close_message)
        app.router.add_get("/window_closed", handle_window_closed)
        app.router.add_get("/health", handle_health)
        _runner = web.AppRunner(app)
        await _runner.setup()
        site = web.TCPSite(_runner, "0.0.0.0", _LISTEN_PORT)
        await site.start()
        _server_started = True
        logger.info(f"Server started on port {_LISTEN_PORT} ({PC_SERVER_URL})")
    except Exception:
        logger.exception("Failed to start HTTP server")

async def _shutdown_server():
    global _server_started, _runner
    if _runner:
        try:
            await _runner.cleanup()
            logger.debug("HTTP server shutdown cleanly")
        except Exception:
            logger.exception("Error shutting down server")
    _server_started = False

# === Telegram-обработчики ===
def get_pc_keyboard_window_closed():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Закрыть режим"))
    return kb


# === Сообщения конфликтов режима ===
CONFLICT_MSG_CHAT_ACTIVE = ("⚠️ Нельзя запустить «Отправить сообщение на компьютер», "
                            "пока активен «Интерактивный чат». "
                            "Сначала закройте чат (кнопка «Закрыть интерактивный чат») "
                            "или продолжайте работать в нём.")

def get_pc_keyboard_window_open():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Свернуть режим отправки сообщений"),
           KeyboardButton("Закрыть режим"))
    return kb


async def pc_enable_handler(message: types.Message):
    """Включаем или разворачиваем режим: запрещаем конфликт с чатом, поднимаем HTTP и проверяем GUI."""
    try:
        bot = message.bot
        chat_id = message.chat.id

        # 0) Проверим конфликт с интерактивным чатом
        if _is_chat_mode_active(chat_id):
            # Только сообщение. Клавиатуру НЕ меняем.
            await message.answer(CONFLICT_MSG_CHAT_ACTIVE)
            return

        await _ensure_server(bot)
        SEND_MODE[chat_id] = True

        # 1) Разворачиваем режим, если он был свернут в Telegram
        was_min = MINIMIZED.pop(chat_id, None)
        if was_min:
            # Если окно уже было открыто — вытащим на передний план
            if _ensure_qapp_and_gui_objects() and comm:
                try:
                    comm.bring_to_front.emit(chat_id)
                except Exception:
                    logger.exception("pc_enable_handler: bring_to_front emit failed")

        # 2) Выбираем правильную клавиатуру и сообщение
        if WINDOW_OPEN.get(chat_id):
            keyboard = get_pc_keyboard_window_open()
            msg = "🔄 Режим развернут. Окно сообщения активно." if was_min else "🔄 Режим уже активен. Окно сообщения на переднем плане."
        else:
            keyboard = get_pc_keyboard_window_closed()
            msg = "🚀 Режим отправки включён!"

        await message.answer(
            f"{msg}\nСервер: {PC_SERVER_URL}\n"
            f"(loop={asyncio.get_event_loop().__class__.__name__}, gui_ready={_ensure_qapp_and_gui_objects()})",
            reply_markup=keyboard
        )
    except Exception:
        logger.exception("Error in pc_enable_handler")
        await message.reply("❌ Ошибка при включении режима отправки. Смотри логи modulsendmess.log.")

async def pc_message_handler(message: types.Message):
    """Показываем окно с текстом (через GUI-поток)."""
    cid = message.chat.id
    # Если режим свернут — НЕ перехватываем сообщения (пусть другие модули ловят свои кнопки)
    if MINIMIZED.get(cid):
        logger.debug("pc_message_handler: minimized -> skip capturing for chat_id=%s", cid)
        return
    try:
        if not SEND_MODE.get(cid):
            return
        text = (message.text or "").strip()
        logger.debug("pc_message_handler: received text=%r for chat_id=%s", text, cid)

        if text in ("Свернуть режим отправки", "Свернуть режим отправки сообщений"):
            MINIMIZED[cid] = True
            logger.debug("pc_message_handler: set MINIMIZED for chat_id=%s", cid)
            await message.answer("Главное меню:", reply_markup=get_main_keyboard())
            return
        if text == "Закрыть режим":
            # Попробуем аккуратно закрыть окно (если открыто) через GUI-поток
            try:
                if _ensure_qapp_and_gui_objects():
                    CLOSED_BY_TELEGRAM[cid] = True
                    comm.close_message.emit(cid)
                    logger.debug("pc_message_handler: requested GUI close for chat_id=%s", cid)
                else:
                    # Fallback: через локальный HTTP
                    timeout = aiohttp.ClientTimeout(total=3)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        await session.get(f"{PC_SERVER_URL}/close_message", params={"chat_id": cid})
                    logger.debug("pc_message_handler: requested HTTP close for chat_id=%s", cid)
            except Exception:
                logger.exception("pc_message_handler: close via Telegram failed")
            # Сброс состояния режима
            SEND_MODE.pop(cid, None)
            MINIMIZED.pop(cid, None)
            WINDOW_OPEN.pop(cid, None)
            await _shutdown_server()
            logger.debug("pc_message_handler: closing mode for chat_id=%s", cid)
            # Если окна не было, сообщим об отключении режима сами. Если было — уведомление придёт из closeEvent.
            if not WINDOWS.get(cid):
                await message.answer("✅ Режим отключён.", reply_markup=get_sound_keyboard())
            return
        if WINDOW_OPEN.get(cid, False):
            logger.debug("pc_message_handler: WINDOW_OPEN already True for chat_id=%s", cid)
            await message.answer("⏳ Подождите, пока текущее окно не закроется.")
            return

        if not _ensure_qapp_and_gui_objects():
            await message.answer("⚠️ GUI не готов. Откройте основное окно приложения и повторите.")
            return

        # ACK-событие: будет .set() из GUI-потока после создания окна
        ev = asyncio.Event()
        WINDOW_OPEN_EVENTS[cid] = ev

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            logger.debug("pc_message_handler: GET /show to %s", PC_SERVER_URL)
            resp = await session.get(f"{PC_SERVER_URL}/show", params={"text": text, "chat_id": cid})
            body = await resp.text()
            logger.debug("pc_message_handler: http status=%s, body=%r", resp.status, body)

        # Ждём ACK (до 5 сек)
        try:
            await asyncio.wait_for(ev.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("pc_message_handler: ACK timeout — окно не подтвердило открытие, chat_id=%s", cid)

        if WINDOW_OPEN.get(cid):
            await message.answer("📨 Сообщение отображено!", reply_markup=get_pc_keyboard_window_open())
        else:
            logger.warning("pc_message_handler: command sent but WINDOW_OPEN is False (cid=%s). "
                           "Возможные причины: GUI-поток отсутствует; окно упало; неверная маршрутизация сигналов.", cid)
            await message.answer("⚠️ Команда отправлена, но окно не открылось. Проверьте логи на ПК (modulsendmess.log).")

    except Exception as e:
        logger.exception("Error in pc_message_handler")
        try:
            await message.answer(f"⚠️ Произошла ошибка: {e}")
        except:
            pass
    finally:
        WINDOW_OPEN_EVENTS.pop(cid, None)

def register_handlers(dp: Dispatcher):
    dp.register_message_handler(pc_enable_handler, lambda msg: msg.text == "Отправить сообщение на компьютер")
    dp.register_message_handler(pc_message_handler, lambda msg: SEND_MODE.get(msg.chat.id) and not MINIMIZED.get(msg.chat.id) and msg.content_type == 'text')

async def _ensure_server(bot: Bot):
    """Поднятие HTTP и инициализация ссылок на цикл бота и GUI-объекты."""
    global _bot, _loop, _server_started
    _bot = bot
    _loop = asyncio.get_event_loop()  # цикл aiogram
    await _start_server()

# === Локальный запуск (для отладки) ===
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(_start_server())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(_shutdown_server())
