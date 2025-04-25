import os

import sys
import time
import threading
import asyncio
import subprocess
import socket
import platform
from datetime import datetime

import psutil
import speedtest
import pyautogui
import logging  # новый импорт для стандартного логирования

# Добавляем импорт для обработки исключения остановки
from aiogram.utils import exceptions

# Глобальная переменная для накопления лог-сообщений
pending_log_messages = []  # все сообщения логов будут сохраняться сюда

# -----------------------------------------------------
# 1. Функции определения пути приложения
# -----------------------------------------------------
def is_frozen():
    return getattr(sys, 'frozen', False)

def get_app_dir():

    if "NUITKA_ONEFILE_PARENT" in os.environ:
        return os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
    elif is_frozen():
        # При скомпилированном варианте возвращаем директорию исполняемого файла,
        # а не текущий рабочий каталог, чтобы внешние папки (например, plugins) корректно определялись.
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

folders = ["лог", "notes", "files", "screenshots", "infiles", "plugins"]
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
def ensure_base_python():
    """
    При старте бота проверяет наличие папки 'python' в рабочей директории.
    Если папка отсутствует, распаковывает архив Python.zip в папку 'python'.
    """
    python_folder = os.path.join(base_dir, "python")
    if not os.path.isdir(python_folder):
        python_zip_path = os.path.join(get_app_dir(), "Python.zip")
        if not os.path.exists(python_zip_path):
            print("Python.zip не найден в каталоге приложения")
            write_bot_log("Python.zip не найден в каталоге приложения")
            return
        try:
            import zipfile
            with zipfile.ZipFile(python_zip_path, 'r') as zip_ref:
                zip_ref.extractall(python_folder)
            print("Базовый Python распакован в папку 'python'")
            write_bot_log("Базовый Python распакован в папку 'python'")
        except Exception as e:
            print(f"[ОШИБКА] Не удалось распаковать Python.zip: {e}")
            write_bot_log(f"[ОШИБКА] Не удалось распаковать Python.zip: {e}")
    else:
        # Если папка уже существует, распаковка не требуется
        write_bot_log("Папка 'python' уже существует, распаковка не требуется")

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
from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtGui import QIcon

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
cmd_mode = {}
in_cmd_menu = {}
power_mode = {}
pending_power_action = {}
infiles_mode = {}
plugins_mode = {}
autostart_mode = {}  # Для настройки автозапуска плагинов

MAX_FILE_SIZE = 50 * 1024 * 1024

current_time_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
bot_log_file = os.path.join(base_dir, "лог", f"log_{current_time_str}_бот.txt")
com_log_file = os.path.join(base_dir, "лог", f"log_{current_time_str}_ком.txt")
plugin_log_file = os.path.join(base_dir, "лог", f"log_{current_time_str}_плагинов.txt")
error_log_file = os.path.join(base_dir, "лог", f"log_{current_time_str}_ошибок.txt")

# -----------------------------------------------------
# 6. Механизм передачи логов в GUI (сигналы)
# -----------------------------------------------------
class LogEmitter(QObject):
    log_message = pyqtSignal(str)

log_emitter = LogEmitter()

class SignalHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_emitter.log_message.emit(msg)
            # Only accumulate non-debug messages
            if record.levelno >= logging.INFO:
                pending_log_messages.append(msg)
        except Exception:
            self.handleError(record)

formatter = logging.Formatter('[%(name)s] %(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

def create_logger(logger_name, log_file, level=logging.INFO):

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.handlers.clear()
    # Файловый хэндлер
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    # Хэндлер для отправки по сигналу
    signal_handler = SignalHandler()
    signal_handler.setFormatter(formatter)
    logger.addHandler(signal_handler)
    return logger
# -----------------------------------------------------
# Debug feature (added)
# -----------------------------------------------------
bot_logger = create_logger("БОТ", bot_log_file)
com_logger = create_logger("КОМ", com_log_file)
plugin_logger = create_logger("ПЛАГИН", plugin_log_file)
error_logger = create_logger("ОШИБКА", error_log_file, level=logging.ERROR)

def get_error_description(error_msg: str) -> str:
    if "Не удалось установить" in error_msg:
        return "Ошибка установки зависимости может возникнуть из-за проблем с сетью или неправильного имени пакета."
    elif "Не удалось обновить pip" in error_msg:
        return "Обновление pip может не удаваться из-за отсутствия прав или проблем с интернет-соединением."
    elif "Не удалось прочитать" in error_msg:
        return "Ошибка чтения файла может быть вызвана повреждением файла или отсутствием доступа."
    elif "При импортировании" in error_msg:
        return "Ошибка импорта модуля может быть вызвана синтаксическими ошибками или отсутствием зависимостей."
    elif "init_plugin" in error_msg:
        return "Ошибка инициализации плагина может быть связана с некорректной реализацией функции."
    elif "Не удалось выполнить pip freeze" in error_msg:
        return "Ошибка выполнения pip freeze может возникнуть из-за проблем с окружением."
    elif "Не удалось удалить папку плагина" in error_msg:
        return "Ошибка удаления папки может возникнуть из-за отсутствия прав доступа или блокировки файлов."
    else:
        return "Описание ошибки не определено. Проверьте входные данные и системные настройки."

def write_error_log(entry: str):

    description = get_error_description(entry)
    error_logger.error(f"{entry} | {description}")

def write_bot_log(entry: str):

    bot_logger.info(entry)
    if "[ОШИБКА]" in entry:
        write_error_log(entry)

def write_com_log(entry: str):

    com_logger.info(entry)
    if "[ОШИБКА]" in entry:
        write_error_log(entry)

def write_plugin_log(entry: str):

    plugin_logger.info(entry)
    if "[ОШИБКА]" in entry:
        write_error_log(entry)

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
        freeze_proc = subprocess.run([pip_exe, "freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if dep.lower() in freeze_proc.stdout.lower():
            write_bot_log(f"Зависимость {dep} уже установлена для плагина {plugin_name}.")
            if notify_chat_id:
                notify(dp, notify_chat_id, f"Зависимость {dep} уже установлена для плагина {plugin_name}.")
            return
        process = subprocess.Popen([pip_exe, "install", "--upgrade", dep],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True)
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


# -----------------------------------------------------
# 7. Функция запуска бота (в отдельном потоке)
# -----------------------------------------------------
def run_bot():
    from keymenu import get_main_keyboard, get_additional_keyboard
    ensure_base_python()
    write_bot_log("Бот запускается...")

    # Загрузка учетных данных из credentials.ini
    global TOKEN, PIN_CODE, allowed_accounts
    TOKEN, PIN_CODE, allowed_ids_str = load_credentials()
    # Load debug status from config.ini
    global debug_enabled
    debug_enabled = config.getboolean(CONFIG_SECTION, 'debug', fallback=False)
    if debug_enabled:
        # Auto-start tracing for bot
        sys.settrace(trace_calls)
        threading.settrace(trace_calls)
        write_debug_log("Debug tracing auto-start at bot launch.")
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

    current_bot = Bot(token=TOKEN, loop=loop)
    dp = Dispatcher(current_bot)
    setattr(current_bot, "dispatcher", dp)
    
    # Получение информации о боте и лог подключения
    try:
        bot_info = loop.run_until_complete(current_bot.get_me())
        write_bot_log(f"Бот подключён: {bot_info.first_name} (@{bot_info.username})")
    except Exception as e:
        write_bot_log(f"[ОШИБКА] Не удалось получить информацию о боте: {e}")

    import Moduls_manager_ext
    Moduls_manager_ext.register_handlers(dp)


    # --- ЭКСТРЕННАЯ КОМАНДА ---
    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "hrp")
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

    def get_os_status():

        return f"ОС: {platform.system()} {platform.release()} ({platform.version()})"

    def get_cpu_status():

        cpu_usage = psutil.cpu_percent(interval=1)
        physical_cores = psutil.cpu_count(logical=False)
        total_cores = psutil.cpu_count(logical=True)
        try:
            cpu_freq = psutil.cpu_freq()
            if cpu_freq:
                current_freq = f"{cpu_freq.current:.2f}"
                min_freq = f"{cpu_freq.min:.2f}"
                max_freq = f"{cpu_freq.max:.2f}"
            else:
                current_freq = min_freq = max_freq = "Недоступно"
        except Exception:
            current_freq = min_freq = max_freq = "Недоступно"
        temp = "Недоступно"
        try:
            temps = psutil.sensors_temperatures()
            if "coretemp" in temps:
                core_temps = [t.current for t in temps["coretemp"] if hasattr(t, "current")]
                if core_temps:
                    temp = f"{sum(core_temps)/len(core_temps):.1f}"
            elif temps:
                sensor = list(temps.values())[0]
                if sensor:
                    temp = sensor[0].current
        except Exception:
            pass
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        uptime_str = str(uptime).split('.')[0]
        return (
            f"CPU: {cpu_usage}% загрузки\n"
            f"Физ. ядер: {physical_cores}, Лог. ядер: {total_cores}\n"
            f"Частота: {current_freq} МГц (мин/макс: {min_freq}/{max_freq})\n"
            f"Температура: {temp}°C\n"
            f"Время работы: {uptime_str}"
        )

    def get_ram_status():

        ram = psutil.virtual_memory()
        return (
            f"RAM: {round(ram.total/(1024**3), 2)} ГБ общий, "
            f"{round(ram.used/(1024**3), 2)} ГБ использовано, "
            f"{round(ram.available/(1024**3), 2)} ГБ доступно\n"
            f"Загрузка: {ram.percent}%"
        )

    def get_disk_status():

        partitions = psutil.disk_partitions()
        result = []
        for partition in partitions:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                result.append(
                    f"Диск {partition.device} ({partition.fstype}, {partition.mountpoint}):\n"
                    f"  {round(usage.total/(1024**3),2)} ГБ всего, "
                    f"{round(usage.used/(1024**3),2)} ГБ использовано ({usage.percent}%), "
                    f"{round(usage.free/(1024**3),2)} ГБ свободно"
                )
            except Exception:
                result.append(f"Диск {partition.device}: недоступно")
        return "\n".join(result)

    def get_network_status():

        hostname = socket.gethostname()
        net_if_stats = psutil.net_if_stats()
        net_if_addrs = psutil.net_if_addrs()
        connected_interface_details = []
        internal_ip = None
        for iface, stats in net_if_stats.items():
            if stats.isup:
                addrs = net_if_addrs.get(iface, [])
                ipv4_found = False
                info_list = []
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        if not addr.address.startswith("127."):
                            ipv4_found = True
                            info_list.append(f"IPv4: {addr.address}")
                    elif addr.family == socket.AF_INET6:
                        info_list.append(f"IPv6: {addr.address}")
                    elif hasattr(socket, 'AF_PACKET') and addr.family == socket.AF_PACKET:
                        info_list.append(f"MAC: {addr.address}")
                if ipv4_found:
                    connected_interface_details.append(f"{iface}: " + ", ".join(info_list))
                    if internal_ip is None:
                        for addr in addrs:
                            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                                internal_ip = addr.address
                                break
        if not internal_ip:
            try:
                internal_ip = socket.gethostbyname(hostname)
            except Exception:
                internal_ip = "Не удалось получить"
        try:
            external_ip = subprocess.check_output("curl -s ifconfig.me", shell=True).decode("utf-8").strip()
        except Exception:
            external_ip = "Не удалось получить"
        return hostname, internal_ip, external_ip, connected_interface_details

    def test_speed():

        try:
            st = speedtest.Speedtest()
            st.get_best_server()
            download = st.download() / 1_000_000
            upload = st.upload() / 1_000_000
            return f"Скорость: загрузка {round(download,2)} Мбит/с, отправка {round(upload,2)} Мбит/с"
        except Exception as e:
            return f"Ошибка теста скорости: {str(e)}"

    def clean_old_screenshots():

        screenshots_dir = os.path.join(base_dir, "screenshots")
        files = sorted(
            [os.path.join(screenshots_dir, f) for f in os.listdir(screenshots_dir)
             if os.path.isfile(os.path.join(screenshots_dir, f))],
            key=os.path.getctime
        )
        while len(files) > 500:
            os.remove(files[0])
            files.pop(0)

    # ------------------------- Авторизация и старт -------------------------
    @dp.message_handler(lambda message: message.from_user.id not in authorized_users, content_types=types.ContentTypes.ANY)
    async def check_pin(message: types.Message):
        user_id = message.from_user.id
        if allowed_accounts:
            if user_id not in allowed_accounts:
                write_bot_log(f"Попытка авторизации неразрешённого пользователя {user_id}.")
                await message.answer("Доступ запрещён: ваш ID не входит в список разрешённых.")
                return
            if PIN_CODE:
                if not message.text or message.text.strip() in ["/start", "start"]:
                    await message.answer("Введите PIN-код:")
                    return
                if message.text.strip() == PIN_CODE:
                    authorized_users.add(user_id)
                    write_bot_log(f"Пользователь {user_id} успешно прошёл аутентификацию.")
                    keyboard = get_main_keyboard()
                    intro_text = (
                        "PIN-код верный. Вы авторизовались.\n\n"
                        "Инструкция:\n\n" + info.HELP_TEXT + "\nВыберите действие:"
                    )
                    await message.answer(intro_text, reply_markup=keyboard)
                    # Отправка накопленных логов пользователю
                    if pending_log_messages:
                        await message.answer("Пока вас не было, вот что произошло:")
                        log_text = "\n".join(pending_log_messages)
                        max_chunk = 4000
                        chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
                        for chunk in chunks:
                            await message.answer(chunk)
                        pending_log_messages.clear()
                        status_str = "включен" if debug_enabled else "выключен"
                        await message.answer(f"Статус дебага: {status_str}.")
                else:
                    write_bot_log(f"Неудачная попытка авторизации пользователя {user_id} с неправильным PIN: {message.text.strip()}")
                    await message.answer("Неверный PIN-код. Попробуйте ещё раз.")
            else:
                authorized_users.add(user_id)
                keyboard = get_main_keyboard()
                await message.answer("Вы авторизовались.", reply_markup=keyboard)
                if pending_log_messages:
                    await message.answer("Пока вас не было, вот что произошло:")
                    log_text = "\n".join(pending_log_messages)
                    max_chunk = 4000
                    chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
                    for chunk in chunks:
                        await message.answer(chunk)
                    pending_log_messages.clear()
                    status_str = "включен" if debug_enabled else "выключен"
                    await message.answer(f"Статус дебага: {status_str}.")
        else:
            if PIN_CODE:
                if not message.text or message.text.strip() in ["/start", "start"]:
                    await message.answer("Введите PIN-код:")
                    return
                if message.text.strip() == PIN_CODE:
                    authorized_users.add(user_id)
                    keyboard = get_main_keyboard()
                    intro_text = (
                        "PIN-код верный. Вы авторизовались.\n\n"
                        "Инструкция:\n\n" + info.HELP_TEXT + "\nВыберите действие:"
                    )
                    await message.answer(intro_text, reply_markup=keyboard)
                    if pending_log_messages:
                        await message.answer("Пока вас не было, вот что произошло:")
                        log_text = "\n".join(pending_log_messages)
                        max_chunk = 4000
                        chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
                        for chunk in chunks:
                            await message.answer(chunk)
                        pending_log_messages.clear()
                        status_str = "включен" if debug_enabled else "выключен"
                        await message.answer(f"Статус дебага: {status_str}.")
                else:
                    write_bot_log(f"Неудачная попытка авторизации пользователя {user_id} с неправильным PIN: {message.text.strip()}")
                    await message.answer("Неверный PIN-код. Попробуйте ещё раз.")
            else:
                authorized_users.add(user_id)
                keyboard = get_main_keyboard()
                await message.answer("Вы авторизовались.", reply_markup=keyboard)
                if pending_log_messages:
                    await message.answer("Пока вас не было, вот что произошло:")
                    log_text = "\n".join(pending_log_messages)
                    max_chunk = 4000
                    chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
                    for chunk in chunks:
                        await message.answer(chunk)
                    pending_log_messages.clear()
                    status_str = "включен" if debug_enabled else "выключен"
                    await message.answer(f"Статус дебага: {status_str}.")

    @dp.message_handler(commands=['start'])
    async def start_command(message: types.Message):
        keyboard = get_main_keyboard()
        await message.answer("Выберите действие:", reply_markup=keyboard)
        write_bot_log(f"Пользователь {message.from_user.id} выдал команду /start.")

    # ------------------------- Основные кнопки (статус, скриншоты) -------------------------
    @dp.message_handler(lambda message: message.text == "Статус сервера")
    async def server_status(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сервера.")
        await message.answer(get_os_status())
        await message.answer(get_cpu_status())
        await message.answer(get_ram_status())
        await message.answer(get_disk_status())

    @dp.message_handler(lambda message: message.text == "Статус сети")
    async def network_status(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сети.")
        hostname, internal_ip, external_ip, interface_details = get_network_status()
        if interface_details:
            for detail in interface_details:
                await message.answer("Интерфейс:\n" + detail)
        else:
            await message.answer("Нет подключённых интерфейсов")
        await message.answer(f"Внутренний IP: {internal_ip}")
        await message.answer(f"Внешний IP: {external_ip}")
        await message.answer("Измерение скорости, подождите...")
        await message.answer(test_speed())

    @dp.message_handler(lambda message: message.text == "Скриншот")
    async def take_screenshot(message: types.Message):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"screenshot_{timestamp}.png"
        path = os.path.join(base_dir, "screenshots", filename)
        screenshot = pyautogui.screenshot()
        screenshot.save(path)
        clean_old_screenshots()
        write_com_log(f"Пользователь {message.from_user.id} сделал скриншот: {filename}.")
        with open(path, "rb") as photo:
            await current_bot.send_photo(message.chat.id, photo)

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

    # ------------------------- CMD -------------------------
    @dp.message_handler(lambda message: message.text == "Назад в меню" and cmd_mode.get(message.from_user.id, False))
    async def cmd_back_to_main(message: types.Message):
        in_cmd_menu[message.from_user.id] = False
        keyboard = get_main_keyboard()
        await message.answer("Возвращаюсь в главное меню. Режим CMD активен.", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "cmd")
    async def cmd_menu(message: types.Message):
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["Запуск CMD", "Завершить CMD", "Назад в меню"]
        if cmd_mode.get(message.from_user.id, False):
            extra_buttons = ["dir", "ipconfig", "tasklist", "ping 8.8.8.8", "netstat", "tracert 8.8.8.8"]
            buttons.extend(extra_buttons)
        keyboard.add(*buttons)
        if cmd_mode.get(message.from_user.id, False):
            in_cmd_menu[message.from_user.id] = True
            await message.answer("Режим CMD активен. Выберите команду.", reply_markup=keyboard)
        else:
            await message.answer("Режим CMD не активен. Запустите его кнопкой «Запуск CMD».", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "Запуск CMD")
    async def start_cmd(message: types.Message):
        if cmd_mode.get(message.from_user.id, False):
            await message.answer("Режим CMD уже запущен!")
        else:
            cmd_mode[message.from_user.id] = True
            in_cmd_menu[message.from_user.id] = True
            write_bot_log(f"Пользователь {message.from_user.id} запустил режим CMD.")
            await message.answer("Режим CMD запущен.")
        await cmd_menu(message)

    @dp.message_handler(lambda message: message.text in ["Завершить CMD", "Закрыть CMD"])
    async def end_cmd(message: types.Message):
        if not power_mode.get(message.from_user.id, False):
            if not cmd_mode.get(message.from_user.id, False):
                await message.answer("Режим CMD не запущен!")
            else:
                cmd_mode[message.from_user.id] = False
                in_cmd_menu[message.from_user.id] = False
                write_bot_log(f"Пользователь {message.from_user.id} завершил режим CMD.")
                await message.answer("Режим CMD завершён.")
        await cmd_menu(message)

    @dp.message_handler(
        lambda message: cmd_mode.get(message.from_user.id, False)
                        and in_cmd_menu.get(message.from_user.id, False)
                        and message.text not in ["Запуск CMD", "Завершить CMD", "Назад в меню", "cmd", "Питание"]
    )
    async def execute_cmd(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} выполнил команду CMD: {message.text}")
        try:
            result = subprocess.run(
                message.text,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            output = result.stdout.strip() or result.stderr.strip() or "Команда выполнена без вывода."
            if len(output) > 4000:
                chunks = [output[i:i+4000] for i in range(0, len(output), 4000)]
                for chunk in chunks:
                    await message.answer(chunk)
            else:
                await message.answer(output)
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")

    # ------------------------- Логи -------------------------
    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "лог")
    async def log_menu(message: types.Message):
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        buttons = ["лог устройства", "лог бота", "лог менеджера плагинов", "лог ошибок", "дебаг", "назад бота"]
        keyboard.add(*buttons)
        await message.answer("Выберите тип лога:", reply_markup=keyboard)

    @dp.message_handler(lambda message: message.text == "лог устройства")
    async def device_log(message: types.Message):
        write_bot_log(f"Пользователь {message.from_user.id} запросил лог устройства.")
        try:
            with open(com_log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text.strip():
                log_text = "Лог устройства: логов нет."
            max_chunk = 4000
            chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
            for chunk in chunks:
                await message.answer(chunk)
        except Exception as e:
            await message.answer(f"Ошибка чтения лога устройства: {str(e)}")

    @dp.message_handler(lambda message: message.text == "лог бота")
    async def bot_log_handler(message: types.Message):
        write_bot_log(f"Пользователь {message.from_user.id} запросил лог бота.")
        try:
            with open(bot_log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text.strip():
                log_text = "Лог бота: логов нет."
            max_chunk = 4000
            chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
            for chunk in chunks:
                await message.answer(chunk)
        except Exception as e:
            await message.answer(f"Ошибка чтения лога бота: {str(e)}")

    @dp.message_handler(lambda message: message.text == "лог менеджера плагинов")
    async def plugin_log_handler(message: types.Message):
        write_bot_log(f"Пользователь {message.from_user.id} запросил лог менеджера плагинов.")
        try:
            with open(plugin_log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text.strip():
                log_text = "Лог менеджера плагинов: логов нет."
            max_chunk = 4000
            chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
            for chunk in chunks:
                await message.answer(chunk)
        except Exception as e:
            await message.answer(f"Ошибка чтения лога менеджера плагинов: {str(e)}")

    @dp.message_handler(lambda message: message.text == "лог ошибок")
    async def error_log_handler(message: types.Message):
        write_bot_log(f"Пользователь {message.from_user.id} запросил лог ошибок.")
        try:
            with open(error_log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text.strip():
                log_text = "Ошибок нет."
            max_chunk = 4000
            chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
            for chunk in chunks:
                await message.answer(chunk)
        except Exception as e:
            await message.answer(f"Ошибка чтения лога ошибок: {str(e)}")

    
    @dp.message_handler(lambda message: message.text == "дебаг")
    async def debug_menu(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} открыл меню дебага.")
        # Отправляем статус дебага
        status_str = "включен" if debug_enabled else "выключен"
        await message.answer(f"Статус дебага: {status_str}.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Вкл дебаг", "Выкл дебаг")
        keyboard.add("Прочитать лог дебага", "Назад в меню логов")
        await message.answer("Меню дебага:", reply_markup=keyboard)
    @dp.message_handler(lambda message: message.text == "Вкл дебаг")
    async def enable_debug(message: types.Message):
        global debug_enabled
        if debug_enabled:
            await message.answer("Дебаг уже включен.")
        else:
            debug_enabled = True
            write_com_log(f"Пользователь {message.from_user.id} включил дебаг.")
            await message.answer("Дебаг включен.")
            # Сохраняем статус в config.ini
            if CONFIG_SECTION not in config:
                config[CONFIG_SECTION] = {}
            config[CONFIG_SECTION]['debug'] = 'True'
            _save_config()
            write_bot_log("Статус дебага сохранён в config.ini")
            # Запускаем трассировку
            sys.settrace(trace_calls)
            threading.settrace(trace_calls)
            write_debug_log("Debug tracing started by user.")
            await debug_menu(message)
    @dp.message_handler(lambda message: message.text == "Выкл дебаг")
    async def disable_debug(message: types.Message):
        global debug_enabled
        if not debug_enabled:
            await message.answer("Дебаг уже выключен.")
        else:
            debug_enabled = False
            write_com_log(f"Пользователь {message.from_user.id} выключил дебаг.")
            await message.answer("Дебаг выключен.")
            # Сохраняем статус в config.ini
            if CONFIG_SECTION not in config:
                config[CONFIG_SECTION] = {}
            config[CONFIG_SECTION]['debug'] = 'False'
            _save_config()
            write_bot_log("Статус дебага сохранён в config.ini")
            await debug_menu(message)
    @dp.message_handler(lambda message: message.text == "Прочитать лог дебага")
    async def read_debug_log(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил лог дебага.")
        try:
            with open(debug_log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text.strip():
                log_text = "Лог дебага: логов нет."
            max_chunk = 4000
            chunks = [log_text[i:i+max_chunk] for i in range(0, len(log_text), max_chunk)]
            for chunk in chunks:
                await message.answer(chunk)
        except Exception as e:
            await message.answer(f"Ошибка чтения лога дебага: {str(e)}")

    @dp.message_handler(lambda message: message.text == "Назад в меню логов")
    async def back_from_debug_menu(message: types.Message):
        # Return to log menu from debug menu
        await log_menu(message)
        write_com_log(f"Пользователь {message.from_user.id} вернулся в меню логов из дебага.")

    @dp.message_handler(lambda message: message.text == "назад бота")
    async def back_from_log_menu(message: types.Message):
        await additional_menu(message)
        write_bot_log(f"Пользователь {message.from_user.id} вышел из меню логов.")

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
            # Две кнопки для возврата: в меню плагинов и в главное меню
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

    # Добавляем on_shutdown для корректного завершения работы бота
    async def on_shutdown(dispatcher: Dispatcher):
        write_bot_log("Выполняется shutdown бота.")
        await dispatcher.bot.close()

    # Собственно блокирующий запуск поллинга (в отдельном потоке)
    try:
        executor.start_polling(dp, skip_updates=True, on_shutdown=on_shutdown)
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
# 9. Профи‑обработка конфигурации (токен, PIN, ID)
# -----------------------------------------------------
CONFIG_FILE = os.path.join(base_dir, "config.ini")
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

debug_enabled = False
debug_log_file = os.path.join(base_dir, "лог", f"log_{current_time_str}_debаг.txt")
debug_logger = create_logger("DEBUG", debug_log_file, level=logging.DEBUG)
def write_debug_log(entry: str):
    if debug_enabled:
        debug_logger.debug(entry)





# ----------------------------------------
# Debug tracing for functions and variables
# ----------------------------------------
# Debug tracing for functions and variables
def trace_calls(frame, event, arg):
    if not debug_enabled:
        return
    filename = frame.f_code.co_filename
    # Трассируем только в рамках приложения
    if not filename.startswith(base_dir):
        return
    name = frame.f_code.co_name
    modname = frame.f_globals.get("__name__", "")
    if modname.startswith("logging") or name in ("trace_calls", "write_debug_log"):
        return
    if event == "call":
        write_debug_log(f"Calling {name}")
    elif event == "return":
        write_debug_log(f"Returned from {name}")
    return trace_calls


# Enable tracing if debug is enabled
if debug_enabled:
    write_debug_log("Debug enabled: starting trace")
    sys.settrace(trace_calls)
    threading.settrace(trace_calls)
# End of debug tracing setup

if __name__ == "__main__":
    write_bot_log("Запуск приложения. Инициализация GUI.")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    write_bot_log("GUI инициализирован.")
    sys.exit(app.exec_())