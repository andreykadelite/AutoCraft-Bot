import os
import re
import shutil
import datetime
import zipfile
from aiogram import types
from keymenu import create_plugins_ext_menu, backup_main_keyboard, create_list_keyboard, get_additional_keyboard
import stat
import sys
import gc
import subprocess
import threading

# ------------------------ Глобальные состояния ------------------------
# Режимы для работы с плагинами
delete_mode = {}
autostart_mode = {}

# Дополнительные состояния для удаления плагинов
deletion_pending = {}     # Хранит выбранный плагин для удаления (по user_id)
deletion_sub_mode = {}    # Подрежим удаления: "select" или "confirm"

# Новый режим сброса настроек плагинов
reset_mode = {}           # True, если пользователь находится в режиме сброса настроек плагинов
reset_confirm_pending = {}  # Для подтверждения: {user_id: ("all",) или ("individual", plugin)}

# Новый режим скачивания плагина
download_mode = {}        # Флаг режима скачивания плагина (по user_id)

# Режим установки плагина из ZIP-архива
zip_install_mode = {}     # Флаг установки через ZIP (по user_id)
zip_uploaded = {}         # Путь к полученному ZIP (по user_id)
zip_checked = {}          # Путь к распакованной и проверенной директории плагина
zip_original_name = {}    # Оригинальное имя загруженного ZIP-файла (по user_id)

# Режим работы с резервными копиями
backup_menu_mode = {}           # Флаг режима резервных копий (по user_id)
backup_sub_mode = {}            # Подрежим резервных операций: "create", "restore", "clear" или None
backup_restore_pending = {}     # Для подтверждения восстановления: {user_id: (backup_file_path, plugin_name)}
backup_clear_pending = {}       # Флаг ожидания подтверждения очистки резервных копий (по user_id)

# ------------------------ Каталоги ------------------------
PLUGIN_DIR = os.path.join(os.getcwd(), "plugins")
BACKUP_DIR = os.path.join(os.getcwd(), "plugins_backup")
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(PLUGIN_DIR, exist_ok=True)

# ------------------------ Вспомогательные функции ------------------------

def force_rmtree(path):
    """
    Удаляет директорию, используя shutil.rmtree с onerror, который пытается изменить права доступа.
    Это позволяет удалить папку даже если некоторые файлы защищены.
    """
    def onerror(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass
    if os.path.exists(path):
        shutil.rmtree(path, onerror=onerror)

def unload_plugin_modules(plugin_name):
    """
    Выгружает из памяти все модули, имена которых начинаются с plugin_name + '_'.
    Это позволяет корректно снять ссылки на плагин, чтобы его виртуальное окружение можно было удалить.
    Возвращает список удалённых модулей.
    """
    unloaded = []
    prefix = plugin_name + "_"
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(prefix):
            del sys.modules[mod_name]
            unloaded.append(mod_name)
    gc.collect()
    return unloaded

def get_plugins_list():
    """
    Возвращает список названий папок (плагинов) из каталога PLUGIN_DIR.
    """
    return [item for item in os.listdir(PLUGIN_DIR)
            if os.path.isdir(os.path.join(PLUGIN_DIR, item))]


def extract_plugin_name_from_backup(backup_base):
    """
    В данном варианте имя резервной копии совпадает с именем плагина.
    """
    return backup_base

def reset_plugin_settings(plugin_folder):
    """
    Сбрасывает настройки плагина:
      – Выгружает модули плагина из памяти.
      – Удаляет виртуальное окружение (папку "venv"), даже если оно занято.
      – Удаляет стандартные папки, которые могли быть созданы плагином (например, "лог", "cache", "temp").
      – Удаляет файлы .pyc.
    Возвращает список служебных сообщений об удалённых объектах.
    """
    messages = []
    plugin_name = os.path.basename(plugin_folder)
    unloaded = unload_plugin_modules(plugin_name)
    if unloaded:
        messages.append(f"Выгружены модули: {', '.join(unloaded)}")
    venv_path = os.path.join(plugin_folder, "venv")
    if os.path.isdir(venv_path):
        try:
            force_rmtree(venv_path)
            messages.append(f"Удалено виртуальное окружение: {venv_path}")
        except Exception as e:
            messages.append(f"Не удалось удалить виртуальное окружение {venv_path}: {e}")
    for sub in ["лог", "cache", "temp"]:
        sub_path = os.path.join(plugin_folder, sub)
        if os.path.isdir(sub_path):
            try:
                force_rmtree(sub_path)
                messages.append(f"Удалена папка: {sub_path}")
            except Exception as e:
                messages.append(f"Не удалось удалить папку {sub_path}: {e}")
    removed_files = []
    for root, _, files in os.walk(plugin_folder):
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                    removed_files.append(f)
                except Exception:
                    pass
    if removed_files:
        messages.append(f"Удалены .pyc файлы: {', '.join(removed_files)}")
    return messages

def perform_full_restart():
    """
    Полностью перезапускает бот, как это делают опытные разработчики:
    1) Все фоновые (неосновные) потоки помечаются как daemon, чтобы они не блокировали завершение.
    2) Порождается новый процесс с теми же аргументами (через subprocess.Popen).
    3) Текущий процесс принудительно завершается через os._exit(0).
    Такой подход гарантирует полный перезапуск даже при наличии не-демоновых потоков.
    """
    # Помечаем все потоки, кроме текущего, как daemon
    current = threading.current_thread()
    for t in threading.enumerate():
        if t is not current:
            try:
                t.daemon = True
            except Exception:
                pass

    sys.stdout.flush()
    sys.stderr.flush()
    exe = sys.executable
    if not os.path.isfile(exe):
        exe = sys.argv[0]
    if not os.path.isfile(exe):
        print("Не удалось определить исполняемый файл для перезапуска.")
        os._exit(1)
    try:
        subprocess.Popen([exe] + sys.argv[1:])
        os._exit(0)
    except Exception as e:
        print(f"Не удалось выполнить перезапуск: {e}")
        os._exit(1)

# Попытка импортировать функции автозапуска из главного модуля
try:
    from __main__ import scan_available_plugins, load_autostart_config, save_autostart_config
except ImportError:
    def scan_available_plugins():
        return {}
    def load_autostart_config():
        return []
    def save_autostart_config(config):
        pass

# ------------------------ Регистрация обработчиков ------------------------
def register_handlers(dp):
    """
    Регистрирует обработчики дополнительного функционала в меню плагинов.
    Импортируйте этот модуль и вызовите register_handlers(dp) в основном файле 6.5.py.
    """

    # ===== Установка плагина из ZIP-архива =====
    @dp.message_handler(lambda m: m.text == "Установка плагинов")
    async def zip_installation_menu(message: types.Message):
        uid = message.from_user.id
        zip_install_mode[uid] = True
        zip_uploaded[uid] = None
        zip_checked[uid] = None
        await message.answer(
            "Режим установки плагина активирован.\nОтправьте ZIP-архив и затем нажмите «Проверить» или «Отмена».",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True)
                .add(types.KeyboardButton("Проверить"))
                .add(types.KeyboardButton("Отмена"))
        )

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def receive_zip_plugin(message: types.Message):
        uid = message.from_user.id
        if not zip_install_mode.get(uid, False):
            return
        doc = message.document
        if not doc.file_name.lower().endswith('.zip'):
            await message.answer("Ошибка: отправленный файл не является ZIP-архивом.")
            return
        temp_dir = os.path.join(os.getcwd(), "temp_plugins")
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, f"{uid}_{doc.file_name}")
        await message.document.download(destination=file_path)
        zip_uploaded[uid] = file_path
        zip_original_name[uid] = doc.file_name
        await message.answer("ZIP-архив получен. Нажмите «Проверить» для проверки содержимого.")

    @dp.message_handler(lambda m: zip_install_mode.get(m.from_user.id, False) and m.text == "Проверить")
    async def check_zip_plugin(message: types.Message):
        uid = message.from_user.id
        if not zip_uploaded.get(uid):
            await message.answer("Ошибка: сначала отправьте ZIP-архив плагина.")
            return
        file_path = zip_uploaded[uid]
        try:
            extract_dir = os.path.join(os.getcwd(), "temp_plugins", f"extracted_{uid}")
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(file_path, 'r') as zf:
                zf.extractall(extract_dir)
            def find_root(path):
                while True:
                    ents = os.listdir(path)
                    if len(ents) == 1 and os.path.isdir(os.path.join(path, ents[0])):
                        path = os.path.join(path, ents[0])
                    else:
                        break
                return path
            plugin_root = find_root(extract_dir)
            valid = any(fname.endswith(".py") for _, _, files in os.walk(plugin_root) for fname in files)
            if not valid:
                await message.answer("Ошибка: в архиве не найдено ни одного .py файла.")
                return
            zip_checked[uid] = plugin_root
            await message.answer(
                "Проверка архива пройдена успешно.\nНажмите «Установить» для установки плагина или «Отмена» для отмены.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True)
                    .add(types.KeyboardButton("Установить"))
                    .add(types.KeyboardButton("Отмена"))
            )
        except Exception as e:
            await message.answer(f"Ошибка при проверке архива: {e}")

    @dp.message_handler(lambda m: zip_install_mode.get(m.from_user.id, False) and m.text == "Установить")
    async def install_zip_plugin(message: types.Message):
        uid = message.from_user.id
        plugin_root = zip_checked.get(uid)
        if not plugin_root:
            await message.answer("Ошибка: сначала пройдите проверку архива (нажмите «Проверить»).")
            return
        plugin_name = os.path.splitext(zip_original_name.get(uid, os.path.basename(plugin_root)))[0]
        target = os.path.join(PLUGIN_DIR, plugin_name)
        if os.path.exists(target):
            await message.answer(f"Ошибка: плагин «{plugin_name}» уже установлен.")
            return
        try:
            shutil.move(plugin_root, target)
            await message.answer(f"Плагин «{plugin_name}» успешно установлен и запущен.", reply_markup=create_plugins_ext_menu())
        except Exception as e:
            await message.answer(f"Ошибка установки плагина: {e}")
        zip_install_mode[uid] = False
        zip_uploaded.pop(uid, None)
        zip_checked.pop(uid, None)
        zip_original_name.pop(uid, None)

    @dp.message_handler(lambda m: zip_install_mode.get(m.from_user.id, False) and m.text == "Отмена")
    async def cancel_zip_install(message: types.Message):
        uid = message.from_user.id
        if zip_uploaded.get(uid):
            try:
                os.remove(zip_uploaded[uid])
            except Exception:
                pass
        zip_install_mode[uid] = False
        zip_uploaded.pop(uid, None)
        zip_checked.pop(uid, None)
        zip_original_name.pop(uid, None)
        await message.answer("Установка плагина отменена.", reply_markup=create_plugins_ext_menu())

    # ===== Режим сброса настроек плагинов =====
    @dp.message_handler(lambda m: m.text == "Сброс настроек плагинов")
    async def reset_plugins_menu(message: types.Message):
        uid = message.from_user.id
        reset_mode[uid] = True
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(types.KeyboardButton("Сбросить все настройки плагинов по умолчанию"))
        kb.add(types.KeyboardButton("Сброс настроек отдельных плагинов"))
        kb.add(types.KeyboardButton("Назад"))
        await message.answer("Режим сброса настроек плагинов активирован.\nВыберите действие:", reply_markup=kb)

    @dp.message_handler(lambda m: reset_mode.get(m.from_user.id, False) and m.text == "Сбросить все настройки плагинов по умолчанию")
    async def confirm_reset_all_prompt(message: types.Message):
        uid = message.from_user.id
        reset_confirm_pending[uid] = ("all",)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(types.KeyboardButton("Да"), types.KeyboardButton("Нет"))
        await message.answer("Вы уверены, что хотите сбросить все настройки плагинов по умолчанию?", reply_markup=kb)

    @dp.message_handler(lambda m: reset_mode.get(m.from_user.id, False) and m.text == "Сброс настроек отдельных плагинов")
    async def reset_individual_menu(message: types.Message):
        uid = message.from_user.id
        plugins = get_plugins_list()
        if not plugins:
            await message.answer("Нет установленных плагинов для сброса настроек.", reply_markup=create_plugins_ext_menu())
            reset_mode[uid] = False
            return
        kb = create_list_keyboard(plugins)
        await message.answer("Выберите плагин для сброса настроек:", reply_markup=kb)

    @dp.message_handler(lambda m: reset_mode.get(m.from_user.id, False) and m.text not in [
        "Назад",
        "Сбросить все настройки плагинов по умолчанию",
        "Сброс настроек отдельных плагинов",
        "Да",
        "Нет"
    ])
    async def confirm_reset_individual_prompt(message: types.Message):
        uid = message.from_user.id
        plugin = message.text
        if not os.path.isdir(os.path.join(PLUGIN_DIR, plugin)):
            await message.answer(f"Плагин «{plugin}» не найден.", reply_markup=create_plugins_ext_menu())
            reset_mode[uid] = False
            return
        reset_confirm_pending[uid] = ("individual", plugin)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(types.KeyboardButton("Да"), types.KeyboardButton("Нет"))
        await message.answer(f"Вы уверены, что хотите сбросить настройки плагина «{plugin}»?", reply_markup=kb)

    @dp.message_handler(lambda m: reset_mode.get(m.from_user.id, False) and m.text in ["Да", "Нет"] and m.from_user.id in reset_confirm_pending)
    async def process_reset_confirmation(message: types.Message):
        uid = message.from_user.id
        confirmation = message.text
        op = reset_confirm_pending.pop(uid)
        if confirmation == "Да":
            if op[0] == "all":
                plugins = get_plugins_list()
                details = []
                for plugin in plugins:
                    folder = os.path.join(PLUGIN_DIR, plugin)
                    msgs = reset_plugin_settings(folder)
                    details.append(f"{plugin}: " + "; ".join(msgs) if msgs else f"{plugin}: нет изменений")
                await message.answer("Сброс настроек всех плагинов выполнен:\n" + "\n".join(details),
                                     reply_markup=create_plugins_ext_menu())
            elif op[0] == "individual":
                plugin = op[1]
                target = os.path.join(PLUGIN_DIR, plugin)
                msgs = reset_plugin_settings(target)
                msg_detail = "; ".join(msgs) if msgs else "нет изменений"
                await message.answer(f"Сброс настроек плагина «{plugin}» выполнен: {msg_detail}",
                                     reply_markup=create_plugins_ext_menu())
        else:
            await message.answer("Операция сброса настроек отменена.", reply_markup=create_plugins_ext_menu())
        reset_mode[uid] = False

    @dp.message_handler(lambda m: reset_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def reset_mode_back(message: types.Message):
        uid = message.from_user.id
        reset_mode[uid] = False
        await message.answer("Режим сброса настроек плагинов отменён.", reply_markup=create_plugins_ext_menu())

    # ===== Режим удаления плагинов =====
    @dp.message_handler(lambda m: m.text == "Удаление плагинов")
    async def deletion_menu(message: types.Message):
        uid = message.from_user.id
        delete_mode[uid] = True
        deletion_sub_mode[uid] = "select"
        deletion_pending[uid] = None
        plugins = get_plugins_list()
        if not plugins:
            await message.answer("Нет плагинов для удаления.", reply_markup=create_plugins_ext_menu())
            delete_mode[uid] = False
            deletion_sub_mode[uid] = None
            return
        kb = create_list_keyboard(plugins)
        await message.answer("Выберите плагин для удаления:", reply_markup=kb)

    @dp.message_handler(lambda m: delete_mode.get(m.from_user.id, False) and deletion_sub_mode.get(m.from_user.id) == "select" and m.text != "Назад")
    async def deletion_plugin_selected(message: types.Message):
        uid = message.from_user.id
        plugin = message.text
        deletion_pending[uid] = plugin
        deletion_sub_mode[uid] = "confirm"
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(types.KeyboardButton("Удалить без резервной копии"))
        kb.add(types.KeyboardButton("Сделать резервную копию и удалить"))
        kb.add(types.KeyboardButton("Отмена"))
        await message.answer(f"Плагин «{plugin}» выбран для удаления.\nВыберите вариант действия:", reply_markup=kb)

    @dp.message_handler(lambda m: delete_mode.get(m.from_user.id, False) and deletion_sub_mode.get(m.from_user.id) == "confirm")
    async def deletion_confirmation(message: types.Message):
        uid = message.from_user.id
        option = message.text
        plugin = deletion_pending.get(uid)
        target = os.path.join(PLUGIN_DIR, plugin)
        unloaded = unload_plugin_modules(plugin)
        if unloaded:
            await message.answer(f"Выгружены модули плагина: {', '.join(unloaded)}")
        if option == "Удалить без резервной копии":
            if os.path.isdir(target):
                try:
                    force_rmtree(target)
                    await message.answer(f"Плагин «{plugin}» удалён без резервной копии.", reply_markup=create_plugins_ext_menu())
                except Exception as e:
                    await message.answer(f"Ошибка удаления плагина: {e}", reply_markup=create_plugins_ext_menu())
            else:
                await message.answer(f"Плагин «{plugin}» не найден.", reply_markup=create_plugins_ext_menu())
        elif option == "Сделать резервную копию и удалить":
            if os.path.isdir(target):
                backup_filename = f"{plugin}.zip"
                backup_path = os.path.join(BACKUP_DIR, backup_filename)
                try:
                    shutil.make_archive(backup_path[:-4], 'zip', root_dir=target)
                    force_rmtree(target)
                    await message.answer(f"Резервная копия плагина «{plugin}» создана, и плагин удалён.\nФайл: {backup_filename}", reply_markup=create_plugins_ext_menu())
                except Exception as e:
                    await message.answer(f"Ошибка создания резервной копии и удаления плагина: {e}", reply_markup=create_plugins_ext_menu())
            else:
                await message.answer(f"Плагин «{plugin}» не найден.", reply_markup=create_plugins_ext_menu())
        else:
            await message.answer("Операция удаления плагина отменена.", reply_markup=create_plugins_ext_menu())
        delete_mode[uid] = False
        deletion_sub_mode[uid] = None
        deletion_pending.pop(uid, None)

    @dp.message_handler(lambda m: delete_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def deletion_menu_back(message: types.Message):
        uid = message.from_user.id
        delete_mode[uid] = False
        deletion_sub_mode[uid] = None
        deletion_pending.pop(uid, None)
        await message.answer("Режим удаления плагинов отменён.", reply_markup=create_plugins_ext_menu())

    # ===== Режим работы с резервными копиями =====
    @dp.message_handler(lambda m: m.text == "Резервные копии")
    async def backup_main_menu_handler(message: types.Message):
        uid = message.from_user.id
        backup_menu_mode[uid] = True
        backup_sub_mode[uid] = None
        await message.answer("Режим резервных копий активирован.", reply_markup=backup_main_keyboard())

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False) and m.text == "Сделать резервную копию")
    async def backup_create_menu(message: types.Message):
        uid = message.from_user.id
        backup_sub_mode[uid] = "create"
        plugins = get_plugins_list()
        if not plugins:
            await message.answer("Нет плагинов для создания резервной копии.", reply_markup=backup_main_keyboard())
            backup_sub_mode[uid] = None
            return
        kb = create_list_keyboard(plugins)
        await message.answer("Выберите плагин для резервного копирования:", reply_markup=kb)

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False)
                                 and backup_sub_mode.get(m.from_user.id) == "create"
                                 and m.text not in ["Назад", "Да", "Нет"])
    async def process_backup_creation(message: types.Message):
        uid = message.from_user.id
        plugin = message.text
        target = os.path.join(PLUGIN_DIR, plugin)
        if not os.path.isdir(target):
            await message.answer(f"Плагин «{plugin}» не найден.", reply_markup=backup_main_keyboard())
            backup_sub_mode[uid] = None
            return
        backup_filename = f"{plugin}.zip"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        try:
            shutil.make_archive(backup_path[:-4], 'zip', root_dir=target)
            await message.answer(f"Резервная копия плагина «{plugin}» успешно создана.\nФайл: {backup_filename}", reply_markup=backup_main_keyboard())
        except Exception as e:
            await message.answer(f"Ошибка резервного копирования: {e}", reply_markup=backup_main_keyboard())
        backup_sub_mode[uid] = None

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False) and m.text == "Восстановить из резервной копии")
    async def backup_restore_menu(message: types.Message):
        uid = message.from_user.id
        backup_sub_mode[uid] = "restore"
        files = [f for f in os.listdir(BACKUP_DIR) if f.lower().endswith(".zip")]
        if not files:
            await message.answer("Нет резервных копий.", reply_markup=backup_main_keyboard())
            backup_sub_mode[uid] = None
            return
        kb = create_list_keyboard([f[:-4] for f in files])
        await message.answer("Выберите резервную копию для восстановления:", reply_markup=kb)

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False)
                                 and backup_sub_mode.get(m.from_user.id) == "restore"
                                 and m.text not in ["Назад", "Да", "Нет"])
    async def process_backup_restore(message: types.Message):
        uid = message.from_user.id
        backup_base = message.text.strip()
        backup_file = backup_base + ".zip"
        backup_file_path = os.path.join(BACKUP_DIR, backup_file)
        if not os.path.isfile(backup_file_path):
            await message.answer("Резервная копия не найдена.", reply_markup=backup_main_keyboard())
            backup_sub_mode[uid] = None
            return
        plugin = extract_plugin_name_from_backup(backup_base)
        target = os.path.join(PLUGIN_DIR, plugin)
        if os.path.exists(target):
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add("Да", "Нет")
            await message.answer(f"Плагин «{plugin}» уже существует и будет заменён.\nПодтвердите замену:", reply_markup=kb)
            backup_restore_pending[uid] = (backup_file_path, plugin)
            return
        try:
            with zipfile.ZipFile(backup_file_path, 'r') as zf:
                extract_dir = os.path.join(os.getcwd(), f"temp_plugins/restore_{uid}")
                if os.path.exists(extract_dir):
                    shutil.rmtree(extract_dir)
                os.makedirs(extract_dir, exist_ok=True)
                zf.extractall(extract_dir)
            def find_root(path):
                while True:
                    ents = os.listdir(path)
                    if len(ents) == 1 and os.path.isdir(os.path.join(path, ents[0])):
                        path = os.path.join(path, ents[0])
                    else:
                        break
                return path
            plugin_root = find_root(extract_dir)
            shutil.move(plugin_root, target)
            await message.answer(f"Плагин «{plugin}» успешно восстановлен.", reply_markup=backup_main_keyboard())
        except Exception as e:
            await message.answer(f"Ошибка при восстановлении плагина «{plugin}»: {e}", reply_markup=backup_main_keyboard())
        backup_sub_mode[uid] = None

    @dp.message_handler(lambda m: backup_restore_pending.get(m.from_user.id) is not None)
    async def backup_restore_confirmation(message: types.Message):
        uid = message.from_user.id
        if message.text not in ["Да", "Нет"]:
            return
        backup_file_path, plugin = backup_restore_pending.get(uid)
        if message.text == "Да":
            target = os.path.join(PLUGIN_DIR, plugin)
            try:
                if os.path.exists(target):
                    shutil.rmtree(target)
                with zipfile.ZipFile(backup_file_path, 'r') as zf:
                    extract_dir = os.path.join(os.getcwd(), f"temp_plugins/restore_{uid}")
                    if os.path.exists(extract_dir):
                        shutil.rmtree(extract_dir)
                    os.makedirs(extract_dir, exist_ok=True)
                    zf.extractall(extract_dir)
                def find_root(path):
                    while True:
                        ents = os.listdir(path)
                        if len(ents) == 1 and os.path.isdir(os.path.join(path, ents[0])):
                            path = os.path.join(path, ents[0])
                        else:
                            break
                    return path
                plugin_root = find_root(extract_dir)
                shutil.move(plugin_root, target)
                await message.answer(f"Плагин «{plugin}» успешно восстановлен.", reply_markup=backup_main_keyboard())
            except Exception as e:
                await message.answer(f"Ошибка при восстановлении плагина «{plugin}»: {e}", reply_markup=backup_main_keyboard())
        else:
            await message.answer("Восстановление отменено.", reply_markup=backup_main_keyboard())
        backup_restore_pending.pop(uid, None)
        backup_sub_mode[uid] = None

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False) and m.text == "Очистить резервные копии")
    async def backup_clear_menu(message: types.Message):
        uid = message.from_user.id
        backup_sub_mode[uid] = "clear"
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Да", "Нет")
        await message.answer("Внимание! Все резервные копии будут удалены. Вы уверены?", reply_markup=kb)

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False)
                                 and backup_sub_mode.get(m.from_user.id) == "clear"
                                 and m.text in ["Да", "Нет"])
    async def backup_clear_confirmation(message: types.Message):
        uid = message.from_user.id
        if message.text == "Да":
            try:
                for f in os.listdir(BACKUP_DIR):
                    if f.lower().endswith(".zip"):
                        os.remove(os.path.join(BACKUP_DIR, f))
                await message.answer("Все резервные копии успешно удалены.", reply_markup=backup_main_keyboard())
            except Exception as e:
                await message.answer(f"Ошибка очистки резервных копий: {e}", reply_markup=backup_main_keyboard())
        else:
            await message.answer("Очистка резервных копий отменена.", reply_markup=backup_main_keyboard())
        backup_sub_mode[uid] = None

    @dp.message_handler(lambda m: backup_menu_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def backup_menu_back(message: types.Message):
        uid = message.from_user.id
        backup_menu_mode[uid] = False
        backup_sub_mode[uid] = None
        await message.answer("Возвращаюсь в меню плагинов.", reply_markup=create_plugins_ext_menu())

    # ===== Режим настройки автозапуска плагинов =====
    @dp.message_handler(lambda m: m.text == "Настроить автозапуск")
    async def configure_autostart(message: types.Message):
        uid = message.from_user.id
        autostart_mode[uid] = True
        available = scan_available_plugins()
        autostart = load_autostart_config()
        if not available:
            await message.answer("Нет плагинов для автозапуска.", reply_markup=create_plugins_ext_menu())
            autostart_mode[uid] = False
            return
        buttons = [f"{info['meta'].get('name', key)} [{'Вкл' if key in autostart else 'Выкл'}]" for key, info in available.items()]
        kb = create_list_keyboard(buttons)
        await message.answer("Режим автозапуска активирован.\nНажмите на плагин для переключения его статуса.", reply_markup=kb)

    @dp.message_handler(lambda m: autostart_mode.get(m.from_user.id, False) and " [" in m.text and m.text != "Назад")
    async def toggle_autostart_plugin(message: types.Message):
        uid = message.from_user.id
        text = message.text
        plugin_display = text.split(" [")[0].strip().lower()
        available = scan_available_plugins()
        matched = None
        for key, info in available.items():
            if info["meta"].get("name", key).strip().lower() == plugin_display:
                matched = key
                break
        if not matched:
            await message.answer("Ошибка: плагин не найден.", reply_markup=create_plugins_ext_menu())
            return
        autostart = load_autostart_config()
        if matched in autostart:
            autostart.remove(matched)
            new_status = "Выкл"
        else:
            autostart.append(matched)
            new_status = "Вкл"
        save_autostart_config(autostart)
        await message.answer(f"Плагин {matched} автозапуск переключен на {new_status}.", reply_markup=create_plugins_ext_menu())
        await configure_autostart(message)

    @dp.message_handler(lambda m: autostart_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def autostart_back(message: types.Message):
        uid = message.from_user.id
        autostart_mode[uid] = False
        await message.answer("Режим автозапуска отключён.", reply_markup=create_plugins_ext_menu())

    # ===== Режим скачивания плагина =====
    @dp.message_handler(lambda m: m.text == "Скачать плагин")
    async def download_plugin_menu(message: types.Message):
        uid = message.from_user.id
        download_mode[uid] = True
        plugins = get_plugins_list()
        if not plugins:
            await message.answer("Нет установленных плагинов для скачивания.", reply_markup=create_plugins_ext_menu())
            download_mode.pop(uid, None)
            return
        kb = create_list_keyboard(plugins)
        await message.answer("Выберите плагин для скачивания:", reply_markup=kb)

    @dp.message_handler(lambda m: download_mode.get(m.from_user.id, False) and m.text == "Назад")
    async def download_mode_back(message: types.Message):
        uid = message.from_user.id
        download_mode.pop(uid, None)
        await message.answer("Режим скачивания плагина отменён.", reply_markup=create_plugins_ext_menu())

    @dp.message_handler(lambda m: download_mode.get(m.from_user.id, False) and m.text not in ["Назад"])
    async def process_plugin_download(message: types.Message):
        uid = message.from_user.id
        plugin = message.text
        plugin_path = os.path.join(PLUGIN_DIR, plugin)
        if not os.path.isdir(plugin_path):
            await message.answer(f"Плагин «{plugin}» не найден.", reply_markup=create_plugins_ext_menu())
            return
        temp_zip_dir = os.path.join(os.getcwd(), "temp_plugin_zips")
        os.makedirs(temp_zip_dir, exist_ok=True)
        zip_filename = f"{plugin}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_filepath = os.path.join(temp_zip_dir, zip_filename)
        try:
            shutil.make_archive(zip_filepath[:-4], 'zip', root_dir=plugin_path)
            with open(zip_filepath, 'rb') as document:
                await message.answer_document(document)
            os.remove(zip_filepath)
        except Exception as e:
            await message.answer(f"Ошибка при создании архива плагина: {e}", reply_markup=create_plugins_ext_menu())
        plugins = get_plugins_list()
        if not plugins:
            await message.answer("Нет установленных плагинов для скачивания.", reply_markup=create_plugins_ext_menu())
            download_mode.pop(uid, None)
        else:
            kb = create_list_keyboard(plugins)
            await message.answer("Выберите плагин для скачивания:", reply_markup=kb)

    # ===== Новый режим полного перезапуска =====
    @dp.message_handler(lambda m: m.text == "Полный перезапуск")
    async def full_restart_handler(message: types.Message):
        await message.answer("Бот полностью перезапускается... Ожидайте. Все системные сообщения будут выведены в лог.")
        import asyncio
        asyncio.get_running_loop().call_later(2, perform_full_restart)

    # ===== Меню плагинов =====
    @dp.message_handler(lambda m: m.text == "Плагины")
    async def merged_plugins_menu(message: types.Message):
        await message.answer("Добро пожаловать в менеджер плагинов.\nВыберите действие:", reply_markup=create_plugins_ext_menu())

    @dp.message_handler(lambda m: m.text == "Вернуться")
    async def return_to_additional(message: types.Message):
        await message.answer("Возвращаюсь в дополнительное меню.", reply_markup=get_additional_keyboard())
