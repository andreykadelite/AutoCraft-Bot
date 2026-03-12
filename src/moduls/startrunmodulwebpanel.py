from __future__ import annotations

import configparser
import sqlite3
import threading
import time
import re
import sys
import os
import socket
import asyncio

from pathlib import Path
from typing import Dict, Optional, Any, TYPE_CHECKING

# --- Typing aliases (Pylance-friendly) ---
# Этот модуль может импортироваться без aiogram/web_dashboard, поэтому прямые аннотации вида
# types.Message / Dispatcher / WebPanelServer могут давать предупреждения Pylance.
# Здесь заводим безопасные алиасы для аннотаций, не влияя на рантайм.
if TYPE_CHECKING:
    from aiogram.dispatcher import Dispatcher as DispatcherT
    from aiogram.types import Message as MessageT
    from aiogram.types import ReplyKeyboardMarkup as ReplyKeyboardMarkupT
    from web_dashboard.server import WebPanelServer as WebPanelServerT
else:
    DispatcherT = Any  # type: ignore
    MessageT = Any  # type: ignore
    ReplyKeyboardMarkupT = Any  # type: ignore
    WebPanelServerT = Any  # type: ignore




# --- Import aliasing: предотвращаем двойной импорт модуля (startrunmodulwebpanel vs moduls.startrunmodulwebpanel) ---
try:
    _this_mod = sys.modules.get(__name__)
    if _this_mod is not None:
        if __name__ == "startrunmodulwebpanel":
            sys.modules.setdefault("moduls.startrunmodulwebpanel", _this_mod)
        elif __name__ == "moduls.startrunmodulwebpanel":
            sys.modules.setdefault("startrunmodulwebpanel", _this_mod)
except Exception:
    # Никогда не ломаем импорт из-за алиасов.
    pass

# --- Optional aiogram import (модуль может импортироваться без запущенного Telegram-бота) ---
_AIOGRAM_AVAILABLE = True
_AIOGRAM_IMPORT_ERROR = None
try:
    from aiogram import types
    from aiogram.dispatcher import Dispatcher
except Exception as _e:  # pragma: no cover
    _AIOGRAM_AVAILABLE = False
    _AIOGRAM_IMPORT_ERROR = _e
    types = None  # type: ignore
    Dispatcher = object  # type: ignore


# --- Интеграция с ядром AutoCraft-Bot (без жёсткой зависимости от запущенного Telegram-бота) ---
try:
    import __main__  # type: ignore
    base_dir = getattr(__main__, 'base_dir', None)
    write_bot_log = getattr(__main__, 'write_bot_log', None)
except Exception:
    base_dir = None
    write_bot_log = None

# Безопасный логгер-заглушка (чтобы модуль никогда не падал при импорте)
if not callable(write_bot_log):
    def write_bot_log(*args, **kwargs):  # type: ignore
        return

# Безопасное определение base_dir без импорта windows_startup (чтобы не было побочных эффектов)
def _detect_base_dir() -> str:
    # 1) base_dir из __main__
    try:
        if base_dir:
            return str(base_dir)
    except Exception:
        pass

    # 2) EXE / frozen: рядом с исполняемым файлом
    try:
        exe = getattr(sys, 'executable', '')
        if exe:
            p = Path(exe).resolve()
            if p.exists():
                return str(p.parent)
    except Exception:
        pass

    # 3) Обычный запуск: текущая папка
    try:
        return str(Path.cwd())
    except Exception:
        return ''

base_dir = _detect_base_dir()
# --- Optional imports: модуль может жить отдельно, без меню/реестра/панели ---
def _noop(*args, **kwargs):
    return None

# keymenu (клавиатура утилит)
try:
    from keymenu import get_utilities_keyboard as _get_utilities_keyboard_real
except Exception as _e:
    _get_utilities_keyboard_real = None
    write_bot_log(f'[webpanel] keymenu не импортирован: {_e}')

# utilities_registry (реестр утилит)
try:
    from utilities_registry import register_utility as _register_utility_real
except Exception as _e:
    _register_utility_real = None
    write_bot_log(f'[webpanel] utilities_registry не импортирован: {_e}')

# web_dashboard (сама панель)
_WEBPANEL_AVAILABLE = True
_WEBPANEL_IMPORT_ERROR = None
try:
    from web_dashboard import config as panel_config
    from web_dashboard.server import WebPanelServer
except Exception as _e:
    try:
        # Fallback для запусков, где пакет доступен как moduls.web_dashboard
        from moduls.web_dashboard import config as panel_config  # type: ignore
        from moduls.web_dashboard.server import WebPanelServer  # type: ignore
        _WEBPANEL_AVAILABLE = True
        _WEBPANEL_IMPORT_ERROR = None
    except Exception as _e2:
        _WEBPANEL_AVAILABLE = False
        _WEBPANEL_IMPORT_ERROR = _e2
        panel_config = None  # type: ignore
        WebPanelServer = None  # type: ignore
        write_bot_log(f'[webpanel] web_dashboard не импортирован: {_e}; fallback moduls.web_dashboard: {_e2}')

# Безопасные прокси
def get_utilities_keyboard():  # type: ignore
    try:
        if callable(_get_utilities_keyboard_real):
            return _get_utilities_keyboard_real()
    except Exception:
        pass
    # fallback: пустая клавиатура
    if types and hasattr(types, 'ReplyKeyboardMarkup'):
        try:
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add('Назад')
            return kb
        except Exception:
            return None
    return None


def register_utility(*args, **kwargs):  # type: ignore
    try:
        if callable(_register_utility_real):
            return _register_utility_real(*args, **kwargs)
    except Exception as _e:
        write_bot_log(f'[webpanel] Не удалось зарегистрировать утилиту: {_e}')
    return None

# --- Состояние ---
webpanel_mode: Dict[int, bool] = {}
webpanel_step: Dict[int, str] = {}
webpanel_ctx: Dict[int, Dict[str, Any]] = {}

_server_lock = threading.Lock()
_server: Optional[WebPanelServerT] = None
_restart_lock = threading.Lock()
_restart_in_progress: bool = False

# --- Автозапуск веб-панели (только влияет на автозапуск при импорте модуля) ---
_AUTOSTART_SECTION = "webpanel_autostart"
_AUTOSTART_KEY = "enabled"

_autostart_lock = threading.Lock()
_autostart_enabled: bool = False

# --- Тексты кнопок (важно держать константами, чтобы не ловить опечатки) ---
BTN_START = "Запустить веб-панель"
BTN_STOP = "Остановить веб-панель"
BTN_RESTART = "Перезапустить веб-панель"
BTN_STATUS = "Статус веб-панели"
BTN_URL = "Адрес панели"
BTN_CHANGE_PASS = "Добавить пользователя"
BTN_CANCEL_PASS = "Отмена операции с пользователями"
BTN_SHOW_CREDENTIALS = "Менеджер пользователей"
BTN_API = "API панели"
BTN_LOGS = "Последние логи панели"
BTN_SETTINGS = "Настройки панели"
BTN_CANCEL_SETTINGS = "Отмена настроек панели"
BTN_AUTOSTART_ON = "Включить автозапуск панели"
BTN_AUTOSTART_OFF = "Выключить автозапуск панели"
BTN_BACK = "Назад в утилиты"
BTN_MINIMIZE = "Свернуть веб-панель"
BTN_USER_MANAGER_REFRESH = "Обновить список пользователей"
BTN_USER_SHOW_CREDENTIALS = "Показать логин/пароль пользователя"
BTN_USER_CHANGE_PASSWORD = "Сменить пароль пользователя"
BTN_USER_DELETE = "Удалить пользователя"
BTN_USER_SELECT_OTHER = "Выбрать другого пользователя"
BTN_USER_MANAGER_EXIT = "Выйти из менеджера пользователей"

# Старые подписи (для совместимости, если у пользователя где-то остались старые клавиатуры/сообщения)
BTN_START_OLD = "Старт веб-панели"
BTN_STOP_OLD = "Остановить веб-панель"

# --- Авторизация и отложенная регистрация кнопки в "Утилитах" ---
# Важно: этот модуль может импортироваться ДО авторизации. Поэтому:
# 1) Проверку делаем динамически через __main__.authorized_users (не держим "снимок" переменной).
# 2) Регистрацию утилиты выполняем ПОСЛЕ авторизации (как только authorized_users станет непустым).
_auth_poll_thread_started: bool = False
_auth_poll_lock = threading.Lock()

_utility_registered: bool = False
_utility_lock = threading.Lock()


def _get_authorized_users():
    try:
        import __main__  # type: ignore
        return getattr(__main__, "authorized_users", None)
    except Exception:
        return None


def _check_any_authorized() -> bool:
    au = _get_authorized_users()
    try:
        return bool(au)
    except Exception:
        return False


def _is_authorized(user_id: int) -> bool:
    au = _get_authorized_users()
    if not au:
        return False
    try:
        # list/set/tuple/dict (для dict проверит ключи)
        return user_id in au
    except Exception:
        try:
            return bool(au)
        except Exception:
            return False


def _register_utility_once() -> None:
    """Регистрирует кнопку в 'Утилитах' ровно один раз."""
    global _utility_registered
    with _utility_lock:
        if _utility_registered:
            return
        try:
            register_utility(
                key="web_dashboard",
                title="Веб-панель AutoCraft",
                trigger_text="Веб-панель AutoCraft",
                group="utilities",
                order=20,
                description="Запуск и статус веб-панели (Flask) с real-time мониторингом и API.",
            )
            _utility_registered = True
        except Exception as e:
            try:
                write_bot_log(
                    f"Не удалось зарегистрировать утилиту 'Веб-панель AutoCraft' после авторизации: {e}"
                )
            except Exception:
                pass


def _start_auth_poll_once(dp: DispatcherT) -> None:
    """Запускает один daemon-thread, который ждёт авторизацию и регистрирует утилиту."""
    global _auth_poll_thread_started
    with _auth_poll_lock:
        if _auth_poll_thread_started:
            return
        _auth_poll_thread_started = True

    # Если уже авторизованы (например, восстановили сессию), регистрируем сразу
    if _check_any_authorized():
        _register_utility_once()
        return

    def _worker():
        while not _check_any_authorized():
            time.sleep(0.25)

        # Пытаемся зарегистрировать в потоке loop бота (если доступен), иначе напрямую
        try:
            loop = getattr(getattr(dp, "bot", None), "loop", None)
            if loop and getattr(loop, "is_running", lambda: False)():
                loop.call_soon_threadsafe(_register_utility_once)
            else:
                _register_utility_once()
        except Exception:
            _register_utility_once()

    threading.Thread(
        target=_worker,
        name="webpanel_wait_auth_register",
        daemon=True,
    ).start()



def _get_panel_port(srv: Optional[WebPanelServerT]) -> Optional[int]:
    if not srv:
        return None
    try:
        cfg = getattr(getattr(srv, "runtime", None), "config", None)
        port = getattr(cfg, "port", None)
        if port is None:
            return None
        return int(port)
    except Exception:
        return None


def _probe_local_port(port: int, timeout: float = 0.25) -> bool:
    """Проверяем локально 127.0.0.1:port (подходит и для host=0.0.0.0)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(max(0.05, float(timeout)))
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def _is_effectively_running(srv: Optional[WebPanelServerT]) -> bool:
    """Истинный статус: сначала доверяем is_running(), затем делаем fallback-проверку порта."""
    if not srv:
        return False
    try:
        if srv.is_running():
            return True
    except Exception:
        pass
    port = _get_panel_port(srv)
    if port:
        return _probe_local_port(port)
    return False


def _is_child_process() -> bool:
    """Пытаемся определить, что это "боевой" дочерний процесс бота (watchdog child)."""
    try:
        argv = [str(a).strip().lower() for a in (sys.argv or [])]
        if "--child" in argv or any(a.endswith("--child") for a in argv):
            return True
    except Exception:
        pass
    try:
        import __main__  # type: ignore
        for key in ("IS_CHILD", "is_child", "child_process", "WATCHDOG_CHILD", "RUN_CHILD", "run_child"):
            if bool(getattr(__main__, key, False)):
                return True
    except Exception:
        pass
    return False


def _should_autostart_in_this_process() -> bool:
    """Гейт для автозапуска.

    По умолчанию автозапуск разрешаем ТОЛЬКО в child-процессе, чтобы Telegram-команды
    могли корректно останавливать панель.
    """
    try:
        val = os.environ.get("AUTOCRAFT_WEBPANEL_AUTOSTART_ANY_PROCESS", "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    except Exception:
        pass
    return _is_child_process()


def _is_panel_running() -> bool:
    srv = _server
    if not srv:
        return False
    return _is_effectively_running(srv)


async def _await_effective_state(
    srv: Optional[WebPanelServerT],
    want_running: bool,
    timeout: float = 6.0,
) -> bool:
    """Ждём реального состояния (по is_running + проверке порта)."""
    try:
        end = time.time() + max(0.1, float(timeout))
    except Exception:
        end = time.time() + 6.0
    while time.time() < end:
        if _is_effectively_running(srv) == bool(want_running):
            return True
        await asyncio.sleep(0.15)
    return _is_effectively_running(srv) == bool(want_running)


def _wait_effective_state_sync(
    srv: Optional[WebPanelServerT],
    want_running: bool,
    timeout: float = 6.0,
) -> bool:
    """Синхронное ожидание состояния панели (для GUI и web-view)."""
    try:
        end = time.time() + max(0.1, float(timeout))
    except Exception:
        end = time.time() + 6.0
    while time.time() < end:
        if _is_effectively_running(srv) == bool(want_running):
            return True
        time.sleep(0.15)
    return _is_effectively_running(srv) == bool(want_running)



def _config_ini_path() -> Path:
    """Путь до data/config.ini (и в исходниках, и в EXE)."""
    try:
        root = Path(base_dir) if base_dir else Path.cwd()
    except Exception:
        root = Path.cwd()
    return root / "data" / "config.ini"


def _ini_upsert_value(path: Path, section: str, key: str, value: str) -> None:
    """Обновить/добавить значение в INI, стараясь максимально сохранить остальной файл."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raw = path.read_text(encoding="utf-8", errors="ignore")
        lines = raw.splitlines()
    else:
        lines = []

    section_re = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=.*$", re.IGNORECASE)

    target_section_idx: Optional[int] = None
    target_section_end: Optional[int] = None

    # Находим секцию и её границы
    current_section: Optional[str] = None
    for i, line in enumerate(lines):
        m = section_re.match(line)
        if m:
            if current_section is not None and current_section.strip().lower() == section.strip().lower():
                target_section_end = i
            current_section = (m.group(1) or "").strip()
            if current_section.lower() == section.strip().lower():
                target_section_idx = i

    if target_section_idx is not None and target_section_end is None:
        target_section_end = len(lines)

    # Если секции нет, добавляем в конец
    if target_section_idx is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(f"[{section}]")
        lines.append(f"{key}={value}")
    else:
        assert target_section_end is not None
        # Ищем ключ внутри секции
        replaced = False
        for j in range(target_section_idx + 1, target_section_end):
            if key_re.match(lines[j]) and not lines[j].lstrip().startswith((";", "#")):
                indent = key_re.match(lines[j]).group(1) if key_re.match(lines[j]) else ""
                lines[j] = f"{indent}{key}={value}"
                replaced = True
                break
        if not replaced:
            # Вставляем ключ перед следующей секцией
            insert_at = target_section_end
            lines.insert(insert_at, f"{key}={value}")

    text_out = "\n".join(lines).rstrip() + "\n"
    path.write_text(text_out, encoding="utf-8")


def _load_autostart_setting() -> bool:
    """Читает data/config.ini и обновляет _autostart_enabled."""
    global _autostart_enabled
    with _autostart_lock:
        ini_path = _config_ini_path()
        try:
            parser = configparser.ConfigParser()
            if ini_path.exists():
                parser.read(ini_path, encoding="utf-8")
            raw = parser.get(_AUTOSTART_SECTION, _AUTOSTART_KEY, fallback="0")
            _autostart_enabled = str(raw).strip().lower() in ("1", "true", "yes", "on")
        except Exception as e:
            write_bot_log(f"Ошибка чтения config.ini для автозапуска веб-панели: {e}")
            _autostart_enabled = False
        return _autostart_enabled


def _set_autostart_setting(
    enabled: bool,
    actor: str = "system",
    source: str = "system",
) -> None:
    """Записывает настройку автозапуска в data/config.ini и обновляет _autostart_enabled."""
    global _autostart_enabled
    saved_ok = False
    old_enabled = False
    with _autostart_lock:
        old_enabled = bool(_autostart_enabled)
        ini_path = _config_ini_path()
        try:
            _ini_upsert_value(ini_path, _AUTOSTART_SECTION, _AUTOSTART_KEY, "1" if enabled else "0")
            _autostart_enabled = bool(enabled)
            saved_ok = True
        except Exception as e:
            write_bot_log(f"Ошибка записи config.ini для автозапуска веб-панели: {e}")
            srv = _server
            if srv is not None:
                _safe_audit(
                    srv,
                    str(actor),
                    "panel_autostart_update",
                    False,
                    source=source,
                    details=str(e),
                )
            return

    changed = bool(old_enabled != _autostart_enabled)
    state = "включён" if _autostart_enabled else "выключен"
    write_bot_log(
        f"[WEBPANEL] Автозапуск панели {state}: actor={actor} source={source} changed={int(changed)}"
    )
    if saved_ok:
        srv = _server
        if srv is not None:
            _safe_audit(
                srv,
                str(actor),
                "panel_autostart_update",
                True,
                source=source,
                details=f"enabled={int(_autostart_enabled)} changed={int(changed)}",
            )




def _autostart_panel_on_import() -> None:
    """Если включён автозапуск, стартуем панель сразу при импорте модуля.

    Важно:
    - Автозапуск влияет на старт при импорте, НЕ нажатие кнопки в Telegram.
    - В режиме watchdog панель должна стартовать в child-процессе, иначе её невозможно
      корректно остановить командой из Telegram.
    """

    enabled = _load_autostart_setting()
    if not enabled:
        return

    if not _WEBPANEL_AVAILABLE:
        write_bot_log('[webpanel] Автозапуск пропущен: web_dashboard не доступен.')
        return

    try:
        start_block_reason = _get_panel_start_block_reason_safe()
    except Exception as e:
        write_bot_log(f'Автозапуск веб-панели: не удалось проверить пользователей: {e}')
        return
    if start_block_reason:
        write_bot_log(f'Автозапуск веб-панели пропущен: {start_block_reason}')
        return

    # В watchdog-схеме автозапускаемся только в child-процессе.
    if not _should_autostart_in_this_process():
        try:
            write_bot_log(
                f'[webpanel] Автозапуск включён, но пропущен (не child-процесс). pid={os.getpid()} argv={sys.argv}'
            )
        except Exception:
            pass
        return

    try:
        srv = _ensure_server()
    except Exception as e:
        write_bot_log(f'Автозапуск веб-панели: не удалось инициализировать сервер: {e}')
        return

    try:
        if not _is_effectively_running(srv):
            with _server_lock:
                srv.start()
            write_bot_log('Автозапуск веб-панели: панель запущена при импорте модуля.')
            _safe_audit(srv, 'system', 'panel_autostart_on_import', True, source='import')
    except Exception as e:
        write_bot_log(f'Автозапуск веб-панели: ошибка запуска: {e}')
        _safe_audit(srv, 'system', 'panel_autostart_on_import', False, source='import', details=str(e))


def _get_keyboard(password_mode: bool = False, settings_mode: bool = False) -> ReplyKeyboardMarkupT:
    """Динамическая клавиатура: показывает только корректные кнопки по текущему статусу."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    # Режим пользовательского мастера: оставляем только кнопку отмены
    if password_mode:
        kb.add(BTN_CANCEL_PASS)
        return kb

    # Режим настроек: оставляем только кнопку отмены
    if settings_mode:
        kb.add(BTN_CANCEL_SETTINGS)
        return kb

    running = _is_panel_running()

    # 1) Управление питанием панели
    if running:
        kb.add(BTN_STOP, BTN_RESTART)
    else:
        kb.add(BTN_START)

    # 1.1) Автозапуск (влияет только на автозапуск при импорте модуля, НЕ запускает панель сразу)
    kb.add(BTN_AUTOSTART_OFF if _autostart_enabled else BTN_AUTOSTART_ON)

    # 2) Инфо
    kb.add(BTN_STATUS, BTN_URL)
    kb.add(BTN_CHANGE_PASS, BTN_API)
    kb.add(BTN_SHOW_CREDENTIALS)
    kb.add(BTN_LOGS, BTN_SETTINGS)

    # 3) Выход: если панель запущена, вместо "Назад" показываем "Свернуть"
    kb.add(BTN_MINIMIZE if running else BTN_BACK)
    return kb


def _get_cancel_keyboard() -> ReplyKeyboardMarkupT:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(BTN_CANCEL_PASS)
    return kb


def _get_role_select_keyboard(roles: list[str]) -> ReplyKeyboardMarkupT:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    ordered = sorted({(item or "").strip() for item in roles if (item or "").strip()})
    for role_name in ordered:
        kb.add(role_name)
    kb.add(BTN_CANCEL_PASS)
    return kb


def _get_user_select_keyboard(users: list[dict[str, Any]]) -> ReplyKeyboardMarkupT:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    usernames = sorted(
        {
            str(item.get("username") or "").strip()
            for item in users
            if str(item.get("username") or "").strip()
        }
    )
    for login in usernames:
        kb.add(login)
    kb.add(BTN_USER_MANAGER_REFRESH, BTN_USER_MANAGER_EXIT)
    return kb


def _get_user_actions_keyboard() -> ReplyKeyboardMarkupT:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(BTN_USER_SHOW_CREDENTIALS, BTN_USER_CHANGE_PASSWORD)
    kb.add(BTN_USER_DELETE, BTN_USER_SELECT_OTHER)
    kb.add(BTN_USER_MANAGER_EXIT)
    return kb


def _format_user_line(item: dict[str, Any]) -> str:
    username = str(item.get("username") or "").strip()
    name = str(item.get("name") or "").strip() or username
    roles = item.get("roles") or []
    roles_text = ", ".join([str(r).strip() for r in roles if str(r).strip()]) or "без роли"
    active = "активен" if bool(item.get("active", True)) else "отключен"
    pwd_state = str(item.get("password_state") or "").strip().lower()
    if pwd_state == "saved":
        pwd_mark = "сохранён"
    elif pwd_state == "hash_only":
        pwd_mark = "только хэш"
    elif pwd_state == "missing":
        pwd_mark = "не задан"
    else:
        pwd_mark = "сохранён" if bool(item.get("password_saved")) else "не сохранён"
    return f"- {username} ({name}) | роли: {roles_text} | {active} | пароль: {pwd_mark}"


def _format_users_list_text(users: list[dict[str, Any]]) -> str:
    if not users:
        return "Пользователи не найдены."
    lines = [_format_user_line(item) for item in users]
    return "Пользователи панели:\n" + "\n".join(lines)


def _load_panel_users_safe() -> list[dict[str, Any]]:
    return panel_config.list_panel_users(base_dir)


def _load_panel_roles_safe() -> list[str]:
    return panel_config.list_panel_roles(base_dir)


def _get_panel_bootstrap_state_safe() -> dict[str, Any]:
    getter = getattr(panel_config, "get_panel_bootstrap_state", None)
    if callable(getter):
        return getter(base_dir)
    users = _load_panel_users_safe()
    super_admin_users = []
    for item in users:
        roles = [str(role).strip() for role in (item.get("roles") or []) if str(role).strip()]
        if "Super Admin" in roles:
            super_admin_users.append(str(item.get("username") or "").strip())
    return {
        "has_users": bool(users),
        "has_super_admin": bool(super_admin_users),
        "super_admin_users": super_admin_users,
        "user_count": len(users),
    }


def _get_panel_start_block_reason_safe() -> str:
    reason_getter = getattr(panel_config, "get_panel_start_block_reason", None)
    if callable(reason_getter):
        return str(reason_getter(base_dir) or "").strip()
    state = _get_panel_bootstrap_state_safe()
    if not bool(state.get("has_users")):
        return (
            "Панель не может быть запущена: не найдено ни одного пользователя. "
            "Создайте первого пользователя с ролью Super Admin."
        )
    if not bool(state.get("has_super_admin")):
        return (
            "Панель не может быть запущена: отсутствует пользователь с ролью Super Admin. "
            "Создайте первого Super Admin или назначьте роль существующему пользователю."
        )
    return ""


def _first_super_admin_hint_text() -> str:
    return (
        "Первый запуск панели:\n"
        "создайте первого пользователя с ролью Super Admin.\n"
        "Введите логин (по умолчанию: admin)."
    )


def _ensure_server() -> WebPanelServerT:
    global _server
    if not _WEBPANEL_AVAILABLE or WebPanelServer is None or panel_config is None:
        raise RuntimeError(f'web_dashboard недоступен: {_WEBPANEL_IMPORT_ERROR}')
    with _server_lock:
        if _server is None:
            _server = WebPanelServer(base_dir)

            # если старый конфиг с локальным хостом — открываем наружу
            if _server.runtime.config.host in ("127.0.0.1", "localhost"):
                _server.runtime.config.host = "0.0.0.0"
                panel_config.save_config(base_dir, _server.runtime.config)
        return _server


def restart_panel_sync(
    actor: str = "system",
    source: str = "system",
    stop_timeout: float = 10.0,
    start_timeout: float = 8.0,
) -> tuple[bool, str]:
    """Полный перезапуск панели: stop -> wait -> start -> wait."""
    global _restart_in_progress
    with _restart_lock:
        if _restart_in_progress:
            write_bot_log(
                f"[WEBPANEL] Перезапуск отклонён: уже выполняется actor={actor} source={source}"
            )
            return False, "Перезапуск уже выполняется. Подождите завершения текущей операции."
        _restart_in_progress = True

    srv: Optional[WebPanelServerT] = None
    try:
        write_bot_log(f"[WEBPANEL] Перезапуск запрошен: actor={actor} source={source}")
        srv = _ensure_server()
        was_running = _is_effectively_running(srv)

        if not was_running:
            _safe_audit(
                srv,
                str(actor),
                "panel_restart",
                False,
                source=source,
                details="panel_not_running",
            )
            return False, "Панель не запущена. Сначала запустите панель."

        with _server_lock:
            srv.stop()
        stopped = _wait_effective_state_sync(srv, False, timeout=stop_timeout)
        if not stopped:
            raise RuntimeError(
                "Панель не остановилась полностью: порт всё ещё отвечает. "
                "Вероятно, процесс панели запущен из другого экземпляра AutoCraft."
            )

        with _server_lock:
            srv.start()

        started = _wait_effective_state_sync(srv, True, timeout=start_timeout)
        if not started:
            raise RuntimeError("Панель не подтвердила запуск (порт не открылся).")

        _safe_audit(srv, str(actor), "panel_restart", True, source=source)
        url = ""
        try:
            url = srv.url()
        except Exception:
            url = ""
        if url:
            write_bot_log(
                f"[WEBPANEL] Перезапуск завершён: actor={actor} source={source} url={url}"
            )
            return True, f"Веб-панель перезапущена.\nАдрес: {url}"
        write_bot_log(f"[WEBPANEL] Перезапуск завершён: actor={actor} source={source}")
        return True, "Веб-панель перезапущена."
    except Exception as e:
        write_bot_log(
            f"[WEBPANEL] Ошибка перезапуска: actor={actor} source={source} error={e}"
        )
        _safe_audit(
            srv,
            str(actor),
            "panel_restart",
            False,
            source=source,
            details=str(e),
        )
        return False, _format_error("перезапустить веб-панель", e)
    finally:
        with _restart_lock:
            _restart_in_progress = False


def _panel_status() -> str:
    srv = _server
    if srv and _is_panel_running():
        try:
            return f"Панель запущена: {srv.url()}"
        except Exception:
            return "Панель запущена."
    try:
        block_reason = _get_panel_start_block_reason_safe()
    except Exception:
        block_reason = ""
    if block_reason:
        return f"Панель не запущена.\n{block_reason}"
    return "Панель не запущена."



def _format_error(action: str, exc: Exception) -> str:
    detail = str(exc).strip()
    if not detail:
        detail = repr(exc)
    lowered = detail.lower()
    if "limits" in lowered and "lua_scripts" in lowered:
        return (
            f"Не удалось {action}. Не найдены Lua-скрипты limits для ограничителя.\n"
            "Проверьте, что создана папка data/limits_resources или пересоберите EXE."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            f"Не удалось {action}. {detail}\n"
            "Проверьте, что папка web_dashboard с templates/static доступна."
        )
    return f"Не удалось {action}. Ошибка: {detail}"


def _safe_audit(
    srv: Optional[WebPanelServerT],
    actor: str,
    action: str,
    result: bool,
    **kwargs,
) -> None:
    if not srv:
        return
    # Важно для отзывчивости GUI/бота:
    # если панель ещё не запускалась, runtime.audit() форсирует тяжёлый bootstrap Flask/FAB.
    # Для операций до первого старта это даёт заметные "подвисания", поэтому audit
    # пишем только когда приложение уже инициализировано или сервер реально запущен.
    try:
        runtime = getattr(srv, "runtime", None)
        app_loaded = bool(getattr(runtime, "app", None) is not None)
    except Exception:
        app_loaded = False
    if not app_loaded:
        try:
            if not bool(srv.is_running()):
                return
        except Exception:
            return
    try:
        srv.runtime.audit(actor, action, result, **kwargs)
    except Exception as e:
        write_bot_log(f"Ошибка аудита панели: {e}")


def _parse_settings(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not text:
        return result
    # Поддерживаем ввод в одну строку: host=0.0.0.0 port=5000 debug=0 retention=7
    # Также допускаем переносы строк.
    raw = text.replace("\r", " ").replace("\n", " ").strip()
    if not raw:
        return result
    for token in raw.split():
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        k = (k or "").strip().lower()
        v = (v or "").strip()
        if k:
            result[k] = v
    return result


def _normalize_log_value(value: Optional[str], limit: int = 200) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ""
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _tail_db_logs(base_dir: str, limit: int = 120) -> tuple[str, Optional[str]]:
    try:
        db_path = panel_config.get_db_path(base_dir)
    except Exception as e:
        return "", f"Не удалось определить путь к базе: {e}"

    if not db_path.exists():
        return "", f"База не найдена: {db_path}"

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

        logs: list[tuple[str, str]] = []

        if "panel_audit" in tables:
            cur.execute(
                "SELECT created_at, user, action, target, result, source, ip, details "
                "FROM panel_audit ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            for row in cur.fetchall():
                ts = row["created_at"] or ""
                target = _normalize_log_value(row["target"], 120)
                details = _normalize_log_value(row["details"], 200)
                line = (
                    f"[AUDIT] {ts} user={row['user']} action={row['action']} "
                    f"target={target} result={row['result']} source={row['source']} ip={row['ip']}"
                )
                if details:
                    line += f" details={details}"
                logs.append((str(ts), line))

        if "panel_jobs" in tables:
            cur.execute(
                "SELECT created_at, user, operation, status, source, stderr "
                "FROM panel_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            for row in cur.fetchall():
                ts = row["created_at"] or ""
                stderr = _normalize_log_value(row["stderr"], 200)
                line = (
                    f"[JOB] {ts} user={row['user']} op={row['operation']} "
                    f"status={row['status']} source={row['source']}"
                )
                if stderr:
                    line += f" stderr={stderr}"
                logs.append((str(ts), line))

        if not logs:
            return "", None

        logs.sort(key=lambda item: item[0], reverse=True)
        lines = [line for _ts, line in logs[:limit]]
        return "\n".join(lines).strip(), None
    except Exception as e:
        return "", f"Ошибка чтения базы: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _split_log_chunks(text: str, max_len: int = 3500) -> list[str]:
    text = text.replace("```", "`​``")
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]
# --- Защита от двойной/ранней регистрации хендлеров ---
_registered_dispatchers = set()
_registered_dp_lock = threading.Lock()


def register_handlers(dp: DispatcherT):
    """Регистрация хендлеров модуля управления веб-панелью.

    Важно: модуль может импортироваться/инициализироваться до запуска Telegram-бота.
    Поэтому регистрация должна быть максимально безопасной и никогда не должна валить весь процесс.
    """

    # Если aiogram/dispatcher ещё не готовы или бот не запущен, просто выходим (без падения).
    try:
        if not _AIOGRAM_AVAILABLE or not _WEBPANEL_AVAILABLE:
            write_bot_log('[webpanel] Пропуск register_handlers: зависимости не готовы (aiogram/web_dashboard).')
            return False
        if dp is None or not hasattr(dp, 'message_handler'):
            write_bot_log('[webpanel] Пропуск register_handlers: Dispatcher не готов (бот не запущен).')
            return False
        with _registered_dp_lock:
            if id(dp) in _registered_dispatchers:
                return True
            _registered_dispatchers.add(id(dp))
    except Exception as _e:
        try:
            write_bot_log(f'[webpanel] Ошибка при подготовке register_handlers: {_e}')
        except Exception:
            pass
        return False


    # Регистрируем кнопку в "Утилитах" сразу (аналогично nostartrunmodulwinrun.py).
    # Реестр утилит перезаписывает по key, поэтому повторная регистрация не страшна.
    try:
        register_utility(
            key="web_dashboard",
            title="Веб-панель AutoCraft",
            trigger_text="Веб-панель AutoCraft",
            group="utilities",
            order=20,
            description="Запуск и статус веб-панели (Flask) с real-time мониторингом и API.",
        )
    except Exception:
        pass


    # --- Вход в модуль ---
    @dp.message_handler(lambda m: _is_authorized(m.from_user.id) and m.text == "Веб-панель AutoCraft")
    async def webpanel_entry(message: MessageT):
        user_id = message.from_user.id
        webpanel_mode[user_id] = True
        webpanel_step.pop(user_id, None)
        webpanel_ctx.pop(user_id, None)

        try:
            srv = _ensure_server()
        except Exception as e:
            # В frozen/EXE сборках чаще всего это проблема с путями к templates/static
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            webpanel_mode.pop(user_id, None)
            webpanel_step.pop(user_id, None)
            webpanel_ctx.pop(user_id, None)
            await message.answer(
                _format_error("инициализировать веб-панель", e),
                reply_markup=get_utilities_keyboard(),
            )
            return

        status_text = _panel_status()
        try:
            start_block_reason = _get_panel_start_block_reason_safe()
        except Exception as e:
            start_block_reason = _format_error("проверить готовность запуска панели", e)

        if start_block_reason:
            webpanel_step[user_id] = "await_first_super_admin_login"
            webpanel_ctx[user_id] = {
                "first_admin_login_default": "admin",
                "first_admin_flow_source": "entry",
            }
            await message.answer(
                "Модуль <Веб-панель AutoCraft>.\n"
                "Панель показывает метрики Windows/AutoCraft в реальном времени, имеет API и веб-интерфейс.\n"
                f"{status_text}\n"
                f"URL: {srv.url()}\n\n"
                f"{start_block_reason}\n\n"
                f"{_first_super_admin_hint_text()}",
                reply_markup=_get_cancel_keyboard(),
                parse_mode="Markdown",
            )
            return

        await message.answer(
            "Модуль <Веб-панель AutoCraft>.\n"
            "Панель показывает метрики Windows/AutoCraft в реальном времени, имеет API и веб-интерфейс.\n"
            f"{status_text}\n"
            f"URL: {srv.url()}",
            reply_markup=_get_keyboard(password_mode=False),
            parse_mode="Markdown",
        )

    # --- Автозапуск панели (влияет только на автозапуск при импорте модуля) ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text in (BTN_AUTOSTART_ON, BTN_AUTOSTART_OFF)
    )
    async def webpanel_autostart_toggle(message: MessageT):
        enable = message.text == BTN_AUTOSTART_ON
        _set_autostart_setting(
            enable,
            actor=str(message.from_user.id),
            source="telegram",
        )

        running = _is_panel_running()
        running_note = (
            "Сейчас панель запущена, это действие её не останавливает." if running else "Сейчас панель остановлена, это действие её не запускает."
        )

        await message.answer(
            ("Автозапуск веб-панели включён. " if enable else "Автозапуск веб-панели выключен. ")
            + "Он сработает при следующем запуске/перезапуске AutoCraft-Bot (когда этот модуль импортируется).\n"
            + running_note,
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Выход (обычный) ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_BACK
    )
    async def webpanel_exit(message: MessageT):
        user_id = message.from_user.id
        running = _is_panel_running()
        srv = _server
        url = srv.url() if (running and srv) else None
        webpanel_mode.pop(user_id, None)
        webpanel_step.pop(user_id, None)
        webpanel_ctx.pop(user_id, None)
        text = "Возврат в утилиты."
        if running:
            text += "\nВнимание: веб-панель всё ещё запущена."
            if url:
                text += "\nАдрес: " + url
        await message.answer(text, reply_markup=get_utilities_keyboard())

    # --- "Свернуть" (в утилиты, но панель оставляем запущенной) ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_MINIMIZE
    )
    async def webpanel_minimize(message: MessageT):
        user_id = message.from_user.id
        webpanel_mode.pop(user_id, None)
        webpanel_step.pop(user_id, None)
        webpanel_ctx.pop(user_id, None)

        # Важно: здесь мы НЕ останавливаем сервер. Только предупреждаем.
        srv = _server
        url = srv.url() if (srv and srv.is_running()) else None
        warn = "Внимание: веб-панель всё ещё запущена."
        if url:
            warn += f"\nАдрес: {url}"
        await message.answer(f"Выход в утилиты.\n{warn}", reply_markup=get_utilities_keyboard())

    # --- Запуск/остановка одной кнопкой ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text in (BTN_START, BTN_STOP, BTN_START_OLD, BTN_STOP_OLD)
    )
    async def webpanel_toggle(message: MessageT):
        with _restart_lock:
            if _restart_in_progress:
                await message.answer(
                    "Панель сейчас перезапускается. Дождитесь завершения операции.",
                    reply_markup=_get_keyboard(password_mode=False),
                )
                return

        want_start = message.text in (BTN_START, BTN_START_OLD)
        if want_start:
            try:
                start_block_reason = _get_panel_start_block_reason_safe()
            except Exception as e:
                await message.answer(
                    _format_error("проверить готовность запуска панели", e),
                    reply_markup=_get_keyboard(password_mode=False),
                )
                return
            if start_block_reason:
                webpanel_step[message.from_user.id] = "await_first_super_admin_login"
                webpanel_ctx[message.from_user.id] = {
                    "first_admin_login_default": "admin",
                    "first_admin_flow_source": "start",
                }
                await message.answer(
                    f"{start_block_reason}\n\n{_first_super_admin_hint_text()}",
                    reply_markup=_get_cancel_keyboard(),
                )
                return

        try:
            srv = _ensure_server()
        except Exception as e:
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            action = "запустить веб-панель" if want_start else "остановить веб-панель"
            await message.answer(
                _format_error(action, e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        running = _is_effectively_running(srv)

        # Если пользователь нажал "Запустить", но уже запущено (или наоборот)
        if want_start and running:
            await message.answer(
                f"Панель уже запущена.\nАдрес: {srv.url()}",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        if (not want_start) and (not running):
            await message.answer(
                "Панель уже остановлена.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        try:
            if want_start:
                with _server_lock:
                    srv.start()

                ok = await _await_effective_state(srv, True, timeout=8.0)
                if not ok:
                    raise RuntimeError("Панель не подтвердила запуск (порт не открылся).")

                write_bot_log(f"Пользователь {message.from_user.id} запустил веб-панель.")
                _safe_audit(srv, str(message.from_user.id), "panel_start", True, source="telegram")

                reply_text = f"Панель запущена.\nАдрес: {srv.url()}"

                await message.answer(
                    reply_text,
                    reply_markup=_get_keyboard(password_mode=False),
                    parse_mode="Markdown",
                )

            else:
                with _server_lock:
                    srv.stop()

                ok = await _await_effective_state(srv, False, timeout=10.0)
                if not ok:
                    # Если панель всё ещё слушает порт, НЕ меняем клавиатуру на "Запустить".
                    # Это сигнализирует, что стоп не сработал (часто это другой процесс).
                    await message.answer(
                        "Команда остановки отправлена, но панель всё ещё отвечает по порту. "
                        "Похоже, она запущена из другого процесса (например, watchdog-родителя) "
                        "или зависла при завершении.\n"
                        "Попробуйте сделать «Полный перезапуск» AutoCraft или перезапустить программу.",
                        reply_markup=_get_keyboard(password_mode=False),
                    )
                    return

                write_bot_log(f"Пользователь {message.from_user.id} остановил веб-панель.")
                _safe_audit(srv, str(message.from_user.id), "panel_stop", True, source="telegram")
                await message.answer(
                    "Панель остановлена.",
                    reply_markup=_get_keyboard(password_mode=False),
                )

        except Exception as e:
            write_bot_log(f"Ошибка управления веб-панелью: {e}")
            _safe_audit(
                srv,
                str(message.from_user.id),
                "panel_toggle",
                False,
                source="telegram",
                details=str(e),
            )
            action = "запустить веб-панель" if want_start else "остановить веб-панель"
            await message.answer(
                _format_error(action, e),
                reply_markup=_get_keyboard(password_mode=False),
            )

    # --- Перезапуск ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_RESTART
    )
    async def webpanel_restart(message: MessageT):
        try:
            srv = _ensure_server()
        except Exception as e:
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            await message.answer(
                _format_error("перезапустить веб-панель", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        with _restart_lock:
            if _restart_in_progress:
                await message.answer(
                    "Перезапуск уже выполняется. Подождите завершения операции.",
                    reply_markup=_get_keyboard(password_mode=False),
                )
                return

        if not _is_effectively_running(srv):
            await message.answer(
                "Панель не запущена. Сначала запустите панель.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        await message.answer(
            "Панель перезапускается. Подождите несколько секунд...",
            reply_markup=_get_keyboard(password_mode=False),
        )

        ok, reply_text = await asyncio.to_thread(
            restart_panel_sync,
            str(message.from_user.id),
            "telegram",
            10.0,
            8.0,
        )
        if ok:
            write_bot_log(f"Пользователь {message.from_user.id} перезапустил веб-панель.")
        else:
            write_bot_log(f"Ошибка перезапуска веб-панели (telegram): {reply_text}")
        await message.answer(
            reply_text,
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Статус ---

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_STATUS
    )
    async def webpanel_status(message: MessageT):
        try:
            srv = _ensure_server()
        except Exception as e:
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            await message.answer(
                _format_error("получить статус веб-панели", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        status_text = _panel_status()
        await message.answer(
            f"{status_text}\nХост: {srv.runtime.config.host}\nПорт: {srv.runtime.config.port}",
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- URL ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_URL
    )
    async def webpanel_url(message: MessageT):
        try:
            srv = _ensure_server()
        except Exception as e:
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            await message.answer(
                _format_error("получить адрес веб-панели", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        await message.answer(f"Адрес панели: {srv.url()}", reply_markup=_get_keyboard(password_mode=False))

    # --- API ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_API
    )
    async def webpanel_api_help(message: MessageT):
        await message.answer(
            "Ключевые эндпоинты:\n"
            "- /api/health\n"
            "- /api/overview, /api/metrics?minutes=...\n"
            "- /api/processes, /api/services, /api/network\n"
            "- /api/logs/tail, /api/windows/events\n"
            "- /api/alerts (GET), /api/alerts/mute/<id>\n"
            "- /api/autocraft/status, /api/autocraft/plugins, /api/autocraft/logs\n"
            "- /api/audit, /api/settings (GET/PUT), /api/actions/diagnostic-bundle\n"
            "Авторизация: Bearer-токен после /api/login или X-Panel-Token.",
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Менеджер пользователей: вход ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_SHOW_CREDENTIALS
    )
    async def webpanel_user_manager_entry(message: MessageT):
        user_id = message.from_user.id
        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("получить список пользователей панели", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[user_id] = "await_user_select"
        webpanel_ctx[user_id] = {}
        await message.answer(
            _format_users_list_text(users) + "\n\nВыберите пользователя (кнопкой с логином).",
            reply_markup=_get_user_select_keyboard(users),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) in {"await_user_select", "await_user_action"}
        and m.text == BTN_USER_MANAGER_REFRESH
    )
    async def webpanel_user_manager_refresh(message: MessageT):
        user_id = message.from_user.id
        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("обновить список пользователей", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[user_id] = "await_user_select"
        webpanel_ctx[user_id] = {}
        await message.answer(
            _format_users_list_text(users) + "\n\nВыберите пользователя.",
            reply_markup=_get_user_select_keyboard(users),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) in {"await_user_select", "await_user_action"}
        and m.text == BTN_USER_MANAGER_EXIT
    )
    async def webpanel_user_manager_exit(message: MessageT):
        webpanel_step.pop(message.from_user.id, None)
        webpanel_ctx.pop(message.from_user.id, None)
        await message.answer(
            "Менеджер пользователей закрыт.",
            reply_markup=_get_keyboard(password_mode=False),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_select"
        and m.text not in {BTN_USER_MANAGER_REFRESH, BTN_USER_MANAGER_EXIT}
    )
    async def webpanel_user_manager_select(message: MessageT):
        selected_login = (message.text or "").strip()
        if not selected_login:
            await message.answer(
                "Выберите пользователя кнопкой из списка.",
                reply_markup=_get_user_select_keyboard(await asyncio.to_thread(_load_panel_users_safe)),
            )
            return
        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("получить список пользователей", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        user_map = {
            str(item.get("username") or "").strip(): item
            for item in users
            if str(item.get("username") or "").strip()
        }
        if selected_login not in user_map:
            await message.answer(
                "Пользователь не найден в списке. Нажмите обновление и выберите снова.",
                reply_markup=_get_user_select_keyboard(users),
            )
            return
        webpanel_ctx[message.from_user.id] = {"selected_username": selected_login}
        webpanel_step[message.from_user.id] = "await_user_action"
        selected_info = user_map[selected_login]
        await message.answer(
            "Выбран пользователь:\n"
            + _format_user_line(selected_info)
            + "\n\nВыберите действие.",
            reply_markup=_get_user_actions_keyboard(),
        )

    # --- Логи ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_LOGS
    )
    async def webpanel_logs(message: MessageT):
        text, error = _tail_db_logs(base_dir, limit=120)
        if error:
            write_bot_log(f"Ошибка чтения логов панели из базы: {error}")
            await message.answer(
                f"Не удалось получить логи из базы: {error}",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        if not text:
            await message.answer("Логи пустые.", reply_markup=_get_keyboard(password_mode=False))
            return

        chunks = _split_log_chunks(text)
        for idx, chunk in enumerate(chunks):
            payload = f"```text\n{chunk}\n```"
            try:
                await message.answer(
                    payload,
                    parse_mode="Markdown",
                    reply_markup=_get_keyboard(password_mode=False) if idx == 0 else None,
                )
            except Exception as e:
                write_bot_log(f"Ошибка отправки логов панели: {e}")
                try:
                    await message.answer(
                        chunk,
                        reply_markup=_get_keyboard(password_mode=False) if idx == 0 else None,
                    )
                except Exception as e2:
                    write_bot_log(f"Ошибка отправки логов панели без Markdown: {e2}")
                    await message.answer(
                        f"Не удалось отправить логи: {e2}",
                        reply_markup=_get_keyboard(password_mode=False),
                    )
                return


    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_SETTINGS
    )
    async def webpanel_settings(message: MessageT):
        webpanel_step[message.from_user.id] = "await_settings"
        cfg = panel_config.load_config(base_dir)
        await message.answer(
            "Введите новые настройки в формате ключ=значение.\n"
            f"Текущие: host={cfg.host} port={cfg.port} debug={int(cfg.debug)} retention={cfg.retention_days} refresh={cfg.overview_refresh_seconds}\n"
            "Пример: host=0.0.0.0 port=5000 debug=0 retention=7 refresh=10\n"
            "refresh — интервал обновления обзора в секундах (0 = отключить).\n"
            f"Для отмены нажмите: {BTN_CANCEL_SETTINGS}",
            reply_markup=_get_keyboard(settings_mode=True),
        )

    # --- Настройки: отмена ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_settings"
        and m.text == BTN_CANCEL_SETTINGS
    )
    async def webpanel_settings_cancel(message: MessageT):
        webpanel_step.pop(message.from_user.id, None)
        await message.answer("Настройки без изменений.", reply_markup=_get_keyboard(password_mode=False))

    # --- Настройки: сохранение ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_settings"
        and (m.text or "").strip() != BTN_CANCEL_SETTINGS
    )
    async def webpanel_settings_save(message: MessageT):
        data = _parse_settings(message.text or "")
        if not data:
            await message.answer(
                "Не удалось распознать настройки. Используйте формат ключ=значение.",
                reply_markup=_get_keyboard(settings_mode=True),
            )
            return

        cfg = panel_config.load_config(base_dir)

        if "host" in data:
            cfg.host = data["host"]
        if "port" in data:
            try:
                cfg.port = int(data["port"])
            except Exception:
                pass
        if "debug" in data:
            cfg.debug = data["debug"].lower() in ("1", "true", "yes", "on")
        if "retention" in data:
            try:
                cfg.retention_days = int(data["retention"])
            except Exception:
                pass
        refresh_raw = (
            data.get("refresh")
            or data.get("overview_refresh")
            or data.get("overview_refresh_seconds")
        )
        if refresh_raw is not None:
            try:
                refresh_val = int(refresh_raw)
                if refresh_val <= 0:
                    cfg.overview_refresh_seconds = 0
                else:
                    cfg.overview_refresh_seconds = max(2, min(refresh_val, 120))
            except Exception:
                pass

        panel_config.save_config(base_dir, cfg)
        try:
            srv = _ensure_server()
        except Exception as e:
            write_bot_log(f"Ошибка инициализации веб-панели: {e}")
            await message.answer(
                _format_error("сохранить настройки веб-панели", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        srv.runtime.config = cfg
        _safe_audit(srv, str(message.from_user.id), "panel_settings_update", True, source="telegram")

        webpanel_step.pop(message.from_user.id, None)
        await message.answer(
            "Настройки сохранены. Для применения перезапустите панель.",
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Первый запуск: создание первого Super Admin ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_first_super_admin_login"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_first_admin_login(message: MessageT):
        default_login = "admin"
        ctx = webpanel_ctx.setdefault(message.from_user.id, {})
        raw_login = (message.text or "").strip()
        login = raw_login or str(ctx.get("first_admin_login_default") or default_login)
        if len(login) < 3 or (" " in login):
            await message.answer(
                "Логин должен быть минимум 3 символа и без пробелов.\n"
                "Введите логин первого Super Admin (Enter = admin).",
                reply_markup=_get_cancel_keyboard(),
            )
            return

        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("проверить логин первого Super Admin", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return

        used = {
            str(item.get("username") or "").strip().casefold()
            for item in users
            if str(item.get("username") or "").strip()
        }
        if login.casefold() in used:
            await message.answer(
                "Пользователь с таким логином уже существует. Введите другой логин.",
                reply_markup=_get_cancel_keyboard(),
            )
            return

        ctx["first_admin_login"] = login
        webpanel_step[message.from_user.id] = "await_first_super_admin_password"
        await message.answer(
            f"Введите пароль для {login} (минимум 6 символов).",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_first_super_admin_password"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_first_admin_password(message: MessageT):
        password = (message.text or "").strip()
        if len(password) < 6:
            await message.answer(
                "Пароль слишком короткий. Минимум 6 символов.",
                reply_markup=_get_cancel_keyboard(),
            )
            return

        ctx = webpanel_ctx.setdefault(message.from_user.id, {})
        ctx["first_admin_password"] = password
        webpanel_step[message.from_user.id] = "await_first_super_admin_password_confirm"
        await message.answer(
            "Повторите пароль для подтверждения.",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_first_super_admin_password_confirm"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_first_admin_password_confirm(message: MessageT):
        password_confirm = (message.text or "").strip()
        ctx = webpanel_ctx.get(message.from_user.id, {})
        login = str(ctx.get("first_admin_login") or "").strip()
        password = str(ctx.get("first_admin_password") or "").strip()
        if not login or not password:
            webpanel_step.pop(message.from_user.id, None)
            webpanel_ctx.pop(message.from_user.id, None)
            await message.answer(
                "Данные мастера первого запуска потеряны. Начните заново.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return

        if password != password_confirm:
            webpanel_step[message.from_user.id] = "await_first_super_admin_password"
            await message.answer(
                "Пароли не совпадают. Введите пароль снова.",
                reply_markup=_get_cancel_keyboard(),
            )
            return

        try:
            await asyncio.to_thread(
                panel_config.create_panel_user,
                base_dir,
                login,
                login,
                password,
                "Super Admin",
            )
        except Exception as e:
            await message.answer(
                _format_error("создать первого Super Admin", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return

        flow_source = str(ctx.get("first_admin_flow_source") or "").strip().lower()
        webpanel_step.pop(message.from_user.id, None)
        webpanel_ctx.pop(message.from_user.id, None)

        if flow_source == "start":
            try:
                srv = _ensure_server()
                with _server_lock:
                    srv.start()
                started = await _await_effective_state(srv, True, timeout=8.0)
                if started:
                    _safe_audit(
                        srv,
                        str(message.from_user.id),
                        "panel_start_after_first_super_admin",
                        True,
                        source="telegram",
                    )
                    await message.answer(
                        f"Первый пользователь {login} (Super Admin) создан.\n"
                        f"Панель запущена.\nАдрес: {srv.url()}",
                        reply_markup=_get_keyboard(password_mode=False),
                    )
                    return
                await message.answer(
                    f"Первый пользователь {login} (Super Admin) создан, "
                    "но запуск панели не подтверждён.",
                    reply_markup=_get_keyboard(password_mode=False),
                )
                return
            except Exception as e:
                await message.answer(
                    f"Первый пользователь {login} (Super Admin) создан, "
                    f"но панель не удалось запустить: {e}",
                    reply_markup=_get_keyboard(password_mode=False),
                )
                return

        await message.answer(
            f"Первый пользователь {login} (Super Admin) создан.\n"
            "Теперь можно запускать веб-панель.",
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Добавление пользователя: вход в мастер ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and m.text == BTN_CHANGE_PASS
    )
    async def webpanel_add_user_start(message: MessageT):
        user_id = message.from_user.id
        webpanel_step[user_id] = "await_new_user_login"
        webpanel_ctx[user_id] = {}
        await message.answer(
            "Введите логин нового пользователя (без пробелов).\n"
            f"Для отмены нажмите: {BTN_CANCEL_PASS}",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id)
        in {
            "await_first_super_admin_login",
            "await_first_super_admin_password",
            "await_first_super_admin_password_confirm",
            "await_new_user_login",
            "await_new_user_name",
            "await_new_user_role",
            "await_new_user_password",
            "await_user_change_password",
            "await_user_delete_confirm",
        }
        and (m.text or "").strip() in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_users_cancel(message: MessageT):
        webpanel_step.pop(message.from_user.id, None)
        webpanel_ctx.pop(message.from_user.id, None)
        await message.answer(
            "Операция с пользователями отменена.",
            reply_markup=_get_keyboard(password_mode=False),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_new_user_login"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_add_user_login(message: MessageT):
        login = (message.text or "").strip()
        if len(login) < 3 or (" " in login):
            await message.answer(
                "Логин некорректный. Минимум 3 символа, без пробелов.",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("проверить логин пользователя", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return
        used = {
            str(item.get("username") or "").strip().casefold()
            for item in users
            if str(item.get("username") or "").strip()
        }
        if login.casefold() in used:
            await message.answer(
                "Пользователь с таким логином уже существует. Введите другой логин.",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        ctx = webpanel_ctx.setdefault(message.from_user.id, {})
        ctx["new_user_login"] = login
        webpanel_step[message.from_user.id] = "await_new_user_name"
        await message.answer(
            "Введите имя пользователя (можно оставить короткое, например: Оператор).",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_new_user_name"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_add_user_name(message: MessageT):
        display_name = (message.text or "").strip()
        if not display_name:
            await message.answer(
                "Имя не может быть пустым. Введите имя пользователя.",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        try:
            roles = await asyncio.to_thread(_load_panel_roles_safe)
        except Exception as e:
            await message.answer(
                _format_error("получить список ролей", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return
        ctx = webpanel_ctx.setdefault(message.from_user.id, {})
        ctx["new_user_name"] = display_name
        webpanel_step[message.from_user.id] = "await_new_user_role"
        await message.answer(
            "Выберите роль для нового пользователя.",
            reply_markup=_get_role_select_keyboard(roles),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_new_user_role"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_add_user_role(message: MessageT):
        role_name = (message.text or "").strip()
        try:
            roles = await asyncio.to_thread(_load_panel_roles_safe)
        except Exception as e:
            await message.answer(
                _format_error("получить список ролей", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return
        if role_name not in roles:
            await message.answer(
                "Роль не найдена. Выберите роль кнопкой из списка.",
                reply_markup=_get_role_select_keyboard(roles),
            )
            return
        ctx = webpanel_ctx.setdefault(message.from_user.id, {})
        ctx["new_user_role"] = role_name
        webpanel_step[message.from_user.id] = "await_new_user_password"
        await message.answer(
            "Введите пароль для нового пользователя (минимум 6 символов).",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_new_user_password"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_add_user_password(message: MessageT):
        password = (message.text or "").strip()
        if len(password) < 6:
            await message.answer(
                "Пароль слишком короткий. Минимум 6 символов.",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        ctx = webpanel_ctx.get(message.from_user.id, {})
        login = str(ctx.get("new_user_login") or "").strip()
        name = str(ctx.get("new_user_name") or "").strip()
        role_name = str(ctx.get("new_user_role") or "").strip()
        if not login or not name or not role_name:
            webpanel_step.pop(message.from_user.id, None)
            webpanel_ctx.pop(message.from_user.id, None)
            await message.answer(
                "Данные мастера добавления потеряны. Начните заново.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        try:
            await asyncio.to_thread(
                panel_config.create_panel_user,
                base_dir,
                login,
                name,
                password,
                role_name,
            )
            srv = _ensure_server()
            _safe_audit(srv, str(message.from_user.id), "create_user_via_bot", True, source="telegram")
        except Exception as e:
            try:
                srv = _ensure_server()
                _safe_audit(
                    srv,
                    str(message.from_user.id),
                    "create_user_via_bot",
                    False,
                    source="telegram",
                    details=str(e),
                )
            except Exception:
                pass
            await message.answer(
                _format_error("добавить пользователя панели", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return
        webpanel_step.pop(message.from_user.id, None)
        webpanel_ctx.pop(message.from_user.id, None)
        await message.answer(
            f"Пользователь создан.\nЛогин: {login}\nПароль: {password}\nРоль: {role_name}",
            reply_markup=_get_keyboard(password_mode=False),
        )

    # --- Менеджер пользователей: действия с выбранным пользователем ---
    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_action"
        and m.text == BTN_USER_SELECT_OTHER
    )
    async def webpanel_user_select_other(message: MessageT):
        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            await message.answer(
                _format_error("получить список пользователей", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[message.from_user.id] = "await_user_select"
        webpanel_ctx[message.from_user.id] = {}
        await message.answer(
            _format_users_list_text(users) + "\n\nВыберите пользователя.",
            reply_markup=_get_user_select_keyboard(users),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_action"
        and m.text == BTN_USER_SHOW_CREDENTIALS
    )
    async def webpanel_user_show_credentials(message: MessageT):
        ctx = webpanel_ctx.get(message.from_user.id, {})
        username = str(ctx.get("selected_username") or "").strip()
        if not username:
            await message.answer(
                "Сначала выберите пользователя.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        try:
            payload = await asyncio.to_thread(panel_config.get_panel_user_credentials, base_dir, username)
        except Exception as e:
            await message.answer(
                _format_error("показать логин и пароль пользователя", e),
                reply_markup=_get_user_actions_keyboard(),
            )
            return
        stored_password = str(payload.get("password") or "")
        password_state = str(payload.get("password_state") or "").strip().lower()
        if not stored_password:
            if password_state == "hash_only":
                stored_password = "(в БД хранится только хэш, текущий пароль нельзя показать; задайте новый через «Сменить пароль пользователя»)"
            else:
                stored_password = "(пароль не задан или не сохранён в открытом виде; задайте новый через «Сменить пароль пользователя»)"
        await message.answer(
            f"Логин: {payload.get('username')}\nПароль: {stored_password}",
            reply_markup=_get_user_actions_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_action"
        and m.text == BTN_USER_CHANGE_PASSWORD
    )
    async def webpanel_user_change_password_start(message: MessageT):
        ctx = webpanel_ctx.get(message.from_user.id, {})
        username = str(ctx.get("selected_username") or "").strip()
        if not username:
            await message.answer(
                "Сначала выберите пользователя.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[message.from_user.id] = "await_user_change_password"
        await message.answer(
            f"Введите новый пароль для пользователя {username} (минимум 6 символов).",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_change_password"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_user_change_password_save(message: MessageT):
        new_password = (message.text or "").strip()
        if len(new_password) < 6:
            await message.answer(
                "Пароль слишком короткий. Минимум 6 символов.",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        ctx = webpanel_ctx.get(message.from_user.id, {})
        username = str(ctx.get("selected_username") or "").strip()
        if not username:
            webpanel_step.pop(message.from_user.id, None)
            await message.answer(
                "Пользователь не выбран. Откройте менеджер заново.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        try:
            await asyncio.to_thread(
                panel_config.set_panel_user_password,
                base_dir,
                username,
                new_password,
            )
            srv = _ensure_server()
            _safe_audit(
                srv,
                str(message.from_user.id),
                "change_user_password_via_bot",
                True,
                source="telegram",
                target=username,
            )
        except Exception as e:
            try:
                srv = _ensure_server()
                _safe_audit(
                    srv,
                    str(message.from_user.id),
                    "change_user_password_via_bot",
                    False,
                    source="telegram",
                    target=username,
                    details=str(e),
                )
            except Exception:
                pass
            await message.answer(
                _format_error("сменить пароль пользователя", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return
        webpanel_step[message.from_user.id] = "await_user_action"
        await message.answer(
            f"Пароль пользователя {username} обновлён.",
            reply_markup=_get_user_actions_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_action"
        and m.text == BTN_USER_DELETE
    )
    async def webpanel_user_delete_start(message: MessageT):
        ctx = webpanel_ctx.get(message.from_user.id, {})
        username = str(ctx.get("selected_username") or "").strip()
        if not username:
            await message.answer(
                "Сначала выберите пользователя.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[message.from_user.id] = "await_user_delete_confirm"
        await message.answer(
            f"Подтвердите удаление пользователя {username}.\n"
            "Напишите: УДАЛИТЬ",
            reply_markup=_get_cancel_keyboard(),
        )

    @dp.message_handler(
        lambda m: _is_authorized(m.from_user.id)
        and webpanel_mode.get(m.from_user.id)
        and webpanel_step.get(m.from_user.id) == "await_user_delete_confirm"
        and (m.text or "").strip() not in {BTN_CANCEL_PASS, "Отмена"}
    )
    async def webpanel_user_delete_confirm(message: MessageT):
        if (message.text or "").strip().upper() != "УДАЛИТЬ":
            await message.answer(
                "Для удаления отправьте точное подтверждение: УДАЛИТЬ",
                reply_markup=_get_cancel_keyboard(),
            )
            return
        ctx = webpanel_ctx.get(message.from_user.id, {})
        username = str(ctx.get("selected_username") or "").strip()
        if not username:
            webpanel_step.pop(message.from_user.id, None)
            await message.answer(
                "Пользователь не выбран. Откройте менеджер заново.",
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        try:
            await asyncio.to_thread(panel_config.delete_panel_user, base_dir, username)
            srv = _ensure_server()
            _safe_audit(
                srv,
                str(message.from_user.id),
                "delete_user_via_bot",
                True,
                source="telegram",
                target=username,
            )
        except Exception as e:
            try:
                srv = _ensure_server()
                _safe_audit(
                    srv,
                    str(message.from_user.id),
                    "delete_user_via_bot",
                    False,
                    source="telegram",
                    target=username,
                    details=str(e),
                )
            except Exception:
                pass
            await message.answer(
                _format_error("удалить пользователя", e),
                reply_markup=_get_cancel_keyboard(),
            )
            return

        try:
            users = await asyncio.to_thread(_load_panel_users_safe)
        except Exception as e:
            webpanel_step.pop(message.from_user.id, None)
            webpanel_ctx.pop(message.from_user.id, None)
            await message.answer(
                "Пользователь удалён, но не удалось перечитать список: "
                + _format_error("получить список пользователей", e),
                reply_markup=_get_keyboard(password_mode=False),
            )
            return
        webpanel_step[message.from_user.id] = "await_user_select"
        webpanel_ctx[message.from_user.id] = {}
        await message.answer(
            f"Пользователь {username} удалён.\n\n" + _format_users_list_text(users),
            reply_markup=_get_user_select_keyboard(users),
        )

    # --- Fallback ---
    @dp.message_handler(lambda m: _is_authorized(m.from_user.id) and webpanel_mode.get(m.from_user.id))
    async def webpanel_fallback(message: MessageT):
        step = webpanel_step.get(message.from_user.id)
        if step in {
            "await_first_super_admin_login",
            "await_first_super_admin_password",
            "await_first_super_admin_password_confirm",
        }:
            await message.answer(
                "Сейчас выполняется мастер первого запуска. "
                "Введите запрошенные данные или нажмите отмену.",
                reply_markup=_get_cancel_keyboard(),
            )
        elif step in {
            "await_new_user_login",
            "await_new_user_name",
            "await_new_user_role",
            "await_new_user_password",
            "await_user_change_password",
            "await_user_delete_confirm",
        }:
            await message.answer(
                "Сейчас выполняется операция с пользователями. Используйте текущие подсказки или нажмите отмену.",
                reply_markup=_get_cancel_keyboard(),
            )
        elif step == "await_user_select":
            try:
                users = await asyncio.to_thread(_load_panel_users_safe)
            except Exception:
                users = []
            await message.answer(
                "Выберите пользователя кнопкой ниже.",
                reply_markup=_get_user_select_keyboard(users),
            )
        elif step == "await_user_action":
            await message.answer(
                "Выберите действие для выбранного пользователя.",
                reply_markup=_get_user_actions_keyboard(),
            )
        elif step == "await_settings":
            await message.answer(
                "Сейчас режим настройки панели. Введите параметры или нажмите отмену.",
                reply_markup=_get_keyboard(settings_mode=True),
            )
        else:
            await message.answer(
                "Используйте кнопки модуля для управления панелью.",
                reply_markup=_get_keyboard(password_mode=False),
            )

    return True



# --- Инициализация автозапуска при импорте модуля ---
try:
    _autostart_panel_on_import()
except Exception as _e:
    # Никогда не ломаем импортом весь бот.
    try:
        write_bot_log(f"Автозапуск веб-панели: непойманная ошибка: {_e}")
    except Exception:
        pass
