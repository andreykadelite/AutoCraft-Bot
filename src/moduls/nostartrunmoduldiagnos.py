import os
import sys
import asyncio
import time
import platform
import subprocess
from pathlib import Path

from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility  # ✅ реестр утилит


# Режим работы диагностического модуля по пользователям
diag_mode = {}

# Подтверждение опасных действий (чтобы случайно не нажать)
# Требуем повторное нажатие той же кнопки в течение тайм-аута.
danger_confirm = {}
CONFIRM_TTL_SECONDS = 10


def _confirm_danger_action(user_id: int, action: str) -> bool:
    """Вернёт True, если действие подтверждено (повторное нажатие вовремя)."""
    now = time.time()
    state = danger_confirm.get(user_id)

    if state and state.get("action") == action and state.get("until", 0) >= now:
        danger_confirm.pop(user_id, None)
        return True

    danger_confirm[user_id] = {"action": action, "until": now + CONFIRM_TTL_SECONDS}
    return False


def _clear_danger_confirm(user_id: int):
    """Сброс подтверждения опасных действий."""
    danger_confirm.pop(user_id, None)


def get_diag_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Основная клавиатура диагностического модуля.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Открыть временную папку EXE")
    kb.add("Открыть папку с EXE")
    kb.add("Показать диагностику")
    kb.add("Тестовая запись в лог")
    kb.add("Аварийное завершение")
    kb.add("Зависание программы")
    kb.add("Назад в утилиты")
    return kb


def _get_unpack_root() -> Path:
    """
    Папка, куда Nuitka onefile/standalone распаковал проект.

    Логика:
    - В проекте модуль лежит как .../moduls/nostartrunmoduldevdiag.py
      → корень распаковки на уровень выше папки moduls.
    - В несобранном виде это просто корень проекта.
    - Если структура другая или что-то пошло не так —
      используем папку, где лежит исполняемый файл (sys.argv[0]).
    """
    try:
        current = Path(__file__).resolve()
        # .../<root>/moduls/этот_файл.py → поднимаемся на 2 вверх
        candidate = current.parent.parent

        if candidate.exists():
            return candidate
    except Exception:
        pass

    # Фолбэк: каталог исполняемого файла (EXE или python.exe)
    exe_dir = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
    return exe_dir


def _open_folder(path: Path) -> tuple[bool, str]:
    """
    Открытие папки в проводнике/файловом менеджере.
    Возвращает (ok, message).
    """
    path = path.resolve()

    if not path.exists():
        return False, f"Папка не найдена: {path}"

    try:
        if os.name == "nt":
            # Windows: открываем через проводник
            subprocess.Popen(["explorer", str(path)])
        else:
            # На всякий случай — для других платформ
            subprocess.Popen(["xdg-open", str(path)])
        return True, f"Открыл проводник в папке:\n{path}"
    except Exception as e:
        return False, f"Не удалось открыть папку {path}:\n{e}"


def register_handlers(dp: Dispatcher):
    """
    Регистрация хендлеров диагностического модуля.
    """

    # ✅ Регистрируем утилиту в общем реестре
    register_utility(
        key="dev_diagnostics",
        title="Диагностика окружения",
        trigger_text="Диагностика окружения",
        group="utilities",
        order=50,
        description="Диагностический модуль для отладки AutoCraft-Bot и окружения Nuitka.",
    )

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        message.text == "Диагностика окружения"
    )
    async def diag_entry(message: types.Message):
        """
        Точка входа в диагностический модуль.
        """
        user_id = message.from_user.id
        diag_mode[user_id] = True
        _clear_danger_confirm(user_id)

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Диагностика окружения'.")

        kb = get_diag_keyboard()
        await message.answer(
            "🧪 Диагностика окружения (DEV).\n"
            "\n"
            "⚠️ Внимание: этот модуль предназначен для разработчиков.\n"
            "Здесь есть функции принудительной остановки и намеренного зависания процесса.\n"
            "Используй их только осознанно и по необходимости (например, для теста watchdog).\n"
            "\n"
            "Чтобы избежать случайных нажатий, опасные действия требуют подтверждения: \n"
            "нажми ту же кнопку второй раз в течение 10 секунд.\n"
            "\n"
            "Выбирай, что сделать:",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Открыть временную папку EXE"
    )
    async def open_temp_folder(message: types.Message):
        """
        Открыть папку распаковки (onefile/standalone dist).
        """
        user_id = message.from_user.id
        _clear_danger_confirm(user_id)
        unpack_root = _get_unpack_root()

        ok, text = _open_folder(unpack_root)
        write_bot_log(
            f"Пользователь {user_id} запросил открытие временной папки EXE: "
            f"{unpack_root} (ok={ok})."
        )

        await message.answer(text, reply_markup=get_diag_keyboard())

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Открыть папку с EXE"
    )
    async def open_exe_folder(message: types.Message):
        """
        Открыть папку, где лежит EXE (sys.argv[0]).
        В несобранном виде это просто папка со скриптом запуска.
        """
        user_id = message.from_user.id
        _clear_danger_confirm(user_id)

        exe_path = Path(os.path.abspath(sys.argv[0]))
        exe_dir = exe_path.parent

        ok, text = _open_folder(exe_dir)
        write_bot_log(
            f"Пользователь {user_id} запросил открытие папки EXE: "
            f"{exe_dir} (ok={ok})."
        )

        await message.answer(text, reply_markup=get_diag_keyboard())

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Показать диагностику"
    )
    async def show_diagnostics(message: types.Message):
        """
        Показать базовую диагностическую информацию по окружению.
        """
        user_id = message.from_user.id
        _clear_danger_confirm(user_id)

        module = sys.modules.get(__name__)
        is_compiled = bool(getattr(module, "__compiled__", False))

        unpack_root = _get_unpack_root()
        exe_path = Path(os.path.abspath(sys.argv[0]))
        exe_dir = exe_path.parent
        nuitka_env = {
            k: v for k, v in os.environ.items() if k.upper().startswith("NUITKA_")
        }

        info_lines = [
            "🧪 Техническая диагностика:",
            "",
            f"• OS: {platform.system()} {platform.release()}",
            f"• Platform: {platform.platform()}",
            f"• Python / runtime: {sys.version.splitlines()[0]}",
            "",
            f"• sys.executable: {sys.executable}",
            f"• sys.argv[0]: {exe_path}",
            "",
            f"• __file__ модуля: {Path(__file__).resolve()}",
            f"• Папка распаковки (dist/временная): {unpack_root}",
            f"• Папка с EXE: {exe_dir}",
            "",
            f"• Nuitka compiled (__compiled__): {is_compiled}",
            f"• Текущая рабочая папка: {os.getcwd()}",
        ]

        if nuitka_env:
            info_lines.append("")
            info_lines.append("• Переменные окружения Nuitka:")
            for k, v in nuitka_env.items():
                info_lines.append(f"  - {k}={v}")

        text = "\n".join(info_lines)

        write_bot_log(
            f"Пользователь {user_id} запросил диагностическую информацию модуля."
        )

        # На всякий случай упакуем в код-блок, но проверим длину
        if len(text) > 3800:
            await message.answer(text)
        else:
            await message.answer(f"```text\n{text}\n```", parse_mode="Markdown")

        await message.answer(
            "Готово. Можно повторить диагностику или открыть папки.",
            reply_markup=get_diag_keyboard(),
        )

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Тестовая запись в лог"
    )
    async def test_log_entry(message: types.Message):
        """
        Тестовая запись в лог, чтобы проверить работу логирования
        именно в собранном EXE.
        """
        user_id = message.from_user.id
        _clear_danger_confirm(user_id)

        write_bot_log(
            f"[DEV-DIAG] Тестовая запись в лог от пользователя {user_id}. "
            f"EXE: {os.path.abspath(sys.argv[0])}"
        )

        await message.answer(
            "✅ Тестовая запись отправлена в лог.\n"
            "Проверь файл логов, что она туда попала.",
            reply_markup=get_diag_keyboard(),
        )



    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Аварийное завершение"
    )
    async def emergency_exit(message: types.Message):
        """
        Принудительное завершение процесса (для тестирования watchdog/перезапуска).
        Действие опасное: требует подтверждения повторным нажатием.
        """
        user_id = message.from_user.id

        if not _confirm_danger_action(user_id, "emergency_exit"):
            await message.answer(
                "⛔ **Аварийное завершение**\n"
                "\n"
                "Это принудительно завершит процесс бота/GUI прямо сейчас.\n"
                "Повтори кнопку **«Аварийное завершение»** в течение 10 секунд, чтобы подтвердить.\n"
                "Если передумал, просто нажми любую другую кнопку.",
                parse_mode="Markdown",
                reply_markup=get_diag_keyboard(),
            )
            return

        write_bot_log(
            f"[DEV-DIAG] Пользователь {user_id} инициировал аварийное завершение процесса."
        )

        # Сообщение стараемся отправить до выхода
        await message.answer(
            "⛔ Выполняю аварийное завершение процесса.\n"
            "Если запущен watchdog, он должен поднять программу заново.",
            reply_markup=get_diag_keyboard(),
        )

        # Дадим Telegram-ответу уйти в сеть
        await asyncio.sleep(0.7)

        # Жёсткий выход: без graceful shutdown, именно аварийно.
        os._exit(2)

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Зависание программы"
    )
    async def simulate_hang(message: types.Message):
        """
        Намеренная имитация зависания (блокировка основного потока).
        Нужна для теста реакции watchdog на зависшие состояния.
        Действие опасное: требует подтверждения повторным нажатием.
        """
        user_id = message.from_user.id

        if not _confirm_danger_action(user_id, "simulate_hang"):
            await message.answer(
                "🧊 **Зависание программы**\n"
                "\n"
                "Сейчас я намеренно повешу процесс (бот перестанет отвечать).\n"
                "Вернуть работу можно будет только перезапуском/ватчдогом.\n"
                "\n"
                "Повтори кнопку **«Зависание программы»** в течение 10 секунд, чтобы подтвердить.",
                parse_mode="Markdown",
                reply_markup=get_diag_keyboard(),
            )
            return

        write_bot_log(
            f"[DEV-DIAG] Пользователь {user_id} запустил имитацию зависания процесса."
        )

        await message.answer(
            "🧊 Имитирую зависание: сейчас процесс перестанет отвечать.\n"
            "Это нормально для теста. Для восстановления нужен перезапуск/ватчдог.",
            reply_markup=get_diag_keyboard(),
        )

        # Дадим Telegram-ответу уйти в сеть, после чего блокируем поток.
        await asyncio.sleep(0.7)

        # Блокируем event loop намеренно (вызов time.sleep внутри бесконечного цикла).
        while True:
            time.sleep(1)



    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False) and
        message.text == "Назад в утилиты"
    )
    async def back_to_utilities(message: types.Message):
        """
        Выход из диагностического модуля в меню утилит.
        """
        user_id = message.from_user.id
        diag_mode[user_id] = False
        _clear_danger_confirm(user_id)

        write_bot_log(
            f"Пользователь {user_id} вышел из модуля 'Диагностика окружения'."
        )

        kb = get_utilities_keyboard()
        await message.answer(
            "Возвращаю в раздел утилит.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message:
        message.from_user.id in authorized_users and
        diag_mode.get(message.from_user.id, False)
    )
    async def diag_fallback(message: types.Message):
        """
        Любые прочие сообщения, пока активен диагностический режим.
        Напоминаем, что есть кнопки.
        """
        user_id = message.from_user.id
        _clear_danger_confirm(user_id)

        kb = get_diag_keyboard()
        await message.answer(
            "Сейчас активен режим «Диагностика окружения».\n"
            "Используй кнопки:\n"
            "- «Открыть временную папку EXE»\n"
            "- «Открыть папку с EXE»\n"
            "- «Показать диагностику»\n"
            "- «Тестовая запись в лог»\n"
            "- «Аварийное завершение» (подтверждение: повторить за 10 сек)\n"
            "- «Зависание программы» (подтверждение: повторить за 10 сек)\n"
            "или нажми «Назад в утилиты» для выхода.",
            reply_markup=kb,
        )
