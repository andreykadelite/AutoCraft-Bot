import os
import configparser
import subprocess
import sys
import shutil
import zipfile
import asyncio
import urllib.request
import psutil
from aiogram import types
import platform
try:
    from .keymenu import get_main_settings_keyboard, get_additional_keyboard  # type: ignore
except Exception:
    from keymenu import get_main_settings_keyboard, get_additional_keyboard  # type: ignore



# --- служебные утилиты (нужны из‑за переноса modulset в moduls/ и для сборок Nuitka) ---
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")

def get_base_dir_fallback() -> str:
    """Базовая папка проекта: рядом с EXE (в сборке) или рядом с главным .py (в исходниках)."""
    try:
        import __main__
        bd = getattr(__main__, "base_dir", None)
        if bd:
            return str(bd)
    except Exception:
        pass

    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))

    try:
        import __main__
        main_file = getattr(__main__, "__file__", None)
        if main_file:
            return os.path.dirname(os.path.abspath(main_file))
    except Exception:
        pass

    module_dir = os.path.dirname(os.path.abspath(__file__))
    # если modulset лежит в moduls/, поднимаемся на уровень выше
    if os.path.basename(module_dir).lower() == "moduls":
        return os.path.dirname(module_dir)
    return module_dir

def get_app_filename() -> str:
    """Имя файла приложения в base_dir, которое нельзя удалять при сбросах/бэкапах."""
    if _is_frozen():
        return os.path.basename(sys.executable)
    try:
        import __main__
        main_file = getattr(__main__, "__file__", None)
        if main_file:
            return os.path.basename(main_file)
    except Exception:
        pass
    return os.path.basename(sys.argv[0]) if sys.argv else "app.py"

def _ensure_sys_core_on_syspath() -> None:
    base_dir = get_base_dir_fallback()
    for candidate in (base_dir, os.path.join(base_dir, "src")):
        if candidate and os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass


_full_restart_from_message = None


def _get_full_restart_from_message():
    global _full_restart_from_message
    if _full_restart_from_message is None:
        try:
            from sys_core.full_restart import full_restart_from_message as _full_restart_from_message  # type: ignore
        except Exception:
            _ensure_sys_core_on_syspath()
            from sys_core.full_restart import full_restart_from_message as _full_restart_from_message  # type: ignore
    return _full_restart_from_message


async def run_full_restart(message: types.Message) -> None:
    try:
        restart_fn = _get_full_restart_from_message()
    except Exception as e:
        try:
            await message.answer(f"Full restart init failed: {e}")
        except Exception:
            pass
        os._exit(42)
        return
    try:
        await restart_fn(message)
    except Exception as e:
        try:
            await message.answer(f"Full restart failed: {e}")
        except Exception:
            pass
        os._exit(42)



# --- Динамическое меню "Настройки" через реестр (аналог utilities_registry) ---
# Важно: это НЕ ломает старую логику. Если settings_registry отсутствует — просто ничего не регистрируем.
try:
    from .settings_registry import register_setting  # type: ignore
except Exception:
    try:
        from settings_registry import register_setting  # type: ignore
    except Exception:
        register_setting = None




# --- Динамическое меню "Дополнительно" через реестр additional_registry ---
# Регистрируем кнопку "Настройки" в меню «Дополнительно», чтобы не пришлось править другие модули.
try:
    from .additional_registry import register_additional  # type: ignore
except Exception:
    try:
        from additional_registry import register_additional  # type: ignore
    except Exception:
        register_additional = None


def _register_own_additional_menu():
    """Регистрируем пункт «Настройки» в меню «Дополнительно»."""
    if not register_additional:
        return
    try:
        register_additional(
            key="modulset_settings",
            title="Настройки",
            trigger_text="Настройки",
            group="additional",
            order=10,
            description="Открыть меню настроек",
        )
    except Exception:
        # Реестр — вспомогательная штука. Если что-то пошло не так, не валим модуль настроек.
        pass


# Регистрируем пункт сразу при импорте модуля
_register_own_additional_menu()

def _register_own_settings_menu():
    """Регистрируем пункты меню настроек, которые обслуживает этот модуль."""
    if not register_setting:
        return
    try:
        # Кнопка возврата в главное меню обычно обрабатывается отдельным модулем.
        # Здесь мы только добавляем её в список пунктов настроек.
        register_setting(
            key="modulset_main_menu",
            title="Главное меню",
            trigger_text="Главное меню",
            group="settings",
            order=1,
            description="Возврат в главное меню бота",
        )

        register_setting(
            key="modulset_info",
            title="Информация",
            trigger_text="Информация",
            group="settings",
            order=10,
            description="Системная информация (ОС/CPU/RAM/диски/сеть)",
        )

        register_setting(
            key="modulset_auth",
            title="Авторизация",
            trigger_text="Авторизация",
            group="settings",
            order=20,
            description="PIN, разрешённые ID, экспорт данных, смена токена",
        )

        register_setting(
            key="modulset_system",
            title="Система",
            trigger_text="Система",
            group="settings",
            order=30,
            description="Проверки, переустановка Python, полный перезапуск",
        )

        register_setting(
            key="modulset_backup_restore",
            title="Резервное копирование и восстановление",
            trigger_text="Резервное копирование и восстановление",
            group="settings",
            order=40,
            description="Создание/восстановление/удаление полных резервных копий",
        )

        register_setting(
            key="modulset_memory",
            title="Память",
            trigger_text="Память",
            group="settings",
            order=50,
            description="Отчёты по диску/раб.директории/RAM",
        )

        register_setting(
            key="modulset_reset",
            title="Сброс",
            trigger_text="Сброс",
            group="settings",
            order=60,
            description="Полный/выборочный сброс рабочей директории",
        )
    except Exception:
        # Реестр — вспомогательная штука. Если что-то пошло не так, не валим модуль настроек.
        pass


# Регистрируем пункты сразу при импорте модуля
_register_own_settings_menu()


# Поиск архива Python.zip
import tempfile
from pathlib import Path

ZIP_NAME = 'Python.zip'
TARGET_FOLDER = 'python'

def find_zip_file(zip_name: str = ZIP_NAME) -> Path:
    """
    Ищем ZIP (Python.zip) максимально живуче, потому что:
    - modulset теперь лежит в moduls/ (и __file__ указывает на неё),
    - сборка может быть Nuitka onefile (архив может лежать рядом с EXE, либо в temp-распаковке).
    """
    candidates = []

    # 1) Рядом с EXE (самый частый кейс для сборки)
    try:
        candidates.append(Path(sys.executable).parent / zip_name)
    except Exception:
        pass

    # 2) Рядом с базовой папкой проекта/главного скрипта
    try:
        bd = Path(get_base_dir_fallback())
        candidates.append(bd / zip_name)
        # на всякий случай, если файл назвали в другом регистре
        candidates.append(bd / zip_name.lower())
    except Exception:
        pass

    # 3) Рядом с этим модулем и на уровень выше (moduls/..)
    try:
        md = Path(__file__).resolve().parent
        candidates.append(md / zip_name)
        candidates.append(md.parent / zip_name)
        candidates.append(md.parent / zip_name.lower())
    except Exception:
        pass

    # 4) TEMP (и поиск Nuitka onefile-папки onefile_<pid>_*)
    temp_root = Path(tempfile.gettempdir())
    candidates.append(temp_root / zip_name)
    candidates.append(temp_root / zip_name.lower())

    pid = os.getpid()
    try:
        for sub in temp_root.iterdir():
            if not sub.is_dir():
                continue
            # Nuitka onefile любит такие каталоги
            if sub.name.startswith(f"onefile_{pid}_") or sub.name.startswith("onefile_"):
                candidates.append(sub / zip_name)
                candidates.append(sub / zip_name.lower())
    except Exception:
        pass

    for cand in candidates:
        try:
            if cand and cand.is_file():
                return cand
        except Exception:
            continue

    return None

import datetime

# Глобальные переменные
restore_pending = {}  # Для восстановления резервной копии
full_backup_delete_mode = {}  # Для режима удаления: значения "full" или "partial"
partial_delete_selections = {}  # Для хранения выбранных копий в режиме частичного удаления

def get_human_readable_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.2f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.2f} GB"

def folder_summary(path):
    total_size = 0
    file_count = 0
    for dirpath, _, filenames in os.walk(path):
        file_count += len(filenames)
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
    return total_size, file_count

def full_directory_summary(path):
    summary = ""
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                size = os.path.getsize(entry.path)
                summary += f"{entry.name} (файл): {get_human_readable_size(size)}\n"
            elif entry.is_dir():
                s, count = folder_summary(entry.path)
                summary += f"{entry.name}/ (папка): {get_human_readable_size(s)}, файлов: {count}\n"
    except Exception as e:
        summary += f"Ошибка доступа к {path}: {e}\n"
    return summary

# Функция для проверки системных пакетов
def is_allowed(item_name):
    allowed_keywords = ["pip", "setuptools", "wheel"]
    for keyword in allowed_keywords:
        if keyword in item_name.lower():
            return True
    return False


# Глобальные флаги для режимов
change_pin_mode = {}
add_id_mode = {}
delete_id_mode = {}
auth_mode = {}      # Подменю "Авторизация"
system_mode = {}    # Подменю "Система"
reset_mode = {}     # "full" для полного сброса, "selective" для выборочного удаления
selective_deletion = {}
change_token_mode = {}
memory_mode = {}    # Подменю "Память"
backup_restore_mode = {}  # Подменю "Резервное копирование и восстановление"

# Функции загрузки/сохранения настроек (config.ini)
def load_credentials():
    import __main__
    # используем общий парсер и путь из __main__
    cfg = __main__.config
    cfg.read(__main__.CONFIG_FILE, encoding='utf-8')
    if __main__.CONFIG_SECTION not in cfg:
        return "", "", ""
    sec = cfg[__main__.CONFIG_SECTION]
    token = sec.get('token', '')
    pin   = sec.get('pin', '')
    allowed = sec.get('allowed_ids', '')
    return token, pin, allowed

def save_credentials(token, pin, allowed):
    import __main__
    # пишем в ту же секцию credentials в общем config.ini
    cfg = __main__.config
    if __main__.CONFIG_SECTION not in cfg:
        cfg[__main__.CONFIG_SECTION] = {}
    sec = cfg[__main__.CONFIG_SECTION]
    sec['token']       = token
    sec['pin']         = pin
    sec['allowed_ids'] = allowed
    # атомарно сохраняем через _save_config из __main__
    __main__._save_config()


# Получить имя исполняемого файла (для исключения из удаления)
def get_exe_name():
    # историческое имя, но теперь возвращает корректное имя EXE/главного .py
    return get_app_filename()

# Функция полного сброса рабочей директории (исключая credentials.ini, config.ini, python.zip и exe-файл)
def reset_all_working_dir(base_dir):
    """
    Полный сброс рабочей директории.

    Удаляем всё в base_dir, кроме:
    - config.ini
    - Python.zip (или python.zip, регистр неважен)
    - файла приложения (EXE в сборке или главный .py в исходниках)

    credentials.ini намеренно НЕ исключаем, чтобы сброс был "чистый".
    """
    exe_name = get_exe_name()
    errors = []
    for item in os.listdir(base_dir):
        low = item.lower()
        if low == "config.ini" or low == ZIP_NAME.lower() or item == exe_name:
            continue
        item_path = os.path.join(base_dir, item)
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        except Exception:
            errors.append(item)
    return errors

# Функция выборочного удаления элементов
def selective_delete(base_dir, items_to_delete):
    for item in items_to_delete:
        item_path = os.path.join(base_dir, item)
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        except Exception:
            pass

# Функция для сбора информации о системе

def get_system_information():
    import cpuinfo
    import wmi
    import psutil
    import os
    import sys
    # Получаем информацию об ОС через WMI
    try:
        c = wmi.WMI()
        os_info = c.Win32_OperatingSystem()[0]
        os_line = f"ОС: {os_info.Caption} (Версия {os_info.Version}, Сборка {os_info.BuildNumber})"
    except Exception:
        import platform
        uname = platform.uname()
        os_line = f"ОС: {uname.system} {uname.release} ({uname.version})"
    # Информация о процессоре через py-cpuinfo
    try:
        ci = cpuinfo.get_cpu_info()
        brand = ci.get('brand_raw', 'Неизвестно')
        freq = ci.get('hz_actual_friendly', 'N/A')
        cpu_line = f"Процессор: {brand} @ {freq}"
    except Exception:
        import platform
        cpu_line = f"Процессор: {platform.processor()}"
    # Память и диски
    vm = psutil.virtual_memory()
    mem_line = f"Память: Всего {get_human_readable_size(vm.total)}, Использовано {get_human_readable_size(vm.used)} ({vm.percent}%), Доступно {get_human_readable_size(vm.available)}"
    parts = [os_line, cpu_line, mem_line]
    # Диски
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            parts.append(f"Диск {part.device} ({part.mountpoint}): Всего {get_human_readable_size(usage.total)}, Использовано {get_human_readable_size(usage.used)} ({usage.percent}%), Свободно {get_human_readable_size(usage.free)}")
        except Exception:
            parts.append(f"Диск {part.device}: информация недоступна")
    # Сетевые интерфейсы
    net_info = "Сетевые интерфейсы:\n"
    for iface, addrs in psutil.net_if_addrs().items():
        addr_list = []
        for addr in addrs:
            fam = addr.family.name if hasattr(addr.family, "name") else str(addr.family)
            addr_list.append(f"{fam}: {addr.address}")
        net_info += f"{iface}: " + ", ".join(addr_list) + "\n"
    parts.append(net_info)
    parts.append(f"Python: {sys.version}")
    # Плагины и размер рабочей директории
    base_dir_local = get_base_dir_fallback()
    plugins_dir = os.path.join(base_dir_local, "plugins")
    if os.path.isdir(plugins_dir):
        plugins = [name for name in os.listdir(plugins_dir) if os.path.isdir(os.path.join(plugins_dir, name))]
        parts.append(f"Установлено плагинов: {len(plugins)}")
    else:
        parts.append("Папка plugins не найдена.")
    total = 0
    for dirpath, _, filenames in os.walk(base_dir_local):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    parts.append(f"Размер рабочей директории: {get_human_readable_size(total)}")
    return "\n".join(parts)


# Регистрируем обработчики
def register_handlers(dp):
    # Главное меню настроек
    @dp.message_handler(lambda message: message.text == "Настройки")
    async def settings_handler(message: types.Message):
        kb = get_main_settings_keyboard()
        await message.answer("Меню настроек:", reply_markup=kb)

    @dp.message_handler(lambda message: message.text == "Вернуться")
    async def return_to_additional_menu(message: types.Message):
        kb = get_additional_keyboard()
        await message.answer("Меню дополнительно:", reply_markup=kb)

    
    # Обработчик кнопки "Информация"
    @dp.message_handler(lambda message: message.text == "Информация")
    async def info_handler(message: types.Message):
        info_text = get_system_information()
        await message.answer(info_text)
    
    # Подменю "Авторизация"
    @dp.message_handler(lambda message: message.text == "Авторизация")
    async def auth_menu_handler(message: types.Message):
        auth_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Изменить PIN код", "Удалить PIN код")
        kb.add("Добавить ID аккаунтов", "Удалить ID аккаунтов")
        kb.add("Экспорт данных для входа", "Сменить токен", "Возврат в настройки")
        await message.answer("Меню авторизации:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text == "Возврат в настройки" and auth_mode.get(message.from_user.id, False))
    async def auth_back_handler(message: types.Message):
        auth_mode.pop(message.from_user.id, None)
        await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())
    
    # Изменение PIN кода
    @dp.message_handler(lambda message: message.text == "Изменить PIN код" and auth_mode.get(message.from_user.id, False))
    async def change_pin_prompt(message: types.Message):
        change_pin_mode[message.from_user.id] = True
        await message.answer("Введите новый PIN код:")
    
    @dp.message_handler(lambda message: message.from_user.id in change_pin_mode)
    async def process_new_pin(message: types.Message):
        new_pin = message.text.strip()
        try:
            import __main__
            __main__.PIN_CODE = new_pin
            try:
                from __main__ import write_bot_log
                write_bot_log(f"PIN код изменён на: {new_pin} пользователем {message.from_user.id}")
            except Exception:
                pass
            token, old_pin, allowed = load_credentials()
            save_credentials(token, new_pin, allowed)
        except Exception:
            pass
        await message.answer(f"Новый PIN код установлен: {new_pin}")
        change_pin_mode.pop(message.from_user.id, None)
    
    # Удаление PIN кода
    @dp.message_handler(lambda message: message.text == "Удалить PIN код" and auth_mode.get(message.from_user.id, False))
    async def delete_pin_handler(message: types.Message):
        try:
            import __main__
            current_pin = __main__.PIN_CODE
        except Exception:
            current_pin = ""
        if not current_pin:
            await message.answer("PIN код не установлен.")
            return
        try:
            import __main__
            __main__.PIN_CODE = ""
            try:
                from __main__ import write_bot_log
                write_bot_log(f"PIN код удалён пользователем {message.from_user.id}")
            except Exception:
                pass
            token, old_pin, allowed = load_credentials()
            save_credentials(token, "", allowed)
        except Exception:
            pass
        await message.answer("PIN код удалён.")
    
    # Добавление ID аккаунтов
    @dp.message_handler(lambda message: message.text == "Добавить ID аккаунтов" and auth_mode.get(message.from_user.id, False))
    async def add_ids_prompt(message: types.Message):
        add_id_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Отмена")
        await message.answer("Введите ID аккаунта для добавления (один за раз).", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.from_user.id in add_id_mode)
    async def process_single_id(message: types.Message):
        if message.text.strip().lower() == "отмена":
            add_id_mode.pop(message.from_user.id, None)
            await message.answer("Операция добавления ID отменена.", reply_markup=get_main_settings_keyboard())
            return
        try:
            new_id = str(int(message.text.strip()))
        except ValueError:
            await message.answer("Некорректный ID аккаунта. Введите числовой ID или 'Отмена' для отмены.")
            return
        token, pin, allowed = load_credentials()
        allowed_list = [x.strip() for x in allowed.split(",") if x.strip()] if allowed else []
        if new_id in allowed_list:
            await message.answer(f"ID аккаунта {new_id} уже добавлен. Введите другой или 'Отмена'.")
        else:
            allowed_list.append(new_id)
            new_allowed = ", ".join(allowed_list)
            save_credentials(token, pin, new_allowed)
            try:
                import __main__
                __main__.allowed_accounts = set(allowed_list)
            except Exception:
                pass
            await message.answer(f"ID аккаунта {new_id} добавлен. Введите следующий или 'Отмена'.")
    
    # Удаление ID аккаунтов
    @dp.message_handler(lambda message: message.text == "Удалить ID аккаунтов" and auth_mode.get(message.from_user.id, False))
    async def delete_ids_prompt(message: types.Message):
        token, pin, allowed = load_credentials()
        allowed_list = [x.strip() for x in allowed.split(",") if x.strip()] if allowed else []
        if not allowed_list:
            await message.answer("Список ID пуст.")
            return
        delete_id_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for acc in allowed_list:
            kb.add(acc)
        kb.add("Отмена")
        await message.answer("Выберите ID для удаления:", reply_markup=kb)
    
    @dp.message_handler(lambda message: delete_id_mode.get(message.from_user.id, False) and message.text in ["Возврат в настройки", "Отмена"])
    async def delete_ids_cancel(message: types.Message):
        delete_id_mode.pop(message.from_user.id, None)
        await message.answer("Операция удаления ID отменена.", reply_markup=get_main_settings_keyboard())
    
    @dp.message_handler(lambda message: delete_id_mode.get(message.from_user.id, False) and message.text.lower() not in ["назад", "отмена"])
    async def process_delete_id_button(message: types.Message):
        token, pin, allowed = load_credentials()
        allowed_list = [x.strip() for x in allowed.split(",") if x.strip()] if allowed else []
        if message.text not in allowed_list:
            await message.answer("Выбран некорректный ID.")
            return
        allowed_list.remove(message.text)
        new_allowed = ", ".join(allowed_list)
        save_credentials(token, pin, new_allowed)
        try:
            import __main__
            __main__.allowed_accounts = set(allowed_list)
        except Exception:
            pass
        await message.answer(f"Удалён ID: {message.text}.\nТекущий список: {new_allowed}")
        if allowed_list:
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for acc in allowed_list:
                kb.add(acc)
            kb.add("Отмена")
            await message.answer("Выберите следующий для удаления:", reply_markup=kb)
        else:
            delete_id_mode.pop(message.from_user.id, None)
            auth_kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            auth_kb.add("Изменить PIN код", "Удалить PIN код")
            auth_kb.add("Добавить ID аккаунтов", "Удалить ID аккаунтов")
            auth_kb.add("Экспорт данных для входа", "Сменить токен", "Возврат в настройки")
            await message.answer("Список ID пуст. Возвращаюсь в меню авторизации.", reply_markup=auth_kb)
    
    @dp.message_handler(lambda message: message.text == "Экспорт данных для входа" and auth_mode.get(message.from_user.id, False))
    async def export_login_data(message: types.Message):
        try:
            import __main__
            token = __main__.TOKEN
        except Exception:
            token = ""
        try:
            pin_code = __main__.PIN_CODE
        except Exception:
            pin_code = ""
        try:
            allowed = __main__.allowed_accounts
        except Exception:
            allowed = set()
        pin_display = pin_code if pin_code else "PIN код не установлен"
        allowed_display = ", ".join(str(x) for x in allowed) if allowed else "ID не установлены"
        data = f"Token: {token}\nPIN Code: {pin_display}\nAllowed Accounts: {allowed_display}"
        await message.answer(data)
        filename = "login_data.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(data)
        await message.answer_document(open(filename, "rb"))
        os.remove(filename)
    
    # Обработчик смены токена
    @dp.message_handler(lambda message: message.text == "Сменить токен" and auth_mode.get(message.from_user.id, False))
    async def change_token_prompt(message: types.Message):
        change_token_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Отмена")
        await message.answer("Введите новый токен бота (или 'Отмена'):", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.from_user.id in change_token_mode and message.text.strip().lower() == "отмена")
    async def cancel_token_change(message: types.Message):
        change_token_mode.pop(message.from_user.id, None)
        await message.answer("Операция смены токена отменена.", reply_markup=get_main_settings_keyboard())
    
    @dp.message_handler(lambda message: message.from_user.id in change_token_mode and isinstance(change_token_mode.get(message.from_user.id), bool))
    async def process_new_token(message: types.Message):
        new_token = message.text.strip()
        from aiogram import Bot
        try:
            temp_bot = Bot(token=new_token)
            me = await temp_bot.get_me()
            bot_username = me.username
        except Exception:
            await message.answer("Некорректный токен. Попробуйте снова или 'Отмена'.")
            return
        change_token_mode[message.from_user.id] = new_token
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет", "Отмена")
        await message.answer(f"Найден бот: @{bot_username}. Сохранить токен?", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет", "Отмена"] and message.from_user.id in change_token_mode and isinstance(change_token_mode.get(message.from_user.id), str))
    async def confirm_token_change(message: types.Message):
        if message.text == "Да":
            new_token = change_token_mode.pop(message.from_user.id)
            token, pin, allowed = load_credentials()
            save_credentials(new_token, pin, allowed)
            await message.answer("Токен изменён. Бот перезапускается...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            change_token_mode.pop(message.from_user.id, None)
            await message.answer("Операция отменена.", reply_markup=get_main_settings_keyboard())
    
    # Новый раздел: Резервное копирование и восстановление
    @dp.message_handler(lambda message: message.text == "Резервное копирование и восстановление")
    async def backup_restore_menu_handler(message: types.Message):
        backup_restore_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Создать полную резервную копию", "Восстановить полную резервную копию", "Удаление полных резервных копий")
        kb.add("Возврат в настройки")
        await message.answer("Меню резервного копирования и восстановления:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text == "Создать полную резервную копию")
    async def create_full_backup_handler(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        backup_folder = os.path.join(base_dir, "full_backups")
        os.makedirs(backup_folder, exist_ok=True)
        exe_name = get_exe_name()
        backup_name = f"full_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        backup_path = os.path.join(backup_folder, backup_name)
        files_to_archive = []
        for item in os.listdir(base_dir):
            low = item.lower()
            if item in ("full_backups", exe_name) or low in ("credentials.ini", ZIP_NAME.lower()):
                continue
            item_path = os.path.join(base_dir, item)
            if os.path.isfile(item_path):
                files_to_archive.append((item_path, item))
            elif os.path.isdir(item_path):
                for root, dirs, files in os.walk(item_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, base_dir)
                        files_to_archive.append((file_path, arcname))
        total_files = len(files_to_archive)
        next_threshold = 5
        current_file = 0
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as backup_zip:
            for file_path, arcname in files_to_archive:
                backup_zip.write(file_path, arcname=arcname)
                current_file += 1
                percent = (current_file / total_files) * 100
                if percent >= next_threshold:
                    await message.answer(f"Создание резервной копии: {int(next_threshold)}% завершено...")
                    next_threshold += 5
        await message.answer(f"Полная резервная копия создана: {backup_name}")
    
    @dp.message_handler(lambda message: message.text == "Восстановить полную резервную копию")
    async def restore_full_backup_menu(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        backup_folder = os.path.join(base_dir, "full_backups")
        if not os.path.isdir(backup_folder):
            await message.answer("Папка с резервными копиями не найдена.")
            return
        backups = [f for f in os.listdir(backup_folder) if f.lower().endswith(".zip")]
        if not backups:
            await message.answer("Нет доступных резервных копий.")
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for backup in backups:
            kb.add(backup)
        kb.add("Возврат в настройки")
        await message.answer("Выберите резервную копию для восстановления:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text.lower().endswith(".zip") and "full_backup_" in message.text)
    async def process_full_backup_restore(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        backup_folder = os.path.join(base_dir, "full_backups")
        backup_file = message.text
        backup_path = os.path.join(backup_folder, backup_file)
        if not os.path.isfile(backup_path):
            await message.answer("Выбранная резервная копия не найдена.")
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        restore_pending[message.from_user.id] = backup_path
        await message.answer(f"Вы уверены, что хотите восстановить резервную копию {backup_file}? Это приведёт к перезапуску бота.", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and message.from_user.id in restore_pending)
    async def confirm_full_backup_restore(message: types.Message):
        import __main__
        backup_path = restore_pending.pop(message.from_user.id, None)
        if message.text == "Да" and backup_path:
            try:
                try:
                    _get_full_restart_from_message()
                except Exception:
                    pass
                base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
                exe_name = get_exe_name()
                deletion_errors = []
                for item in os.listdir(base_dir):
                    low = item.lower()
                    if item == exe_name or item == "full_backups" or low in ("credentials.ini", ZIP_NAME.lower()):
                        continue
                    item_path = os.path.join(base_dir, item)
                    try:
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    except Exception as e:
                        deletion_errors.append(f"{item}: {str(e)}")
                extraction_errors = {}
                try:
                    with zipfile.ZipFile(backup_path, 'r') as backup_zip:
                        files = backup_zip.namelist()
                        total_files = len(files)
                        next_threshold = 5
                        for i, file in enumerate(files, start=1):
                            try:
                                backup_zip.extract(member=file, path=base_dir)
                            except Exception as e:
                                extraction_errors[file] = str(e)
                            percent = (i / total_files) * 100
                            if percent >= next_threshold:
                                await message.answer(f"Восстановление: {int(next_threshold)}% завершено...")
                                next_threshold += 5
                except Exception as e:
                    await message.answer(f"Ошибка при восстановлении: {e}")
                error_report = ""
                if deletion_errors:
                    error_report += "Ошибки удаления:\n" + "\n".join(deletion_errors) + "\n"
                if extraction_errors:
                    error_report += "Ошибки восстановления:\n" + "\n".join([f"{k}: {v}" for k, v in extraction_errors.items()])
                if error_report:
                    await message.answer("Восстановление завершено с ошибками:\n" + error_report)
                else:
                    await message.answer("Резервная копия успешно восстановлена без ошибок.")
                await message.answer("Бот полностью перезапускается... Ожидайте. Все системные сообщения будут выведены в лог.")
            except Exception as e:
                try:
                    await message.answer(f"Restore error: {e}")
                except Exception:
                    pass
            finally:
                await run_full_restart(message)

        else:
            await message.answer("Операция восстановления отменена. Возвращаюсь в меню настроек.", reply_markup=get_main_settings_keyboard())
    
    # Новая логика удаления резервных копий
    @dp.message_handler(lambda message: message.text == "Удаление полных резервных копий")
    async def delete_full_backups_menu(message: types.Message):
        uid = message.from_user.id
        full_backup_delete_mode[uid] = None
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Полное удаление", "Частичное удаление")
        kb.add("Возврат в настройки")
        await message.answer("Выберите режим удаления резервных копий:", reply_markup=kb)
    
    # Режим полного удаления
    @dp.message_handler(lambda message: message.text == "Полное удаление")
    async def full_delete_mode_handler(message: types.Message):
        uid = message.from_user.id
        full_backup_delete_mode[uid] = "full"
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        backup_folder = os.path.join(base_dir, "full_backups")
        if not os.path.isdir(backup_folder):
            await message.answer("Папка с резервными копиями не найдена.", reply_markup=get_main_settings_keyboard())
            return
        backups = [f for f in os.listdir(backup_folder) if f.lower().endswith(".zip")]
        if not backups:
            await message.answer("Нет резервных копий для удаления.", reply_markup=get_main_settings_keyboard())
            return
        backup_list = "\n".join(backups)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        await message.answer("Внимание! При полном удалении будут удалены все резервные копии:\n" + backup_list + "\nВы уверены?", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and full_backup_delete_mode.get(message.from_user.id) == "full")
    async def confirm_full_delete_handler(message: types.Message):
        uid = message.from_user.id
        if message.text == "Да":
            import __main__
            base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
            backup_folder = os.path.join(base_dir, "full_backups")
            try:
                shutil.rmtree(backup_folder)
                os.makedirs(backup_folder, exist_ok=True)
                await message.answer("Все резервные копии успешно удалены.", reply_markup=get_main_settings_keyboard())
            except Exception as e:
                await message.answer(f"Ошибка при полном удалении резервных копий: {e}", reply_markup=get_main_settings_keyboard())
        else:
            await message.answer("Операция отменена. Возвращаюсь в меню настроек.", reply_markup=get_main_settings_keyboard())
        full_backup_delete_mode.pop(uid, None)
    
    # Режим частичного удаления
    @dp.message_handler(lambda message: message.text == "Частичное удаление")
    async def partial_delete_mode_handler(message: types.Message):
        uid = message.from_user.id
        full_backup_delete_mode[uid] = "partial"
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        backup_folder = os.path.join(base_dir, "full_backups")
        if not os.path.isdir(backup_folder):
            await message.answer("Папка с резервными копиями не найдена.", reply_markup=get_main_settings_keyboard())
            return
        backups = [f for f in os.listdir(backup_folder) if f.lower().endswith(".zip")]
        if not backups:
            await message.answer("Нет резервных копий для удаления.", reply_markup=get_main_settings_keyboard())
            return
        partial_delete_selections[uid] = {}
        for backup in backups:
            partial_delete_selections[uid][backup] = False
        await display_partial_delete_keyboard(message)
    
    async def display_partial_delete_keyboard(message):
        uid = message.from_user.id
        selections = partial_delete_selections.get(uid, {})
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        if any(selections.values()):
            kb.add("Удалить")
        for backup, selected in selections.items():
            status = "выбрано" if selected else "не выбрано"
            kb.add(f"{backup} ({status})")
        kb.add("Возврат в настройки")
        await message.answer("Выберите резервные копии для удаления (нажмите для переключения статуса):", reply_markup=kb)
    
    @dp.message_handler(lambda message: (" (выбрано)" in message.text or " (не выбрано)" in message.text) and full_backup_delete_mode.get(message.from_user.id) == "partial")
    async def toggle_backup_selection(message):
        uid = message.from_user.id
        selections = partial_delete_selections.get(uid, {})
        backup_name = message.text.split(" (")[0]
        if backup_name in selections:
            selections[backup_name] = not selections[backup_name]
        await display_partial_delete_keyboard(message)
    
    @dp.message_handler(lambda message: message.text == "Удалить" and full_backup_delete_mode.get(message.from_user.id) == "partial")
    async def confirm_partial_delete(message):
        uid = message.from_user.id
        selections = partial_delete_selections.get(uid, {})
        selected_backups = [name for name, sel in selections.items() if sel]
        if not selected_backups:
            await message.answer("Ни одна резервная копия не выбрана.", reply_markup=get_main_settings_keyboard())
            full_backup_delete_mode.pop(uid, None)
            partial_delete_selections.pop(uid, None)
            return
        selected_list = "\n".join(selected_backups)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        await message.answer("Внимание! Будут удалены следующие резервные копии:\n" + selected_list + "\nВы уверены?", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and full_backup_delete_mode.get(message.from_user.id) == "partial")
    async def process_partial_delete_confirmation(message):
        uid = message.from_user.id
        if message.text == "Да":
            selections = partial_delete_selections.get(uid, {})
            selected_backups = [name for name, sel in selections.items() if sel]
            errors = []
            import __main__
            base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
            backup_folder = os.path.join(base_dir, "full_backups")
            for backup in selected_backups:
                backup_path = os.path.join(backup_folder, backup)
                try:
                    os.remove(backup_path)
                except Exception as e:
                    errors.append(f"{backup}: {str(e)}")
            if errors:
                await message.answer("Не удалось удалить следующие резервные копии:\n" + "\n".join(errors), reply_markup=get_main_settings_keyboard())
            else:
                await message.answer("Выбранные резервные копии успешно удалены.", reply_markup=get_main_settings_keyboard())
        else:
            await message.answer("Операция частичного удаления отменена. Возвращаюсь в меню настроек.", reply_markup=get_main_settings_keyboard())
        full_backup_delete_mode.pop(uid, None)
        partial_delete_selections.pop(uid, None)
    
    # Подменю "Память"
    @dp.message_handler(lambda message: message.text == "Память")
    async def memory_menu(message: types.Message):
        memory_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Занимаемое место на диске", "инфо. по содержимому раб.директории")
        kb.add("Полный отчет по рабочей директории", "Заним. место в RAM")
        kb.add("Возврат в настройки")
        await message.answer("Меню памяти:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text == "Занимаемое место на диске")
    async def disk_usage_handler(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        size_bytes = 0
        for dirpath, _, filenames in os.walk(base_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    size_bytes += os.path.getsize(fp)
        size_mb = size_bytes / (1024 * 1024)
        await message.answer(f"Размер рабочей директории: {size_mb:.2f} МБ.")
    
    @dp.message_handler(lambda message: message.text == "инфо. по содержимому раб.директории")
    async def info_specific_folders_handler(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        folders = ["python", "plugins_backup", "plugins"]
        response = "Сводка по указанным папкам:\n"
        for folder in folders:
            folder_path = os.path.join(base_dir, folder)
            if os.path.isdir(folder_path):
                s, count = folder_summary(folder_path)
                response += f"\n{folder}:\n  Размер: {get_human_readable_size(s)}\n  Файлов: {count}"
            else:
                response += f"\n{folder}: папка не найдена."
        await message.answer(response)
    
    @dp.message_handler(lambda message: message.text == "Полный отчет по рабочей директории")
    async def full_directory_handler(message: types.Message):
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        summary = full_directory_summary(base_dir)
        if len(summary) > 4000:
            for i in range(0, len(summary), 4000):
                await message.answer(summary[i:i+4000])
        else:
            await message.answer(summary)
    
    @dp.message_handler(lambda message: message.text == "Заним. место в RAM")
    async def ram_usage_handler(message: types.Message):
        process = psutil.Process(os.getpid())
        used_bytes = process.memory_info().rss
        used_mb = used_bytes / (1024 * 1024)
        used_gb = used_mb / 1024
        vm = psutil.virtual_memory()
        total_gb = vm.total / (1024 ** 3)
        available_gb = vm.available / (1024 ** 3)
        total_mb = vm.total / (1024 * 1024)
        available_mb = vm.available / (1024 * 1024)
        response = (f"Память бота: {used_mb:.2f} МБ ({used_gb:.2f} ГБ)\n"
                    f"Общая память системы: {total_mb:.2f} МБ ({total_gb:.2f} ГБ)\n"
                    f"Использовано: {vm.used / (1024 * 1024):.2f} МБ, свободно: {available_mb:.2f} МБ\n"
                    f"Загрузка памяти: {vm.percent}%")
        await message.answer(response)
    
    # Подменю "Сброс"
    @dp.message_handler(lambda message: message.text == "Сброс")
    async def reset_menu_handler(message: types.Message):
        reset_mode[message.from_user.id] = ""
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Сбросить все настройки и удалить папки", "Выборочное удаление")
        kb.add("Возврат в настройки")
        await message.answer("Меню сброса:\nВыберите режим сброса:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text == "Сбросить все настройки и удалить папки" and reset_mode.get(message.from_user.id, "") != "selective")
    async def reset_all_handler(message: types.Message):
        reset_mode[message.from_user.id] = "full"
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        info_text = ("Внимание! При сбросе будут удалены все файлы и папки в рабочей директории, "
                             "кроме: config.ini, python.zip и исполняемого файла.\n" 
                             "Также файл credentials.ini будет удалён.\n" 
                             "После этого бот автоматически перезапустится, а папки будут восстановлены пустыми.\n" 
                             "Вы уверены?")
        await message.answer(info_text, reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and reset_mode.get(message.from_user.id, "") == "full")
    async def reset_all_confirmation(message: types.Message):
        if message.text == "Да":
            try:
                try:
                    _get_full_restart_from_message()
                except Exception:
                    pass
                import __main__
                base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
                errors = reset_all_working_dir(base_dir)
                if errors:
                    report_text = "Не удалось удалить следующие элементы: " + ", ".join(errors)
                else:
                    report_text = "Все элементы успешно удалены."
                await message.answer(report_text + "\nБот перезапускается...")
            except Exception as e:
                try:
                    await message.answer(f"Reset error: {e}")
                except Exception:
                    pass
            finally:
                await run_full_restart(message)
        else:
            reset_mode.pop(message.from_user.id, None)
            await message.answer("Операция сброса отменена.", reply_markup=get_main_settings_keyboard())
    
    @dp.message_handler(lambda message: message.text == "Выборочное удаление" and reset_mode.get(message.from_user.id, "") != "full")
    async def selective_deletion_menu(message: types.Message):
        reset_mode[message.from_user.id] = "selective"
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        selective_deletion[message.from_user.id] = {}
        items = os.listdir(base_dir)
        exe_name = get_exe_name()
        filtered = [item for item in items if (item != exe_name and item.lower() not in ("credentials.ini", ZIP_NAME.lower()))]
        for item in filtered:
            selective_deletion[message.from_user.id][item] = False
        await update_selective_keyboard(message)
    
    async def update_selective_keyboard(message):
        uid = message.from_user.id
        selections = selective_deletion.get(uid, {})
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        if any(selections.values()):
            kb.add("Удалить")
        for item, selected in selections.items():
            status = "выбрано" if selected else "не выбрано"
            kb.add(f"{item} ({status})")
        kb.add("Возврат в настройки")
        await message.answer("Выберите элементы для удаления:", reply_markup=kb)
    
    @dp.message_handler(lambda message: reset_mode.get(message.from_user.id, "") == "selective" and " (" in message.text and message.text not in ["Удалить", "Возврат в настройки"])
    async def toggle_selective_item(message: types.Message):
        uid = message.from_user.id
        selections = selective_deletion.get(uid, {})
        text = message.text
        if " (" in text:
            item = text.split(" (")[0]
            if item in selections:
                selections[item] = not selections[item]
        await update_selective_keyboard(message)
    
    @dp.message_handler(lambda message: message.text == "Удалить" and reset_mode.get(message.from_user.id, "") == "selective")
    async def confirm_selective_deletion(message: types.Message):
        uid = message.from_user.id
        selections = selective_deletion.get(uid, {})
        items_to_delete = [item for item, sel in selections.items() if sel]
        if not items_to_delete:
            await message.answer("Ни один элемент не выбран.")
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        info = "Будут удалены следующие элементы:\n" + "\n".join(items_to_delete) + "\nВы уверены?"
        await message.answer(info, reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text in ["Да", "Нет"] and reset_mode.get(message.from_user.id, "") == "selective")
    async def selective_deletion_confirmation(message: types.Message):
        uid = message.from_user.id
        if message.text == "Да":
            import __main__
            base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
            selections = selective_deletion.get(uid, {})
            items_to_delete = [item for item, sel in selections.items() if sel]
            selective_delete(base_dir, items_to_delete)
            await message.answer("Выбранные элементы удалены.")
            reset_mode.pop(uid, None)
            selective_deletion.pop(uid, None)
            await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())
        else:
            reset_mode.pop(uid, None)
            selective_deletion.pop(uid, None)
            await message.answer("Операция выборочного удаления отменена.", reply_markup=get_main_settings_keyboard())
    
    @dp.message_handler(lambda message: message.text == "Возврат в настройки" and message.from_user.id in reset_mode)
    async def reset_back(message: types.Message):
        uid = message.from_user.id
        reset_mode.pop(uid, None)
        if uid in selective_deletion:
            selective_deletion.pop(uid, None)
        await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())
    
    # Подменю "Система"
    @dp.message_handler(lambda message: message.text == "Система")
    async def system_menu_handler(message: types.Message):
        system_mode[message.from_user.id] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Проверка системы", "Проверка целостности")
        kb.add("Переустановить python")
        kb.add("Полный перезапуск", "Возврат в настройки")
        await message.answer("Меню системы:", reply_markup=kb)
    
    @dp.message_handler(lambda message: message.text == "Проверка системы" and system_mode.get(message.from_user.id, False))
    async def system_check_handler(message: types.Message):
        try:
            import __main__
            base_python = __main__.get_base_python_exe()
        except Exception as e:
            await message.answer(f"Ошибка получения Python: {e}")
            return
        try:
            version_proc = subprocess.run([base_python, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            version_info = version_proc.stdout.strip() if version_proc.stdout else version_proc.stderr.strip()
            freeze_proc = subprocess.run([base_python, "-m", "pip", "freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            freeze_info = freeze_proc.stdout.strip() if freeze_proc.stdout else freeze_proc.stderr.strip()
            response = f"Версия Python: {version_info}\nУстановленные пакеты:\n{freeze_info}"
        except Exception as e:
            response = f"Ошибка проверки системы: {e}"
        await message.answer(response)
    
    @dp.message_handler(lambda message: message.text == "Проверка целостности" and system_mode.get(message.from_user.id, False))
    async def integrity_check_handler(message: types.Message):
        try:
            import __main__
            base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
            zip_path = find_zip_file()
            # Проверка архива
            if zip_path:
                msg = f"Архив Python.zip найден: {zip_path}"
            else:
                msg = "Архив Python.zip не найден ни в одной из проверяемых локаций."
            messages = [msg]
            # Проверка папки python
            python_folder = os.path.join(base_dir, TARGET_FOLDER)
            if os.path.isdir(python_folder):
                messages.append(f"Папка python существует: {python_folder}")
                exe = os.path.join(python_folder, "python.exe") if os.name == 'nt' else os.path.join(python_folder, "bin", "python")
                if os.path.exists(exe):
                    messages.append(f"Исполняемый файл найден: {exe}")
                else:
                    messages.append(f"Исполняемый файл не найден в папке python: {exe}")
            else:
                messages.append("Папка python не найдена.")
            # Формируем результат
            errors = [m for m in messages if "не найден" in m]
            if not errors:
                result_text = "Проверка целостности пройдена успешно. Все необходимые элементы на месте.\n" + "\n".join(messages)
            else:
                result_text = "Обнаружены проблемы при проверке целостности:\n" + "\n".join(messages)
            await message.answer(result_text)
        except Exception as e:
            await message.answer(f"Ошибка проверки целостности: {e}")


    @dp.message_handler(lambda message: message.text == "Переустановить python" and system_mode.get(message.from_user.id, False))
    async def reinstall_python_handler(message: types.Message):
        await message.answer("Начинается переустановка Python...")
        # Ищем архив с Python
        zip_path = find_zip_file()
        if not zip_path:
            await message.answer(f"Не найден файл {ZIP_NAME}")
            return
        import __main__
        base_dir = getattr(__main__, "base_dir", get_base_dir_fallback())
        python_folder = os.path.join(base_dir, TARGET_FOLDER)
        # Удаляем старую папку python
        if os.path.exists(python_folder):
            shutil.rmtree(python_folder)
            await message.answer("Старая папка python удалена.")
        os.makedirs(python_folder, exist_ok=True)
        # Распаковываем архив
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                files = zip_ref.namelist()
                total_files = len(files)
                next_threshold = 5
                for i, file in enumerate(files, start=1):
                    zip_ref.extract(member=file, path=python_folder)
                    percent = (i / total_files) * 100
                    if percent >= next_threshold:
                        await message.answer(f"Извлечено {int(next_threshold)}% файлов...")
                        next_threshold += 5
            await message.answer("Переустановка Python завершена.")
        except Exception as e:
            await message.answer(f"Ошибка переустановки Python: {e}")

    @dp.message_handler(lambda message: message.text == "Полный перезапуск" and system_mode.get(message.from_user.id, False))
    async def full_restart_handler(message: types.Message):
        try:
            await message.answer("Бот полностью перезапускается... Ожидайте. Все системные сообщения будут выведены в лог.")
        finally:
            await run_full_restart(message)

    
    @dp.message_handler(lambda message: message.text == "Возврат в настройки" and system_mode.get(message.from_user.id, False))
    async def system_back_handler(message: types.Message):
        system_mode.pop(message.from_user.id, None)
        await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())


    # Обработчик 'Возврат в настройки' в подменю 'Память'
    @dp.message_handler(lambda message: message.text == "Возврат в настройки" and memory_mode.get(message.from_user.id, False))
    async def memory_back_handler(message: types.Message):
        memory_mode.pop(message.from_user.id, None)
        await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())

    # Обработчик 'Возврат в настройки' в подменю 'Резервное копирование и восстановление'
    @dp.message_handler(lambda message: message.text == "Возврат в настройки" and backup_restore_mode.get(message.from_user.id, False))
    async def backup_restore_back_handler(message: types.Message):
        backup_restore_mode.pop(message.from_user.id, None)
        full_backup_delete_mode.pop(message.from_user.id, None)
        partial_delete_selections.pop(message.from_user.id, None)
        restore_pending.pop(message.from_user.id, None)
        await message.answer("Меню настроек:", reply_markup=get_main_settings_keyboard())
