#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import subprocess
import tempfile
import platform
import locale
from datetime import datetime
from aiogram import types
from aiogram.dispatcher import Dispatcher

# Чтобы команда входа в консоль не обрабатывалась другими хэндлерами.
try:
    from aiogram.dispatcher.handler import CancelHandler
except Exception:
    CancelHandler = None

# Реестр главного меню (динамическое "Главное меню").
# Если mainmenu_registry отсутствует, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item
except ImportError:
    try:
        from moduls.mainmenu_registry import register_main_item  # если реестр лежит рядом с этим модулем
    except ImportError:
        register_main_item = None


def _register_mainmenu_item():
    """
    Регистрирует кнопку 'консоль python' в реестре главного меню.

    Кнопка:
    - title:        "консоль python" (то, что видит пользователь)
    - trigger_text: "консоль python" (то, что обрабатывает handler)
    - group:        "main"
    """
    if register_main_item is None:
        return

    try:
        register_main_item(
            key="python_console_root",
            title="консоль python",
            trigger_text="консоль python",
            group="main",
            order=50,
            description="Интерактивная Python-консоль"
        )
    except Exception:
        # Не роняем модуль, если что-то пошло не так при регистрации.
        pass

# Состояния для каждого пользователя:
python_con_mode = {}
last_command = {}
danger_mode = {}
quick_visible = {}

# Шаблоны для опасного кода
DANGEROUS_PATTERNS = [
    "os.system",
    "os.popen",
    "subprocess",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.check_output",
    "eval(",
    "exec(",
    "compile(",
    "open(",
    "file(",
    "__import__",
    "importlib",
    "shutil",
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "sys.exit",
    "exit(",
    "kill(",
    "signal",
    "ctypes",
    "multiprocessing",
    "threading",
    "socket",
    "pickle.load",
    "pickle.loads",
    "yaml.load",
    "yaml.full_load",
    "marshal.load",
    "marshal.loads"
]

def is_code_safe(code: str) -> bool:
    lower_code = code.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in lower_code:
            return False
    return True

from typing import Optional, List


def _looks_like_python_exe(exe_path: str) -> bool:
    try:
        bn = os.path.basename(exe_path).lower()
    except Exception:
        return False
    return bn in ("python", "python3", "python.exe", "pythonw.exe", "python3.exe")


def get_base_python_exe() -> str:
    """
    Возвращает путь к встроенному Python, который лежит в папке 'python' рядом с программой.

    Важно:
    - модуль может находиться в moduls/, поэтому опираться на os.getcwd() нельзя;
    - в EXE (Nuitka) sys.executable указывает на сам EXE, это НЕ интерпретатор Python.
    """
    from pathlib import Path

    def _probe(base: "Path") -> Optional[str]:
        if sys.platform.startswith("win"):
            for name in ("python.exe", "pythonw.exe"):
                p = base / "python" / name
                if p.exists():
                    return str(p)
        else:
            p = base / "python" / "bin" / "python"
            if p.exists():
                return str(p)
        return None

    candidates = []  # type: List["Path"]

    # 1) База приложения через __main__.get_app_dir (если есть в проекте)
    try:
        from __main__ import get_app_dir
        base_dir = Path(get_app_dir()).resolve()
        candidates.append(base_dir)
    except Exception:
        pass

    # 2) Папка, где лежит главный файл (если запускается как .py)
    try:
        import __main__ as _main
        main_file = getattr(_main, "__file__", None)
        if main_file:
            candidates.append(Path(main_file).resolve().parent)
    except Exception:
        pass

    # 3) Папка, где лежит текущий модуль, и его родители (модуль теперь в moduls/)
    try:
        module_dir = Path(__file__).resolve().parent
        candidates.append(module_dir.parent)   # обычно это корень проекта
        candidates.append(module_dir)          # на всякий случай
        # если структура типа src/moduls, пробуем подняться выше
        for up in module_dir.parents:
            candidates.append(up)
            if len(candidates) > 8:
                break
    except Exception:
        pass

    # 4) Папка рядом с sys.executable (полезно для portable-развёртки)
    try:
        candidates.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass

    # 5) Текущая рабочая директория (последним шансом)
    try:
        candidates.append(Path(os.getcwd()).resolve())
    except Exception:
        pass

    # Убираем дубли
    seen = set()
    uniq = []  # type: List["Path"]
    for c in candidates:
        s = str(c)
        if s not in seen:
            uniq.append(c)
            seen.add(s)

    # Ищем встроенный python
    for base in uniq:
        hit = _probe(base)
        if hit:
            return hit

    # Фолбэк: если мы реально запущены обычным интерпретатором Python
    if _looks_like_python_exe(sys.executable):
        return sys.executable

    # Ничего не нашли
    return ""

async def execute_python_code(code: str) -> str:
    python_exe = get_base_python_exe()
    if not python_exe or not os.path.exists(python_exe):
        return "Ошибка: встроенный Python не найден (папка 'python' должна быть рядом с программой)."
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    try:
        proc = await asyncio.create_subprocess_exec(
            python_exe, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        # Decode output with system preferred encoding and replace invalid bytes
        enc = locale.getpreferredencoding(False)
        stdout_text = stdout.decode(enc, errors="replace").strip()
        stderr_text = stderr.decode(enc, errors="replace").strip()
        output = (stdout_text + "\n" + stderr_text).strip()
        return output if output else "Нет вывода."
    except Exception as e:
        return f"Ошибка выполнения: {e}"
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def get_console_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    current_danger = danger_mode.get(user_id, False)
    show_quick = quick_visible.get(user_id, True)
    toggle_quick_label = "Скрыть быстрые команды" if show_quick else "Показать быстрые команды"
    toggle_danger_label = "Запретить ввод опасных команд" if current_danger else "Разрешить ввод опасных команд"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(toggle_quick_label)
    if show_quick:
        kb.row("Системная информация", "Время", "Привет мир")
        kb.row("Python версия", "Список файлов", "Случайное число")
        kb.row("UUID", "Последняя команда")
        kb.row("Очистка Python", "Установка pip", "Обновление pip")
    kb.row(toggle_danger_label, "Выход")
    return kb

async def get_system_info() -> str:
    uname = platform.uname()
    info = f"Система: {uname.system} {uname.release}\n"
    info += f"Процессор: {uname.processor}\n"
    info += f"Машина: {uname.machine}\n"
    info += f"Python: {platform.python_version()}\n"
    info += f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    return info

async def get_current_time() -> str:
    return f"Текущее время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

def register_handlers(dp: Dispatcher):
    # При регистрации обработчиков регистрируем кнопку в главном меню.
    _register_mainmenu_item()
    @dp.message_handler(lambda message: message.text and message.text.strip().lower() == "консоль python")
    async def start_console(message: types.Message):
        user_id = message.from_user.id
        python_con_mode[user_id] = True
        danger_mode.setdefault(user_id, False)
        quick_visible.setdefault(user_id, True)
        await message.answer(
            "Python-консоль активирована. Выбирай команду или вводи код вручную.",
            reply_markup=get_console_keyboard(user_id)
        )
        if CancelHandler:
            raise CancelHandler()

    @dp.message_handler(lambda message: message.from_user.id in python_con_mode and python_con_mode.get(message.from_user.id, False))
    async def handle_console(message: types.Message):
        user_id = message.from_user.id
        text = message.text.strip()

        # Переключение показа быстрых команд
        if text in ["Показать быстрые команды", "Скрыть быстрые команды"]:
            current = quick_visible.get(user_id, True)
            quick_visible[user_id] = not current
            result_msg = "Быстрые команды показаны." if quick_visible[user_id] else "Быстрые команды скрыты."
            await message.answer("Команда:")
            await message.answer("toggle_quick_commands")
            await message.answer("Результат:")
            await message.answer(result_msg, reply_markup=get_console_keyboard(user_id))
            return

        # Быстрые команды
        quick_commands = {
            "Системная информация": """import platform
from datetime import datetime
uname = platform.uname()
print(f"Система: {uname.system} {uname.release}")
print(f"Процессор: {uname.processor}")
print(f"Машина: {uname.machine}")
print(f"Python: {platform.python_version()}")
print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")""",
            "Время": """from datetime import datetime
print(f"Текущее время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")""",
            "Привет мир": "print('Привет, мир!')",
            "Python версия": "import platform; print(platform.python_version())",
            "Список файлов": "import os; print(os.listdir('.'))",
            "Случайное число": "import random; print(random.randint(1,100))",
            "UUID": "import uuid; print(uuid.uuid4())",
            "Очистка Python": """import sys, subprocess, pkg_resources
installed = [dist.project_name for dist in pkg_resources.working_set]
for pkg in installed:
    if pkg.lower() not in ('pip', 'setuptools', 'wheel'):
        subprocess.run([sys.executable, '-m', 'pip', 'uninstall', pkg, '-y'])
print("Очистка Python завершена.")""",
            "Установка pip": """import sys, subprocess
subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'])
print("pip установлен.")""",
            "Обновление pip": """import sys, subprocess
subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip'])
print("pip обновлён до последней версии.")"""
        }

        dangerous_quick = {"Очистка Python", "Установка pip", "Обновление pip"}

        if text in quick_commands:
            if text in dangerous_quick and not danger_mode.get(user_id, False):
                await message.answer("Ошибка: обнаружена опасная команда. Для её выполнения включи опасный режим.")
                return
            cmd = quick_commands[text]
            await message.answer("Команда:")
            await message.answer(cmd)
            output = await execute_python_code(cmd)
            await message.answer("Результат:")
            await message.answer(output, reply_markup=get_console_keyboard(user_id))
            last_command[user_id] = cmd
            return

        if text == "Последняя команда":
            last = last_command.get(user_id)
            if last:
                await message.answer("Команда:")
                await message.answer(last)
                output = await execute_python_code(last)
                await message.answer("Результат:")
                await message.answer(output, reply_markup=get_console_keyboard(user_id))
            else:
                await message.answer("Последняя команда отсутствует.", reply_markup=get_console_keyboard(user_id))
            return

        if text in ["Разрешить ввод опасных команд", "Запретить ввод опасных команд"]:
            current = danger_mode.get(user_id, False)
            danger_mode[user_id] = not current
            mode_msg = "Опасный режим включён. Будьте осторожны!" if danger_mode[user_id] else "Безопасный режим включён. Опасные команды запрещены."
            await message.answer("Команда:")
            await message.answer("toggle_danger_mode")
            await message.answer("Результат:")
            await message.answer(mode_msg, reply_markup=get_console_keyboard(user_id))
            return

        if text == "Выход":
            python_con_mode[user_id] = False
            try:
                try:
                    from keymenu import get_main_keyboard
                except ImportError:
                    from moduls.keymenu import get_main_keyboard
                main_kb = get_main_keyboard()
            except Exception:
                main_kb = types.ReplyKeyboardRemove()
            await message.answer("Команда:")
            await message.answer("exit_console")
            await message.answer("Результат:")
            await message.answer("Вы вышли из Python-консоли.", reply_markup=main_kb)
            return

        # Ручной ввод кода
        if not danger_mode.get(user_id, False) and not is_code_safe(text):
            await message.answer("Ошибка: обнаружены потенциально опасные конструкции в коде. Для выполнения такого кода переключись в опасный режим.", reply_markup=get_console_keyboard(user_id))
            return

        last_command[user_id] = text
        await message.answer("Команда:")
        await message.answer(text)
        output = await execute_python_code(text)
        await message.answer("Результат:")
        if len(output) > 1000:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
                tmp.write(output)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                await message.answer_document(f, caption="Вывод выполнения кода:")
            os.remove(tmp_path)
        else:
            await message.answer(output, reply_markup=get_console_keyboard(user_id))

def register(dp: Dispatcher):
    register_handlers(dp)
