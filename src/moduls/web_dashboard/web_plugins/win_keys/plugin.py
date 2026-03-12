# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import current_app, jsonify, render_template_string, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ...security import panel_has_access as has_access

try:
    import pyautogui  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pyautogui = None

try:
    import ctypes
    from ctypes import wintypes
except Exception:  # pragma: no cover - optional dependency
    ctypes = None
    wintypes = None


_LOGGER = logging.getLogger("panel.plugins")
_TEMPLATE_ROOT: Path | None = None
_STORAGE_FILE_PATH: Path | None = None
_STORAGE_LOCK = threading.RLock()
_STORAGE_LAST_ERROR = ""

_DATA_FILE_NAME = "win_keys_store.json"
_MAX_NAME_LEN = 120
_MAX_TEXT_LEN = 2000
_MAX_REPEAT = 20
_MAX_INTERVAL_MS = 2000
_MAX_CUSTOM_ACTIONS = 300
_MAX_HISTORY_ITEMS = 400

_ALLOWED_ACTION_TYPES = {
    "hotkey",
    "key",
    "text",
    "mouse_click",
    "mouse_double",
    "mouse_down",
    "mouse_up",
    "scroll",
}

_MOUSE_BUTTONS = {"left", "right", "middle"}

_KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "menu": "apps",
    "context": "apps",
    "contextmenu": "apps",
    "command": "winleft",
    "cmd": "winleft",
    "meta": "winleft",
    "win": "winleft",
    "windows": "winleft",
    "super": "winleft",
    "option": "alt",
    "spacebar": "space",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "prtsc": "printscreen",
    "prt_sc": "printscreen",
    "caps": "capslock",
}

_KEY_DISPLAY = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "winleft": "Win",
    "winright": "Win",
    "esc": "Esc",
    "enter": "Enter",
    "tab": "Tab",
    "space": "Space",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "printscreen": "PrtSc",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "apps": "Menu",
    "backspace": "Backspace",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}

_SINGLE_KEY_SUGGESTIONS: list[tuple[str, str]] = [
    ("enter", "Enter"),
    ("tab", "Tab"),
    ("space", "Space"),
    ("backspace", "Backspace"),
    ("esc", "Esc"),
    ("delete", "Delete"),
    ("insert", "Insert"),
    ("home", "Home"),
    ("end", "End"),
    ("pageup", "Page Up"),
    ("pagedown", "Page Down"),
    ("up", "Arrow Up"),
    ("down", "Arrow Down"),
    ("left", "Arrow Left"),
    ("right", "Arrow Right"),
    ("printscreen", "Print Screen"),
    ("pause", "Pause"),
    ("capslock", "Caps Lock"),
    ("numlock", "Num Lock"),
    ("scrolllock", "Scroll Lock"),
    ("apps", "Context Menu"),
    ("winleft", "Win Left"),
    ("winright", "Win Right"),
    ("shift", "Shift"),
    ("ctrl", "Ctrl"),
    ("alt", "Alt"),
    ("`", "`"),
    ("-", "-"),
    ("=", "="),
    ("+", "+"),
    ("[", "["),
    ("]", "]"),
    ("\\", "\\"),
    (";", ";"),
    ("'", "'"),
    (",", ","),
    (".", "."),
    ("/", "/"),
]

_WIN_INPUT_KEYBOARD = 1
_WIN_KEYEVENTF_KEYUP = 0x0002
_WIN_KEYEVENTF_UNICODE = 0x0004

_WIN_VK_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "winleft": 0x5B,
    "winright": 0x5C,
    "apps": 0x5D,
    "numlock": 0x90,
    "scrolllock": 0x91,
}

_WIN_CHAR_KEYMAP: dict[str, tuple[int, tuple[str, ...]]] = {
    ",": (0xBC, ()),
    "<": (0xBC, ("shift",)),
    "-": (0xBD, ()),
    "_": (0xBD, ("shift",)),
    ".": (0xBE, ()),
    ">": (0xBE, ("shift",)),
    "/": (0xBF, ()),
    "?": (0xBF, ("shift",)),
    "`": (0xC0, ()),
    "~": (0xC0, ("shift",)),
    ";": (0xBA, ()),
    ":": (0xBA, ("shift",)),
    "=": (0xBB, ()),
    "+": (0xBB, ("shift",)),
    "[": (0xDB, ()),
    "{": (0xDB, ("shift",)),
    "\\": (0xDC, ()),
    "|": (0xDC, ("shift",)),
    "]": (0xDD, ()),
    "}": (0xDD, ("shift",)),
    "'": (0xDE, ()),
    '"': (0xDE, ("shift",)),
    "!": (0x31, ("shift",)),
    "@": (0x32, ("shift",)),
    "#": (0x33, ("shift",)),
    "$": (0x34, ("shift",)),
    "%": (0x35, ("shift",)),
    "^": (0x36, ("shift",)),
    "&": (0x37, ("shift",)),
    "*": (0x38, ("shift",)),
    "(": (0x39, ("shift",)),
    ")": (0x30, ("shift",)),
}

_USER32 = None
_WIN_VK_KEYSCAN = None
_WIN_SENDINPUT = None
_WIN_INPUT_STRUCT = None
_WIN_KEYBDINPUT_STRUCT = None

if os.name == "nt" and ctypes and wintypes:
    try:
        _USER32 = ctypes.WinDLL("user32", use_last_error=True)
        _WIN_VK_KEYSCAN = _USER32.VkKeyScanW
        _WIN_VK_KEYSCAN.argtypes = [wintypes.WCHAR]
        _WIN_VK_KEYSCAN.restype = ctypes.c_short
        _WIN_SENDINPUT = _USER32.SendInput
        _WIN_SENDINPUT.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        _WIN_SENDINPUT.restype = wintypes.UINT

        _ULONG_PTR = getattr(
            wintypes,
            "ULONG_PTR",
            ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong,
        )

        class _WinMouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR),
            ]

        class _WinKeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR),
            ]

        class _WinHardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _WinInputUnion(ctypes.Union):
            _fields_ = [
                ("mi", _WinMouseInput),
                ("ki", _WinKeyboardInput),
                ("hi", _WinHardwareInput),
            ]

        class _WinInput(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", _WinInputUnion),
            ]

        _WIN_INPUT_STRUCT = _WinInput
        _WIN_KEYBDINPUT_STRUCT = _WinKeyboardInput
    except Exception:
        _USER32 = None
        _WIN_VK_KEYSCAN = None
        _WIN_SENDINPUT = None
        _WIN_INPUT_STRUCT = None
        _WIN_KEYBDINPUT_STRUCT = None


if pyautogui:
    try:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
    except Exception:
        pass


def _set_storage_error(message: str) -> None:
    global _STORAGE_LAST_ERROR
    _STORAGE_LAST_ERROR = str(message or "").strip()


def _clear_storage_error() -> None:
    _set_storage_error("")


def _get_storage_error() -> str:
    return str(_STORAGE_LAST_ERROR or "").strip()


def _hotkey(*keys: str) -> dict[str, Any]:
    return {"type": "hotkey", "keys": list(keys)}


def _key(key: str) -> dict[str, Any]:
    return {"type": "key", "key": key}


def _mouse_click(button: str = "left", clicks: int = 1) -> dict[str, Any]:
    kind = "mouse_double" if clicks > 1 else "mouse_click"
    return {"type": kind, "button": button}


def _scroll(vertical: int = 0, horizontal: int = 0) -> dict[str, Any]:
    return {"type": "scroll", "vertical": vertical, "horizontal": horizontal}


_EXAMPLE_PRESETS_VERSION = 2
_EXAMPLE_CUSTOM_PRESETS: list[dict[str, Any]] = [
    {
        "name": "Открыть диспетчер задач",
        "action": _hotkey("ctrl", "shift", "esc"),
    },
    {
        "name": "Скриншот фрагмента экрана",
        "action": _hotkey("winleft", "shift", "s"),
    },
    {
        "name": "Показать рабочий стол",
        "action": _hotkey("winleft", "d"),
    },
    {
        "name": "Вставить шаблон приветствия",
        "action": {"type": "text", "text": "Здравствуйте! Напишите, если нужна помощь.", "repeat": 1, "interval_ms": 30},
    },
    {
        "name": "Правый клик мышью",
        "action": {"type": "mouse_click", "button": "right"},
    },
    {
        "name": "Прокрутка вниз",
        "action": {"type": "scroll", "vertical": -500, "horizontal": 0},
    },
]


_BUILTIN_DEFINITIONS: list[tuple[str, str, dict[str, Any], str]] = [
    ("Windows", "Открыть окно Выполнить", _hotkey("winleft", "r"), "Win + R"),
    ("Windows", "Открыть Проводник", _hotkey("winleft", "e"), "Win + E"),
    ("Windows", "Параметры Windows", _hotkey("winleft", "i"), "Win + I"),
    ("Windows", "Поиск Windows", _hotkey("winleft", "s"), "Win + S"),
    ("Windows", "Показать рабочий стол", _hotkey("winleft", "d"), "Win + D"),
    ("Windows", "Заблокировать компьютер", _hotkey("winleft", "l"), "Win + L"),
    ("Windows", "Быстрые ссылки", _hotkey("winleft", "x"), "Win + X"),
    ("Windows", "Буфер обмена Windows", _hotkey("winleft", "v"), "Win + V"),
    ("Windows", "Эмодзи-панель", _hotkey("winleft", "."), "Win + ."),
    ("Windows", "Сменить раскладку", _hotkey("winleft", "space"), "Win + Space"),
    ("Windows", "Быстрые настройки", _hotkey("winleft", "a"), "Win + A"),
    ("Windows", "Подключения/устройства", _hotkey("winleft", "k"), "Win + K"),
    ("Windows", "Проекция экрана", _hotkey("winleft", "p"), "Win + P"),
    ("Windows", "Диктовка", _hotkey("winleft", "h"), "Win + H"),
    ("Windows", "Панель игр Xbox", _hotkey("winleft", "g"), "Win + G"),
    ("Windows", "Запись (Xbox Game Bar)", _hotkey("winleft", "alt", "r"), "Win + Alt + R"),
    ("Windows", "Виртуальные рабочие столы", _hotkey("winleft", "tab"), "Win + Tab"),
    ("Windows", "Новый виртуальный стол", _hotkey("winleft", "ctrl", "d"), "Win + Ctrl + D"),
    ("Windows", "Предыдущий виртуальный стол", _hotkey("winleft", "ctrl", "left"), "Win + Ctrl + Left"),
    ("Windows", "Следующий виртуальный стол", _hotkey("winleft", "ctrl", "right"), "Win + Ctrl + Right"),
    ("Windows", "Закрыть виртуальный стол", _hotkey("winleft", "ctrl", "f4"), "Win + Ctrl + F4"),
    ("Windows", "Свернуть все окна", _hotkey("winleft", "m"), "Win + M"),
    ("Windows", "Восстановить свернутые окна", _hotkey("winleft", "shift", "m"), "Win + Shift + M"),
    ("Windows", "Свернуть остальные окна", _hotkey("winleft", "home"), "Win + Home"),
    ("Windows", "Привязать окно влево", _hotkey("winleft", "left"), "Win + Left"),
    ("Windows", "Привязать окно вправо", _hotkey("winleft", "right"), "Win + Right"),
    ("Windows", "Развернуть окно", _hotkey("winleft", "up"), "Win + Up"),
    ("Windows", "Свернуть/восстановить окно", _hotkey("winleft", "down"), "Win + Down"),
    ("Windows", "Перенести окно на левый монитор", _hotkey("winleft", "shift", "left"), "Win + Shift + Left"),
    ("Windows", "Перенести окно на правый монитор", _hotkey("winleft", "shift", "right"), "Win + Shift + Right"),
    ("Windows", "Снимок экрана в файл", _hotkey("winleft", "printscreen"), "Win + PrtSc"),
    ("Windows", "Фрагмент экрана", _hotkey("winleft", "shift", "s"), "Win + Shift + S"),
    ("Windows", "Лупа: увеличить", _hotkey("winleft", "+"), "Win + +"),
    ("Windows", "Лупа: уменьшить", _hotkey("winleft", "-"), "Win + -"),
    ("Windows", "Лупа: закрыть", _hotkey("winleft", "esc"), "Win + Esc"),
    ("Windows", "Спецвозможности", _hotkey("winleft", "u"), "Win + U"),
    ("Windows", "Перезапуск графического драйвера", _hotkey("winleft", "ctrl", "shift", "b"), "Win + Ctrl + Shift + B"),
    ("Windows", "Системные свойства", _hotkey("winleft", "pause"), "Win + Pause"),
    ("Windows", "Панель уведомлений (старые версии)", _hotkey("winleft", "b"), "Win + B"),
    ("Windows", "Переключение приложений панели задач", _hotkey("winleft", "t"), "Win + T"),
    ("Windows", "Быстрый просмотр рабочего стола", _hotkey("winleft", ","), "Win + ,"),
    ("Windows", "Открыть закреплённое приложение #1", _hotkey("winleft", "1"), "Win + 1"),
    ("Windows", "Открыть закреплённое приложение #2", _hotkey("winleft", "2"), "Win + 2"),
    ("Windows", "Открыть закреплённое приложение #3", _hotkey("winleft", "3"), "Win + 3"),
    ("Windows", "Открыть закреплённое приложение #4", _hotkey("winleft", "4"), "Win + 4"),
    ("Windows", "Открыть закреплённое приложение #5", _hotkey("winleft", "5"), "Win + 5"),
    ("Система", "Переключение приложений", _hotkey("alt", "tab"), "Alt + Tab"),
    ("Система", "Переключение приложений (назад)", _hotkey("alt", "shift", "tab"), "Alt + Shift + Tab"),
    ("Система", "Сменить окно без списка", _hotkey("alt", "esc"), "Alt + Esc"),
    ("Система", "Закрыть активное окно", _hotkey("alt", "f4"), "Alt + F4"),
    ("Система", "Системное меню окна", _hotkey("alt", "space"), "Alt + Space"),
    ("Система", "Экран безопасности Windows", _hotkey("ctrl", "alt", "delete"), "Ctrl + Alt + Delete"),
    ("Система", "Диспетчер задач", _hotkey("ctrl", "shift", "esc"), "Ctrl + Shift + Esc"),
    ("Система", "Открыть меню Пуск", _hotkey("ctrl", "esc"), "Ctrl + Esc"),
    ("Система", "Контекстное меню", _hotkey("shift", "f10"), "Shift + F10"),
    ("Система", "Повтор действия", _hotkey("f4"), "F4"),
    ("Система", "Переименование (часто)", _key("f2"), "F2"),
    ("Система", "Обновить", _key("f5"), "F5"),
    ("Система", "Полноэкранный режим", _key("f11"), "F11"),
    ("Текст", "Выделить всё", _hotkey("ctrl", "a"), "Ctrl + A"),
    ("Текст", "Копировать", _hotkey("ctrl", "c"), "Ctrl + C"),
    ("Текст", "Вставить", _hotkey("ctrl", "v"), "Ctrl + V"),
    ("Текст", "Вырезать", _hotkey("ctrl", "x"), "Ctrl + X"),
    ("Текст", "Отменить", _hotkey("ctrl", "z"), "Ctrl + Z"),
    ("Текст", "Повторить", _hotkey("ctrl", "y"), "Ctrl + Y"),
    ("Текст", "Повторить (вариант)", _hotkey("ctrl", "shift", "z"), "Ctrl + Shift + Z"),
    ("Текст", "Найти", _hotkey("ctrl", "f"), "Ctrl + F"),
    ("Текст", "Заменить", _hotkey("ctrl", "h"), "Ctrl + H"),
    ("Текст", "Открыть файл", _hotkey("ctrl", "o"), "Ctrl + O"),
    ("Текст", "Сохранить", _hotkey("ctrl", "s"), "Ctrl + S"),
    ("Текст", "Сохранить как", _hotkey("ctrl", "shift", "s"), "Ctrl + Shift + S"),
    ("Текст", "Печать", _hotkey("ctrl", "p"), "Ctrl + P"),
    ("Текст", "Новый документ", _hotkey("ctrl", "n"), "Ctrl + N"),
    ("Текст", "Закрыть документ/вкладку", _hotkey("ctrl", "w"), "Ctrl + W"),
    ("Текст", "Закрыть документ (вариант)", _hotkey("ctrl", "f4"), "Ctrl + F4"),
    ("Текст", "Удалить слово слева", _hotkey("ctrl", "backspace"), "Ctrl + Backspace"),
    ("Текст", "Удалить слово справа", _hotkey("ctrl", "delete"), "Ctrl + Delete"),
    ("Текст", "В начало строки", _key("home"), "Home"),
    ("Текст", "В конец строки", _key("end"), "End"),
    ("Текст", "В начало документа", _hotkey("ctrl", "home"), "Ctrl + Home"),
    ("Текст", "В конец документа", _hotkey("ctrl", "end"), "Ctrl + End"),
    ("Текст", "Выделить до начала строки", _hotkey("shift", "home"), "Shift + Home"),
    ("Текст", "Выделить до конца строки", _hotkey("shift", "end"), "Shift + End"),
    ("Текст", "Выделить слово влево", _hotkey("ctrl", "shift", "left"), "Ctrl + Shift + Left"),
    ("Текст", "Выделить слово вправо", _hotkey("ctrl", "shift", "right"), "Ctrl + Shift + Right"),
    ("Текст", "Копировать (Insert)", _hotkey("ctrl", "insert"), "Ctrl + Insert"),
    ("Текст", "Вставить (Insert)", _hotkey("shift", "insert"), "Shift + Insert"),
    ("Текст", "Вырезать (Delete)", _hotkey("shift", "delete"), "Shift + Delete"),
    ("Браузер", "Новая вкладка", _hotkey("ctrl", "t"), "Ctrl + T"),
    ("Браузер", "Новое окно", _hotkey("ctrl", "n"), "Ctrl + N"),
    ("Браузер", "Приватное окно", _hotkey("ctrl", "shift", "n"), "Ctrl + Shift + N"),
    ("Браузер", "Закрыть вкладку", _hotkey("ctrl", "w"), "Ctrl + W"),
    ("Браузер", "Открыть закрытую вкладку", _hotkey("ctrl", "shift", "t"), "Ctrl + Shift + T"),
    ("Браузер", "Следующая вкладка", _hotkey("ctrl", "tab"), "Ctrl + Tab"),
    ("Браузер", "Предыдущая вкладка", _hotkey("ctrl", "shift", "tab"), "Ctrl + Shift + Tab"),
    ("Браузер", "Перейти в адресную строку", _hotkey("ctrl", "l"), "Ctrl + L"),
    ("Браузер", "Обновить страницу", _hotkey("ctrl", "r"), "Ctrl + R"),
    ("Браузер", "Жёсткое обновление", _hotkey("ctrl", "shift", "r"), "Ctrl + Shift + R"),
    ("Браузер", "Масштаб +", _hotkey("ctrl", "="), "Ctrl + ="),
    ("Браузер", "Масштаб -", _hotkey("ctrl", "-"), "Ctrl + -"),
    ("Браузер", "Масштаб 100%", _hotkey("ctrl", "0"), "Ctrl + 0"),
    ("Браузер", "Загрузки", _hotkey("ctrl", "j"), "Ctrl + J"),
    ("Браузер", "История", _hotkey("ctrl", "h"), "Ctrl + H"),
    ("Браузер", "Закладки", _hotkey("ctrl", "d"), "Ctrl + D"),
    ("Браузер", "Панель закладок", _hotkey("ctrl", "shift", "b"), "Ctrl + Shift + B"),
    ("Браузер", "Стереть данные браузера", _hotkey("ctrl", "shift", "delete"), "Ctrl + Shift + Delete"),
    ("Браузер", "Инструменты разработчика", _hotkey("ctrl", "shift", "i"), "Ctrl + Shift + I"),
    ("Браузер", "Назад", _hotkey("alt", "left"), "Alt + Left"),
    ("Браузер", "Вперёд", _hotkey("alt", "right"), "Alt + Right"),
    ("Браузер", "Вкладка 1", _hotkey("ctrl", "1"), "Ctrl + 1"),
    ("Браузер", "Вкладка 2", _hotkey("ctrl", "2"), "Ctrl + 2"),
    ("Браузер", "Последняя вкладка", _hotkey("ctrl", "9"), "Ctrl + 9"),
    ("Проводник", "Новая папка", _hotkey("ctrl", "shift", "n"), "Ctrl + Shift + N"),
    ("Проводник", "Скопировать путь", _hotkey("ctrl", "shift", "c"), "Ctrl + Shift + C"),
    ("Проводник", "Выделить адресную строку", _hotkey("alt", "d"), "Alt + D"),
    ("Проводник", "Перейти в родительскую папку", _hotkey("alt", "up"), "Alt + Up"),
    ("Проводник", "Открыть свойства", _hotkey("alt", "enter"), "Alt + Enter"),
    ("Проводник", "Обновить список", _key("f5"), "F5"),
    ("Проводник", "Переименовать", _key("f2"), "F2"),
    ("Проводник", "Поиск в текущей папке", _hotkey("ctrl", "e"), "Ctrl + E"),
    ("Проводник", "Панель предпросмотра", _hotkey("alt", "p"), "Alt + P"),
    ("Проводник", "Выбрать адрес", _hotkey("ctrl", "l"), "Ctrl + L"),
    ("Скриншоты", "Скриншот всего экрана", _key("printscreen"), "PrtSc"),
    ("Скриншоты", "Скриншот активного окна", _hotkey("alt", "printscreen"), "Alt + PrtSc"),
    ("Скриншоты", "Фрагмент экрана Windows", _hotkey("winleft", "shift", "s"), "Win + Shift + S"),
    ("Мышь", "Левый клик", _mouse_click("left", 1), "ЛКМ"),
    ("Мышь", "Двойной левый клик", _mouse_click("left", 2), "ЛКМ x2"),
    ("Мышь", "Правый клик", _mouse_click("right", 1), "ПКМ"),
    ("Мышь", "Средний клик", _mouse_click("middle", 1), "СКМ"),
    ("Мышь", "Нажать ЛКМ (hold)", {"type": "mouse_down", "button": "left"}, "Mouse Down"),
    ("Мышь", "Отпустить ЛКМ", {"type": "mouse_up", "button": "left"}, "Mouse Up"),
    ("Мышь", "Колесо вверх", _scroll(vertical=400), "Wheel Up"),
    ("Мышь", "Колесо вниз", _scroll(vertical=-400), "Wheel Down"),
    ("Мышь", "Горизонтально влево", _scroll(horizontal=-250), "Wheel Left"),
    ("Мышь", "Горизонтально вправо", _scroll(horizontal=250), "Wheel Right"),
]


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _normalize_text(value: Any, max_len: int = 200) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text


def _normalize_name(value: Any) -> str:
    text = _normalize_text(value, _MAX_NAME_LEN)
    if not text:
        return "Без названия"
    return text


def _windows_keyboard_backend_ready() -> bool:
    return bool(os.name == "nt" and _WIN_SENDINPUT and _WIN_INPUT_STRUCT and _WIN_KEYBDINPUT_STRUCT)


def _is_pyautogui_key_supported(key: str) -> bool:
    if not pyautogui:
        return False
    token = str(key or "").strip().lower()
    if not token:
        return False

    platform_module = getattr(pyautogui, "platformModule", None)
    mapping = getattr(platform_module, "keyboardMapping", None)
    if isinstance(mapping, dict):
        mapped = mapping.get(token)
        if mapped is None or mapped == -1:
            return False
        return True

    if hasattr(pyautogui, "KEYBOARD_KEYS"):
        try:
            return token in pyautogui.KEYBOARD_KEYS
        except Exception:
            return False
    return False


def _resolve_windows_key_token(token: str) -> tuple[int, tuple[int, ...]] | None:
    normalized = str(token or "").strip().lower()
    if not normalized:
        return None

    if normalized in _WIN_VK_KEYS:
        return _WIN_VK_KEYS[normalized], ()

    fn_match = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", normalized)
    if fn_match:
        return 0x6F + int(fn_match.group(1)), ()

    if len(normalized) == 1:
        char = normalized
        if "a" <= char <= "z":
            return ord(char.upper()), ()
        if "0" <= char <= "9":
            return ord(char), ()
        mapped = _WIN_CHAR_KEYMAP.get(char)
        if mapped:
            vk_code, implicit_names = mapped
            implicit_vks = tuple(_WIN_VK_KEYS[name] for name in implicit_names if name in _WIN_VK_KEYS)
            return vk_code, implicit_vks

        if _WIN_VK_KEYSCAN:
            try:
                vk_state = int(_WIN_VK_KEYSCAN(char))
            except Exception:
                vk_state = -1
            if vk_state != -1:
                vk_code = vk_state & 0xFF
                modifiers = (vk_state >> 8) & 0xFF
                implicit_vks: list[int] = []
                if modifiers & 1:
                    implicit_vks.append(_WIN_VK_KEYS["shift"])
                if modifiers & 2:
                    implicit_vks.append(_WIN_VK_KEYS["ctrl"])
                if modifiers & 4:
                    implicit_vks.append(_WIN_VK_KEYS["alt"])
                if vk_code:
                    return vk_code, tuple(implicit_vks)
    return None


def _send_windows_key_event(vk_code: int, key_up: bool = False, scan_code: int = 0, unicode_mode: bool = False) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для клавиатуры недоступен."
    if not ctypes:
        return False, "ctypes недоступен."

    flags = _WIN_KEYEVENTF_KEYUP if key_up else 0
    if unicode_mode:
        flags |= _WIN_KEYEVENTF_UNICODE
        vk_code = 0

    input_obj = _WIN_INPUT_STRUCT()
    input_obj.type = _WIN_INPUT_KEYBOARD
    input_obj.ki = _WIN_KEYBDINPUT_STRUCT(
        wVk=vk_code,
        wScan=scan_code,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )

    ctypes.set_last_error(0)
    sent = int(_WIN_SENDINPUT(1, ctypes.byref(input_obj), ctypes.sizeof(_WIN_INPUT_STRUCT)))
    if sent != 1:
        return False, f"SendInput вернул {sent}, WinError={ctypes.get_last_error()}."
    return True, ""


def _release_pressed_windows_keys(pressed: list[int]) -> None:
    for vk_code in reversed(pressed):
        try:
            _send_windows_key_event(vk_code, key_up=True)
        except Exception:
            continue


def _send_windows_hotkey(keys: list[str]) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для hotkey недоступен."
    if not keys:
        return False, "Для hotkey не указаны клавиши."

    pressed: list[int] = []
    pressed_set: set[int] = set()
    error_message = ""

    try:
        for raw_key in keys:
            token = str(raw_key or "").strip().lower()
            resolved = _resolve_windows_key_token(token)
            if not resolved:
                error_message = f"Клавиша '{raw_key}' не поддерживается в Windows API."
                break

            vk_code, implicit_modifiers = resolved
            for modifier_vk in implicit_modifiers:
                if modifier_vk in pressed_set:
                    continue
                ok, message = _send_windows_key_event(modifier_vk, key_up=False)
                if not ok:
                    error_message = f"Не удалось зажать модификатор для '{raw_key}': {message}"
                    break
                pressed.append(modifier_vk)
                pressed_set.add(modifier_vk)
            if error_message:
                break

            if vk_code in pressed_set:
                continue
            ok, message = _send_windows_key_event(vk_code, key_up=False)
            if not ok:
                error_message = f"Не удалось нажать '{raw_key}': {message}"
                break
            pressed.append(vk_code)
            pressed_set.add(vk_code)
    finally:
        _release_pressed_windows_keys(pressed)

    if error_message:
        return False, error_message
    return True, ""


def _send_windows_key(key: str) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для клавиши недоступен."

    token = str(key or "").strip().lower()
    if not token:
        return False, "Клавиша не указана."

    resolved = _resolve_windows_key_token(token)
    if not resolved:
        return False, f"Клавиша '{key}' не поддерживается в Windows API."

    vk_code, implicit_modifiers = resolved
    pressed_mods: list[int] = []

    try:
        for modifier_vk in implicit_modifiers:
            ok, message = _send_windows_key_event(modifier_vk, key_up=False)
            if not ok:
                return False, f"Не удалось зажать модификатор для '{key}': {message}"
            pressed_mods.append(modifier_vk)

        ok_down, down_message = _send_windows_key_event(vk_code, key_up=False)
        if not ok_down:
            return False, f"Не удалось нажать '{key}': {down_message}"

        ok_up, up_message = _send_windows_key_event(vk_code, key_up=True)
        if not ok_up:
            return False, f"Не удалось отпустить '{key}': {up_message}"
    finally:
        _release_pressed_windows_keys(pressed_mods)

    return True, ""


def _send_windows_text(text: str) -> tuple[bool, str]:
    if not _windows_keyboard_backend_ready():
        return False, "Windows API для текста недоступен."

    for index, ch in enumerate(str(text)):
        if ch == "\r":
            continue
        if ch == "\n":
            ok, message = _send_windows_key("enter")
            if not ok:
                return False, f"Ошибка на символе #{index + 1} (newline): {message}"
            continue
        if ch == "\t":
            ok, message = _send_windows_key("tab")
            if not ok:
                return False, f"Ошибка на символе #{index + 1} (tab): {message}"
            continue

        data = ch.encode("utf-16-le")
        for offset in range(0, len(data), 2):
            unit = int.from_bytes(data[offset : offset + 2], "little")
            ok_down, down_message = _send_windows_key_event(0, key_up=False, scan_code=unit, unicode_mode=True)
            if not ok_down:
                return False, f"Ошибка на символе #{index + 1}: {down_message}"
            ok_up, up_message = _send_windows_key_event(0, key_up=True, scan_code=unit, unicode_mode=True)
            if not ok_up:
                return False, f"Ошибка на символе #{index + 1}: {up_message}"

    return True, ""


def _is_compiled_runtime() -> bool:
    try:
        if bool(globals().get("__compiled__", False)):
            return True
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        return True

    for env_name in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        if str(os.environ.get(env_name, "") or "").strip():
            return True

    try:
        exe = Path(str(getattr(sys, "executable", "") or "")).name.lower()
        if exe.endswith(".exe") and exe not in ("python.exe", "pythonw.exe"):
            return True
    except Exception:
        pass

    return False


def _is_temp_runtime_path(path: Path) -> bool:
    lower = str(path).lower()
    if "_mei" in lower or "\\onefile_" in lower or "/onefile_" in lower:
        return True
    for env_name in ("NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        env_val = str(os.environ.get(env_name, "") or "").strip()
        if not env_val:
            continue
        try:
            env_path = Path(env_val).resolve()
        except Exception:
            env_path = Path(env_val)
        try:
            path.resolve().relative_to(env_path)
            return True
        except Exception:
            continue
    return False


def _candidate_base_dirs() -> list[Path]:
    items: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except Exception:
            resolved = Path(str(path))
        key = os.path.normcase(str(resolved))
        if key in seen:
            return
        seen.add(key)
        items.append(resolved)

    if _is_compiled_runtime():
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass

    panel_base_env = str(os.environ.get("PANEL_BASE_DIR", "") or "").strip()
    if panel_base_env:
        _add(Path(panel_base_env))

    try:
        cfg_base = current_app.config.get("BASE_DIR")
    except Exception:
        cfg_base = None
    if cfg_base:
        _add(Path(str(cfg_base)))

    try:
        _add(Path(sys.argv[0]).resolve().parent)
    except Exception:
        pass

    try:
        _add(Path.cwd().resolve())
    except Exception:
        pass

    if not _is_compiled_runtime():
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass

    return items


def _resolve_storage_file() -> Path:
    global _STORAGE_FILE_PATH
    if _STORAGE_FILE_PATH:
        return _STORAGE_FILE_PATH

    fallback = Path.cwd() / "data" / _DATA_FILE_NAME
    for base in _candidate_base_dirs():
        if _is_temp_runtime_path(base):
            continue
        data_dir = base / "data"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        _STORAGE_FILE_PATH = data_dir / _DATA_FILE_NAME
        return _STORAGE_FILE_PATH

    try:
        fallback.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _STORAGE_FILE_PATH = fallback
    return _STORAGE_FILE_PATH


def _default_store() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": 0,
        "example_presets_version": 0,
        "custom_actions": [],
        "history": [],
    }


def _normalize_key_token(token: Any) -> str | None:
    text = str(token or "").strip().lower()
    if not text:
        return None
    text = _KEY_ALIASES.get(text, text)

    if os.name == "nt":
        if _windows_keyboard_backend_ready():
            if _resolve_windows_key_token(text):
                return text
            return None
        if _is_pyautogui_key_supported(text):
            return text
        return None

    if _is_pyautogui_key_supported(text):
        return text
    if len(text) == 1:
        return text
    if re.fullmatch(r"f([1-9]|1[0-2]|2[0-4])", text):
        return text
    return None


def _normalize_hotkey(value: Any) -> tuple[list[str], list[str]]:
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = [part for part in value.split("+")]
    elif isinstance(value, list):
        raw_items = [str(item) for item in value]

    normalized: list[str] = []
    invalid: list[str] = []
    for item in raw_items:
        source = str(item or "").strip()
        if not source:
            continue
        key = _normalize_key_token(source)
        if not key:
            invalid.append(source)
            continue
        normalized.append(key)
    return normalized, invalid


def _available_single_keys() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_option(raw_value: str, raw_label: str = "") -> None:
        normalized = _normalize_key_token(raw_value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        label = str(raw_label or "").strip() or _display_key(normalized)
        options.append({"value": normalized, "label": label})

    for value, label in _SINGLE_KEY_SUGGESTIONS:
        add_option(value, label)

    for ch in "abcdefghijklmnopqrstuvwxyz":
        add_option(ch, ch.upper())
    for digit in "0123456789":
        add_option(digit, digit)
    for idx in range(1, 25):
        key_name = f"f{idx}"
        add_option(key_name, key_name.upper())

    return options


def _normalize_button(value: Any) -> str:
    button = str(value or "left").strip().lower()
    if button in _MOUSE_BUTTONS:
        return button
    return "left"


def _normalize_action_payload(payload: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict):
        return None, "Некорректный формат команды."

    action_type = str(payload.get("type") or "").strip().lower()
    if not action_type:
        return None, "Не указан тип действия."
    if action_type not in _ALLOWED_ACTION_TYPES:
        return None, f"Неизвестный тип действия: {action_type}"

    repeat = _coerce_int(payload.get("repeat"), 1)
    interval_ms = _coerce_int(payload.get("interval_ms"), 30)
    repeat = max(1, min(_MAX_REPEAT, repeat))
    interval_ms = max(0, min(_MAX_INTERVAL_MS, interval_ms))

    action: dict[str, Any] = {
        "type": action_type,
        "repeat": repeat,
        "interval_ms": interval_ms,
    }

    if action_type == "hotkey":
        keys, invalid = _normalize_hotkey(payload.get("keys") or payload.get("combo") or payload.get("hotkey"))
        if invalid:
            preview = ", ".join(invalid[:6])
            if len(invalid) > 6:
                preview = f"{preview}, ..."
            return None, f"Не удалось распознать клавиши: {preview}"
        if not keys:
            return None, "Для hotkey нужна хотя бы одна клавиша."
        action["keys"] = keys
        return action, ""

    if action_type == "key":
        key = _normalize_key_token(payload.get("key"))
        if not key:
            return None, "Не удалось распознать клавишу."
        action["key"] = key
        return action, ""

    if action_type == "text":
        text = str(payload.get("text") or "")
        if not text:
            return None, "Текст пустой."
        if len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN]
        action["text"] = text
        return action, ""

    if action_type in {"mouse_click", "mouse_double", "mouse_down", "mouse_up"}:
        action["button"] = _normalize_button(payload.get("button"))
        x = payload.get("x")
        y = payload.get("y")
        if x is not None:
            action["x"] = _coerce_int(x)
        if y is not None:
            action["y"] = _coerce_int(y)
        return action, ""

    if action_type == "scroll":
        vertical = _coerce_int(payload.get("vertical"), 0)
        horizontal = _coerce_int(payload.get("horizontal"), 0)

        direction = str(payload.get("direction") or "").strip().lower()
        amount = _coerce_int(payload.get("amount"), 0)
        if direction and amount:
            if direction == "up":
                vertical = abs(amount)
            elif direction == "down":
                vertical = -abs(amount)
            elif direction == "left":
                horizontal = -abs(amount)
            elif direction == "right":
                horizontal = abs(amount)

        if vertical == 0 and horizontal == 0:
            return None, "Для прокрутки укажите vertical/horizontal или direction+amount."

        action["vertical"] = vertical
        action["horizontal"] = horizontal
        x = payload.get("x")
        y = payload.get("y")
        if x is not None:
            action["x"] = _coerce_int(x)
        if y is not None:
            action["y"] = _coerce_int(y)
        return action, ""

    return None, "Некорректный формат действия."


def _display_key(key: str) -> str:
    if key in _KEY_DISPLAY:
        return _KEY_DISPLAY[key]
    if len(key) == 1:
        return key.upper()
    return key


def _action_summary(action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "")
    if action_type == "hotkey":
        keys = action.get("keys") or []
        if isinstance(keys, list) and keys:
            return " + ".join(_display_key(str(item)) for item in keys)
        return "Hotkey"
    if action_type == "key":
        return _display_key(str(action.get("key") or ""))
    if action_type == "text":
        text = str(action.get("text") or "")
        text = text.replace("\n", " ").strip()
        return text[:80] + ("..." if len(text) > 80 else "")
    if action_type in {"mouse_click", "mouse_double", "mouse_down", "mouse_up"}:
        button = str(action.get("button") or "left")
        label = {
            "mouse_click": "Mouse Click",
            "mouse_double": "Mouse Double Click",
            "mouse_down": "Mouse Down",
            "mouse_up": "Mouse Up",
        }.get(action_type, action_type)
        return f"{label} ({button})"
    if action_type == "scroll":
        v = _coerce_int(action.get("vertical"), 0)
        h = _coerce_int(action.get("horizontal"), 0)
        return f"Scroll v={v}, h={h}"
    return action_type


def _extend_builtin_with_single_keys() -> None:
    category = "Клавиши по отдельности"
    existing_pairs: set[tuple[str, str]] = set()
    for item in _BUILTIN_DEFINITIONS:
        if len(item) != 4:
            continue
        item_category, _, action, _ = item
        if not isinstance(action, dict):
            continue
        if str(action.get("type") or "").strip().lower() != "key":
            continue
        key_name = str(action.get("key") or "").strip().lower()
        if key_name:
            existing_pairs.add((str(item_category or ""), key_name))

    for entry in _available_single_keys():
        if not isinstance(entry, dict):
            continue
        key_value = str(entry.get("value") or "").strip().lower()
        if not key_value:
            continue
        pair = (category, key_value)
        if pair in existing_pairs:
            continue
        existing_pairs.add(pair)

        label = str(entry.get("label") or "").strip() or _display_key(key_value)
        _BUILTIN_DEFINITIONS.append(
            (
                category,
                f"Клавиша {label}",
                _key(key_value),
                label,
            )
        )


def _hotkey_signature(keys: list[str]) -> tuple[str, ...]:
    return tuple(sorted(str(item or "").strip().lower() for item in keys if str(item or "").strip()))


def _is_secure_attention_hotkey(keys: list[str]) -> bool:
    return _hotkey_signature(keys) == ("alt", "ctrl", "delete")


def _send_secure_attention_hotkey(keys: list[str]) -> tuple[bool, str]:
    details: list[str] = []

    try:
        from ...views import remote_desktop as remote_desktop_view
    except Exception as exc:
        remote_desktop_view = None  # type: ignore[assignment]
        details.append(f"secure backend import: {exc}")

    send_fn = getattr(remote_desktop_view, "_send_secure_attention_hotkey", None) if remote_desktop_view else None
    if callable(send_fn):
        try:
            backend, message = send_fn(list(keys))
            text = str(message or "").strip()
            if not text:
                backend_name = str(backend or "").strip() or "backend"
                text = f"Ctrl+Alt+Delete отправлен ({backend_name})."
            return True, text
        except Exception as exc:
            details.append(str(exc).strip() or "secure backend execution failed")
    else:
        details.append("secure backend unavailable")

    ok, fallback_message = _send_windows_hotkey(keys)
    if ok:
        if details:
            return True, "Ctrl+Alt+Delete отправлен через Windows API (fallback)."
        return True, "Ctrl+Alt+Delete отправлен через Windows API."

    fallback_text = str(fallback_message or "").strip()
    if details:
        detail_text = "; ".join(item for item in details if item)
        if fallback_text:
            return False, f"{detail_text}. fallback: {fallback_text}"
        return False, detail_text
    return False, fallback_text or "Ctrl+Alt+Delete не отправлен."


def _blocked_reason(action: dict[str, Any]) -> str:
    return ""


def _session_warning() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        session_id = ctypes.c_ulong()
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            return ""
        active_id = kernel32.WTSGetActiveConsoleSessionId()
        if active_id != session_id.value:
            return (
                " Команда выполнена в системной сессии; "
                "на рабочем столе пользователя эффект может быть не виден."
            )
    except Exception:
        return ""
    return ""


def _normalize_custom_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action, error = _normalize_action_payload(item.get("action") or {})
        if error or not action:
            continue
        item_id = str(item.get("id") or "").strip() or uuid.uuid4().hex[:12]
        created_at = _coerce_int(item.get("created_at"), int(time.time()))
        updated_at = _coerce_int(item.get("updated_at"), created_at)
        normalized.append(
            {
                "id": item_id,
                "name": _normalize_name(item.get("name")),
                "action": action,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    normalized.sort(key=lambda x: int(x.get("updated_at", 0)), reverse=True)
    return normalized[:_MAX_CUSTOM_ACTIONS]


def _ensure_example_presets_no_lock(store: dict[str, Any]) -> bool:
    changed = False
    current_version = _coerce_int(store.get("example_presets_version"), 0)
    custom_items = list(store.get("custom_actions") or [])
    if custom_items or current_version >= _EXAMPLE_PRESETS_VERSION:
        if store.get("example_presets_version") != current_version:
            store["example_presets_version"] = current_version
            changed = True
        return changed

    now = int(time.time())
    generated: list[dict[str, Any]] = []
    for item in _EXAMPLE_CUSTOM_PRESETS:
        action, error = _normalize_action_payload(item.get("action") or {})
        if error or not action:
            continue
        generated.append(
            {
                "id": uuid.uuid4().hex[:12],
                "name": _normalize_name(item.get("name")),
                "action": action,
                "created_at": now,
                "updated_at": now,
            }
        )
    if generated:
        store["custom_actions"] = _normalize_custom_items(generated + custom_items)
        changed = True
    if current_version != _EXAMPLE_PRESETS_VERSION:
        store["example_presets_version"] = _EXAMPLE_PRESETS_VERSION
        changed = True
    return changed


def _normalize_history_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action, error = _normalize_action_payload(item.get("action") or {})
        if error or not action:
            continue
        ts = _coerce_int(item.get("ts"), int(time.time()))
        history_item = {
            "id": str(item.get("id") or "").strip() or uuid.uuid4().hex[:12],
            "ts": ts,
            "source": _normalize_text(item.get("source"), 40) or "manual",
            "name": _normalize_name(item.get("name") or "Команда"),
            "summary": _normalize_text(item.get("summary") or _action_summary(action), 200),
            "ok": bool(item.get("ok")),
            "message": _normalize_text(item.get("message"), 260),
            "user": _normalize_text(item.get("user"), 80),
            "action": action,
        }
        normalized.append(history_item)
    normalized.sort(key=lambda x: int(x.get("ts", 0)), reverse=True)
    return normalized[:_MAX_HISTORY_ITEMS]


def _normalize_store(raw: Any) -> dict[str, Any]:
    store = _default_store()
    if isinstance(raw, dict):
        store["version"] = _coerce_int(raw.get("version"), 1)
        store["updated_at"] = _coerce_int(raw.get("updated_at"), 0)
        store["example_presets_version"] = _coerce_int(raw.get("example_presets_version"), 0)
        store["custom_actions"] = _normalize_custom_items(raw.get("custom_actions"))
        store["history"] = _normalize_history_items(raw.get("history"))
    return store


def _read_store_no_lock() -> dict[str, Any]:
    path = _resolve_storage_file()
    if not path.exists():
        _clear_storage_error()
        return _default_store()

    raw_data = ""
    decode_error = ""
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "utf-16"):
        try:
            raw_data = path.read_text(encoding=encoding)
            break
        except Exception as exc:
            decode_error = f"{type(exc).__name__}: {exc}"
            continue

    if not raw_data:
        if decode_error:
            _set_storage_error(f"Не удалось прочитать JSON-хранилище ({path}): {decode_error}")
        return _default_store()

    try:
        payload = json.loads(raw_data)
    except Exception as exc:
        _set_storage_error(f"Повреждено JSON-хранилище ({path}): {type(exc).__name__}: {exc}")
        try:
            broken_name = path.with_name(f"{path.stem}.broken_{int(time.time())}{path.suffix}")
            path.replace(broken_name)
        except Exception:
            pass
        return _default_store()

    _clear_storage_error()
    return _normalize_store(payload)


def _write_store_no_lock(store: dict[str, Any]) -> None:
    try:
        path = _resolve_storage_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _normalize_store(store)
        payload["updated_at"] = int(time.time())
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        _clear_storage_error()
    except Exception as exc:
        _set_storage_error(f"Ошибка записи JSON-хранилища ({path}): {type(exc).__name__}: {exc}")
        raise


def _load_store() -> dict[str, Any]:
    with _STORAGE_LOCK:
        store = _read_store_no_lock()
        if _ensure_example_presets_no_lock(store):
            try:
                _write_store_no_lock(store)
            except Exception:
                pass
        return _normalize_store(store)


def _append_history(
    action: dict[str, Any],
    *,
    source: str,
    name: str,
    ok: bool,
    message: str,
    user: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with _STORAGE_LOCK:
        store = _read_store_no_lock()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": int(time.time()),
            "source": _normalize_text(source, 40) or "manual",
            "name": _normalize_name(name),
            "summary": _action_summary(action),
            "ok": bool(ok),
            "message": _normalize_text(message, 260),
            "user": _normalize_text(user, 80),
            "action": action,
        }
        history = [entry] + list(store.get("history") or [])
        store["history"] = _normalize_history_items(history)
        try:
            _write_store_no_lock(store)
        except Exception:
            pass
        return entry, list(store["history"])


def _run_mouse_action(action_type: str, action: dict[str, Any]) -> None:
    if not pyautogui:
        raise RuntimeError("pyautogui недоступен")

    button = _normalize_button(action.get("button"))
    has_xy = ("x" in action and "y" in action)
    x = _coerce_int(action.get("x"), 0)
    y = _coerce_int(action.get("y"), 0)

    if action_type == "mouse_click":
        if has_xy:
            pyautogui.click(x=x, y=y, button=button)
        else:
            pyautogui.click(button=button)
        return

    if action_type == "mouse_double":
        if has_xy:
            pyautogui.doubleClick(x=x, y=y, button=button)
        else:
            pyautogui.doubleClick(button=button)
        return

    if action_type == "mouse_down":
        if has_xy:
            pyautogui.mouseDown(x=x, y=y, button=button)
        else:
            pyautogui.mouseDown(button=button)
        return

    if action_type == "mouse_up":
        if has_xy:
            pyautogui.mouseUp(x=x, y=y, button=button)
        else:
            pyautogui.mouseUp(button=button)


def _execute_action(action: dict[str, Any]) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Отправка клавиш и мыши поддерживается только в Windows."

    action_type = str(action.get("type") or "")
    repeat = max(1, min(_MAX_REPEAT, _coerce_int(action.get("repeat"), 1)))
    interval_ms = max(0, min(_MAX_INTERVAL_MS, _coerce_int(action.get("interval_ms"), 30)))
    keyboard_backend = "windows_api" if _windows_keyboard_backend_ready() else "pyautogui"

    blocked = _blocked_reason(action)
    if blocked:
        return False, blocked

    if action_type in {"mouse_click", "mouse_double", "mouse_down", "mouse_up", "scroll"} and not pyautogui:
        return False, "Для действий мыши нужен pyautogui. Установите библиотеку и перезапустите сервис."
    if action_type in {"hotkey", "key", "text"} and keyboard_backend == "pyautogui" and not pyautogui:
        return False, "Ни Windows API, ни pyautogui недоступны для отправки клавиш."

    try:
        for index in range(repeat):
            if action_type == "hotkey":
                keys = [str(item) for item in (action.get("keys") or [])]
                if not keys:
                    return False, "Для hotkey не указаны клавиши."
                if _is_secure_attention_hotkey(keys):
                    ok, message = _send_secure_attention_hotkey(keys)
                    if not ok:
                        return False, f"Hotkey не отправлен: {message}"
                    continue
                if keyboard_backend == "windows_api":
                    ok, message = _send_windows_hotkey(keys)
                    if not ok:
                        return False, f"Hotkey не отправлен: {message}"
                else:
                    unsupported = [key for key in keys if not _is_pyautogui_key_supported(key)]
                    if unsupported:
                        preview = ", ".join(unsupported)
                        return False, f"Hotkey не отправлен: pyautogui не поддерживает клавиши: {preview}"
                    pyautogui.hotkey(*keys)
            elif action_type == "key":
                key = str(action.get("key") or "").strip()
                if not key:
                    return False, "Клавиша не указана."
                if keyboard_backend == "windows_api":
                    ok, message = _send_windows_key(key)
                    if not ok:
                        return False, f"Клавиша не отправлена: {message}"
                else:
                    if not _is_pyautogui_key_supported(key):
                        return False, f"Клавиша не отправлена: pyautogui не поддерживает '{key}'."
                    pyautogui.press(key)
            elif action_type == "text":
                text = str(action.get("text") or "")
                if not text:
                    return False, "Текст пустой."
                if keyboard_backend == "windows_api":
                    ok, message = _send_windows_text(text)
                    if not ok:
                        return False, f"Текст не отправлен: {message}"
                else:
                    pyautogui.write(text, interval=0)
            elif action_type in {"mouse_click", "mouse_double", "mouse_down", "mouse_up"}:
                _run_mouse_action(action_type, action)
            elif action_type == "scroll":
                vertical = _coerce_int(action.get("vertical"), 0)
                horizontal = _coerce_int(action.get("horizontal"), 0)
                x = action.get("x")
                y = action.get("y")
                x_val = _coerce_int(x) if x is not None else None
                y_val = _coerce_int(y) if y is not None else None

                if vertical:
                    if x_val is not None and y_val is not None:
                        pyautogui.scroll(vertical, x=x_val, y=y_val)
                    else:
                        pyautogui.scroll(vertical)
                if horizontal:
                    if not hasattr(pyautogui, "hscroll"):
                        return False, "Горизонтальная прокрутка не поддерживается текущей версией pyautogui."
                    if x_val is not None and y_val is not None:
                        pyautogui.hscroll(horizontal, x=x_val, y=y_val)
                    else:
                        pyautogui.hscroll(horizontal)
            else:
                return False, "Неподдерживаемый тип действия."

            if index < repeat - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

        warning = _session_warning()
        backend = "Windows API" if action_type in {"hotkey", "key", "text"} and keyboard_backend == "windows_api" else "pyautogui"
        repeat_suffix = f" x{repeat}" if repeat > 1 else ""
        return True, f"OK: команда отправлена ({backend}): {_action_summary(action)}{repeat_suffix}.{warning}"
    except Exception as exc:
        return False, f"Ошибка отправки: {type(exc).__name__}: {exc}"


def _preset_id(index: int, category: str, name: str) -> str:
    raw = f"{index}_{category}_{name}".lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = raw.strip("_")
    if not raw:
        raw = f"preset_{index:03d}"
    return raw


def _build_builtin_actions() -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for idx, (category, name, action, combo) in enumerate(_BUILTIN_DEFINITIONS, start=1):
        normalized, error = _normalize_action_payload(action)
        if error or not normalized:
            continue
        blocked_reason = _blocked_reason(normalized)
        actions.append(
            {
                "id": _preset_id(idx, category, name),
                "name": name,
                "category": category,
                "combo": combo or _action_summary(normalized),
                "action": copy.deepcopy(normalized),
                "blocked": bool(blocked_reason),
                "blocked_reason": blocked_reason,
            }
        )
    return actions


_extend_builtin_with_single_keys()
_BUILTIN_ACTIONS = _build_builtin_actions()


def _is_csrf_valid() -> bool:
    payload = {}
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or payload.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _template_root() -> Path:
    if _TEMPLATE_ROOT:
        try:
            return Path(_TEMPLATE_ROOT)
        except Exception:
            pass
    return Path(__file__).resolve().parent


def _load_template(root: Path | None = None) -> str:
    template_path = (root or _template_root()) / "index.html"
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return template_path.read_text(encoding=encoding).lstrip("\ufeff")
        except Exception:
            continue
    return ""


def _current_state_payload() -> dict[str, Any]:
    store = _load_store()
    warnings: list[str] = []
    if os.name != "nt":
        warnings.append("Текущая ОС не Windows. Команды отправки будут недоступны.")
    if not _windows_keyboard_backend_ready() and not pyautogui:
        warnings.append("Нет Windows API и pyautogui: отправка клавиш недоступна.")
    if not pyautogui:
        warnings.append("Не найден pyautogui: действия мыши и прокрутка недоступны.")
    storage_error = _get_storage_error()
    if storage_error:
        warnings.append(storage_error)

    return {
        "ok": True,
        "builtins": _BUILTIN_ACTIONS,
        "custom_actions": store.get("custom_actions") or [],
        "history": store.get("history") or [],
        "single_keys": _available_single_keys(),
        "storage_file": str(_resolve_storage_file()),
        "limits": {
            "max_name_len": _MAX_NAME_LEN,
            "max_text_len": _MAX_TEXT_LEN,
            "max_repeat": _MAX_REPEAT,
            "max_interval_ms": _MAX_INTERVAL_MS,
            "max_custom_actions": _MAX_CUSTOM_ACTIONS,
            "max_history_items": _MAX_HISTORY_ITEMS,
        },
        "pyautogui_available": bool(pyautogui),
        "windows_keyboard_api_available": _windows_keyboard_backend_ready(),
        "windows": os.name == "nt",
        "warnings": warnings,
    }


class WinKeysView(BaseView):
    route_base = "/plugins/winkeys"
    base_permissions = ["can_list"]

    def _render(self):
        template_source = _load_template()
        if not template_source:
            return "Шаблон расширения не найден.", 500

        return render_template_string(
            template_source,
            config_url=url_for(f"{self.__class__.__name__}.config"),
            send_url=url_for(f"{self.__class__.__name__}.send"),
            custom_save_url=url_for(f"{self.__class__.__name__}.custom_save"),
            custom_delete_url=url_for(f"{self.__class__.__name__}.custom_delete"),
            custom_clear_url=url_for(f"{self.__class__.__name__}.custom_clear"),
            history_clear_url=url_for(f"{self.__class__.__name__}.history_clear"),
            static_url="/plugins/winkeys/static",
            base_template=self.appbuilder.base_template,
            appbuilder=self.appbuilder,
            current_app=current_app,
        )

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        return self._render()

    @expose("/config", methods=["GET"])
    @has_access
    @permission_name("list")
    def config(self):
        return jsonify(_current_state_payload())

    @expose("/send", methods=["POST"])
    @has_access
    @permission_name("list")
    def send(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        action_payload = payload.get("action") if isinstance(payload, dict) else None
        if not isinstance(action_payload, dict):
            action_payload = payload if isinstance(payload, dict) else {}

        action, error = _normalize_action_payload(action_payload)
        if error or not action:
            return jsonify({"ok": False, "error": error or "Некорректная команда."}), 400

        source = _normalize_text(payload.get("source"), 40) or "manual"
        name = _normalize_name(payload.get("name") or payload.get("title") or _action_summary(action))

        ok, message = _execute_action(action)
        user = getattr(current_user, "username", "web")
        history_entry, history = _append_history(
            action,
            source=source,
            name=name,
            ok=ok,
            message=message,
            user=str(user),
        )

        try:
            _LOGGER.info(
                "winkeys user=%s ok=%s source=%s action=%s summary=%s",
                user,
                ok,
                source,
                action.get("type"),
                history_entry.get("summary"),
            )
        except Exception:
            pass

        status = 200 if ok else 500
        action_summary = _action_summary(action)
        response = {
            "ok": ok,
            "message": message,
            "status": "success" if ok else "error",
            "delivered": bool(ok),
            "action_summary": action_summary,
            "action_type": action.get("type"),
            "history_entry": history_entry,
            "history": history,
        }
        if not ok:
            response["error"] = message
        return jsonify(response), status

    @expose("/custom/save", methods=["POST"])
    @has_access
    @permission_name("list")
    def custom_save(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        custom_id = str(payload.get("id") or "").strip()
        name = _normalize_name(payload.get("name"))

        action_payload = payload.get("action") if isinstance(payload.get("action"), dict) else payload
        action, error = _normalize_action_payload(action_payload)
        if error or not action:
            return jsonify({"ok": False, "error": error or "Некорректное действие."}), 400

        now = int(time.time())

        with _STORAGE_LOCK:
            store = _read_store_no_lock()
            custom_actions = list(store.get("custom_actions") or [])

            updated = False
            for item in custom_actions:
                if str(item.get("id")) != custom_id or not custom_id:
                    continue
                item["name"] = name
                item["action"] = action
                item["updated_at"] = now
                updated = True
                break

            if not updated:
                if len(custom_actions) >= _MAX_CUSTOM_ACTIONS:
                    return jsonify({"ok": False, "error": "Превышен лимит сохранённых команд."}), 400
                custom_actions.append(
                    {
                        "id": custom_id or uuid.uuid4().hex[:12],
                        "name": name,
                        "action": action,
                        "created_at": now,
                        "updated_at": now,
                    }
                )

            custom_actions = _normalize_custom_items(custom_actions)
            store["custom_actions"] = custom_actions
            try:
                _write_store_no_lock(store)
            except Exception:
                return jsonify({"ok": False, "error": _get_storage_error() or "Ошибка записи JSON-хранилища."}), 500

        return jsonify(
            {
                "ok": True,
                "message": "Команда сохранена.",
                "custom_actions": custom_actions,
            }
        )

    @expose("/custom/delete", methods=["POST"])
    @has_access
    @permission_name("list")
    def custom_delete(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        payload = request.get_json(silent=True) or {}
        custom_id = str(payload.get("id") or "").strip()
        if not custom_id:
            return jsonify({"ok": False, "error": "Не указан ID команды."}), 400

        with _STORAGE_LOCK:
            store = _read_store_no_lock()
            custom_actions = list(store.get("custom_actions") or [])
            after = [item for item in custom_actions if str(item.get("id")) != custom_id]
            store["custom_actions"] = _normalize_custom_items(after)
            try:
                _write_store_no_lock(store)
            except Exception:
                return jsonify({"ok": False, "error": _get_storage_error() or "Ошибка записи JSON-хранилища."}), 500

        return jsonify({"ok": True, "message": "Команда удалена.", "custom_actions": store["custom_actions"]})

    @expose("/custom/clear", methods=["POST"])
    @has_access
    @permission_name("list")
    def custom_clear(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        with _STORAGE_LOCK:
            store = _read_store_no_lock()
            store["custom_actions"] = []
            store["example_presets_version"] = _EXAMPLE_PRESETS_VERSION
            try:
                _write_store_no_lock(store)
            except Exception:
                return jsonify({"ok": False, "error": _get_storage_error() or "Ошибка записи JSON-хранилища."}), 500

        return jsonify({"ok": True, "message": "Список сохранённых команд очищен.", "custom_actions": []})

    @expose("/history/clear", methods=["POST"])
    @has_access
    @permission_name("list")
    def history_clear(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": "Подтверждение не прошло. Обновите страницу."}), 403

        with _STORAGE_LOCK:
            store = _read_store_no_lock()
            store["history"] = []
            try:
                _write_store_no_lock(store)
            except Exception:
                return jsonify({"ok": False, "error": _get_storage_error() or "Ошибка записи JSON-хранилища."}), 500

        return jsonify({"ok": True, "message": "История очищена.", "history": []})


def register(appbuilder, app, plugin):
    global _TEMPLATE_ROOT
    try:
        if plugin and getattr(plugin, "root", None):
            _TEMPLATE_ROOT = Path(plugin.root)
    except Exception:
        pass
    return WinKeysView

