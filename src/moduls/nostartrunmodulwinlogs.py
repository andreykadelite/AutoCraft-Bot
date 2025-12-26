import subprocess
from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility  # ✅ реестр утилит

# Режим просмотра логов Windows по пользователям
winlog_mode = {}
winlog_selected_log = {}
winlog_in_filter_menu = {}

# Соответствие названий кнопок и внутренних имён журналов Windows
LOG_SOURCES = {
    "Системный журнал": "System",
    "Журнал приложений": "Application",
    "Журнал безопасности": "Security",
    "Журнал установки": "Setup",
    "Журнал обновлений": "Microsoft-Windows-WindowsUpdateClient/Operational",
}

# Соответствие кнопок фильтров и уровней событий Windows
# Level: 1 - Critical, 2 - Error, 3 - Warning, 4 - Information, 5 - Verbose
FILTER_TYPES = {
    "Последние 20 записей": None,
    "Только ошибки (20)": 2,
    "Только предупреждения (20)": 3,
    "Только критические (20)": 1,
}


def get_winlog_category_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура выбора журнала Windows.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Системный журнал")
    kb.add("Журнал приложений")
    kb.add("Журнал безопасности")
    kb.add("Журнал установки")
    kb.add("Журнал обновлений")
    kb.add("Назад в утилиты")
    return kb


def get_winlog_filter_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура выбора фильтра/режима просмотра для выбранного журнала.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Последние 20 записей")
    kb.add("Только ошибки (20)")
    kb.add("Только предупреждения (20)")
    kb.add("Только критические (20)")
    kb.add("Назад к журналам")
    return kb


def read_event_log(log_name: str, count: int = 20, level: int = None) -> str:
    """
    Читает события из журнала Windows через wevtutil.
    :param log_name: Имя журнала (например, System, Application, Security).
    :param count: Количество последних записей.
    :param level: Уровень события (1,2,3,4,5) или None для всех.
    :return: Текстовый вывод журнала.
    """
    base_cmd = f'wevtutil qe "{log_name}" /f:text /c:{count} /rd:true'
    if level is not None:
        # Фильтр по уровню события через XPath, пример: /q:"*[System[(Level=2)]]"
        query = f'"*[System[(Level={level})]]"'
        cmd = f"{base_cmd} /q:{query}"
    else:
        cmd = base_cmd

    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    output = result.stdout.strip() or result.stderr.strip() or "Записей не найдено."
    return output


def register_handlers(dp: Dispatcher):
    """
    Регистрация хендлеров для просмотра логов Windows.
    """

    # ✅ Регистрируем утилиту в общем реестре, чтобы она появилась в динамическом меню "Утилиты"
    register_utility(
        key="winlogs",
        title="Просмотр логов Windows",
        trigger_text="Просмотр логов Windows",
        group="utilities",
        order=10,
        description="Просмотр системных журналов Windows (System, Application, Security и др.)",
    )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Просмотр логов Windows"
    )
    async def winlog_entry(message: types.Message):
        """
        Точка входа в модуль просмотра логов.
        """
        user_id = message.from_user.id
        winlog_mode[user_id] = True
        winlog_selected_log[user_id] = None
        winlog_in_filter_menu[user_id] = False

        write_bot_log(f"Пользователь {user_id} открыл модуль 'Просмотр логов Windows'.")

        keyboard = get_winlog_category_keyboard()
        await message.answer(
            "📄 Просмотр логов Windows.\n"
            "Выбери журнал, который хочешь посмотреть:",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winlog_mode.get(message.from_user.id, False) and
        message.text == "Назад к журналам"
    )
    async def winlog_back_to_categories(message: types.Message):
        """
        Возврат из меню фильтров к выбору журнала.
        """
        user_id = message.from_user.id

        # Выходим из меню фильтра, обнуляем выбранный журнал —
        # пусть пользователь выберет его заново.
        winlog_in_filter_menu[user_id] = False
        winlog_selected_log[user_id] = None

        keyboard = get_winlog_category_keyboard()
        await message.answer(
            "Выбор журнала Windows.\n"
            "Можешь выбрать другой журнал или вернуться в раздел утилит кнопкой "
            "«Назад в утилиты».",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winlog_mode.get(message.from_user.id, False) and
        message.text == "Назад в утилиты"
    )
    async def winlog_back_to_utilities(message: types.Message):
        """
        Выход из модуля логов обратно в меню утилит.
        """
        user_id = message.from_user.id

        winlog_mode[user_id] = False
        winlog_selected_log.pop(user_id, None)
        winlog_in_filter_menu.pop(user_id, None)

        keyboard = get_utilities_keyboard()
        await message.answer(
            "Возвращаюсь в раздел утилит.",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winlog_mode.get(message.from_user.id, False) and
        message.text in LOG_SOURCES.keys()
    )
    async def winlog_select_log(message: types.Message):
        """
        Выбор конкретного журнала (Система, Приложение и т.д.).
        """
        user_id = message.from_user.id
        log_title = message.text
        log_name = LOG_SOURCES.get(log_title)

        if not log_name:
            keyboard = get_winlog_category_keyboard()
            await message.answer(
                "Не удалось определить журнал. Попробуй выбрать его из меню ещё раз.",
                reply_markup=keyboard
            )
            return

        winlog_selected_log[user_id] = log_name
        winlog_in_filter_menu[user_id] = True

        write_bot_log(
            f"Пользователь {user_id} выбрал журнал Windows: {log_title} ({log_name})."
        )

        keyboard = get_winlog_filter_keyboard()
        await message.answer(
            f"Выбран журнал: *{log_title}*.\n"
            "Теперь выбери, какие записи показать:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winlog_mode.get(message.from_user.id, False) and
        winlog_in_filter_menu.get(message.from_user.id, False) and
        message.text in FILTER_TYPES.keys()
    )
    async def winlog_show_logs(message: types.Message):
        """
        Показ записей выбранного журнала с выбранным фильтром.
        """
        user_id = message.from_user.id
        log_name = winlog_selected_log.get(user_id)

        if not log_name:
            # На всякий случай вернёмся к выбору журнала
            winlog_in_filter_menu[user_id] = False
            keyboard = get_winlog_category_keyboard()
            await message.answer(
                "Сначала выбери журнал Windows.",
                reply_markup=keyboard
            )
            return

        filter_title = message.text
        level = FILTER_TYPES.get(filter_title)
        count = 20

        write_bot_log(
            f"Пользователь {user_id} запросил журнал '{log_name}', фильтр '{filter_title}', "
            f"уровень={level}, количество={count}."
        )

        await message.answer("⌛ Получаю данные журнала, подожди пару секунд...")

        try:
            output = read_event_log(log_name, count=count, level=level)
        except Exception as e:
            await message.answer(f"Ошибка при чтении журнала: {e}")
            return

        # Разбиваем вывод на части по 4000 символов, чтобы не упереться в лимит Telegram
        if len(output) > 4000:
            for i in range(0, len(output), 4000):
                chunk = output[i:i + 4000]
                await message.answer(f"```text\n{chunk}\n```", parse_mode="Markdown")
        else:
            await message.answer(f"```text\n{output}\n```", parse_mode="Markdown")

        # После вывода снова показываем меню фильтров
        keyboard = get_winlog_filter_keyboard()
        await message.answer(
            "Можешь выбрать другой фильтр для этого журнала или нажать "
            "«Назад к журналам» для возврата.",
            reply_markup=keyboard
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        winlog_mode.get(message.from_user.id, False)
    )
    async def winlog_fallback(message: types.Message):
        """
        Обработчик любых остальных сообщений, пока пользователь находится
        в режиме просмотра логов Windows.
        Подсказывает, как пользоваться меню.
        """
        user_id = message.from_user.id

        if not winlog_in_filter_menu.get(user_id):
            keyboard = get_winlog_category_keyboard()
            await message.answer(
                "Сейчас активен режим просмотра логов Windows.\n"
                "Используй кнопки меню для выбора журнала или нажми "
                "«Назад в утилиты» для выхода в раздел утилит.",
                reply_markup=keyboard
            )
        else:
            keyboard = get_winlog_filter_keyboard()
            await message.answer(
                "Сейчас активен режим просмотра логов Windows.\n"
                "Используй кнопки для выбора фильтра или нажми "
                "«Назад к журналам» для возврата к выбору журнала.",
                reply_markup=keyboard
            )
