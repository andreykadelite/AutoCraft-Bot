
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
modulopenchat.py — интерактивный чат-окно для ПК с двусторонней связью Telegram <-> ПК.
Обновление: история сообщений теперь на QListWidget (вместо QPlainTextEdit), чтобы стрелками
вверх/вниз можно было перемещаться по отдельным сообщениям и корректно читать их скринридером.
— Каждое сообщение — отдельный элемент списка.
— Стрелки ↑/↓ читают предыдущие/следующие сообщения.
— PageUp/PageDown, Home/End работают как обычно.
— Ctrl+C копирует текст выбранного сообщения в буфер обмена.
— Новые сообщения НЕ сбрасывают выделение, если вы читаете старые (фокус на списке и курсор не на последнем элементе).
"""

import os
import sys
import logging
import configparser
import asyncio
import socket
import random
import argparse
import platform
import threading
from datetime import datetime

from pathlib import Path

from aiohttp import web
import aiohttp
import winsound

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QStyleFactory,
    QLineEdit, QShortcut, QSizePolicy, QStyle, QListWidget, QListWidgetItem, QAbstractItemView
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
logger = logging.getLogger("modulchat")
logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

fh = logging.FileHandler(str(log_dir / 'modulchat.log'), encoding='utf-8')
fh.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
fh.setFormatter(formatter)
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    logger.addHandler(fh)

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.INFO)
sh.setFormatter(formatter)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    logger.addHandler(sh)

logger.info("=== modulchat start ===")
logger.info(f"Python={sys.version.split()[0]} | OS={platform.system()} {platform.release()} | PID={os.getpid()} | Thread={threading.current_thread().name}")

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

def _is_send_mode_active(cid: int) -> bool:
    """Проверка, активен ли режим \"Отправить сообщение на компьютер\" для данного chat_id."""
    try:
        from modulsendmess import SEND_MODE as _SEND_MODE, WINDOW_OPEN as _SEND_WIN
    except Exception:
        try:
            from .modulsendmess import SEND_MODE as _SEND_MODE, WINDOW_OPEN as _SEND_WIN  # type: ignore
        except Exception:
            return False
    try:
        return bool(_SEND_MODE.get(cid) or _SEND_WIN.get(cid))
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

CHAT_MODE: dict[int, bool] = {}
CHAT_WINDOW_OPEN: dict[int, bool] = {}
CHAT_MINIMIZED: dict[int, bool] = {}
CHAT_WINDOWS: dict[int, "ChatWindow"] = {}
CHAT_WINDOW_ACK: dict[int, asyncio.Event] = {}

CLOSED_BY_TELEGRAM: dict[int, bool] = {}

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

# === Стилизация (локально на окно) ===
def _apply_window_theme(widget: QWidget):
    try:
        widget.setFont(QFont('Segoe UI', 10))
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
            QLineEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
                color: #ffffff;
            }
            QListWidget {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
                color: #ffffff;
            }
        """)
    except Exception:
        logger.exception("Failed to apply window-local theme")

# === Окно чата ===
class ChatWindow(QWidget):
    def __init__(self, chat_id: int):
        super().__init__()
        self.chat_id = _norm_chat_id(chat_id)
        self._closed = False

        _apply_window_theme(self)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Интерактивный чат")
        self.setAccessibleName("Окно интерактивного чата")
        self.setAccessibleDescription(
            "Окно двустороннего чата между компьютером и Телеграм. "
            "Слева список сообщений, ниже поле ввода и кнопки. "
            "Используйте стрелки вверх/вниз, PageUp/PageDown для чтения разных сообщений."
        )

        # Иконка
        try:
            icon_path = str((base_dir / "icon.png").resolve())
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
            else:
                self.setWindowIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        except Exception:
            pass

        # Верстка
        root = QVBoxLayout(self)
        root.setContentsMargins(15, 15, 15, 15)
        root.setSpacing(10)

        # История: теперь список
        self.history = QListWidget(self)
        self.history.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history.setAccessibleName("История чата")
        self.history.setAccessibleDescription(
            "Список сообщений. Каждое сообщение — отдельная строка. "
            "Стрелками вверх и вниз выбирайте сообщения для чтения."
        )
        self.history.setMinimumHeight(200)
        self.history.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.history)

        # Быстрые хоткеи для истории
        QShortcut(QKeySequence("Ctrl+C"), self.history, activated=self._copy_current_message)

        # Поле ввода + кнопка отправки
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit(self)
        self.input.setAccessibleName("Поле ввода")
        self.input.setAccessibleDescription("Введите сообщение и нажмите Enter или кнопку Отправить.")
        self.input.returnPressed.connect(self._send_current)
        input_row.addWidget(self.input)

        self.btn_send = QPushButton("&Отправить", self)
        self.btn_send.setAccessibleName("Кнопка отправить")
        self.btn_send.setAccessibleDescription("Отправляет введённое сообщение в Телеграм и добавляет в историю.")
        self.btn_send.clicked.connect(self._send_current)
        input_row.addWidget(self.btn_send)
        root.addLayout(input_row)

        # Набор кнопок управления
        ctl_row = QHBoxLayout()
        ctl_row.setSpacing(8)

        self.btn_min = QPushButton("С&вернуть", self)
        self.btn_min.setAccessibleName("Кнопка свернуть")
        self.btn_min.setAccessibleDescription("Сворачивает окно чата.")
        self.btn_min.clicked.connect(self._on_minimize)
        ctl_row.addWidget(self.btn_min)

        self.btn_close = QPushButton("&Закрыть", self)
        self.btn_close.setAccessibleName("Кнопка закрыть")
        self.btn_close.setAccessibleDescription("Закрывает чат и отключает режим.")
        self.btn_close.clicked.connect(self._on_close_click)
        ctl_row.addWidget(self.btn_close)

        root.addLayout(ctl_row)

        # Порядок табов: история -> ввод -> отправить -> свернуть -> закрыть
        self.setTabOrder(self.history, self.input)
        self.setTabOrder(self.input, self.btn_send)
        self.setTabOrder(self.btn_send, self.btn_min)
        self.setTabOrder(self.btn_min, self.btn_close)

        # Горячие клавиши: Esc — закрыть
        QShortcut(QKeySequence("Escape"), self, activated=self._on_close_click)

        # Размеры
        try:
            screen = QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail:
                w = max(600, int(avail.width() * 0.45))
                h = max(380, int(avail.height() * 0.40))
            else:
                w, h = 640, 420
        except Exception:
            w, h = 640, 420
        self.setMinimumSize(520, 340)
        self.resize(w, h)

        self.show()
        # Фокус по умолчанию на истории, чтобы стрелки сразу читали сообщения
        self.history.setFocus(Qt.OtherFocusReason)
        self._play_notify()

    # === Публичные методы окна ===
    def append_incoming(self, text: str):
        """Добавить во входящий поток (из Телеграма)."""
        if not text:
            return
        self._append_line(f"Telegram: {text}", incoming=True)
        self._play_notify()

    def append_outgoing(self, text: str):
        """Добавить в исходящий поток (от пользователя)."""
        if not text:
            return
        self._append_line(f"Вы: {text}", incoming=False)

    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # === Внутренние обработчики ===
    def _append_line(self, line: str, incoming: bool):
        """Добавляет строку в историю без потери позиции чтения скринридером."""
        try:
            # Сохраним, читает ли пользователь «прошлые» сообщения
            user_reading_old = (
                self.history.hasFocus()
                and self.history.currentRow() not in (-1, self.history.count() - 1)
            )
            timestamp = datetime.now().strftime("%H:%M:%S")
            item_text = f"[{timestamp}] {line}"

            item = QListWidgetItem(item_text)
            # Для возможной стилизации на будущее
            item.setData(Qt.UserRole, {"incoming": incoming})
            self.history.addItem(item)

            last_index = self.history.count() - 1

            if user_reading_old:
                # Не трогаем текущий выбор, не прыгаем вниз
                self.history.scrollToItem(item, hint=QAbstractItemView.PositionAtBottom)
            else:
                # Перейдём на новое сообщение, чтобы скринридер сразу прочитал последнее,
                # если пользователь НЕ читает старые
                self.history.setCurrentRow(last_index)
                self.history.scrollToItem(item, hint=QAbstractItemView.PositionAtBottom)
        except Exception:
            logger.exception("append_line failed")

    def _copy_current_message(self):
        try:
            row = self.history.currentRow()
            if row < 0:
                self._beep()
                return
            text = self.history.item(row).text()
            QApplication.clipboard().setText(text)
        except Exception:
            logger.exception("copy_current_message failed")

    def _send_current(self):
        text = self.input.text().strip()
        if not text:
            self._beep()
            return
        self.append_outgoing(text)
        self.input.clear()
        # Отправка в Телеграм
        cid = getattr(self, "chat_id", None)
        if not cid or not _bot or not _loop:
            logger.warning("send_current: bot/loop/chat_id unavailable")
            return
        try:
            prefix = "\U0001F4AC Отправлено из окна чата на ПК:\n" if CHAT_MINIMIZED.get(cid) else ""
            asyncio.run_coroutine_threadsafe(_bot.send_message(cid, f"{prefix}{text}"), _loop)
        except Exception:
            logger.exception("send_current: failed to send to Telegram")

    def _on_minimize(self):
        cid = getattr(self, "chat_id", None)
        try:
            self.showMinimized()
            if isinstance(cid, int):
                CHAT_MINIMIZED[cid] = True
                # Сообщим в Телеграм и переключим клавиатуру
                if _bot and _loop:
                    asyncio.run_coroutine_threadsafe(
                        _bot.send_message(cid, "🔽 Чат свернут. Сообщения продолжают поступать.", reply_markup=get_main_keyboard()),
                        _loop
                    )
        except Exception:
            logger.exception("Minimize failed")

    def _on_close_click(self):
        """Нажатие на кнопку Закрыть — закрываем окно. Уведомление в Telegram отправится в closeEvent()."""
        try:
            self.close()
        except Exception:
            logger.exception("Close click failed")

    def _play_notify(self):
        def _play():
            for alias in ("SystemNotification", "SystemAsterisk", "SystemDefault", "SystemExclamation"):
                try:
                    winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
                    return
                except Exception:
                    continue
        threading.Thread(target=_play, daemon=True).start()

    def _beep(self):
        try:
            winsound.MessageBeep()
        except Exception:
            pass

    # === Закрытие окна ===
    def closeEvent(self, event):
        cid = getattr(self, "chat_id", None)
        logger.debug("ChatWindow.closeEvent: window closed, chat_id=%s", cid)
        try:
            if isinstance(cid, int):
                CHAT_WINDOWS.pop(cid, None)
                CHAT_WINDOW_OPEN.pop(cid, None)
                was_minimized = CHAT_MINIMIZED.get(cid, False)

                # Сброс режима
                CHAT_MODE.pop(cid, None)
                CHAT_MINIMIZED.pop(cid, None)

                ev = CHAT_WINDOW_ACK.pop(cid, None)
                if ev and not ev.is_set():
                    ev.set()

                if _bot and _loop and cid != 0:
                    try:
                        closed_by_tg = CLOSED_BY_TELEGRAM.pop(cid, False)
                        if closed_by_tg:
                            note = "🛑 Чат закрыт по команде из Telegram."
                            kb = get_sound_keyboard()
                        else:
                            note = "❌ Чат закрыт пользователем."
                            kb = None if was_minimized else get_sound_keyboard()
                        asyncio.run_coroutine_threadsafe(
                            _bot.send_message(cid, f"{note} Режим чата отключён." , reply_markup=kb),
                            _loop
                        )
                    except Exception:
                        logger.exception("closeEvent: notify send_message failed")
        except Exception:
            logger.exception("closeEvent state cleanup error")
        super().closeEvent(event)

# === Мосты GUI-потоков ===
class GuiInvoker(QObject):
    @pyqtSlot("PyQt_PyObject")
    def bring_to_front(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            win = CHAT_WINDOWS.get(chat_id)
            if win:
                win.bring_to_front()
        except Exception:
            logger.exception("GuiInvoker.bring_to_front error")

    
    @pyqtSlot("PyQt_PyObject")
    def open_chat(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            app = QApplication.instance()
            if app is None:
                logger.error("GuiInvoker.open_chat: QApplication.instance() is None — окно не будет создано")
                return
            # Закрыть старое окно, если есть
            old = CHAT_WINDOWS.get(chat_id)
            if old:
                try: old.close()
                except: pass
            win = ChatWindow(chat_id)
            CHAT_WINDOWS[chat_id] = win
            CHAT_WINDOW_OPEN[chat_id] = True
            ack = CHAT_WINDOW_ACK.get(chat_id)
            if ack and not ack.is_set():
                ack.set()
            logger.debug("GuiInvoker.open_chat: window created OK (chat_id=%d)", chat_id)
        except Exception:
            logger.exception("GuiInvoker.open_chat error")

    @pyqtSlot(str, "PyQt_PyObject")
    def append_text(self, text, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            win = CHAT_WINDOWS.get(chat_id)
            if not win:
                # Если окна нет — создаём и добавляем
                self.open_chat(chat_id)
                win = CHAT_WINDOWS.get(chat_id)
            if win:
                win.append_incoming(text or "")
        except Exception:
            logger.exception("GuiInvoker.append_text error")

    @pyqtSlot("PyQt_PyObject")
    def close_chat(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            ids = list(CHAT_WINDOWS.keys()) if chat_id == 0 else [chat_id]
            for cid in ids:
                w = CHAT_WINDOWS.pop(cid, None)
                if w:
                    w.close()
            logger.debug("GuiInvoker.close_chat: closed ids=%s", ids)
        except Exception:
            logger.exception("GuiInvoker.close_chat error")

    @pyqtSlot("PyQt_PyObject")
    def minimize_chat(self, chat_id_obj):
        try:
            chat_id = _norm_chat_id(chat_id_obj)
            win = CHAT_WINDOWS.get(chat_id)
            if win:
                win._on_minimize()
        except Exception:
            logger.exception("GuiInvoker.minimize_chat error")

class Communicator(QObject):
    open_chat = pyqtSignal(object)             # chat_id
    bring_to_front = pyqtSignal(object)        # chat_id
    append_text = pyqtSignal(str, object)      # text, chat_id
    close_chat = pyqtSignal(object)            # chat_id (0 -> все)
    minimize_chat = pyqtSignal(object)         # chat_id

comm: Communicator | None = None
gui_invoker: GuiInvoker | None = None

def _ensure_qapp_and_gui_objects() -> bool:
    global comm, gui_invoker
    app = QApplication.instance()
    if app is None:
        logger.error("QApplication.instance() is None. Откройте основное окно (gui.py) прежде чем создавать чат.")
        return False

    if gui_invoker is None:
        gui_invoker = GuiInvoker()
        gui_invoker.moveToThread(app.thread())
    if comm is None:
        comm = Communicator()
        comm.moveToThread(app.thread())
        comm.open_chat.connect(gui_invoker.open_chat, type=Qt.QueuedConnection)
        comm.bring_to_front.connect(gui_invoker.bring_to_front, type=Qt.QueuedConnection)
        comm.append_text.connect(gui_invoker.append_text, type=Qt.QueuedConnection)
        comm.close_chat.connect(gui_invoker.close_chat, type=Qt.QueuedConnection)
        comm.minimize_chat.connect(gui_invoker.minimize_chat, type=Qt.QueuedConnection)
        logger.debug("Communicator & GuiInvoker are ready (affinity=GUI thread).")
    return True

# === HTTP-слой (fallback/API) ===
async def handle_chat_open(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.open_chat.emit(cid)
        logger.debug("handle_chat_open: queued open_chat (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_chat_open")
        return web.Response(status=500, text="Internal Server Error")

async def handle_chat_append(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        text = request.rel_url.query.get("text", "")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.append_text.emit(text, cid)
        logger.debug("handle_chat_append: queued append_text (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_chat_append")
        return web.Response(status=500, text="Internal Server Error")

async def handle_chat_close(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.close_chat.emit(cid if cid else 0)
        logger.debug("handle_chat_close: queued close_chat (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_chat_close")
        return web.Response(status=500, text="Internal Server Error")

async def handle_chat_minimize(request):
    try:
        if not _ensure_qapp_and_gui_objects():
            return web.Response(status=503, text="GUI unavailable")
        cid = int(request.rel_url.query.get("chat_id", "0"))
        comm.minimize_chat.emit(cid)
        logger.debug("handle_chat_minimize: queued minimize_chat (cid=%s)", cid)
        return web.Response(text="OK")
    except Exception:
        logger.exception("Error in handle_chat_minimize")
        return web.Response(status=500, text="Internal Server Error")

async def handle_health(request):
    app = QApplication.instance()
    data = {
        "has_qapp": app is not None,
        "gui_thread": str(app.thread()) if app else None,
        "current_thread": threading.current_thread().name,
        "server_started": _server_started,
        "window_open_keys": list(CHAT_WINDOW_OPEN.keys()),
    }
    return web.json_response(data)

async def _start_server():
    global _server_started, _runner
    if _server_started:
        return
    try:
        app = web.Application()
        app.router.add_get("/chat/open", handle_chat_open)
        app.router.add_get("/chat/append", handle_chat_append)
        app.router.add_get("/chat/close", handle_chat_close)
        app.router.add_get("/chat/minimize", handle_chat_minimize)
        app.router.add_get("/health", handle_health)
        _runner = web.AppRunner(app)
        await _runner.setup()
        site = web.TCPSite(_runner, "0.0.0.0", _LISTEN_PORT)
        await site.start()
        _server_started = True
        logger.info(f"Chat server started on port {_LISTEN_PORT} ({PC_SERVER_URL})")
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

# === Клавиатуры Телеграм ===
def get_chat_keyboard_window_closed():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Создать интерактивный чат"))
    return kb


# === Сообщения конфликтов режима ===
CONFLICT_MSG_SEND_ACTIVE = ("⚠️ Нельзя запустить «Интерактивный чат», "
                            "пока активен режим «Отправить сообщение на компьютер». "
                            "Сначала закройте текущий режим (кнопка «Закрыть режим») "
                            "или продолжайте работу в нём.")

def get_chat_keyboard_window_open():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Свернуть интерактивный чат"),
           KeyboardButton("Закрыть интерактивный чат"))
    return kb

# === Хэндлеры Телеграм ===

async def chat_enable_handler(message: types.Message):
    """Создание/разворачивание интерактивного чата. Блокировка при активном режиме отправки."""
    try:
        bot = message.bot
        chat_id = message.chat.id

        # 0) Проверим конфликт с режимом отправки сообщения на компьютер
        if _is_send_mode_active(chat_id):
            # Только сообщение. Клавиатуру НЕ меняем.
            await message.answer(CONFLICT_MSG_SEND_ACTIVE)
            return

        await _ensure_server(bot)
        CHAT_MODE[chat_id] = True
        # Снимаем свёрнутый режим при повторной активации (разворачиваем)
        CHAT_MINIMIZED[chat_id] = False

        # Если окно уже есть — выводим на передний план
        if CHAT_WINDOW_OPEN.get(chat_id, False):
            try:
                if _ensure_qapp_and_gui_objects():
                    if comm:
                        comm.bring_to_front.emit(chat_id)
                keyboard = get_chat_keyboard_window_open()
                msg = "✅ Чат развёрнут. Вывожу окно на передний план."
            except Exception:
                msg = "🔄 Чат уже открыт."
                keyboard = get_chat_keyboard_window_open()
        else:
            # Запускаем окно через мост GUI и ждём ACK
            if not _ensure_qapp_and_gui_objects():
                await message.answer("⚠️ GUI не готов. Откройте основное окно приложения и повторите.")
                return

            ack = asyncio.Event()
            CHAT_WINDOW_ACK[chat_id] = ack
            comm.open_chat.emit(chat_id)
            try:
                await asyncio.wait_for(ack.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("chat_enable_handler: ACK timeout — окно не подтвердило открытие, chat_id=%s", chat_id)

            if CHAT_WINDOW_OPEN.get(chat_id):
                keyboard = get_chat_keyboard_window_open()
                msg = "✅ Чат открыт."
            else:
                keyboard = get_chat_keyboard_window_closed()
                msg = "⚠️ Команда отправлена, но окно не открылось. Проверьте логи на ПК (modulchat.log)."

        await message.answer(
            f"{msg}\nСервер: {PC_SERVER_URL}\n(gui_ready={_ensure_qapp_and_gui_objects()})",
            reply_markup=keyboard
        )
    except Exception:
        logger.exception("Error in chat_enable_handler")
        await message.reply("❌ Ошибка при создании чата. Смотри логи modulchat.log.")

async def chat_text_handler(message: types.Message):
    """Перехват текста из Телеграма и доставка в окно чата."""
    cid = message.chat.id
    try:
        if not CHAT_MODE.get(cid):
            return
        text = (message.text or "").strip()
        logger.debug("chat_text_handler: received text=%r for chat_id=%s", text, cid)

        # Команды из клавиатуры
        if text == "Свернуть интерактивный чат":
            CHAT_MINIMIZED[cid] = True
            # Не трогаем окно на ПК — сворачиваем только режим захвата в Telegram
            await message.answer("\U0001F53D Чат свёрнут на стороне Telegram. Окно на ПК остаётся открытым.\nТексты из Telegram временно не доставляются в окно.", reply_markup=get_main_keyboard())
            return

        if text == "Закрыть интерактивный чат":
            try:
                if _ensure_qapp_and_gui_objects():
                    CLOSED_BY_TELEGRAM[cid] = True
                    comm.close_chat.emit(cid)
                else:
                    timeout = aiohttp.ClientTimeout(total=3)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        await session.get(f"{PC_SERVER_URL}/chat/close", params={"chat_id": cid})
            except Exception:
                logger.exception("chat_text_handler: close via Telegram failed")
            # Сброс состояния
            CHAT_MODE.pop(cid, None)
            CHAT_MINIMIZED.pop(cid, None)
            CHAT_WINDOW_OPEN.pop(cid, None)
            await _shutdown_server()
            # Если окна уже нет, уведомим сами
            if not CHAT_WINDOWS.get(cid):
                await message.answer("✅ Чат отключён.", reply_markup=get_sound_keyboard())
            return

        # Если режим свёрнут на стороне Telegram — игнорируем обычные тексты (команды выше обрабатываются)
        if CHAT_MINIMIZED.get(cid):
            logger.debug("chat_text_handler: minimized -> text ignored for chat_id=%s", cid)
            return

        # Обычный текст — доставляем в окно
        if not _ensure_qapp_and_gui_objects():
            await message.answer("⚠️ GUI не готов. Откройте основное окно приложения и повторите.")
            return

        comm.append_text.emit(text, cid)
        # ACK пользователю необязателен, но можно кратко отозваться:
        await message.answer("📨 Доставлено в окно чата.", reply_markup=get_chat_keyboard_window_open())

    except Exception as e:
        logger.exception("Error in chat_text_handler")
        try:
            await message.answer(f"⚠️ Произошла ошибка: {e}")
        except:
            pass

def register_handlers(dp: Dispatcher):
    dp.register_message_handler(chat_enable_handler, lambda msg: msg.text == "Создать интерактивный чат")
    dp.register_message_handler(chat_text_handler, lambda msg: CHAT_MODE.get(msg.chat.id) and not CHAT_MINIMIZED.get(msg.chat.id) and msg.content_type == 'text')

# === Инициализация сервера/бота ===
async def _ensure_server(bot: Bot):
    global _bot, _loop, _server_started
    _bot = bot
    _loop = asyncio.get_event_loop()
    await _start_server()

# === Локальный запуск (для отладки HTTP/GUI без бота) ===
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(_start_server())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(_shutdown_server())
