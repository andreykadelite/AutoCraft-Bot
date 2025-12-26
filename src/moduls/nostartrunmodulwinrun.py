import os
import webbrowser
import subprocess
from typing import Dict, List, Tuple

from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility  # ✅ регистрация в реестре утилит

# Режим Win+R по пользователям
winrun_mode: Dict[int, bool] = {}
winrun_waiting_command: Dict[int, bool] = {}
winrun_history: Dict[int, List[str]] = {}
winrun_last_result: Dict[int, str] = {}

MAX_HISTORY = 30


def _ensure_user_state(user_id: int) -> None:
    """
    Гарантирует, что для пользователя инициализированы структуры данных.
    """
    if user_id not in winrun_mode:
        winrun_mode[user_id] = False
    if user_id not in winrun_waiting_command:
        winrun_waiting_command[user_id] = False
    if user_id not in winrun_history:
        winrun_history[user_id] = []
    if user_id not in winrun_last_result:
        winrun_last_result[user_id] = ""


def _add_to_history(user_id: int, command: str) -> None:
    """
    Добавляет команду в историю пользователя.
    """
    if not command:
        return
    _ensure_user_state(user_id)
    history = winrun_history[user_id]
    history.append(command)
    if len(history) > MAX_HISTORY:
        # Храним только последние N записей
        winrun_history[user_id] = history[-MAX_HISTORY:]


def get_winrun_keyboard(waiting_command: bool = False) -> types.ReplyKeyboardMarkup:
    """
    Клавиатура основного меню режима 'Выполнить win+r'.

    Если waiting_command = True — пользователь сейчас вводит строку команды,
    добавляем кнопку 'Отменить ввод команды'.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    if waiting_command:
        kb.add("Отменить ввод команды")
        kb.add("История команд")
        kb.add("Справка Win+R")
        kb.add("Назад в утилиты")
    else:
        kb.add("Ввести команду")
        kb.add("Повторить последнюю")
        kb.add("История команд")
        kb.add("Справка Win+R")
        kb.add("Назад в утилиты")

    return kb


def _is_url(text: str) -> bool:
    """
    Простейшая проверка URL.
    """
    lowered = text.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _run_winr_command(raw_command: str) -> Tuple[bool, str]:
    """
    Запускает команду максимально похоже на окно Win+R.
    Возвращает (успех, текст_результата_для_пользователя).
    """
    cmd = (raw_command or "").strip()
    if not cmd:
        return False, "Пустая строка. Введи имя программы, папки, документа или Интернет‑ресурса."

    # Поддержка переменных окружения вида %appdata%, %windir% и т.п.
    cmd = os.path.expandvars(cmd)
    simple_token = cmd.split()[0] if cmd.split() else ""

    # 1. URL — открываем в браузере / через ShellExecute
    if _is_url(cmd):
        try:
            # На Windows os.startfile для URL ближе всего к поведению Win+R
            if hasattr(os, "startfile"):
                os.startfile(cmd)  # type: ignore[attr-defined]
            else:
                webbrowser.open(cmd)
            return True, f"Открываю адрес:\n`{cmd}`"
        except Exception as e:
            return False, f"Не удалось открыть адрес:\n`{cmd}`\nОшибка: {e}"

    # 2. Если это путь к файлу или каталогу — открываем через ассоциации Windows
    if os.path.exists(cmd):
        try:
            if hasattr(os, "startfile"):
                os.startfile(cmd)  # type: ignore[attr-defined]
            else:
                # На всякий случай fallback, если нет startfile (другая ОС)
                if os.path.isdir(cmd):
                    subprocess.Popen(["xdg-open", cmd])
                else:
                    subprocess.Popen([cmd])

            if os.path.isdir(cmd):
                return True, f"Открываю папку:\n`{cmd}`"
            else:
                return True, f"Открываю файл/приложение:\n`{cmd}`"
        except Exception as e:
            return False, f"Не удалось открыть файл/папку:\n`{cmd}`\nОшибка: {e}"

    # 3. Отдельный случай: простая команда вида 'explorer.exe', 'notepad.exe' и т.п.
    #    Без аргументов пытаемся отдать её напрямую в ShellExecute через os.startfile.
    if cmd == simple_token and "." in simple_token and hasattr(os, "startfile"):
        try:
            os.startfile(simple_token)  # type: ignore[attr-defined]
            return True, f"Запускаю программу:\n`{cmd}`"
        except Exception:
            # Если не получилось — ниже попробуем через subprocess.Popen
            pass

    # 4. Иначе — считаем, что это команда (как 'notepad', 'calc', 'cmd /k dir')
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
        )
        pid_info = f" (PID {proc.pid})" if getattr(proc, "pid", None) else ""
        return True, f"Команда отправлена на выполнение{pid_info}:\n`{cmd}`"
    except FileNotFoundError:
        return False, (
            "Команда не найдена. Попробуй указать полный путь к исполняемому файлу "
            "или использовать URL/существующую папку."
        )
    except Exception as e:
        return False, f"Ошибка при запуске команды:\n`{cmd}`\nОшибка: {e}"


def register_handlers(dp: Dispatcher):
    """
    Регистрация обработчиков режима 'Выполнить win+r'.
    """
    # ✅ Регистрируем утилиту в общем реестре, чтобы она появилась в меню "Утилиты"
    register_utility(
        key="winrun",
        title="Выполнить win+r",
        trigger_text="Выполнить win+r",
        group="utilities",
        order=20,
        description="Запуск программ, папок и адресов через аналог окна Win+R",
    )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Выполнить win+r"
    )
    async def winrun_entry(message: types.Message):
        """
        Точка входа в режим 'Выполнить win+r'.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        winrun_mode[user_id] = True
        winrun_waiting_command[user_id] = False

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Выполнить win+r'.")

        kb = get_winrun_keyboard()
        await message.answer(
            "🪟 Режим *«Выполнить (Win+R)»*.\n\n"
            "Отсюда можно запускать программы, открывать папки и сайты почти так же, "
            "как через окно Win+R в Windows.\n\n"
            "• Нажми «Ввести команду», чтобы указать, что запустить.\n"
            "• Или «Повторить последнюю», чтобы снова выполнить предыдущую команду.",
            reply_markup=kb,
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        message.text == "Назад в утилиты"
    )
    async def winrun_back_to_utilities(message: types.Message):
        """
        Выход из режима Win+R обратно в меню утилит.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        winrun_mode[user_id] = False
        winrun_waiting_command[user_id] = False

        write_bot_log(f"Пользователь {user_id} вышел из модуля 'Выполнить win+r'.")

        kb = get_utilities_keyboard()
        await message.answer(
            "Возвращаю в раздел утилит.",
            reply_markup=kb
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        message.text == "Ввести команду"
    )
    async def winrun_ask_command(message: types.Message):
        """
        Запрашиваем у пользователя строку, аналогичную полю ввода Win+R.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        winrun_waiting_command[user_id] = True

        kb = get_winrun_keyboard(waiting_command=True)
        await message.answer(
            "Введи команду так же, как в окне Win+R.\n\n"
            "Примеры:\n"
            "• `notepad`\n"
            "• `explorer.exe`\n"
            "• `C:\\Windows`\n"
            "• `https://example.com`\n\n"
            "После отправки я попробую выполнить её на этом компьютере.\n"
            "Если передумал — нажми «Отменить ввод команды».",
            reply_markup=kb,
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        winrun_waiting_command.get(message.from_user.id, False) and
        message.text == "Отменить ввод команды"
    )
    async def winrun_cancel_command(message: types.Message):
        """
        Отмена режима ввода команды — возвращаемся в основное меню Win+R.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        winrun_waiting_command[user_id] = False
        write_bot_log(f"Пользователь {user_id} отменил ввод команды Win+R.")

        kb = get_winrun_keyboard(waiting_command=False)
        await message.answer(
            "Ввод команды отменён. Ты в меню Win+R.",
            reply_markup=kb
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        message.text == "Повторить последнюю"
    )
    async def winrun_repeat_last(message: types.Message):
        """
        Повтор последней выполненной команды.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        history = winrun_history.get(user_id) or []
        if not history:
            kb = get_winrun_keyboard()
            await message.answer(
                "Пока ещё нет ни одной команды в истории.\n"
                "Сначала нажми «Ввести команду» и запусти что‑нибудь.",
                reply_markup=kb
            )
            return

        last_cmd = history[-1]
        ok, result_text = _run_winr_command(last_cmd)
        winrun_last_result[user_id] = result_text

        write_bot_log(
            f"Пользователь {user_id} повторяет команду Win+R: {last_cmd!r}, успех={ok}"
        )

        kb = get_winrun_keyboard()
        await message.answer(result_text, reply_markup=kb, parse_mode="Markdown")

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        message.text == "История команд"
    )
    async def winrun_show_history(message: types.Message):
        """
        Показываем последние команды пользователя.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        history = winrun_history.get(user_id) or []
        kb = get_winrun_keyboard()

        if not history:
            await message.answer(
                "История пока пуста.\n"
                "Запусти что‑нибудь через «Ввести команду», и я запомню это здесь.",
                reply_markup=kb
            )
            return

        text_lines = ["🕘 Последние команды Win+R (от новых к старым):"]
        # Показываем максимум 10 последних, чтобы не засорять чат
        for idx, cmd in enumerate(reversed(history[-10:]), start=1):
            text_lines.append(f"{idx}. `{cmd}`")

        text_lines.append(
            "\nЧтобы повторить команду, можешь:\n"
            "• Нажать «Повторить последнюю» (самую свежую),\n"
            "• Или снова ввести нужную строку через «Ввести команду»."
        )

        await message.answer(
            "\n".join(text_lines),
            reply_markup=kb,
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False) and
        message.text == "Справка Win+R"
    )
    async def winrun_help(message: types.Message):
        """
        Краткая справка по режиму Win+R.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        kb = get_winrun_keyboard()
        await message.answer(
            "ℹ️ *Справка по режиму «Выполнить (Win+R)»*\n\n"
            "Этот модуль повторяет логику окна Win+R в Windows (кроме кнопки «Обзор»):\n"
            "• Ты вводишь имя программы, папки, документа или URL.\n"
            "• Я пытаюсь запустить это на текущем компьютере.\n\n"
            "Как это работает:\n"
            "1. Если строка похожа на адрес (`http://` или `https://`) — открою браузер.\n"
            "2. Если это существующий путь к файлу или папке — открою через ассоциации Windows.\n"
            "3. Иначе передам строку в систему как команду (например, `notepad`, `calc`, `cmd /k dir`).\n\n"
            "История команд хранится отдельно для каждого пользователя (до 30 последних).",
            reply_markup=kb,
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winrun_mode.get(message.from_user.id, False)
    )
    async def winrun_fallback(message: types.Message):
        """
        Обработчик прочих сообщений, пока активен режим Win+R.
        Если ждём ввода команды — пробуем выполнить текст сообщения.
        Иначе подсказываем пользователю, какие есть опции.
        """
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        if winrun_waiting_command.get(user_id, False):
            # Это ввели строку команды
            raw_cmd = (message.text or "").strip()
            winrun_waiting_command[user_id] = False

            _add_to_history(user_id, raw_cmd)

            ok, result_text = _run_winr_command(raw_cmd)
            winrun_last_result[user_id] = result_text

            write_bot_log(
                f"Пользователь {user_id} выполняет Win+R команду: {raw_cmd!r}, успех={ok}"
            )

            kb = get_winrun_keyboard()
            await message.answer(result_text, reply_markup=kb, parse_mode="Markdown")
            return

        kb = get_winrun_keyboard()
        # Если мы здесь — это не команда из меню и не ожидаемый ввод
        await message.answer(
            "Сейчас активен режим *«Выполнить (Win+R)»*.\n\n"
            "Используй кнопки под клавиатурой:\n"
            "• «Ввести команду» — чтобы запустить программу/папку/сайт,\n"
            "• «Повторить последнюю» — чтобы выполнить последнюю команду из истории,\n"
            "• «История команд» — чтобы посмотреть список последних запусков,\n"
            "• «Справка Win+R» — краткая инструкция,\n"
            "• «Назад в утилиты» — чтобы выйти из этого режима.",
            reply_markup=kb,
            parse_mode="Markdown"
        )
