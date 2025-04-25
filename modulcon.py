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

def get_base_python_exe():
    try:
        from __main__ import get_app_dir
        base_dir = get_app_dir()
    except Exception:
        base_dir = os.getcwd()
    if sys.platform.startswith("win"):
        python_exe = os.path.join(base_dir, "python", "python.exe")
    else:
        python_exe = os.path.join(base_dir, "python", "bin", "python")
    return python_exe if os.path.exists(python_exe) else sys.executable

async def execute_python_code(code: str) -> str:
    python_exe = get_base_python_exe()
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
        os.remove(tmp_path)

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
                from keymenu import get_main_keyboard
                main_kb = get_main_keyboard()
            except ImportError:
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
