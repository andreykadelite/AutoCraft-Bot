
import subprocess
from typing import Dict, List, Any

import psutil
from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility  # регистрация в реестре утилит

# --- Состояние модуля "Работа со службами" по пользователям ---
svc_mode: Dict[int, bool] = {}
svc_in_selection_menu: Dict[int, bool] = {}
svc_current_page: Dict[int, int] = {}
svc_service_list: Dict[int, List[Dict[str, Any]]] = {}
svc_selected_name: Dict[int, str] = {}
svc_selected_info: Dict[int, Dict[str, Any]] = {}

PAGE_SIZE = 15
MAX_MSG_LEN = 4000


# ==================== Вспомогательные функции ====================


def get_services_main_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Главное меню работы со службами.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Показать все службы")
    kb.add("Выбор службы")
    kb.add("Назад в утилиты")
    return kb


def get_services_action_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Меню действий над выбранной службой.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Информация о службе")
    kb.add("Запустить службу")
    kb.add("Остановить службу")
    kb.add("Перезапустить службу")
    kb.add("Назад к списку служб")
    kb.add("Назад в меню служб")
    return kb


def refresh_services_list_for_user(user_id: int) -> None:
    """
    Обновляем снимок списка служб для пользователя.
    """
    services: List[Dict[str, Any]] = []
    try:
        if not hasattr(psutil, "win_service_iter"):
            raise RuntimeError("Функции работы со службами доступны только в Windows.")
        for s in psutil.win_service_iter():
            try:
                name = s.name()
                display_name = s.display_name()
                status = s.status()
                services.append(
                    {
                        "name": name,
                        "display_name": display_name,
                        "status": status,
                    }
                )
            except Exception:
                continue
    except Exception as e:
        write_bot_log(f"Ошибка при получении списка служб: {e}")
        services = []

    # Сортируем по имени службы (внутреннее имя)
    services.sort(key=lambda x: (x["name"] or "").lower())

    svc_service_list[user_id] = services
    svc_current_page[user_id] = 0


def build_services_page_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    """
    Клавиатура выбора службы (страницы по 15 служб).
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    services = svc_service_list.get(user_id, [])
    page = svc_current_page.get(user_id, 0)
    total = len(services)
    if total == 0:
        kb.add("Назад в меню служб")
        return kb

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    svc_current_page[user_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for svc in services[start:end]:
        name = svc["name"]
        display_name = svc.get("display_name") or ""
        status = svc.get("status") or ""
        label = display_name or name
        if len(label) > 30:
            label = label[:27] + "..."
        btn_text = f"{name} | {label} [{status}]"
        kb.add(btn_text)

    # Навигация по страницам
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append("Предыдущая страница служб")
        if page < total_pages - 1:
            nav_buttons.append("Следующая страница служб")

    if nav_buttons:
        kb.row(*nav_buttons)

    kb.add("Назад в меню служб")
    return kb


async def send_services_page(message: types.Message, user_id: int) -> None:
    """
    Отправка текущей страницы со списком служб.
    """
    services = svc_service_list.get(user_id, [])
    if not services:
        kb = get_services_main_keyboard()
        await message.answer(
            "Список служб пуст или не удалось получить данные.\n"
            "Убедись, что бот запущен в Windows и с достаточными правами.",
            reply_markup=kb,
        )
        return

    total = len(services)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = svc_current_page.get(user_id, 0)
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    svc_current_page[user_id] = page

    kb = build_services_page_keyboard(user_id)
    text = (
        f"Выбор службы (страница {page + 1} из {total_pages}).\n"
        "Нажми на нужную службу, чтобы перейти к управлению ей.\n"
        "Формат: internal_name | отображаемое имя [статус]."
    )
    await message.answer(text, reply_markup=kb)


async def send_long_text(message: types.Message, text: str) -> None:
    """
    Отправка длинного текста кусками, с учётом лимита Telegram.
    """
    if not text:
        await message.answer("Пустой вывод.")
        return

    if len(text) <= MAX_MSG_LEN:
        await message.answer(f"```text\n{text}\n```", parse_mode="Markdown")
        return

    for i in range(0, len(text), MAX_MSG_LEN):
        chunk = text[i : i + MAX_MSG_LEN]
        await message.answer(f"```text\n{chunk}\n```", parse_mode="Markdown")


def build_full_service_list_text() -> str:
    """
    Формирует полный список служб для текстового вывода.
    """
    lines = ["Список служб (internal_name | отображаемое имя [статус]):"]
    try:
        if not hasattr(psutil, "win_service_iter"):
            raise RuntimeError("Функции работы со службами доступны только в Windows.")
        services = []
        for s in psutil.win_service_iter():
            try:
                name = s.name()
                display_name = s.display_name()
                status = s.status()
                services.append((name, display_name, status))
            except Exception:
                continue

        services.sort(key=lambda x: (x[0] or "").lower())

        for name, display_name, status in services:
            disp = display_name or ""
            lines.append(f"{name:40} | {disp:50} [{status}]")
    except Exception as e:
        lines.append(f"Ошибка при получении списка служб: {e}")

    if len(lines) == 1:
        lines.append("Службы не найдены или недоступны.")
    return "\n".join(lines)


def parse_service_name_from_button(text: str) -> str:
    """
    Пытаемся извлечь internal_name службы из текста кнопки вида
    "Name | DisplayName [status]".
    """
    try:
        name_part = text.split("|", 1)[0].strip()
        return name_part
    except Exception:
        return ""


def get_service_object(name: str):
    """
    Получить объект службы psutil по internal_name.
    """
    try:
        if not hasattr(psutil, "win_service_get"):
            raise RuntimeError("Функции работы со службами доступны только в Windows.")
        return psutil.win_service_get(name)
    except Exception as e:
        write_bot_log(f"Ошибка при получении службы {name}: {e}")
        return None


def run_sc_command(args: list) -> str:
    """
    Запускает команду sc и возвращает объединённый вывод stdout/stderr.
    """
    try:
        result = subprocess.run(
            ["sc"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            shell=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip() or "Команда выполнена без вывода."
        return output
    except FileNotFoundError:
        return "Утилита sc не найдена. Проверь, что бот запущен в Windows."
    except Exception as e:
        return f"Ошибка при выполнении sc: {e}"


# ==================== Регистрация хендлеров ====================


def register_handlers(dp: Dispatcher):
    """
    Регистрация хендлеров для работы со службами Windows.
    """

    # Регистрируем утилиту в общем реестре, чтобы она появилась в меню "Утилиты"
    register_utility(
        key="services",
        title="Работа со службами",
        trigger_text="Работа со службами",
        group="utilities",
        order=30,
        description="Просмотр списка служб и управление их запуском/остановкой",
    )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and message.text == "Работа со службами"
    )
    async def svc_entry(message: types.Message):
        """
        Точка входа в модуль работы со службами.
        """
        user_id = message.from_user.id

        svc_mode[user_id] = True
        svc_in_selection_menu[user_id] = False
        svc_current_page[user_id] = 0
        svc_service_list.pop(user_id, None)
        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Работа со службами'.")

        kb = get_services_main_keyboard()
        await message.answer(
            "🧩 Работа со службами Windows.\n"
            "Могу показать полный список служб или дать выбрать службу для управления.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and message.text == "Назад в утилиты"
    )
    async def svc_back_to_utilities(message: types.Message):
        """
        Выход из модуля служб в раздел утилит.
        """
        user_id = message.from_user.id

        svc_mode[user_id] = False
        svc_in_selection_menu.pop(user_id, None)
        svc_current_page.pop(user_id, None)
        svc_service_list.pop(user_id, None)
        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)

        kb = get_utilities_keyboard()
        await message.answer("Возвращаю в раздел утилит.", reply_markup=kb)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and message.text == "Показать все службы"
    )
    async def svc_show_all(message: types.Message):
        """
        Показ полного списка служб текстом (с разбиением по сообщениям).
        """
        user_id = message.from_user.id

        # При просмотре полного списка сбрасываем выбор службы/режим выбора,
        # чтобы не было путаницы.
        svc_in_selection_menu[user_id] = False
        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)

        write_bot_log(f"Пользователь {user_id} запросил полный список служб.")

        await message.answer("⌛ Получаю список служб, подожди немного...")
        text = build_full_service_list_text()
        await send_long_text(message, text)

        kb = get_services_main_keyboard()
        await message.answer(
            "Готово. Можешь выбрать другие действия в меню работы со службами.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and message.text == "Выбор службы"
    )
    async def svc_select_entry(message: types.Message):
        """
        Вход в режим выбора службы (клавиатура со списком служб).
        """
        user_id = message.from_user.id
        write_bot_log(f"Пользователь {user_id} вошёл в режим выбора службы.")

        await message.answer("⌛ Обновляю список служб...")
        refresh_services_list_for_user(user_id)

        services = svc_service_list.get(user_id, [])
        if not services:
            kb = get_services_main_keyboard()
            await message.answer(
                "Не удалось получить список служб или он пуст.\n"
                "Убедись, что бот запущен в Windows и с достаточными правами.",
                reply_markup=kb,
            )
            return

        svc_in_selection_menu[user_id] = True
        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)

        await send_services_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_in_selection_menu.get(message.from_user.id, False)
        and message.text == "Следующая страница служб"
    )
    async def svc_next_page(message: types.Message):
        """
        Перейти на следующую страницу списка служб.
        """
        user_id = message.from_user.id
        services = svc_service_list.get(user_id, [])
        if not services:
            svc_in_selection_menu[user_id] = False
            kb = get_services_main_keyboard()
            await message.answer(
                "Список служб не инициализирован. Нажми «Выбор службы», чтобы обновить.",
                reply_markup=kb,
            )
            return

        total_pages = (len(services) + PAGE_SIZE - 1) // PAGE_SIZE
        page = svc_current_page.get(user_id, 0)
        if page >= total_pages - 1:
            await message.answer("Это последняя страница списка служб.")
            await send_services_page(message, user_id)
            return

        svc_current_page[user_id] = page + 1
        await send_services_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_in_selection_menu.get(message.from_user.id, False)
        and message.text == "Предыдущая страница служб"
    )
    async def svc_prev_page(message: types.Message):
        """
        Перейти на предыдущую страницу списка служб.
        """
        user_id = message.from_user.id
        services = svc_service_list.get(user_id, [])
        if not services:
            svc_in_selection_menu[user_id] = False
            kb = get_services_main_keyboard()
            await message.answer(
                "Список служб не инициализирован. Нажми «Выбор службы», чтобы обновить.",
                reply_markup=kb,
            )
            return

        page = svc_current_page.get(user_id, 0)
        if page <= 0:
            await message.answer("Это первая страница списка служб.")
            await send_services_page(message, user_id)
            return

        svc_current_page[user_id] = page - 1
        await send_services_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and message.text == "Назад в меню служб"
    )
    async def svc_back_to_menu(message: types.Message):
        """
        Возврат из подменю (список/действия) в главное меню служб.
        """
        user_id = message.from_user.id

        svc_in_selection_menu[user_id] = False
        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)

        kb = get_services_main_keyboard()
        await message.answer("Возвращаюсь в меню работы со службами.", reply_markup=kb)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_selected_name.get(message.from_user.id) is not None
        and message.text == "Назад к списку служб"
    )
    async def svc_back_to_list(message: types.Message):
        """
        Возврат из меню действий к списку служб.
        """
        user_id = message.from_user.id

        svc_selected_name.pop(user_id, None)
        svc_selected_info.pop(user_id, None)
        svc_in_selection_menu[user_id] = True

        await send_services_page(message, user_id)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_selected_name.get(message.from_user.id) is not None
        and message.text == "Информация о службе"
    )
    async def svc_info(message: types.Message):
        """
        Показать подробную информацию о выбранной службе.
        """
        user_id = message.from_user.id
        name = svc_selected_name.get(user_id)
        if not name:
            kb = get_services_main_keyboard()
            await message.answer(
                "Сначала выбери службу через меню «Выбор службы».",
                reply_markup=kb,
            )
            return

        svc_obj = get_service_object(name)
        if svc_obj is None:
            await message.answer(
                "Не удалось получить объект службы.\n"
                "Убедись, что бот запущен в Windows и с достаточными правами.",
            )
            return

        try:
            display_name = svc_obj.display_name()
        except Exception:
            display_name = ""

        try:
            status = svc_obj.status()
        except Exception:
            status = "unknown"

        try:
            binpath = svc_obj.binpath()
        except Exception:
            binpath = ""

        try:
            start_type = svc_obj.start_type()
        except Exception:
            start_type = ""

        info_text = [
            f"Внутреннее имя: {name}",
            f"Отображаемое имя: {display_name or '-'}",
            f"Статус: {status or '-'}",
            f"Тип запуска: {start_type or '-'}",
            f"Путь к исполняемому файлу: {binpath or '-'}",
        ]

        await send_long_text(message, "\n".join(info_text))

        # Обновим сохранённую информацию
        svc_selected_info[user_id] = {
            "name": name,
            "display_name": display_name,
            "status": status,
            "start_type": start_type,
            "binpath": binpath,
        }

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_selected_name.get(message.from_user.id) is not None
        and message.text == "Запустить службу"
    )
    async def svc_start(message: types.Message):
        """
        Запустить выбранную службу.
        """
        user_id = message.from_user.id
        name = svc_selected_name.get(user_id)
        if not name:
            kb = get_services_main_keyboard()
            await message.answer(
                "Сначала выбери службу через меню «Выбор службы».",
                reply_markup=kb,
            )
            return

        svc_obj = get_service_object(name)
        if svc_obj is None:
            await message.answer(
                "Не удалось получить объект службы.\n"
                "Убедись, что бот запущен в Windows и с достаточными правами.",
            )
            return

        try:
            status = svc_obj.status()
        except Exception:
            status = "unknown"

        if status == "running":
            await message.answer("Служба уже запущена.")
            return

        write_bot_log(f"Пользователь {user_id} запросил запуск службы {name}.")

        output = run_sc_command(["start", name])
        await send_long_text(
            message,
            f"Попытка запуска службы '{name}'. Вывод команды sc:\n{output}",
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_selected_name.get(message.from_user.id) is not None
        and message.text == "Остановить службу"
    )
    async def svc_stop(message: types.Message):
        """
        Остановить выбранную службу.
        """
        user_id = message.from_user.id
        name = svc_selected_name.get(user_id)
        if not name:
            kb = get_services_main_keyboard()
            await message.answer(
                "Сначала выбери службу через меню «Выбор службы».",
                reply_markup=kb,
            )
            return

        svc_obj = get_service_object(name)
        if svc_obj is None:
            await message.answer(
                "Не удалось получить объект службы.\n"
                "Убедись, что бот запущен в Windows и с достаточными правами.",
            )
            return

        try:
            status = svc_obj.status()
        except Exception:
            status = "unknown"

        if status == "stopped":
            await message.answer("Служба уже остановлена.")
            return

        write_bot_log(f"Пользователь {user_id} запросил остановку службы {name}.")

        output = run_sc_command(["stop", name])
        await send_long_text(
            message,
            f"Попытка остановки службы '{name}'. Вывод команды sc:\n{output}",
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_selected_name.get(message.from_user.id) is not None
        and message.text == "Перезапустить службу"
    )
    async def svc_restart(message: types.Message):
        """
        Перезапустить выбранную службу (остановить и запустить заново).
        """
        user_id = message.from_user.id
        name = svc_selected_name.get(user_id)
        if not name:
            kb = get_services_main_keyboard()
            await message.answer(
                "Сначала выбери службу через меню «Выбор службы».",
                reply_markup=kb,
            )
            return

        write_bot_log(f"Пользователь {user_id} запросил перезапуск службы {name}.")

        stop_output = run_sc_command(["stop", name])
        start_output = run_sc_command(["start", name])

        combined = (
            f"Попытка перезапуска службы '{name}'.\n"
            f"--- Остановка (sc stop) ---\n{stop_output}\n\n"
            f"--- Запуск (sc start) ---\n{start_output}"
        )
        await send_long_text(message, combined)

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
        and svc_in_selection_menu.get(message.from_user.id, False)
    )
    async def svc_select_service(message: types.Message):
        """
        Обработка нажатия на кнопку службы в режиме выбора.
        """
        user_id = message.from_user.id
        text = message.text or ""
        name = parse_service_name_from_button(text)
        if not name:
            kb = build_services_page_keyboard(user_id)
            await message.answer(
                "Чтобы выбрать службу, нажми на кнопку с форматом "
                "\"internal_name | отображаемое имя [статус]\".",
                reply_markup=kb,
            )
            return

        svc_obj = get_service_object(name)
        if svc_obj is None:
            await message.answer(
                "Не удалось получить объект службы. Возможно, служба была удалена "
                "или бот запущен не в Windows.",
            )
            return

        try:
            display_name = svc_obj.display_name()
        except Exception:
            display_name = ""

        try:
            status = svc_obj.status()
        except Exception:
            status = "unknown"

        try:
            binpath = svc_obj.binpath()
        except Exception:
            binpath = ""

        svc_in_selection_menu[user_id] = False
        svc_selected_name[user_id] = name
        svc_selected_info[user_id] = {
            "name": name,
            "display_name": display_name,
            "status": status,
            "binpath": binpath,
        }

        kb = get_services_action_keyboard()
        await message.answer(
            f"Выбрана служба:\n"
            f"Внутреннее имя: {name}\n"
            f"Отображаемое имя: {display_name or '-'}\n"
            f"Статус: {status or '-'}\n"
            f"Путь: {binpath or '-'}\n"
            "Теперь выбери действие: показать информацию, запустить, "
            "остановить или перезапустить.",
            reply_markup=kb,
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users
        and svc_mode.get(message.from_user.id, False)
    )
    async def svc_fallback(message: types.Message):
        """
        Фолбэк-хендлер для режима работы со службами.
        """
        user_id = message.from_user.id

        if svc_in_selection_menu.get(user_id, False):
            kb = build_services_page_keyboard(user_id)
            await message.answer(
                "Сейчас активен режим выбора службы.\n"
                "Используй кнопки со списком служб или навигацию по страницам, "
                "либо нажми «Назад в меню служб».",
                reply_markup=kb,
            )
        elif svc_selected_name.get(user_id) is not None:
            kb = get_services_action_keyboard()
            await message.answer(
                "Сейчас выбрана конкретная служба.\n"
                "Используй кнопки для просмотра информации, запуска, "
                "остановки или перезапуска, "
                "либо нажми «Назад к списку служб» для возврата.",
                reply_markup=kb,
            )
        else:
            kb = get_services_main_keyboard()
            await message.answer(
                "Сейчас активен режим «Работа со службами».\n"
                "Можешь показать полный список служб или перейти к выбору конкретной службы, "
                "либо нажать «Назад в утилиты» для выхода.",
                reply_markup=kb,
            )
