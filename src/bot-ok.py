
import sys
import time
import psutil
import logging

# --- API Watchdog Mode ---
if "--api-watchdog" in sys.argv:
    try:
        idx = sys.argv.index("--api-watchdog")
        parent_pid = int(sys.argv[idx+1])
        api_pid    = int(sys.argv[idx+2])
    except (ValueError, IndexError):
        print("Usage: <exe> --api-watchdog <parent_pid> <api_pid>")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)
    logging.info(f"API Watchdog started: parent={parent_pid}, api={api_pid}")
    while True:
        if not psutil.pid_exists(parent_pid):
            logging.info("Parent died — killing API process tree")
            try:
                p = psutil.Process(api_pid)
                for c in p.children(recursive=True):
                    c.kill()
                p.kill()
            except Exception as e:
                logging.error(f"Error killing API process: {e}")
            break
        if not psutil.pid_exists(api_pid):
            logging.info("API process exited on its own — stopping watchdog")
            break
        time.sleep(1)
    sys.exit(0)

import logging
import sys
import logging
import sys
from pathlib import Path

# Глобальный обработчик ненехваченных исключений (вынесен в logging_system.py)
import logging_system as logsys
logsys.install_excepthook()


import os
import sys
import time
import threading
import asyncio
import subprocess
import platform
from datetime import datetime

import logging  # новый импорт для стандартного логирования

# Добавляем импорт для обработки исключения остановки
from aiogram.utils import exceptions

# -----------------------------------------------------
# Логирование вынесено в отдельный модуль: logging_system.py
# -----------------------------------------------------# Буферы логов (до старта GUI/до авторизации)
pending_log_messages = logsys.pending_log_messages
pending_tg_logs = logsys.pending_tg_logs
PENDING_TG_MAX = logsys.PENDING_TG_MAX

# Аудит доступа (показывается после авторизации даже при выключенном дебаге)
auth_audit_events = logsys.auth_audit_events
add_auth_audit = logsys.add_auth_audit

# Важные события до авторизации (дайджест)
important_events = logsys.important_events
add_important_event = logsys.add_important_event

# Краткая информация о подключении (заполняется позже, см. start_bot)
connection_summary = "не определено"

# Проксируем отчёт после авторизации, чтобы он видел актуальную сводку подключения
async def send_post_auth_report(message, debug_enabled):
    return await logsys.send_post_auth_report(
        message,
        debug_enabled=debug_enabled,
        connection_summary=connection_summary,
    )

gui_ready = False  # станет True, когда MainWindow подключится и проглотит буфер логов
# -----------------------------------------------------
# 1.0. CLI override for config path (must be **very** early)
# -----------------------------------------------------
_BASE_DIR_OVERRIDE = None
_CONFIG_PATH_OVERRIDE = None

def _scan_cli_for_config_override():
    try:
        argv = sys.argv[1:]
        for idx, arg in enumerate(argv):
            low = str(arg).lower()
            if low in ("--config", "/config"):
                if idx + 1 < len(argv):
                    p = argv[idx+1]
                    p = os.path.abspath(os.path.expanduser(p.strip().strip('"').strip("'")))
                    return p
            if low.startswith("--config=") or low.startswith("/config="):
                p = arg.split("=", 1)[1]
                p = os.path.abspath(os.path.expanduser(p.strip().strip('"').strip("'")))
                return p
    except Exception:
        pass
    return None


_tmp_cfg = _scan_cli_for_config_override()
if _tmp_cfg:
    _CONFIG_PATH_OVERRIDE = _tmp_cfg
    try:
        _BASE_DIR_OVERRIDE = os.path.dirname(_CONFIG_PATH_OVERRIDE)
    except Exception:
        _BASE_DIR_OVERRIDE = None
  # все сообщения логов будут сохраняться сюда

# -----------------------------------------------------
# 1. Функции определения пути приложения
# -----------------------------------------------------
def is_frozen():
    return getattr(sys, 'frozen', False)

def get_app_dir():
    global _BASE_DIR_OVERRIDE
    if '_BASE_DIR_OVERRIDE' in globals() and _BASE_DIR_OVERRIDE:
        return _BASE_DIR_OVERRIDE
    if "NUITKA_ONEFILE_PARENT" in os.environ:
        return os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
    elif is_frozen():
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def get_script_path():
    if "NUITKA_ONEFILE_PARENT" in os.environ:
        return os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"])
    elif is_frozen():
        return os.path.abspath(sys.argv[0])
    else:
        return os.path.abspath(__file__)

APP_PATH = get_script_path()

# -----------------------------------------------------
# 1.0.a Маркер "истинной" папки установки (для onefile-автозапуска)
# -----------------------------------------------------
def _get_program_data_dir():
    return os.environ.get("PROGRAMDATA", r"C:\ProgramData")

_MARKER_DIR = os.path.join(_get_program_data_dir(), "AutoCraftBot")
_MARKER_FILE = os.path.join(_MARKER_DIR, "install_root.txt")

def _get_stub_exe_path():
    return os.environ.get("NUITKA_ONEFILE_PARENT", sys.executable)

def _write_install_root_marker():
    try:
        os.makedirs(_MARKER_DIR, exist_ok=True)
        real_exe = _get_stub_exe_path()
        real_dir = os.path.dirname(os.path.abspath(real_exe))
        with open(_MARKER_FILE, "w", encoding="utf-8") as f:
            f.write(real_dir)
    except Exception:
        pass

def _read_install_root_marker():
    try:
        if os.path.exists(_MARKER_FILE):
            with open(_MARKER_FILE, "r", encoding="utf-8") as f:
                d = f.read().strip()
                if d and os.path.isdir(d):
                    return d
    except Exception:
        pass
    return None

try:
    _write_install_root_marker()
except Exception:
    pass

# Если всё ещё в onefile-времянке и нет явного --config, пытаемся восстановить базу из маркера
try:
    _cur_dir = get_app_dir()
    if (("onefile_" in _cur_dir.lower()) and not _CONFIG_PATH_OVERRIDE):
        marker_dir = _read_install_root_marker()
        if marker_dir and os.path.exists(os.path.join(marker_dir, "config.ini")):
            # Не трогаем base_dir здесь; ниже base_dir = get_app_dir() подхватит _BASE_DIR_OVERRIDE
            try:
                globals()["_BASE_DIR_OVERRIDE"] = marker_dir
            except Exception:
                pass
except Exception:
    pass



# -----------------------------------------------------
# 1.1. Вспомогательная функция для корректного добавления пути в sys.path
# -----------------------------------------------------
def add_site_packages(path):
    import site
    if path not in sys.path:
        # Добавляем каталог с помощью addsitedir, который обрабатывает .pth файлы
        site.addsitedir(path)
        # Сбрасываем кэш импортов
        import importlib
        importlib.invalidate_caches()
    # Обновляем переменную окружения PYTHONPATH
    if "PYTHONPATH" in os.environ:
        paths = os.environ["PYTHONPATH"].split(os.pathsep)
        if path not in paths:
            os.environ["PYTHONPATH"] = os.environ["PYTHONPATH"] + os.pathsep + path
    else:
        os.environ["PYTHONPATH"] = path

# -----------------------------------------------------
# 2. Определяем рабочую директорию и создаём необходимые папки
# -----------------------------------------------------
base_dir = get_app_dir()
print("App dir =", base_dir)
# Guard: if base_dir points to Nuitka temp, switch to parent if available
try:
    temp_indicator = os.path.join(os.environ.get("TEMP", ""), "onefile_")
    if ("onefile_" in base_dir.lower() or (temp_indicator and base_dir.lower().startswith(temp_indicator.lower()))) and "NUITKA_ONEFILE_PARENT" in os.environ:
        base_dir = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
except Exception:
    pass


# Enforce working dir = base_dir (vital for autorun)
try:
    os.chdir(base_dir)
except Exception:
    pass

# Bootstrap log
try:
    _boot_log = os.path.join(base_dir, "autostart_bootstrap.log")
    with open(_boot_log, "a", encoding="utf-8") as _f:
        _f.write("=== BOOT(bot-ok) ===\n")
        _f.write(f"cwd={os.getcwd()}\n")
        _f.write(f"sys.executable={sys.executable}\n")
        _f.write(f"base_dir={base_dir}\n")
        _cfg_guess = _CONFIG_PATH_OVERRIDE or os.path.join(base_dir, "config.ini")
        _f.write(f"config_guess={_cfg_guess} exists={os.path.exists(_cfg_guess)}\n")
        _f.write(f"argv={sys.argv}\n")
        _f.write(f"NUITKA_ONEFILE_PARENT={os.environ.get('NUITKA_ONEFILE_PARENT')!r}\n")
        _f.write(f"marker_file={_MARKER_FILE} marker_dir={_read_install_root_marker()!r}\n")
except Exception:
    pass
# Настройка логирования (вынесено в logging_system.py)
import configparser

API_SECTION = "api_server"
API_USE_STANDARD_KEY = "use_standard_api"

debug_enabled, logger = logsys.configure_bootstrap_logging(
    base_dir=base_dir,
    config_path=(_CONFIG_PATH_OVERRIDE or str(Path(base_dir) / "config.ini")),
    config_section="credentials",
    stdout=sys.stdout,
)



folders = ["log", "notes", "files", "screenshots", "infiles", "plugins"]
for folder in folders:
    path = os.path.join(base_dir, folder)
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        print(f"Создана папка: {path}")
        # Логирование создания папки
        logging.info(f"Создана папка: {path}")
    else:
        print(f"Папка уже существует: {path}")
        # Логирование, что папка уже существует
        logging.info(f"Папка уже существует: {path}")

print("Готово! Бот работает в:", base_dir)

# -----------------------------------------------------
# 3. Функция распаковки базового Python (при старте бота)
# -----------------------------------------------------
def get_base_python_exe():
    """
    Возвращает путь к базовому интерпретатору Python, распакованному из Python.zip.
    Если такой интерпретатор не найден, возвращает текущий sys.executable.
    """
    python_folder = os.path.join(base_dir, "python")
    if platform.system().lower().startswith("win"):
        exe = os.path.join(python_folder, "python.exe")
    else:
        exe = os.path.join(python_folder, "bin", "python")
    if os.path.exists(exe):
        return exe
    return sys.executable

# -----------------------------------------------------
# 4. Импорт aiogram и PyQt5
# -----------------------------------------------------
from aiogram import Bot, Dispatcher, types
import info
from aiogram.utils import executor

from PyQt5.QtWidgets import (
    QApplication
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon

# -----------------------------------------------------
# 4.1. Ранний импорт manager_web_dashboard.py (после тяжелых зависимостей)
# -----------------------------------------------------

def _early_import_startrun_webpanel():
    """Пытаемся загрузить модуль веб-панели как можно раньше.

    Это нужно, чтобы manager_web_dashboard.py успел зарегистрировать себя
    до показа главного меню, и чтобы в EXE (Nuitka) он не потерялся.

    Импорт максимально безопасный: любая ошибка гасится, старт приложения
    продолжает работать.
    """
    try:
        import importlib
        import importlib.util

        moduls_dir = os.path.join(base_dir, 'moduls')
        if os.path.isdir(moduls_dir) and moduls_dir not in sys.path:
            sys.path.insert(0, moduls_dir)

        # 1) Пытаемся как обычный модуль/пакет
        for name in ('manager_web_dashboard', 'moduls.manager_web_dashboard'):
            try:
                m = importlib.import_module(name)
                logging.info(f"[BOOT] manager_web_dashboard imported as {name}")
                return m
            except Exception:
                pass

        # 2) Фолбэк: грузим как файл из base_dir/moduls
        file_path = os.path.join(moduls_dir, 'manager_web_dashboard.py')
        if os.path.isfile(file_path):
            spec = importlib.util.spec_from_file_location('manager_web_dashboard', file_path)
            if spec and spec.loader:
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                sys.modules.setdefault('manager_web_dashboard', m)
                logging.info(f"[BOOT] manager_web_dashboard imported from file: {file_path}")
                return m

        logging.warning('[BOOT] manager_web_dashboard not found (skipped)')
    except Exception as e:
        try:
            logging.warning(f"[BOOT] manager_web_dashboard early import failed: {e}")
        except Exception:
            pass
    return None

# Импортируем модуль веб-панели сразу после тяжелых зависимостей
_startrun_webpanel_module = _early_import_startrun_webpanel()


def _register_startrun_webpanel_handlers(dp):
    """Если модуль загружен, пробуем вызвать register_handlers(dp)."""
    try:
        m = _startrun_webpanel_module
        if m is None:
            return
        fn = getattr(m, 'register_handlers', None)
        if callable(fn):
            fn(dp)
            logging.info('[BOOT] manager_web_dashboard.register_handlers(dp) executed')
    except Exception as e:
        try:
            logging.warning(f"[BOOT] manager_web_dashboard.register_handlers(dp) failed: {e}")
        except Exception:
            pass


def _early_import_activated_users_store():
    """Ранний безопасный импорт модуля БД активированных пользователей."""
    try:
        import importlib
        import importlib.util

        moduls_dir = os.path.join(base_dir, "moduls")
        if os.path.isdir(moduls_dir) and moduls_dir not in sys.path:
            sys.path.insert(0, moduls_dir)

        for name in ("activated_users_store", "moduls.activated_users_store"):
            try:
                module = importlib.import_module(name)
                logging.info(f"[BOOT] activated_users_store imported as {name}")
                return module
            except Exception:
                pass

        file_path = os.path.join(moduls_dir, "activated_users_store.py")
        if os.path.isfile(file_path):
            spec = importlib.util.spec_from_file_location("activated_users_store", file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules.setdefault("activated_users_store", module)
                logging.info(f"[BOOT] activated_users_store imported from file: {file_path}")
                return module
    except Exception as e:
        try:
            logging.warning(f"[BOOT] activated_users_store early import failed: {e}")
        except Exception:
            pass
    return None


_activated_users_store_module = _early_import_activated_users_store()

# -----------------------------------------------------
# 5. Глобальные переменные бота и состояния
# -----------------------------------------------------
TOKEN = ""
PIN_CODE = ""
allowed_accounts = set()  # список разрешённых аккаунтов (ID)

current_bot = None
current_loop = None
bot_thread = None

authorized_users = set()
note_mode = {}
pending_note = {}
file_mode = {}
power_mode = {}
pending_power_action = {}
infiles_mode = {}
plugins_mode = {}
autostart_mode = {}  # Для настройки автозапуска плагинов

MAX_FILE_SIZE = 50 * 1024 * 1024

# -----------------------------------------------------
# Логирование приложения (файлы/буферы/GUI) — теперь через logging_system.py
# -----------------------------------------------------

# Мини-эмиттер для GUI (сигналы). GUI слушает log_emitter.log_message
class LogEmitter(QObject):
    log_message = pyqtSignal(str)

log_emitter = LogEmitter()

# Инициализация файловых логов + привязка к GUI/буферам
logsys.bind_gui(
    log_emitter=log_emitter,
    pending_log_messages_ref=pending_log_messages,
    pending_tg_logs_ref=pending_tg_logs,
    gui_ready_getter=lambda: gui_ready,
)
logsys.init_app_logging(base_dir=base_dir)

# Экспортируем имена как раньше, чтобы остальной код не трогать
bot_log_file = logsys.bot_log_file
com_log_file = logsys.com_log_file
plugin_log_file = logsys.plugin_log_file
error_log_file = logsys.error_log_file
debug_log_file = logsys.debug_log_file

create_logger = logsys.create_logger
bot_logger = logsys.bot_logger
com_logger = logsys.com_logger
plugin_logger = logsys.plugin_logger
error_logger = logsys.error_logger
debug_logger = logsys.debug_logger

write_bot_log = logsys.write_bot_log
write_com_log = logsys.write_com_log
write_plugin_log = logsys.write_plugin_log
write_error_log = logsys.write_error_log
write_debug_log = logsys.write_debug_log
trace_calls = logsys.trace_calls


# -----------------------------------------------------
# CMD module # -----------------------------------------------------
class _DynamicDict(dict):
    __slots__ = ("_module_name", "_attr_name")
    def __init__(self, module_name: str, attr_name: str):
        super().__init__()
        self._module_name = module_name
        self._attr_name = attr_name

    def _target(self):
        mod = sys.modules.get(self._module_name)
        if mod is not None and hasattr(mod, self._attr_name):
            try:
                t = getattr(mod, self._attr_name)
                if isinstance(t, dict):
                    return t
            except Exception:
                pass
        return self

    def get(self, key, default=None):
        t = self._target()
        if t is self:
            return super().get(key, default)
        return t.get(key, default)

    def __getitem__(self, key):
        t = self._target()
        if t is self:
            return super().__getitem__(key)
        return t[key]

    def __setitem__(self, key, value):
        t = self._target()
        if t is self:
            return super().__setitem__(key, value)
        t[key] = value

    def pop(self, key, default=None):
        t = self._target()
        if t is self:
            return super().pop(key, default)
        return t.pop(key, default)

    def setdefault(self, key, default=None):
        t = self._target()
        if t is self:
            return super().setdefault(key, default)
        return t.setdefault(key, default)

    def __contains__(self, key):
        t = self._target()
        if t is self:
            return super().__contains__(key)
        return key in t

    def __len__(self):
        t = self._target()
        if t is self:
            return super().__len__()
        return len(t)

    def items(self):
        t = self._target()
        if t is self:
            return super().items()
        return t.items()

    def keys(self):
        t = self._target()
        if t is self:
            return super().keys()
        return t.keys()

    def values(self):
        t = self._target()
        if t is self:
            return super().values()
        return t.values()

# Expose cmd_mode and in_cmd_menu for gui imports
cmd_mode = _DynamicDict("modulcmd", "cmd_mode")
in_cmd_menu = _DynamicDict("modulcmd", "in_cmd_menu")
# -----------------------------------------------------
# 6.1. Менеджер плагинов с поддержкой изоляции через отдельные venv
# -----------------------------------------------------
import importlib
import traceback
import venv
import json
import shutil

PLUGIN_DIR = os.path.join(base_dir, "plugins")
loaded_plugins = {}  # { "имя_плагина": {"modules": [...], "meta": {...}, "venv_site": <site-packages> } }
plugins_autostart_completed = False  # флаг, что автозапуск плагинов завершился

def notify(dp: Dispatcher, chat_id, text: str):
    try:
        loop = getattr(dp, "loop", None)
        if loop is None:
            loop = current_loop
        if loop is None:
            write_bot_log("[ОШИБКА] Нет доступного event loop для отправки уведомления.")
            return
        asyncio.run_coroutine_threadsafe(dp.bot.send_message(chat_id, text), loop)
    except Exception as e:
        write_bot_log(f"[ОШИБКА] Не удалось отправить уведомление в Telegram: {e}")

def create_plugin_venv(plugin_folder: str, dp: Dispatcher, notify_chat_id=None):
    """
    Создаёт виртуальное окружение для плагина в его папке.
    Используется распакованный базовый Python (из папки python) для создания venv.
    """
    venv_path = os.path.join(plugin_folder, "venv")
    if not os.path.isdir(venv_path):
        try:
            write_bot_log(f"Создаю виртуальное окружение для плагина в {plugin_folder}...")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"Создаю виртуальное окружение для плагина {os.path.basename(plugin_folder)}...")
            base_python = get_base_python_exe()
            subprocess.check_call([base_python, "-m", "venv", venv_path])
            write_bot_log(f"Виртуальное окружение для плагина {os.path.basename(plugin_folder)} создано.")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"Виртуальное окружение для плагина {os.path.basename(plugin_folder)} создано.")
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось создать venv для плагина {os.path.basename(plugin_folder)}: {e}")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"[ОШИБКА] Не удалось создать venv для плагина {os.path.basename(plugin_folder)}: {e}")

def get_plugin_venv_paths(plugin_folder: str):
    venv_path = os.path.join(plugin_folder, "venv")
    if platform.system().lower().startswith("win"):
        pip_exe = os.path.join(venv_path, "Scripts", "pip.exe")
        python_exe = os.path.join(venv_path, "Scripts", "python.exe")
        site_packages = os.path.join(venv_path, "Lib", "site-packages")
    else:
        pip_exe = os.path.join(venv_path, "bin", "pip")
        python_exe = os.path.join(venv_path, "bin", "python")
        site_packages = os.path.join(venv_path, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
    return pip_exe, python_exe, site_packages

def install_dependency_for_plugin(dep: str, pip_exe: str, plugin_name: str, dp: Dispatcher, notify_chat_id=None):
    try:
        freeze_proc = subprocess.run([pip_exe, "freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if dep.lower() in freeze_proc.stdout.lower():
            write_bot_log(f"Зависимость {dep} уже установлена для плагина {plugin_name}.")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"Зависимость {dep} уже установлена для плагина {plugin_name}.")
            return
        process = subprocess.Popen([pip_exe, "install", "--upgrade", dep],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='ignore')
        if process.stdout:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if notify_chat_id:
                    notify(dp, notify_chat_id, f"[{plugin_name}] {line}")
        process.wait()
        if process.returncode != 0:
            error_msg = f"Установка зависимости {dep} для плагина {plugin_name} завершилась с ошибкой, код {process.returncode}"
            write_bot_log(f"[ОШИБКА] {error_msg}")
            write_plugin_log(f"[ОШИБКА] {error_msg}")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"[ОШИБКА] {error_msg}")
        else:
            write_bot_log(f"Успешно установлена зависимость {dep} для плагина {plugin_name}.")
            write_plugin_log(f"Успешно установлена зависимость {dep} для плагина {plugin_name}.")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"Успешно установлена зависимость {dep} для плагина {plugin_name}.")
    except Exception as e:
        write_bot_log(f"[ОШИБКА] Не удалось установить {dep} для плагина {plugin_name}: {e}")
        write_plugin_log(f"[ОШИБКА] Не удалось установить {dep} для плагина {plugin_name}: {e}")
        if notify_chat_id:
            notify(dp, notify_chat_id, f"[ОШИБКА] Не удалось установить {dep} для плагина {plugin_name}: {e}")

def scan_available_plugins():
    available = {}
    if not os.path.isdir(PLUGIN_DIR):
        os.makedirs(PLUGIN_DIR, exist_ok=True)
    for item in sorted(os.listdir(PLUGIN_DIR)):
        folder_path = os.path.join(PLUGIN_DIR, item)
        if not os.path.isdir(folder_path):
            continue
        plugin_name = item
        meta_file = os.path.join(folder_path, plugin_name + ".json")
        meta = {}
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                write_bot_log(f"[ОШИБКА] Не удалось прочитать {plugin_name}.json: {e}")
        meta.setdefault("name", plugin_name)
        available[plugin_name] = {"meta": meta, "folder": folder_path}
    write_bot_log(f"Сканирование плагинов завершено. Найдено {len(available)} плагинов.")
    return available

def reload_all_plugins(dp: Dispatcher, notify_chat_id=None):
    write_bot_log("Начинается перезагрузка плагинов.")
    if notify_chat_id:
        notify(dp, notify_chat_id, "Начинается перезагрузка плагинов.")
    for pname, info in list(loaded_plugins.items()):
        for mod in info["modules"]:
            remove_handlers_from_module(dp, mod.__name__)
    loaded_plugins.clear()
    available = scan_available_plugins()
    write_bot_log(f"Перезагрузка плагинов завершена. Доступно плагинов: {len(available)}.")
    if notify_chat_id:
        notify(dp, notify_chat_id, f"Перезагрузка плагинов завершена. Доступно плагинов: {len(available)}.")
    return [], []

def remove_handlers_from_module(dp: Dispatcher, module_name: str):
    dp.message_handlers.handlers[:] = [
        handler for handler in dp.message_handlers.handlers
        if handler.callback.__module__ != module_name
    ]
    dp.callback_query_handlers.handlers[:] = [
        handler for handler in dp.callback_query_handlers.handlers
        if handler.callback.__module__ != module_name
    ]

# -----------------------------------------------------
# Автозапуск плагинов (через config.ini)
# -----------------------------------------------------
AUTOSTART_SECTION = 'autostart'

def load_autostart_config():
    config.read(CONFIG_FILE, encoding='utf-8')
    if AUTOSTART_SECTION in config:
        plugins_str = config[AUTOSTART_SECTION].get('plugins', '')
        return [p.strip() for p in plugins_str.split(',') if p.strip()]
    return []

def save_autostart_config(plugins_list):
    if AUTOSTART_SECTION not in config:
        config[AUTOSTART_SECTION] = {}
    config[AUTOSTART_SECTION]['plugins'] = ','.join(plugins_list)
    _save_config()
    write_bot_log("Конфигурация автозапуска плагинов сохранена в config.ini")

async def auto_start_plugins(dp: Dispatcher):
    global plugins_autostart_completed
    # Перед автозапуском явно сбрасываем флаг завершения,
    # чтобы при повторном запуске не использовать старое значение.
    plugins_autostart_completed = False
    await asyncio.sleep(5)
    autostart_list = load_autostart_config()
    available = scan_available_plugins()
    for plugin in autostart_list:
        if plugin in available:
            plugin_key = plugin
            info = available[plugin_key]
            if plugin_key not in loaded_plugins:
                folder_path = info["folder"]
                plugin_name = plugin_key
                write_bot_log(f"Начинается автозапуск плагина: {plugin_name}")
                await asyncio.to_thread(create_plugin_venv, folder_path, dp)
                pip_exe, python_exe, site_packages = get_plugin_venv_paths(folder_path)
                meta = info["meta"]
                deps = meta.get("dependencies", [])
                for d in deps:
                    write_bot_log(f"Устанавливаю зависимость {d} для автозапуска плагина {plugin_name}")
                    await asyncio.to_thread(install_dependency_for_plugin, d, pip_exe, plugin_name, dp)
                modules_in_plugin = []
                py_files_found = False
                if site_packages:
                    add_site_packages(site_packages)
                for filename in os.listdir(folder_path):
                    if filename.endswith(".py"):
                        py_files_found = True
                        file_path = os.path.join(folder_path, filename)
                        spec = importlib.util.spec_from_file_location(plugin_name + "_" + filename, file_path)
                        module = importlib.util.module_from_spec(spec)
                        try:
                            spec.loader.exec_module(module)
                            modules_in_plugin.append(module)
                            write_bot_log(f"Импортирован модуль {filename} в плагине {plugin_name} (автозапуск).")
                        except Exception as e:
                            traceback.print_exc()
                            write_bot_log(f"[ОШИБКА] При импортировании {filename} в плагине {plugin_name} (автозапуск): {e}")
                if not py_files_found:
                    write_bot_log(f"[ПРЕДУПРЕЖДЕНИЕ] В папке {plugin_name} не найдено ни одного .py-файла (автозапуск).")
                for mod in modules_in_plugin:
                    if hasattr(mod, "init_plugin"):
                        try:
                            if site_packages:
                                add_site_packages(site_packages)
                            await asyncio.to_thread(mod.init_plugin, dp)
                            write_bot_log(f"Инициализирован init_plugin у модуля {mod.__name__} плагина {plugin_name} (автозапуск).")
                        except Exception as e:
                            traceback.print_exc()
                            write_bot_log(f"[ОШИБКА] init_plugin у модуля {mod.__name__} в плагине {plugin_name} (автозапуск): {e}")
                loaded_plugins[plugin_key] = {
                    "modules": modules_in_plugin,
                    "meta": meta,
                    "venv_site": site_packages
                }
                if modules_in_plugin:
                    write_bot_log(f"Плагин {plugin_name} автозапущен успешно.")
                else:
                    write_bot_log(f"Плагин {plugin_name} не содержит модулей для загрузки (автозапуск).")
        else:
            write_bot_log(f"[ПРЕДУПРЕЖДЕНИЕ] Плагин {plugin} для автозапуска не найден.")
    write_bot_log("Автозапуск плагинов завершён.")
    # Помечаем, что автозапуск плагинов завершился.
    plugins_autostart_completed = True

# -----------------------------------------------------
# 7. Функция запуска бота (в отдельном потоке)
# -----------------------------------------------------
def run_bot():
    from keymenu import get_main_keyboard, get_additional_keyboard

    # Реестр главного меню: динамическая проверка перед показом клавиатуры после авторизации.
    try:
        from mainmenu_registry import get_main_items as get_mainmenu_items
    except ImportError:
        get_mainmenu_items = None


    async def wait_for_mainmenu_items(
            timeout: float = 15.0,
            interval: float = 0.5,
            stable_checks: int = 3
    ) -> bool:
        """Ждём, пока модули зарегистрируют свои пункты в главном меню.

        Логика:
        - ждём появления хотя бы одного пункта;
        - затем ждём, пока количество пунктов подряд несколько раз не изменяется
          (чтобы все модули успели зарегистрироваться);
        - если по таймауту так и не появилось ни одного пункта — возвращаем False.
        """
        if get_mainmenu_items is None:
            return False

        end = time.time() + timeout
        last_count = None
        stable_steps = 0

        while time.time() < end:
            try:
                items = get_mainmenu_items(group="main") or []
                count = len(items)
            except Exception:
                items = []
                count = 0

            if count > 0:
                if last_count is None or count != last_count:
                    # количество пунктов изменилось — считаем, что что‑то ещё догружается
                    last_count = count
                    stable_steps = 1
                else:
                    # количество пунктов то же самое, увеличиваем счётчик стабильности
                    stable_steps += 1

                # считаем, что всё загрузилось, когда несколько циклов подряд
                # количество пунктов не меняется
                if stable_steps >= stable_checks:
                    return True

            await asyncio.sleep(interval)

        # Таймаут: если к этому моменту хоть что‑то успело зарегистрироваться —
        # всё равно возвращаем True, иначе False.
        try:
            items = get_mainmenu_items(group="main") or []
            return len(items) > 0
        except Exception:
            return False

    async def send_authorized_with_menu(message: types.Message):
        """
        Отправляет сообщение об успешной авторизации и клавиатуру главного меню.

        Важно: в EXE некоторые модули могут одновременно отправлять свои стартовые отчёты,
        из-за чего ReplyKeyboard может «съехать». Поэтому:
        - СНАЧАЛА быстро отправляем меню пользователю
        - А проверку «пустоты» меню делаем отдельной задачей, чтобы не тормозить цепочку
        """
        keyboard = None
        try:
            keyboard = get_main_keyboard()
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось сформировать клавиатуру главного меню: {e}")

        # Быстро подтверждаем авторизацию
        try:
            if keyboard is not None:
                await message.answer("Вы авторизовались.", reply_markup=keyboard)
            else:
                await message.answer("Вы авторизовались.")
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось отправить сообщение об авторизации: {e}")

        # Тихая проверка наполненности меню (не блокирует основной сценарий)
        async def _warn_if_menu_empty():
            try:
                has_menu = await wait_for_mainmenu_items(timeout=15.0, interval=0.5, stable_checks=3)
            except Exception:
                return
            if not has_menu:
                try:
                    warn = (
                        "Главное меню пока пустое: модули ещё не зарегистрировали свои кнопки. "
                        "Если через пару секунд ничего не появится, просто напиши /start."
                    )
                    kb = None
                    try:
                        kb = get_main_keyboard()
                    except Exception:
                        kb = None
                    if kb is not None:
                        await message.answer(warn, reply_markup=kb)
                    else:
                        await message.answer(warn)
                except Exception:
                    pass

        try:
            asyncio.create_task(_warn_if_menu_empty())
        except Exception:
            pass


    write_bot_log("Бот запускается...")

    if _activated_users_store_module is None:
        write_bot_log("[ПРЕДУПРЕЖДЕНИЕ] Модуль activated_users_store недоступен: сохранение активированных пользователей отключено.")
    else:
        try:
            db_path = _activated_users_store_module.ensure_storage(base_dir)
            write_bot_log(f"[USERS-DB] Хранилище активированных пользователей: {db_path}")
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось инициализировать БД активированных пользователей: {e}")

    # Загрузка учетных данных из credentials.ini
    global TOKEN, PIN_CODE, allowed_accounts
    TOKEN, PIN_CODE, allowed_ids_str = load_credentials()
    # Добавляем секцию telegram_api в config.ini, если отсутствует
    if not config.has_section("telegram_api"):
        config["telegram_api"] = {"address": "", "port": ""}
        _save_config()

    # Добавляем секцию api_server (флаг use_standard_api), если отсутствует.
    # GUI сохраняет этот флаг именно в секцию [api_server], поэтому бот читает оттуда.
    if not config.has_section(API_SECTION):
        config[API_SECTION] = {API_USE_STANDARD_KEY: "false"}
        _save_config()

    # Load debug status from config.ini
    global debug_enabled
    global connection_summary
    debug_enabled = config.getboolean(CONFIG_SECTION, 'debug', fallback=False)
    logsys.set_debug_enabled(debug_enabled)
    if debug_enabled:
        write_com_log("Дебаг включен из config.ini при запуске.")
    if allowed_ids_str:
        try:
            allowed_accounts = set(int(x.strip()) for x in allowed_ids_str.split(',') if x.strip().isdigit())
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Неверный формат разрешенных аккаунтов в credentials.ini: {e}")
            allowed_accounts = set()

    global current_bot, current_loop
    loop = asyncio.new_event_loop()
    current_loop = loop
    asyncio.set_event_loop(loop)

    # Проверяем настройку GUI: использование стандартного Telegram API сервера
    use_standard_api = config.getboolean(API_SECTION, API_USE_STANDARD_KEY, fallback=False)
    # К какому серверу реально подключились (показываем после авторизации)
    connection_summary = "стандартный Telegram API сервер"
    if use_standard_api:
        write_bot_log("Настройка GUI: использование стандартного Telegram API сервера")
        current_bot = Bot(token=TOKEN, loop=loop)
        try:
            bot_info = loop.run_until_complete(current_bot.get_me())
            write_bot_log(f"Бот подключён через стандартный сервер: {bot_info.first_name} (@{bot_info.username})")
            connection_summary = f"стандартный Telegram API сервер | {bot_info.first_name} (@{bot_info.username})"
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось подключиться к стандартному серверу: {e}")
    else:
        # Настройка подключения к локальному серверу Telegram API из config.ini
        api_server = None
        if config.has_section("telegram_api"):
            address = config["telegram_api"].get("address", "").strip()
            port = config["telegram_api"].get("port", "").strip()
            if address and port:
                api_server = f"http://{address}:{port}"
            elif address:
                api_server = address
        
        # Вывод информации о сервере подключения
        # Попытка подключения к локальному Telegram API серверу с реальными проверками в течение 1 минуты
        if api_server:
            write_bot_log(f"Подключение к локальному Telegram API серверу: {address}:{port}")
            from aiogram.bot.api import TelegramAPIServer
            server_connected = False
            current_bot = None
            max_attempts = 12  # попытки в течение 1 минуты каждые 5 секунд
            for attempt in range(1, max_attempts + 1):
                try:
                    api = TelegramAPIServer.from_base(api_server)
                    tmp_bot = Bot(token=TOKEN, loop=loop, server=api)
                    bot_info = loop.run_until_complete(tmp_bot.get_me())
                    write_bot_log("Успешно подключено к локальному Telegram API серверу")
                    write_bot_log(f"Бот подключён: {bot_info.first_name} (@{bot_info.username})")
                    connection_summary = f"локальный Telegram API сервер ({api_server}) | {bot_info.first_name} (@{bot_info.username})"
                    current_bot = tmp_bot
                    server_connected = True
                    break
                except Exception as e:
                    write_bot_log(f"[ОШИБКА] Попытка {attempt}/{max_attempts} не удалась: {e}")
                    if attempt < max_attempts:
                        time.sleep(5)
            if not server_connected:
                write_bot_log("Не удалось подключиться к локальному API серверу за 1 минуту, переключаюсь на стандартный сервер")
                current_bot = Bot(token=TOKEN, loop=loop)
                try:
                    bot_info = loop.run_until_complete(current_bot.get_me())
                    write_bot_log(f"Бот подключён через стандартный сервер: {bot_info.first_name} (@{bot_info.username})")
                    connection_summary = f"стандартный Telegram API сервер | {bot_info.first_name} (@{bot_info.username})"
                except Exception as e:
                    write_bot_log(f"[ОШИБКА] Не удалось получить информацию о боте: {e}")
        else:
            write_bot_log("Подключение к стандартному Telegram API серверу")
            current_bot = Bot(token=TOKEN, loop=loop)
            try:
                bot_info = loop.run_until_complete(current_bot.get_me())
                write_bot_log(f"Бот подключён: {bot_info.first_name} (@{bot_info.username})")
                connection_summary = f"стандартный Telegram API сервер | {bot_info.first_name} (@{bot_info.username})"
            except Exception as e:
                write_bot_log(f"[ОШИБКА] Не удалось получить информацию о боте: {e}")

    dp = Dispatcher(current_bot)
    setattr(current_bot, "dispatcher", dp)

    # === ВАЖНО: защита авторизации ===
    # В EXE некоторые модули могут регистрировать «широкие» хэндлеры и/или отменять обработку,
    # из-за чего PIN/старт иногда не доходит до этого скрипта. Поэтому хэндлер авторизации
    # регистрируем ДО загрузки модулей и дополнительно блокируем дальнейшую обработку.
    try:
        from aiogram.dispatcher.handler import CancelHandler
    except Exception:
        try:
            from aiogram.dispatcher import CancelHandler
        except Exception:
            CancelHandler = None

    _post_auth_in_progress = set()

    async def _post_auth_sequence(msg: types.Message):
        """После авторизации: отправить пост-лог и «прикрепить» главное меню последним сообщением."""
        uid = msg.from_user.id if msg.from_user else None
        if uid is not None and uid in _post_auth_in_progress:
            return
        if uid is not None:
            _post_auth_in_progress.add(uid)

        try:
            # 1) Пост-отчёт (лог/сводка/аудит)
            try:
                await send_post_auth_report(msg, debug_enabled=debug_enabled)
            except Exception as e:
                write_bot_log(f"[ОШИБКА] send_post_auth_report: {e}")

            # 2) Дать модульным отчётам «пролиться» и потом вернуть клавиатуру наверх
            try:
                await asyncio.sleep(1.0)
            except Exception:
                pass

            try:
                kb = get_main_keyboard()
                await msg.answer("Главное меню ✅", reply_markup=kb)
            except Exception as e:
                write_bot_log(f"[ОШИБКА] Не удалось отправить главное меню после авторизации: {e}")
                try:
                    await msg.answer("Главное меню ✅")
                except Exception:
                    pass
        finally:
            if uid is not None:
                _post_auth_in_progress.discard(uid)

    @dp.message_handler(lambda message: message.from_user.id not in authorized_users, content_types=types.ContentTypes.ANY)
    async def check_pin(message: types.Message):
        user_id = message.from_user.id

        # Если задан список разрешённых аккаунтов — режем всех остальных сразу
        if allowed_accounts and user_id not in allowed_accounts:
            write_bot_log(f"Попытка авторизации неразрешённого пользователя {user_id}.")
            add_auth_audit(f"Запрещённый пользователь {user_id} попытался авторизоваться.")
            try:
                await message.answer("Доступ запрещён: ваш ID не входит в список разрешённых.")
            finally:
                if CancelHandler:
                    raise CancelHandler()
            return

        # Если PIN включён — просим его, иначе авторизуем сразу
        if PIN_CODE:
            # Нечего проверять (не текст/пусто) или /start
            if not getattr(message, "text", None) or message.text.strip() in ["/start", "start"]:
                try:
                    await message.answer("Введите PIN-код:")
                finally:
                    if CancelHandler:
                        raise CancelHandler()
                return

            entered_pin = message.text.strip()
            if entered_pin != PIN_CODE:
                write_bot_log(f"Неудачная попытка авторизации пользователя {user_id} с неправильным PIN.")
                add_auth_audit(f"Пользователь {user_id} ввёл неверный PIN.")
                try:
                    await message.answer("Неверный PIN-код. Попробуйте ещё раз.")
                finally:
                    if CancelHandler:
                        raise CancelHandler()
                return

        # Успешная авторизация
        authorized_users.add(user_id)
        try:
            if _activated_users_store_module is not None:
                _activated_users_store_module.save_activated_user(
                    base_dir=base_dir,
                    user=message.from_user,
                    chat_id=getattr(getattr(message, "chat", None), "id", None),
                    source=("pin_auth" if PIN_CODE else "auth_without_pin"),
                )
            else:
                write_bot_log(
                    f"[ПРЕДУПРЕЖДЕНИЕ] Пользователь {user_id} авторизован, но модуль activated_users_store не загружен."
                )
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось сохранить активированного пользователя {user_id} в БД: {e}")

        # 1) Сразу выдаём меню (чтобы юзер видел клавиатуру, даже если дальше пойдёт «флуд» от модулей)
        await send_authorized_with_menu(message)

        # 2) Пост-отчёт и финальный «пин» клавиатуры делаем задачей, чтобы ничего не зависало
        try:
            asyncio.create_task(_post_auth_sequence(message))
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Не удалось запланировать пост-отчёт после авторизации: {e}")
            try:
                await _post_auth_sequence(message)
            except Exception:
                pass

        # Не даём другим хэндлерам (включая модульные) обработать PIN-сообщение
        if CancelHandler:
            raise CancelHandler()

    # Регистрируем webpanel раньше общего менеджера модулей
    _register_startrun_webpanel_handlers(dp)

    import Moduls_manager_ext
    Moduls_manager_ext.register_handlers(dp)


    # --- ЭКСТРЕННАЯ КОМАНДА ---
    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "hrp" and message.from_user.id in authorized_users)
    async def emergency_exit(message: types.Message):
        user_id = message.from_user.id
        note_mode[user_id] = False
        pending_note.pop(user_id, None)
        file_mode[user_id] = False
        cmd_mode[user_id] = False
        in_cmd_menu[user_id] = False
        power_mode[user_id] = False
        pending_power_action.pop(user_id, None)
        infiles_mode[user_id] = False
        plugins_mode[user_id] = False
        keyboard = get_main_keyboard()
        await message.answer("Экстренное завершение текущего режима. Возвращаюсь в главное меню.", reply_markup=keyboard)

    # ------------------------- Авторизация и старт -------------------------
    @dp.message_handler(commands=['start'])
    async def start_command(message: types.Message):
        keyboard = get_main_keyboard()
        await message.answer("Выберите действие:", reply_markup=keyboard)
        write_bot_log(f"Пользователь {message.from_user.id} выдал команду /start.")

    # ------------------------- Дополнительно -------------------------
    @dp.message_handler(lambda message: message.text == "Дополнительно")
    async def additional_menu(message: types.Message):
        power_mode[message.from_user.id] = False
        plugins_mode[message.from_user.id] = False
        keyboard = get_additional_keyboard()
        await message.answer("Выберите действие:", reply_markup=keyboard)
        write_bot_log(f"Пользователь {message.from_user.id} открыл меню «Дополнительно».")

    @dp.message_handler(lambda message: message.text == "Назад" and not power_mode.get(message.from_user.id, False))
    async def back_from_additional(message: types.Message):
        keyboard = get_main_keyboard()
        await message.answer("Возвращаюсь в главное меню.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Назад в меню" and not cmd_mode.get(message.from_user.id, False))
    async def go_back_to_main(message: types.Message):
        note_mode[message.from_user.id] = False
        file_mode[message.from_user.id] = False
        power_mode[message.from_user.id] = False
        plugins_mode[message.from_user.id] = False
        keyboard = get_main_keyboard()
        await message.answer("Возвращаюсь в главное меню.", reply_markup=keyboard)

    
    # ------------------------- Логи -------------------------
    # Вынесено в отдельный модуль: startrunmodul_logmenu.py
    # ----------------------- Меню плагинов -----------------------
    @dp.message_handler(lambda m: m.text == "Плагины")
    async def plugins_menu_handler(message: types.Message):
        user_id = message.from_user.id
        plugins_mode[user_id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Список плагинов", "Перезагрузить плагины", "настроить автозапуск", "Назад")
        await message.answer("Менеджер плагинов:", reply_markup=keyboard)
        write_bot_log(f"Пользователь {user_id} открыл менеджер плагинов.")

    @dp.message_handler(lambda m: m.text == "Список плагинов")
    async def list_plugins_handler(message: types.Message):
        plugins_mode[message.from_user.id] = True
        available = scan_available_plugins()
        if not available:
            await message.answer("Нет доступных плагинов.")
        else:
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            for plugin_key, info in available.items():
                display_name = info["meta"].get("name", plugin_key)
                kb.add(KeyboardButton(display_name))
            kb.add(KeyboardButton("Плагины"), KeyboardButton("Назад в меню"))
            await message.answer("Выберите плагин для установки/запуска:", reply_markup=kb)

    @dp.message_handler(lambda m: m.text == "Перезагрузить плагины")
    async def refresh_plugins_handler(message: types.Message):
        dp_inner = getattr(message.bot, "dispatcher", None)
        if dp_inner is None:
            await message.answer("Не удалось получить диспетчер. Попробуйте позже.")
            return
        plugins_mode[message.from_user.id] = True
        await message.answer("Перезагружаю плагины, подождите...")
        installed, uninstalled = reload_all_plugins(dp_inner, notify_chat_id=message.chat.id)
        await message.answer("Плагины перезагружены!")
        if installed:
            await message.answer("Установлены новые зависимости: " + ", ".join(installed))
        if uninstalled:
            await message.answer("При перезагрузке были удалены зависимости: " + ", ".join(uninstalled))
        available = scan_available_plugins()
        if available:
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            for plugin_key, info in available.items():
                display_name = info["meta"].get("name", plugin_key)
                kb.add(KeyboardButton(display_name))
            kb.add(KeyboardButton("Назад"))
            await message.answer("Выберите плагин для установки/запуска:", reply_markup=kb)
        else:
            from aiogram.types import ReplyKeyboardMarkup
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add("Список плагинов", "Перезагрузить плагины", "Назад")
            await message.answer("Нет доступных плагинов.", reply_markup=kb)
        write_bot_log(f"Пользователь {message.from_user.id} перезагрузил плагины.")

    @dp.message_handler(lambda m: m.text == "настроить автозапуск")
    async def configure_autostart_handler(message: types.Message):
        user_id = message.from_user.id
        autostart_mode[user_id] = True
        write_bot_log(f"Пользователь {user_id} открыл режим настройки автозапуска плагинов.")
        available = scan_available_plugins()
        autostart = load_autostart_config()
        if not available:
            await message.answer("Нет доступных плагинов для настройки автозапуска.")
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for plugin_key, info in available.items():
            display_name = info["meta"].get("name", plugin_key)
            status = "Вкл" if plugin_key in autostart else "Выкл"
            kb.add(f"{display_name} [{status}]")
        kb.add("Назад")
        await message.answer("Настройка автозапуска плагинов. Нажмите на плагин для переключения его статуса.", reply_markup=kb)

    @dp.message_handler(lambda m: autostart_mode.get(m.from_user.id, False) and " [" in m.text and m.text != "Назад")
    async def toggle_autostart_plugin_handler(message: types.Message):
        user_id = message.from_user.id
        text = message.text
        plugin_display = text.split(" [")[0].strip().lower()
        available = scan_available_plugins()
        matched_plugin = None
        for plugin_key, info in available.items():
            display_name = info["meta"].get("name", plugin_key).strip().lower()
            if display_name == plugin_display:
                matched_plugin = plugin_key
                break
        if not matched_plugin:
            await message.answer("Плагин не найден.")
            return
        autostart = load_autostart_config()
        if matched_plugin in autostart:
            autostart.remove(matched_plugin)
            new_status = "Выкл"
        else:
            autostart.append(matched_plugin)
            new_status = "Вкл"
        save_autostart_config(autostart)
        write_bot_log(f"Пользователь {user_id} переключил автозапуск для плагина {matched_plugin} на {new_status}.")
        await message.answer(f"Плагин {matched_plugin} автозапуск переключен на {new_status}.")
        await configure_autostart_handler(message)

    @dp.message_handler(lambda m: autostart_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def autostart_back_handler(message: types.Message):
        user_id = message.from_user.id
        autostart_mode[user_id] = False
        write_bot_log(f"Пользователь {user_id} вышел из режима настройки автозапуска плагинов.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Список плагинов", "Перезагрузить плагины", "настроить автозапуск", "Назад")
        await message.answer("Менеджер плагинов:", reply_markup=keyboard)

    def is_plugin_message(message: types.Message) -> bool:
        if not message.text:
            return False
        available = scan_available_plugins()
        for plugin_key, info in available.items():
            display_name = info["meta"].get("name", plugin_key).strip().lower()
            if message.text.strip().lower() == display_name:
                return True
        return False

    @dp.message_handler(lambda message: is_plugin_message(message))
    async def run_plugin_if_possible(message: types.Message):
        user_text = message.text.strip().lower()
        available = scan_available_plugins()
        matched = None
        for plugin_key, info in available.items():
            display_name = info["meta"].get("name", plugin_key).strip().lower()
            if user_text == display_name:
                matched = (plugin_key, info)
                break
        if not matched:
            return
        plugin_key, info = matched
        dp_inner = message.bot.dispatcher
        if plugin_key not in loaded_plugins:
            folder_path = info["folder"]
            plugin_name = plugin_key
            write_bot_log(f"Начинается установка плагина: {plugin_name}")
            notify(dp_inner, message.chat.id, f"Начинается установка плагина: {plugin_name}")
            await asyncio.to_thread(create_plugin_venv, folder_path, dp_inner, message.chat.id)
            pip_exe, python_exe, site_packages = get_plugin_venv_paths(folder_path)
            meta = info["meta"]
            deps = meta.get("dependencies", [])
            for d in deps:
                write_bot_log(f"Устанавливаю зависимость {d} для плагина {plugin_name}")
                notify(dp_inner, message.chat.id, f"Устанавливаю зависимость {d} для плагина {plugin_name}")
                await asyncio.to_thread(install_dependency_for_plugin, d, pip_exe, plugin_name, dp_inner, message.chat.id)
            modules_in_plugin = []
            py_files_found = False
            if site_packages:
                add_site_packages(site_packages)
            for filename in os.listdir(folder_path):
                if filename.endswith(".py"):
                    py_files_found = True
                    file_path = os.path.join(folder_path, filename)
                    spec = importlib.util.spec_from_file_location(plugin_name + "_" + filename, file_path)
                    module = importlib.util.module_from_spec(spec)
                    try:
                        spec.loader.exec_module(module)
                        modules_in_plugin.append(module)
                        write_bot_log(f"Импортирован модуль {filename} в плагине {plugin_name}.")
                        notify(dp_inner, message.chat.id, f"Импортирован модуль {filename} в плагине {plugin_name}.")
                    except Exception as e:
                        traceback.print_exc()
                        write_bot_log(f"[ОШИБКА] При импортировании {filename} в плагине {plugin_name}: {e}")
                        notify(dp_inner, message.chat.id, f"[ОШИБКА] При импортировании {filename} в плагине {plugin_name}: {e}")
            if not py_files_found:
                write_bot_log(f"[ПРЕДУПРЕЖДЕНИЕ] В папке {plugin_name} не найдено ни одного .py-файла.")
                notify(dp_inner, message.chat.id, f"[ПРЕДУПРЕЖДЕНИЕ] В папке {plugin_name} не найдено ни одного .py-файла.")
            for mod in modules_in_plugin:
                if hasattr(mod, "init_plugin"):
                    try:
                        if site_packages:
                            add_site_packages(site_packages)
                        await asyncio.to_thread(mod.init_plugin, dp_inner)
                        write_bot_log(f"Инициализирован init_plugin у модуля {mod.__name__} плагина {plugin_name}.")
                        notify(dp_inner, message.chat.id, f"Инициализирован init_plugin у модуля {mod.__name__} плагина {plugin_name}.")
                    except Exception as e:
                        traceback.print_exc()
                        write_bot_log(f"[ОШИБКА] init_plugin у модуля {mod.__name__} в плагине {plugin_name}: {e}")
                        notify(dp_inner, message.chat.id, f"[ОШИБКА] init_plugin у модуля {mod.__name__} в плагине {plugin_name}: {e}")
            loaded_plugins[plugin_key] = {
                "modules": modules_in_plugin,
                "meta": meta,
                "venv_site": site_packages
            }
            if modules_in_plugin:
                write_bot_log(f"Плагин {plugin_name} установлен и загружен успешно.")
                notify(dp_inner, message.chat.id, f"Плагин {plugin_name} установлен и загружен успешно.")
            else:
                write_bot_log(f"Плагин {plugin_name} не содержит модулей для загрузки.")
                notify(dp_inner, message.chat.id, f"Плагин {plugin_name} не содержит модулей для загрузки.")
        info_loaded = loaded_plugins.get(plugin_key, {})
        found_run = False
        for mod in info_loaded.get("modules", []):
            if hasattr(mod, "run_plugin"):
                found_run = True
                try:
                    site_packages = info_loaded.get("venv_site")
                    if site_packages:
                        add_site_packages(site_packages)
                    if asyncio.iscoroutinefunction(mod.run_plugin):
                        asyncio.create_task(mod.run_plugin(message))
                    else:
                        loop = asyncio.get_running_loop()
                        loop.run_in_executor(None, mod.run_plugin, message)
                except Exception as e:
                    await message.answer(f"[ОШИБКА] Ошибка при запуске плагина «{info_loaded['meta'].get('name', plugin_key)}»: {e}")
        if found_run:
            write_bot_log(f"Плагин {info_loaded['meta'].get('name', plugin_key)} успешно запущен через run_plugin.")
            await message.answer(f"Плагин «{info_loaded['meta'].get('name', plugin_key)}» запущен.")
        else:
            write_bot_log(f"У плагина {info_loaded.get('meta', {}).get('name', plugin_key)} отсутствует функция run_plugin.")
            await message.answer(f"У плагина «{info['meta'].get('name', plugin_key)}» нет функции run_plugin.")

    # Запускаем автозапуск плагинов
    asyncio.get_event_loop().create_task(auto_start_plugins(dp))

    async def on_startup(dispatcher: Dispatcher):
        """
        Стартовая логика вынесена в sys_core/startup_telegram.py:
        - бот первым отправляет короткий статус запуска;
        - при отсутствии PIN выполняется автоавторизация и показ главного меню;
        - при наличии PIN запрашивается PIN-код.
        """
        try:
            from sys_core.startup_telegram import run_startup_sequence
        except Exception as e:
            write_bot_log(f"[ПРЕДУПРЕЖДЕНИЕ] Не удалось подключить startup_telegram: {e}")
            return

        try:
            class _StartupReportMessageProxy:
                def __init__(self, tg_bot, chat_id):
                    self._tg_bot = tg_bot
                    self._chat_id = chat_id

                async def answer(self, text, **kwargs):
                    await self._tg_bot.send_message(self._chat_id, text, **kwargs)

            async def _send_startup_post_auth_report(chat_id: int):
                proxy = _StartupReportMessageProxy(dispatcher.bot, chat_id)
                await send_post_auth_report(proxy, debug_enabled=debug_enabled)

            stats = await run_startup_sequence(
                bot=dispatcher.bot,
                base_dir=base_dir,
                pin_code=PIN_CODE,
                allowed_accounts=allowed_accounts,
                authorized_users=authorized_users,
                activated_users_store_module=_activated_users_store_module,
                get_main_keyboard=get_main_keyboard,
                post_auth_report_sender=_send_startup_post_auth_report,
                write_log=write_bot_log,
            )
            write_bot_log(
                "[STARTUP] Выполнено стартовое оповещение: "
                f"targets={stats.get('targets', 0)}, "
                f"status_sent={stats.get('status_sent', 0)}, "
                f"pin_prompted={stats.get('pin_prompted', 0)}, "
                f"auto_authorized={stats.get('auto_authorized', 0)}, "
                f"post_auth_reported={stats.get('post_auth_reported', 0)}."
            )
        except Exception as e:
            write_bot_log(f"[ОШИБКА] Стартовая последовательность завершилась с ошибкой: {e}")

    # Добавляем on_shutdown для корректного завершения работы бота
    async def on_shutdown(dispatcher: Dispatcher):
        write_bot_log("Выполняется shutdown бота.")
        await dispatcher.bot.close()

    # Собственно блокирующий запуск поллинга (в отдельном потоке)
    try:
        executor.start_polling(
            dp,
            skip_updates=True,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
        )
    except exceptions.TerminatedByOtherGetUpdates:
        write_bot_log("TerminatedByOtherGetUpdates: бот остановлен принудительно.")
    except Exception as e:
        write_bot_log(f"[ОШИБКА] Необработанное исключение в run_bot: {e}")
    finally:
        write_bot_log("Polling завершен, бот остановлен.")
        current_bot = None
        current_loop = None

# -----------------------------------------------------
import os
import configparser
import tempfile
import shutil

# -----------------------------------------------------
# 9. Профи-обработка конфигурации (токен, PIN, ID)
# -----------------------------------------------------
CONFIG_FILE = (_CONFIG_PATH_OVERRIDE or os.path.join(base_dir, "config.ini"))
CONFIG_SECTION = 'credentials'

config = configparser.ConfigParser()

def load_credentials():
    """
    Читает config.ini, возвращает (token, pin, allowed_ids_str).
    Если файла/секции нет — создаёт с дефолтами.
    """
    config.read(CONFIG_FILE, encoding='utf-8')
    if CONFIG_SECTION not in config:
        config[CONFIG_SECTION] = {
            'token': '',
            'pin': '',
            'allowed_ids': ''
        }
        _save_config()
        write_bot_log(f"Секция [{CONFIG_SECTION}] не найдена — создана с дефолтами")
    sec = config[CONFIG_SECTION]
    token = sec.get('token', fallback='')
    pin = sec.get('pin', fallback='')
    ids_str = sec.get('allowed_ids', fallback='')
    write_bot_log("Конфиг credentials загружен")
    return token, pin, ids_str

def save_credentials(token: str, pin: str, allowed_ids):
    """
    Обновляет в памяти и сохраняет config.ini атомарно.
    allowed_ids может быть множеством/списком или строкой.
    """
    if CONFIG_SECTION not in config:
        config[CONFIG_SECTION] = {}
    sec = config[CONFIG_SECTION]
    sec['token'] = token
    sec['pin'] = pin
    # если передали set/list, склеиваем, иначе сохраняем как есть
    if isinstance(allowed_ids, (set, list)):
        sec['allowed_ids'] = ','.join(str(i) for i in sorted(allowed_ids))
    else:
        sec['allowed_ids'] = str(allowed_ids)
    _save_config()
    write_bot_log("Конфиг credentials сохранён")

def _save_config():
    """
    Атомарно сохраняет config.ini: сначала во временный файл, затем заменяет оригинал.
    """
    dirpath = os.path.dirname(CONFIG_FILE)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix='config_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmpf:
            config.write(tmpf)
        shutil.move(tmp_path, CONFIG_FILE)
    except Exception as e:
        write_bot_log(f"[ОШИБКА] Не удалось сохранить конфиг: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# -----------------------------------------------------
# 10. Импорт графического интерфейса из файла gui.py
# -----------------------------------------------------
from gui import MainWindow
if __name__ == "__main__":
    import subprocess

    # Настройки heartbeat для контроля зависаний GUI/бота
    HEARTBEAT_FILE = os.path.join(base_dir, "log", "heartbeat_main.txt")
    HEARTBEAT_INTERVAL_SEC = 5          # как часто дочерний процесс пишет "я жив"
    HANG_TIMEOUT_SEC = 60               # сколько секунд без heartbeat считаем зависанием
    STARTUP_GRACE_SEC = 60              # на запуск GUI/бота даём минуту
    HEARTBEAT_CHECK_INTERVAL_SEC = 5    # как часто вотчер проверяет heartbeat

    # Если запущено с --child, то стартуем GUI/бота
    if "--child" in sys.argv:
        try:
            # Функция для обновления heartbeat-файла
            def _update_heartbeat():
                try:
                    os.makedirs(os.path.join(base_dir, "log"), exist_ok=True)
                    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as hb:
                        ts = time.strftime("%Y-%m-%d %H:%M:%S")
                        hb.write(ts)
                except Exception:
                    # Ошибку heartbeat не считаем фатальной
                    pass

            write_bot_log("Запуск приложения. Инициализация GUI.")
            _update_heartbeat()

            app = QApplication(sys.argv)

            # Таймер, который регулярно помечает, что GUI жив.
            # Если главный поток зависнет, таймер перестанет тикать.
            heartbeat_timer = QTimer()
            heartbeat_timer.setInterval(HEARTBEAT_INTERVAL_SEC * 1000)
            heartbeat_timer.timeout.connect(_update_heartbeat)
            heartbeat_timer.start()

            window = MainWindow()
            window.show()
            write_bot_log("GUI инициализирован.")
            ret = app.exec_()
            write_bot_log(f"GUI закрыт с кодом: {ret}")

            # При нормальном выходе уберём heartbeat, чтобы вотчер
            # не считал старый файл актуальным.
            try:
                if os.path.exists(HEARTBEAT_FILE):
                    os.remove(HEARTBEAT_FILE)
            except Exception:
                pass

            sys.exit(ret)
        except Exception:
            write_bot_log(f"[ОШИБКА] Критическая ошибка в GUI:\n{traceback.format_exc()}")
            # На всякий случай сбросим heartbeat
            try:
                if os.path.exists(HEARTBEAT_FILE):
                    os.remove(HEARTBEAT_FILE)
            except Exception:
                pass
            sys.exit(1)
    else:
        # Режим watchdog по умолчанию
        log_path = os.path.join(base_dir, "log", "watchdog.log")
        # Перечитываем config.ini, чтобы узнать актуальный флаг debug
        try:
            config.read(CONFIG_FILE, encoding='utf-8')
            debug_enabled = config.getboolean(CONFIG_SECTION, 'debug', fallback=False)
        except Exception:
            debug_enabled = False
        # Применяем флаг в системе логирования
        logsys.set_debug_enabled(debug_enabled)
        def log(msg: str):
            # Логи вотчера пишем только при включённом debug, чтобы не засорять диск
            if not debug_enabled:
                return
            max_size_mb = 5
            try:
                if os.path.exists(log_path) and os.path.getsize(log_path) > max_size_mb * 1024**2:
                    os.replace(log_path, log_path + ".old")
            except Exception:
                pass
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts} {msg}\n")
            except Exception:
                # последнюю линию лучше проглотить, чтобы вотчер не упал из-за проблем с диском
                pass

        def _kill_telegram_api_processes():
            """
            Пытаемся аккуратно остановить локальный Telegram API сервер перед перезапуском бота.
            Это максимально приближено к поведению кнопки "Полный перезапуск".
            """
            try:
                for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
                    name = (p.info.get("name") or "").lower()
                    exe = (p.info.get("exe") or "").lower()
                    cmdline = " ".join(p.info.get("cmdline") or []).lower()
                    if "telegram-bot-api" in name or "telegram-bot-api" in exe or "telegram-bot-api" in cmdline:
                        try:
                            log(f"[watchdog] Найдён процесс Telegram API pid={p.pid}, пробуем завершить...")
                            p.terminate()
                            try:
                                p.wait(timeout=10)
                                log(f"[watchdog] Процесс Telegram API pid={p.pid} завершён terminate().")
                            except psutil.TimeoutExpired:
                                log(f"[watchdog] Telegram API pid={p.pid} не завершился, посылаем kill().")
                                p.kill()
                        except Exception as e:
                            log(f"[watchdog] Ошибка при завершении Telegram API pid={p.pid}: {e}")
            except Exception as e:
                log(f"[watchdog] Ошибка при поиске процессов Telegram API: {e}")

        def _kill_lhm_processes():
            """
            Ensure LibreHardwareMonitor is not left running when the child app
            process is dead/restarting. We only target the app-managed binary.
            """
            try:
                lhm_dir = os.path.normcase(
                    os.path.abspath(os.path.join(base_dir, "data", "LibreHardwareMonitor.NET.10"))
                )
                target_exe = os.path.normcase(os.path.abspath(os.path.join(lhm_dir, "LibreHardwareMonitor.exe")))
            except Exception:
                lhm_dir = ""
                target_exe = ""

            stopped = 0
            try:
                for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
                    try:
                        name = (p.info.get("name") or "").strip().lower()
                        if name != "librehardwaremonitor.exe":
                            continue

                        exe = p.info.get("exe")
                        cmdline = " ".join(p.info.get("cmdline") or [])

                        match_target = False
                        if exe:
                            try:
                                exe_norm = os.path.normcase(os.path.abspath(exe))
                                if target_exe and exe_norm == target_exe:
                                    match_target = True
                            except Exception:
                                pass
                        if not match_target and cmdline:
                            try:
                                cmdline_norm = os.path.normcase(cmdline)
                                if (target_exe and target_exe in cmdline_norm) or (lhm_dir and lhm_dir in cmdline_norm):
                                    match_target = True
                            except Exception:
                                pass

                        # Fallback: if we cannot read process path/cmdline reliably,
                        # still stop by process name to avoid orphan LHM instances
                        # after app crash/restart.
                        if not match_target and not exe and not cmdline:
                            match_target = True

                        if not match_target:
                            continue

                        log(f"[watchdog] Found LibreHardwareMonitor pid={p.pid}, stopping...")
                        try:
                            p.terminate()
                            p.wait(timeout=6)
                        except psutil.TimeoutExpired:
                            p.kill()
                            p.wait(timeout=6)
                        stopped += 1
                    except (psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue
                    except Exception as e:
                        log(f"[watchdog] Failed to stop LibreHardwareMonitor pid={getattr(p, 'pid', '?')}: {e}")
            except Exception as e:
                log(f"[watchdog] Error while searching LibreHardwareMonitor processes: {e}")

            if stopped:
                log(f"[watchdog] LibreHardwareMonitor processes stopped: {stopped}")

        def spawn_child_passthrough():
            """
            Запуск дочернего процесса с прокидыванием исходных аргументов (например, --tray, --config),
            исключая служебные флаги вотчера (--child, --api-watchdog <pid1> <pid2>).
            """
            exe_path = sys.executable

            # Собираем пользовательские аргументы, пришедшие родителю
            passthrough = []
            i = 1
            while i < len(sys.argv):
                a = sys.argv[i]
                low = str(a).lower()
                if low in ("--child", "/child"):
                    i += 1
                    continue
                if low == "--api-watchdog":
                    # пропустить сам флаг и два PID-а
                    i += 3
                    continue
                passthrough.append(a)
                i += 1

            if is_frozen():
                # onefile/EXE
                cmd = [exe_path] + passthrough + ["--child"]
            else:
                # запуск из .py
                script = os.path.abspath(sys.argv[0])
                cmd = [exe_path, script] + passthrough + ["--child"]

            log(f"▶️ [watchdog] Запускаем бота (passthrough): {cmd}")
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='ignore',
                cwd=base_dir
            )

        def _pump_child_output(proc):
            """Читает stdout дочернего процесса и пишет его в лог вотчера (если включён debug)."""
            try:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    try:
                        line = line.rstrip()
                    except Exception:
                        pass
                    if line:
                        log(f"[BOT] {line}")
            except Exception as e:
                log(f"[watchdog] Ошибка чтения stdout дочернего процесса: {e}")

        restart_count = 0
        MAX_RESTARTS = 5

        while True:
            log("▶️ [watchdog] Старт нового дочернего процесса.")
            proc = spawn_child_passthrough()

            # Поток для чтения stdout дочернего процесса
            reader_thread = threading.Thread(target=_pump_child_output, args=(proc,), daemon=True)
            reader_thread.start()

            start_time = time.time()
            last_heartbeat_ok = start_time

            code = None

            while True:
                # Проверяем, не завершился ли уже процесс
                code = proc.poll()
                if code is not None:
                    break

                now = time.time()

                # Проверка heartbeat
                try:
                    if os.path.exists(HEARTBEAT_FILE):
                        hb_mtime = os.path.getmtime(HEARTBEAT_FILE)
                        if hb_mtime > last_heartbeat_ok:
                            last_heartbeat_ok = hb_mtime
                except Exception as e:
                    log(f"[watchdog] Ошибка доступа к heartbeat-файлу: {e}")

                no_heartbeat_time = now - last_heartbeat_ok
                alive_time = now - start_time

                # Не мучаем процесс в течение фазы запуска
                if alive_time > STARTUP_GRACE_SEC and no_heartbeat_time > HANG_TIMEOUT_SEC:
                    log(f"⚠️ [watchdog] Дочерний процесс не подаёт признаков жизни {int(no_heartbeat_time)} сек. Считаем, что GUI завис.")
                    # Пытаемся сначала остановить локальный Telegram API сервер
                    _kill_telegram_api_processes()
                    _kill_lhm_processes()
                    # Затем убиваем зависший процесс бота
                    try:
                        proc.kill()
                        log("[watchdog] Зависший дочерний процесс убит через kill().")
                    except Exception as e:
                        log(f"[watchdog] Ошибка при kill зависшего процесса: {e}")
                    try:
                        code = proc.wait(timeout=10)
                    except Exception:
                        code = -1
                    break

                time.sleep(HEARTBEAT_CHECK_INTERVAL_SEC)

            # Дожидаемся завершения потока чтения логов (но не бесконечно)
            try:
                reader_thread.join(timeout=5)
            except Exception:
                pass

            if code is None:
                try:
                    code = proc.wait(timeout=1)
                except Exception:
                    code = -1

            log(f"⚠️ [watchdog] Процесс бота завершился с кодом {code}")

            if code == 0:
                log("🛑 [watchdog] Код 0 — считаем, что пользователь закрыл приложение. Завершаемся.")
                _kill_lhm_processes()
                sys.exit(0)
            elif code == 42:
                log("♻️ [watchdog] Получен код 42 — полный рестарт. Останавливаем локальный API (если есть) и сразу запускаем новый процесс.")
                _kill_telegram_api_processes()
                _kill_lhm_processes()
                # сразу продолжаем цикл без увеличения счётчика рестартов
                continue
            else:
                restart_count += 1
                log(f"♻️ [watchdog] Bot crashed/hanged (code={code}). Restart #{restart_count}/{MAX_RESTARTS} через 3 сек...")
                _kill_telegram_api_processes()
                _kill_lhm_processes()
                if restart_count >= MAX_RESTARTS:
                    log(f"♻️ [watchdog] Достигнут лимит рестартов ({MAX_RESTARTS}). Останавливаемся.")
                    sys.exit(code if isinstance(code, int) else 1)
                time.sleep(3)
