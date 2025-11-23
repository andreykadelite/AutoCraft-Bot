import os
import subprocess
from typing import List, Dict, Any

import psutil
from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log

# --- Состояние модуля "Работа с процессами" по пользователям ---
proc_mode: Dict[int, bool] = {}
proc_in_selection_menu: Dict[int, bool] = {}
proc_current_page: Dict[int, int] = {}
proc_process_list: Dict[int, List[Dict[str, Any]]] = {}
proc_selected_pid: Dict[int, int] = {}
proc_selected_info: Dict[int, Dict[str, Any]] = {}

PAGE_SIZE = 15
MAX_MSG_LEN = 4000


# ==================== Вспомогательные функции ====================

def get_process_main_keyboard() -> types.ReplyKeyboardMarkup:
    """Главное меню работы с процессами."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Показать все процессы")
    kb.add("Выбор процесса")
    kb.add("Назад в утилиты")
    return kb


def get_process_action_keyboard() -> types.ReplyKeyboardMarkup:
    """Меню действий над выбранным процессом."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Информация о процессе")
    kb.add("Остановить процесс")
    kb.add("Перезапустить процесс")
    kb.add("Назад к списку процессов")
    kb.add("Назад в меню процессов")
    return kb


def refresh_process_list_for_user(user_id: int) -> None:
    """Обновляем снимок списка процессов для пользователя."""
    processes = []
    for p in psutil.process_iter(attrs=["pid", "name"]):
        try:
            info = p.info
            pid = info.get("pid")
            name = info.get("name") or "Без имени"
            processes.append({"pid": pid, "name": name})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Сортируем сначала по имени, затем по PID
    processes.sort(key=lambda x: ((x["name"] or "").lower(), x["pid"]))

    proc_process_list[user_id] = processes
    proc_current_page[user_id] = 0


def build_process_page_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    """Клавиатура выбора процесса (страницы по 15 процессов)."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    processes = proc_process_list.get(user_id, [])
    page = proc_current_page.get(user_id, 0)
    total = len(processes)
    if total == 0:
        kb.add("Назад в меню процессов")
        return kb

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    proc_current_page[user_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for proc in processes[start:end]:
        pid = proc["pid"]
        name = proc["name"] or "Без имени"
        if len(name) > 30:
            name = name[:27] + "..."
        btn_text = f"{pid} | {name}"
        kb.add(btn_text)

    # Навигация по страницам
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append("Предыдущая страница процессов")
        if page < total_pages - 1:
            nav_buttons.append("Следующая страница процессов")

    if nav_buttons:
        kb.row(*nav_buttons)

    kb.add("Назад в меню процессов")
    return kb


async def send_process_page(message: types.Message, user_id: int) -> None:
    """Отправка текущей страницы со списком процессов."""
    processes = proc_process_list.get(user_id, [])
    if not processes:
        kb = get_process_main_keyboard()
        await message.answer(
            "Список процессов пуст или не удалось получить данные.\n"
            "Попробуй обновить список, нажав «Выбор процесса».",
            reply_markup=kb,
        )
        return

    total = len(processes)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = proc_current_page.get(user_id, 0)
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    proc_current_page[user_id] = page

    kb = build_process_page_keyboard(user_id)
    text = (
        f"Выбор процесса (страница {page + 1} из {total_pages}).\n"
        "Нажми на нужный процесс, чтобы перейти к управлению им.\n"
        "Формат: PID | имя процесса."
    )
    await message.answer(text, reply_markup=kb)


async def send_long_text(message: types.Message, text: str) -> None:
    """Отправка длинного текста кусками, с учётом лимита Telegram."""
    if not text:
        await message.answer("Пустой вывод.")
        return

    if len(text) <= MAX_MSG_LEN:
        await message.answer(f"```text\n{text}\n```", parse_mode="Markdown")
        return

    for i in range(0, len(text), MAX_MSG_LEN):
        chunk = text[i: i + MAX_MSG_LEN]
        await message.answer(f"```text\n{chunk}\n```", parse_mode="Markdown")


def build_full_process_list_text() -> str:
    """Формирует полный список процессов для текстового вывода."""
    lines = ["Список процессов (PID | имя процесса):"]
    try:
        processes = []
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                info = p.info
                pid = info.get("pid")
                name = info.get("name") or "Без имени"
                processes.append((pid, name))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        processes.sort(key=lambda x: (x[1].lower() if x[1] else "", x[0]))

        for pid, name in processes:
            lines.append(f"{pid:>6} | {name}")
    except Exception as e:
        lines.append(f"Ошибка при получении списка процессов: {e}")

    if len(lines) == 1:
        lines.append("Процессы не найдены.")
    return "\n".join(lines)


def parse_pid_from_button(text: str) -> int:
    """Пытаемся извлечь PID из текста кнопки вида "1234 | имя"."""
    try:
        pid_part = text.split("|", 1)[0].strip()
        return int(pid_part)
    except (ValueError, IndexError):
        return 0


# ==================== Регистрация хендлеров ====================


def register_handlers(dp: Dispatcher):
    """Регистрация хендлеров для работы с процессами."""

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and message.text == "Работа с процессами"
    )
    async def proc_entry(message: types.Message):
        """Точка входа в модуль работы с процессами."""
        user_id = message.from_user.id

        proc_mode[user_id] = True
        proc_in_selection_menu[user_id] = False
        proc_current_page[user_id] = 0
        proc_process_list.pop(user_id, None)
        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Работа с процессами'.")

        kb = get_process_main_keyboard()
        await message.answer(
            "🧩 Работа с процессами.\n"
            "Могу показать полный список процессов или дать выбрать процесс для управления.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and message.text == "Назад в утилиты"
    )
    async def proc_back_to_utilities(message: types.Message):
        """Выход из модуля в раздел утилит."""
        user_id = message.from_user.id

        proc_mode[user_id] = False
        proc_in_selection_menu.pop(user_id, None)
        proc_current_page.pop(user_id, None)
        proc_process_list.pop(user_id, None)
        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)

        kb = get_utilities_keyboard()
        await message.answer("Возвращаю в раздел утилит.", reply_markup=kb)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and message.text == "Показать все процессы"
    )
    async def proc_show_all(message: types.Message):
        """Показ полного списка процессов текстом (с разбиением по сообщениям)."""
        user_id = message.from_user.id

        # При просмотре полного списка сбрасываем выбор процесса/режим выбора,
        # чтобы не было путаницы в дальнейшем.
        proc_in_selection_menu[user_id] = False
        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} запросил полный список процессов.")

        await message.answer("⌛ Получаю список процессов, подожди немного...")
        text = build_full_process_list_text()
        await send_long_text(message, text)

        kb = get_process_main_keyboard()
        await message.answer(
            "Готово. Можешь выбрать другие действия в меню работы с процессами.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and message.text == "Выбор процесса"
    )
    async def proc_select_entry(message: types.Message):
        """Вход в режим выбора процесса (клавиатура со списком процессов)."""
        user_id = message.from_user.id
        write_bot_log(f"Пользователь {user_id} вошёл в режим выбора процесса.")

        await message.answer("⌛ Обновляю список процессов...")
        refresh_process_list_for_user(user_id)

        processes = proc_process_list.get(user_id, [])
        if not processes:
            kb = get_process_main_keyboard()
            await message.answer(
                "Не удалось получить список процессов или он пуст.",
                reply_markup=kb,
            )
            return

        proc_in_selection_menu[user_id] = True
        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)

        await send_process_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_in_selection_menu.get(message.from_user.id, False)
        and message.text == "Следующая страница процессов"
    )
    async def proc_next_page(message: types.Message):
        """Перейти на следующую страницу списка процессов."""
        user_id = message.from_user.id
        processes = proc_process_list.get(user_id, [])
        if not processes:
            proc_in_selection_menu[user_id] = False
            kb = get_process_main_keyboard()
            await message.answer(
                "Список процессов не инициализирован. Нажми «Выбор процесса», чтобы обновить.",
                reply_markup=kb,
            )
            return

        total_pages = (len(processes) + PAGE_SIZE - 1) // PAGE_SIZE
        page = proc_current_page.get(user_id, 0)
        if page >= total_pages - 1:
            await message.answer("Это последняя страница списка процессов.")
            await send_process_page(message, user_id)
            return

        proc_current_page[user_id] = page + 1
        await send_process_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_in_selection_menu.get(message.from_user.id, False)
        and message.text == "Предыдущая страница процессов"
    )
    async def proc_prev_page(message: types.Message):
        """Перейти на предыдущую страницу списка процессов."""
        user_id = message.from_user.id
        processes = proc_process_list.get(user_id, [])
        if not processes:
            proc_in_selection_menu[user_id] = False
            kb = get_process_main_keyboard()
            await message.answer(
                "Список процессов не инициализирован. Нажми «Выбор процесса», чтобы обновить.",
                reply_markup=kb,
            )
            return

        page = proc_current_page.get(user_id, 0)
        if page <= 0:
            await message.answer("Это первая страница списка процессов.")
            await send_process_page(message, user_id)
            return

        proc_current_page[user_id] = page - 1
        await send_process_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and message.text == "Назад в меню процессов"
    )
    async def proc_back_to_menu(message: types.Message):
        """Возврат из подменю (список/действия) в главное меню процессов."""
        user_id = message.from_user.id

        proc_in_selection_menu[user_id] = False
        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)

        kb = get_process_main_keyboard()
        await message.answer("Возвращаюсь в меню работы с процессами.", reply_markup=kb)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_selected_pid.get(message.from_user.id) is not None
        and message.text == "Назад к списку процессов"
    )
    async def proc_back_to_list(message: types.Message):
        """Возврат из меню действий к списку процессов."""
        user_id = message.from_user.id

        proc_selected_pid.pop(user_id, None)
        proc_selected_info.pop(user_id, None)
        proc_in_selection_menu[user_id] = True

        await send_process_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_selected_pid.get(message.from_user.id) is not None
        and message.text == "Информация о процессе"
    )
    async def proc_info(message: types.Message):
        """Показать подробную информацию о выбранном процессе."""
        user_id = message.from_user.id
        pid = proc_selected_pid.get(user_id)
        if not pid:
            kb = get_process_main_keyboard()
            await message.answer(
                "Сначала выбери процесс через меню «Выбор процесса».",
                reply_markup=kb,
            )
            return

        try:
            p = psutil.Process(pid)
            name = p.name()
            exe = ""
            cmdline = []
            username = ""
            status = ""
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            try:
                cmdline = p.cmdline()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            try:
                username = p.username()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            try:
                status = p.status()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            info_text = [
                f"PID: {pid}",
                f"Имя: {name}",
                f"Пользователь: {username or '-'}",
                f"Статус: {status or '-'}",
                f"Путь к файлу: {exe or '-'}",
                "Командная строка:",
                " ".join(cmdline) if cmdline else "-",
            ]

            await send_long_text(message, "\n".join(info_text))

            # Обновим сохранённую информацию
            proc_selected_info[user_id] = {
                "pid": pid,
                "name": name,
                "exe": exe,
                "cmdline": cmdline,
            }
        except psutil.NoSuchProcess:
            kb = get_process_main_keyboard()
            await message.answer(
                "Этот процесс уже завершён. Выбери другой процесс.",
                reply_markup=kb,
            )
        except psutil.AccessDenied:
            await message.answer(
                "Недостаточно прав для получения подробной информации о процессе."
            )
        except Exception as e:
            await message.answer(f"Ошибка при получении информации о процессе: {e}")

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_selected_pid.get(message.from_user.id) is not None
        and message.text == "Остановить процесс"
    )
    async def proc_kill(message: types.Message):
        """Остановить (завершить) выбранный процесс."""
        user_id = message.from_user.id
        pid = proc_selected_pid.get(user_id)
        if not pid:
            kb = get_process_main_keyboard()
            await message.answer(
                "Сначала выбери процесс через меню «Выбор процесса».",
                reply_markup=kb,
            )
            return

        if pid == os.getpid():
            await message.answer(
                "Это сам бот. Останавливать себя изнутри не буду, а то всё упадёт."
            )
            return

        try:
            p = psutil.Process(pid)
            name = p.name()
            write_bot_log(
                f"Пользователь {user_id} запросил остановку процесса {name} (PID {pid})."
            )

            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                p.kill()

            await message.answer(f"Процесс {name} (PID {pid}) остановлен.")
        except psutil.NoSuchProcess:
            await message.answer("Процесс уже завершён.")
        except psutil.AccessDenied:
            await message.answer(
                "Недостаточно прав, чтобы завершить этот процесс. "
                "Попробуй запустить бота/скрипт от имени администратора."
            )
        except Exception as e:
            await message.answer(f"Ошибка при завершении процесса: {e}")

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_selected_pid.get(message.from_user.id) is not None
        and message.text == "Перезапустить процесс"
    )
    async def proc_restart(message: types.Message):
        """Перезапустить выбранный процесс (остановить и запустить заново)."""
        user_id = message.from_user.id
        old_pid = proc_selected_pid.get(user_id)
        if not old_pid:
            kb = get_process_main_keyboard()
            await message.answer(
                "Сначала выбери процесс через меню «Выбор процесса».",
                reply_markup=kb,
            )
            return

        if old_pid == os.getpid():
            await message.answer(
                "Это сам бот. Перезапускать себя изнутри я не буду — управлять тогда будет некому."
            )
            return

        info = proc_selected_info.get(user_id, {})

        try:
            p = psutil.Process(old_pid)
            name = p.name()
            try:
                exe = info.get("exe") or p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                exe = info.get("exe") or ""
            try:
                cmdline = info.get("cmdline") or p.cmdline()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cmdline = info.get("cmdline") or []
        except psutil.NoSuchProcess:
            # Процесс уже умер, попробуем просто запустить по сохранённым данным
            name = info.get("name") or "неизвестный"
            exe = info.get("exe") or ""
            cmdline = info.get("cmdline") or []
        except psutil.AccessDenied:
            await message.answer(
                "Недостаточно прав, чтобы перезапустить этот процесс. "
                "Попробуй запустить бота/скрипт от имени администратора."
            )
            return
        except Exception as e:
            await message.answer(f"Ошибка при подготовке к перезапуску процесса: {e}")
            return

        # Сначала пытаемся остановить текущий процесс, если он ещё жив
        try:
            p = psutil.Process(old_pid)
            write_bot_log(
                f"Пользователь {user_id} запросил перезапуск процесса {p.name()} (PID {old_pid})."
            )
            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                p.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            await message.answer(
                "Недостаточно прав, чтобы остановить процесс перед перезапуском."
            )
            return
        except Exception as e:
            await message.answer(f"Ошибка при остановке процесса: {e}")
            return

        # Формируем команду для запуска
        start_cmd = None
        if cmdline:
            start_cmd = cmdline
        elif exe:
            start_cmd = [exe]

        if not start_cmd:
            await message.answer(
                "Не удалось определить, как запустить этот процесс заново "
                "(нет пути к файлу и командной строки)."
            )
            return

        try:
            new_proc = subprocess.Popen(start_cmd)
            new_pid = new_proc.pid
            proc_selected_pid[user_id] = new_pid
            proc_selected_info[user_id] = {
                "pid": new_pid,
                "name": name,
                "exe": exe,
                "cmdline": cmdline,
            }
            write_bot_log(
                f"Процесс {name} перезапущен пользователем {user_id}: "
                f"старый PID {old_pid}, новый PID {new_pid}."
            )
            await message.answer(
                f"Процесс {name} перезапущен.\n"
                f"Старый PID: {old_pid}\n"
                f"Новый PID: {new_pid}"
            )
        except Exception as e:
            await message.answer(f"Не удалось запустить новый процесс: {e}")

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
        and proc_in_selection_menu.get(message.from_user.id, False)
    )
    async def proc_select_process(message: types.Message):
        """Обработка нажатия на кнопку процесса в режиме выбора."""
        user_id = message.from_user.id
        text = message.text or ""
        pid = parse_pid_from_button(text)
        if not pid:
            # Не похоже на кнопку процесса — подсказываем
            kb = build_process_page_keyboard(user_id)
            await message.answer(
                "Чтобы выбрать процесс, нажми на кнопку с форматом \"PID | имя процесса\".",
                reply_markup=kb,
            )
            return

        try:
            p = psutil.Process(pid)
            name = p.name()
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                exe = ""
            try:
                cmdline = p.cmdline()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cmdline = []
        except psutil.NoSuchProcess:
            await message.answer(
                "Этот процесс уже завершён. Обнови список и выбери другой."
            )
            return
        except Exception as e:
            await message.answer(f"Ошибка при выборе процесса: {e}")
            return

        proc_in_selection_menu[user_id] = False
        proc_selected_pid[user_id] = pid
        proc_selected_info[user_id] = {
            "pid": pid,
            "name": name,
            "exe": exe,
            "cmdline": cmdline,
        }

        kb = get_process_action_keyboard()
        await message.answer(
            f"Выбран процесс:\n"
            f"PID: {pid}\n"
            f"Имя: {name}\n"
            f"Путь: {exe or '-'}\n"
            "Теперь выбери действие: показать информацию, остановить или перезапустить.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and proc_mode.get(message.from_user.id, False)
    )
    async def proc_fallback(message: types.Message):
        """Фолбэк-хендлер для режима работы с процессами."""
        user_id = message.from_user.id

        if proc_in_selection_menu.get(user_id, False):
            kb = build_process_page_keyboard(user_id)
            await message.answer(
                "Сейчас активен режим выбора процесса.\n"
                "Используй кнопки со списком процессов или навигацию по страницам, "
                "либо нажми «Назад в меню процессов».",
                reply_markup=kb,
            )
        elif proc_selected_pid.get(user_id) is not None:
            kb = get_process_action_keyboard()
            await message.answer(
                "Сейчас выбран конкретный процесс.\n"
                "Используй кнопки для просмотра информации, остановки или перезапуска, "
                "либо нажми «Назад к списку процессов» для возврата.",
                reply_markup=kb,
            )
        else:
            kb = get_process_main_keyboard()
            await message.answer(
                "Сейчас активен режим «Работа с процессами».\n"
                "Можешь показать полный список процессов или перейти к выбору конкретного процесса, "
                "либо нажать «Назад в утилиты» для выхода.",
                reply_markup=kb,
            )
