import os
import asyncio
from asyncio.subprocess import PIPE, STDOUT
import subprocess
from aiogram import types
from aiogram.dispatcher import Dispatcher
from aiogram.utils.exceptions import MessageNotModified

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from bat_templates import BAT_TEMPLATES

# Состояния по пользователям
batrun_mode = {}           # активен ли вообще режим работы с BAT
batrun_state = {}          # текущее состояние:
                           # None, "await_path", "choose_from_list",
                           # "await_confirm", "await_bat_file",
                           # "await_text_content", "await_text_filename",
                           # "choose_template", "running"
batrun_selected_file = {}  # выбранный BAT-файл (полный путь)
batrun_files = {}          # доступные BAT-файлы из bat-files по пользователю
batrun_temp_text = {}      # временное хранилище текста BAT при создании из текста

# Шаблоны BAT и пагинация по ним
TEMPLATES_PER_PAGE = 15
batrun_template_titles = {}  # user_id -> список названий шаблонов
batrun_template_page = {}    # user_id -> текущая страница (0-based)

# Данные для запущенных процессов
batrun_process = {}        # user_id -> asyncio.subprocess.Process
batrun_output_task = {}    # user_id -> asyncio.Task
batrun_output_message = {} # user_id -> (chat_id, message_id)


def get_base_dir() -> str:
    """
    Пытаемся взять base_dir из __main__, если есть.
    Если нет - используем директорию текущего файла.
    """
    try:
        import __main__
        base_dir = getattr(__main__, "base_dir", None)
        if base_dir:
            return base_dir
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def get_bat_dir() -> str:
    """
    Папка bat-files рядом с основным скриптом.
    Создаётся автоматически, если её нет.
    """
    base_dir = get_base_dir()
    bat_dir = os.path.join(base_dir, "bat-files")
    os.makedirs(bat_dir, exist_ok=True)
    return bat_dir


def get_bat_main_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Главное меню работы с BAT.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Запустить BAT по пути")
    kb.add("BAT из папки")
    kb.add("Стандартные шаблонные конструкции")
    kb.add("Создать BAT из текста")
    kb.add("Загрузить BAT-файл")
    kb.add("Назад в утилиты")
    return kb


def get_bat_list_keyboard(file_names) -> types.ReplyKeyboardMarkup:
    """
    Меню для выбора BAT-файла из папки bat-files.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for name in file_names:
        kb.add(name)
    kb.add("Назад в BAT-меню")
    return kb


def get_bat_templates_keyboard(titles, has_prev: bool = False, has_next: bool = False) -> types.ReplyKeyboardMarkup:
    """
    Меню для выбора стандартного BAT-шаблона с поддержкой пагинации.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for title in titles:
        kb.add(title)
    # Кнопки навигации по страницам шаблонов
    if has_prev:
        kb.add("Пред. страница СШК")
    if has_next:
        kb.add("След. страница СШК")
    kb.add("Назад в BAT-меню")
    return kb


def get_template_page_data(user_id: int):
    """
    Возвращает список заголовков шаблонов для текущей страницы пользователя,
    а также флаги наличия предыдущей/следующей страниц и номера страниц.
    """
    titles = batrun_template_titles.get(user_id) or []
    total = len(titles)
    if total == 0:
        return [], False, False, 0, 0

    page = batrun_template_page.get(user_id, 0)
    total_pages = (total - 1) // TEMPLATES_PER_PAGE + 1

    # Нормализуем номер страницы
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    batrun_template_page[user_id] = page

    start = page * TEMPLATES_PER_PAGE
    end = start + TEMPLATES_PER_PAGE
    page_titles = titles[start:end]

    has_prev = page > 0
    has_next = page < total_pages - 1

    return page_titles, has_prev, has_next, page, total_pages


def get_bat_confirm_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура подтверждения запуска BAT.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("✅ Запустить этот BAT")
    kb.add("❌ Отмена запуска BAT")
    return kb


def get_bat_running_keyboard() -> types.ReplyKeyboardMarkup:
    """
    Клавиатура во время выполнения BAT.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("⏹ Остановить BAT")
    kb.add("Назад в BAT-меню")
    return kb


def read_bat_content(path: str) -> str:
    """
    Чтение содержимого BAT-файла с попыткой нескольких кодировок.
    """
    for enc in ("utf-8", "cp1251", "cp866"):
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                return f.read()
        except Exception:
            continue
    # Фолбэк на байты
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return "<не удалось прочитать содержимое файла>"


def run_bat_file_sync(path: str) -> str:
    """
    Синхронный запуск BAT-файла и возврат вывода.
    ОСТАВЛЕНО ДЛЯ СОВМЕСТИМОСТИ, В ОСНОВНОМ ИСПОЛЬЗУЕТСЯ АСИНХРОННЫЙ СТРИМ.
    """
    cmd = f'"{path}"'
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="cp866",  # консольная кодировка Windows
        errors="ignore",
    )
    output = result.stdout.strip() or result.stderr.strip() or "Команда выполнена без вывода."
    output += f"\n\n[Код возврата: {result.returncode}]"
    return output


def sanitize_filename(name: str) -> str:
    """
    Убираем из имени файла недопустимые символы для Windows.
    """
    forbidden = '\\/:*?"<>|'
    cleaned = "".join(ch for ch in name if ch not in forbidden).strip()
    if not cleaned:
        return ""
    return cleaned


def ensure_unique_path(directory: str, filename: str) -> str:
    """
    Возвращает путь, который не существует.
    Если файл уже есть, добавляет _1, _2 и т.д. перед расширением.
    """
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{counter}{ext}")
        counter += 1
    return candidate



def sanitize_telegram_text(text: str) -> str:
    """
    Подготовка текста для отправки в Telegram:
    – нормализуем переводы строк;
    – вырезаем управляющие символы (< 0x20), кроме \n и \t.
    Это помогает избежать ошибок парсинга JSON на стороне Telegram.
    """
    if not text:
        return ""
    # Нормализуем переводы строк
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Убираем управляющие символы, кроме перевода строки и табуляции
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or ord(ch) >= 32
    )
    return text



async def start_bat_and_stream(message: types.Message, path: str) -> None:
    """
    Запускает BAT-файл асинхронно, стримит вывод в Telegram
    и даёт возможность остановить процесс.

    ВАЖНО: «живого» сообщения с edit_message_text больше нет.
    Вывод всегда отправляется отдельными сообщениями (резервный режим),
    чтобы избежать проблем с лимитами Telegram и ошибками обновления сообщений.
    """
    user_id = message.from_user.id
    cmd = f'"{path}"'

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=PIPE,
            stderr=STDOUT,
        )
    except Exception as e:
        err_text = f"Ошибка при запуске BAT:\n{repr(e)}"
        write_bot_log(f"Ошибка запуска BAT для пользователя {user_id}: {repr(e)}")
        await message.answer(err_text)
        return

    batrun_process[user_id] = proc
    batrun_state[user_id] = "running"

    running_keyboard = get_bat_running_keyboard()
    await message.answer(
        "BAT-файл запущен. Вывод будет приходить отдельными сообщениями.\n"
        "Чтобы остановить выполнение, используй кнопку «⏹ Остановить BAT».",
        reply_markup=running_keyboard,
    )

    async def reader():
        buf = ""             # полный накопленный вывод
        sent_len = 0         # сколько символов уже отправлено пользователю
        had_any_output = False
        loop = asyncio.get_event_loop()
        last_send_time = 0.0

        try:
            while True:
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    break

                if isinstance(chunk, bytes):
                    text = chunk.decode("cp866", errors="ignore")
                else:
                    text = str(chunk)

                if not text:
                    continue

                text = sanitize_telegram_text(text)
                if not text:
                    continue

                had_any_output = True
                buf += text

                # Не спамим: отправляем не чаще, чем раз в 1.5 секунды
                now = loop.time()
                if (now - last_send_time) >= 1.5 and len(buf) > sent_len:
                    new_part = buf[sent_len:]
                    if new_part.strip():
                        # Ограничиваем размер одного сообщения
                        MAX_PART_CHARS = 1500
                        part = new_part[-MAX_PART_CHARS:]
                        await message.answer(
                            "Фрагмент вывода BAT:\n"
                            f"{part}"
                        )
                        sent_len = len(buf)
                        last_send_time = now

            # Ждём завершения процесса
            await proc.wait()
            rc = proc.returncode
        except Exception as e:
            err_info = f"Ошибка при чтении вывода BAT: {repr(e)}"
            write_bot_log(err_info)
            await message.answer(err_info)
            rc = None
        finally:
            batrun_process.pop(user_id, None)
            batrun_output_task.pop(user_id, None)
            batrun_output_message.pop(user_id, None)
            batrun_state[user_id] = None

            # Финальное сообщение с итогом выполнения
            if not buf.strip():
                if had_any_output:
                    buf_final = "Команда выполнена без выводимых строк или окно завершилось."
                else:
                    buf_final = (
                        "BAT-файл не вывел ничего в стандартный вывод.\n"
                        "Возможные причины:\n"
                        "• скрипт открывает отдельное окно и пишет туда;\n"
                        "• BAT работает в графическом режиме;\n"
                        "• вывод перенаправлен в файл или в другое приложение."
                    )
            else:
                buf_final = buf

            if rc is not None:
                buf_final += f"\n\n[Код возврата: {rc}]"

            # Лимитируем размер финального сообщения
            MAX_FINAL_CHARS = 3500
            if len(buf_final) > MAX_FINAL_CHARS:
                display_final = buf_final[-MAX_FINAL_CHARS:]
                prefix = "Итоговый вывод BAT (последние 3500 символов):\n"
            else:
                display_final = buf_final
                prefix = "Итоговый вывод BAT:\n"

            await message.answer(
                prefix + display_final
            )

            # Сообщаем о завершении и возвращаем стандартное меню
            kb_done = get_bat_main_keyboard()
            await message.answer(
                "BAT-процесс завершён.",
                reply_markup=kb_done,
            )

    task = asyncio.create_task(reader())
    batrun_output_task[user_id] = task


def register_handlers(dp: Dispatcher):
    """
    Регистрация хендлеров для работы с BAT-файлами.
    """

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        message.text == "Работа с BAT"
    )
    async def bat_entry(message: types.Message):
        """
        Вход в режим работы с BAT.
        """
        user_id = message.from_user.id
        batrun_mode[user_id] = True
        batrun_state[user_id] = None
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)

        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)
        bat_dir = get_bat_dir()
        write_bot_log(
            f"Пользователь {user_id} открыл модуль 'Работа с BAT'. Папка BAT: {bat_dir}"
        )

        keyboard = get_bat_main_keyboard()
        await message.answer(
            "🧩 Режим работы с BAT-файлами.\n\n"
            "Возможности:\n"
            "• Запустить BAT по полному пути.\n"
            "• Выбрать и запустить BAT из папки bat-files.\n"
            "• Стандартные шаблонные конструкции (готовые примеры BAT).\n"
            "• Создать BAT из текста и сохранить в bat-files.\n"
            "• Загрузить готовый BAT-файл и сохранить в bat-files.\n\n"
            f"Папка для файлов: `{bat_dir}`",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Назад в утилиты"
    )
    async def bat_back_to_utilities(message: types.Message):
        """
        Выход из модуля работы с BAT обратно в меню утилит.
        Если в этот момент идёт выполнение BAT — просто возвращаем клавиатуру,
        а процесс продолжит работать.
        """
        user_id = message.from_user.id
        batrun_mode[user_id] = False
        batrun_state[user_id] = None
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        keyboard = get_utilities_keyboard()
        await message.answer(
            "Возвращаюсь в раздел утилит.",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Запустить BAT по пути"
    )
    async def bat_start_by_path(message: types.Message):
        """
        Режим: ожидание полного пути к BAT-файлу.
        """
        user_id = message.from_user.id

        if batrun_state.get(user_id) == "running":
            await message.answer(
                "Сейчас уже выполняется BAT-файл. Сначала останови его или дождись завершения.",
                reply_markup=get_bat_running_keyboard(),
            )
            return

        batrun_state[user_id] = "await_path"
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Назад в BAT-меню")
        await message.answer(
            "Отправь полный путь к .bat файлу, который нужно запустить.\n\n"
            "Например: `C:\\\\scripts\\\\test.bat`",
            reply_markup=kb,
            parse_mode="Markdown",
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "BAT из папки"
    )
    async def bat_from_folder(message: types.Message):
        """
        Выбор BAT-файла из папки bat-files.
        """
        user_id = message.from_user.id

        if batrun_state.get(user_id) == "running":
            await message.answer(
                "Сейчас уже выполняется BAT-файл. Сначала останови его или дождись завершения.",
                reply_markup=get_bat_running_keyboard(),
            )
            return

        bat_dir = get_bat_dir()

        files = []
        try:
            for name in os.listdir(bat_dir):
                full = os.path.join(bat_dir, name)
                if os.path.isfile(full) and name.lower().endswith(".bat"):
                    files.append(name)
        except Exception as e:
            await message.answer(f"Ошибка доступа к папке BAT: {e}")
            return

        if not files:
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "В папке bat-files пока нет .bat файлов.\n"
                f"Путь к папке: `{bat_dir}`\n\n"
                "Закинь туда свои BAT-скрипты, создай их через этот модуль "
                "или загрузи файлом, а потом попробуй ещё раз.",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return

        batrun_state[user_id] = "choose_from_list"
        batrun_files[user_id] = files
        batrun_selected_file.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        keyboard = get_bat_list_keyboard(files)
        await message.answer(
            "Выбери BAT-файл из списка для просмотра и запуска:",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Стандартные шаблонные конструкции"
    )
    async def bat_templates_menu(message: types.Message):
        """
        Меню выбора стандартных BAT-шаблонов.
        """
        user_id = message.from_user.id

        if batrun_state.get(user_id) == "running":
            await message.answer(
                "Сейчас уже выполняется BAT-файл. Сначала останови его или дождись завершения.",
                reply_markup=get_bat_running_keyboard(),
            )
            return

        titles = list(BAT_TEMPLATES.keys())
        if not titles:
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "Стандартные шаблоны BAT не найдены.",
                reply_markup=keyboard,
            )
            return

        # Сохраняем полный список шаблонов и начинаем с первой страницы
        titles.sort()
        batrun_template_titles[user_id] = titles
        batrun_template_page[user_id] = 0

        batrun_state[user_id] = "choose_template"
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)

        page_titles, has_prev, has_next, page, total_pages = get_template_page_data(user_id)
        keyboard = get_bat_templates_keyboard(page_titles, has_prev, has_next)
        await message.answer(
            "Выбери стандартный BAT-шаблон из списка.\n"
            "После выбора покажу описание, содержимое и спрошу подтверждение запуска.\n"
            f"Страница {page + 1} из {total_pages}.",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Создать BAT из текста"
    )
    async def bat_create_from_text(message: types.Message):
        """
        Создание BAT-файла из отправленного текста.
        """
        user_id = message.from_user.id

        if batrun_state.get(user_id) == "running":
            await message.answer(
                "Сейчас уже выполняется BAT-файл. Сначала останови его или дождись завершения.",
                reply_markup=get_bat_running_keyboard(),
            )
            return

        batrun_state[user_id] = "await_text_content"
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Назад в BAT-меню")
        await message.answer(
            "Отправь текст BAT-скрипта ОДНИМ сообщением.\n\n"
            "После этого я спрошу, под каким именем сохранить файл в папку bat-files.",
            reply_markup=kb,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Загрузить BAT-файл"
    )
    async def bat_upload_file_command(message: types.Message):
        """
        Включаем режим ожидания загрузки BAT-файла как документа.
        """
        user_id = message.from_user.id

        if batrun_state.get(user_id) == "running":
            await message.answer(
                "Сейчас уже выполняется BAT-файл. Сначала останови его или дождись завершения.",
                reply_markup=get_bat_running_keyboard(),
            )
            return

        batrun_state[user_id] = "await_bat_file"
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Назад в BAT-меню")
        await message.answer(
            "Отправь BAT-файл как документ (файл), а не как текст.\n"
            "Я сохраню его в папку bat-files рядом со скриптом.",
            reply_markup=kb,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "Назад в BAT-меню"
    )
    async def bat_back_to_menu(message: types.Message):
        """
        Возврат в главное меню работы с BAT.
        """
        user_id = message.from_user.id
        # не трогаем запущенный процесс, только состояние меню
        if batrun_state.get(user_id) != "running":
            batrun_state[user_id] = None
        batrun_selected_file.pop(user_id, None)
        batrun_files.pop(user_id, None)
        batrun_temp_text.pop(user_id, None)
        batrun_template_titles.pop(user_id, None)
        batrun_template_page.pop(user_id, None)

        keyboard = get_bat_main_keyboard()
        await message.answer(
            "Главное меню работы с BAT.",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        message.text == "⏹ Остановить BAT"
    )
    async def bat_stop(message: types.Message):
        """
        Остановка текущего BAT-процесса.
        """
        user_id = message.from_user.id
        proc = batrun_process.get(user_id)

        if not proc:
            await message.answer(
                "Сейчас нет запущенного BAT-процесса.",
                reply_markup=get_bat_main_keyboard(),
            )
            return

        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        except Exception as e:
            err_info = f"Не удалось остановить BAT-процесс: {repr(e)}"
            write_bot_log(err_info)
            await message.answer(err_info)
            return

        await message.answer(
            "Пробую остановить BAT-процесс...",
            reply_markup=get_bat_running_keyboard(),
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "await_path"
    )
    async def bat_handle_path(message: types.Message):
        """
        Обработка текста как пути к BAT-файлу.
        """
        user_id = message.from_user.id
        path = message.text.strip().strip('"')

        if not path.lower().endswith(".bat"):
            await message.answer("Это не похоже на .bat файл. Укажи путь к .bat файлу.")
            return

        if not os.path.isfile(path):
            await message.answer("Файл по этому пути не найден. Проверь путь и попробуй ещё раз.")
            return

        batrun_selected_file[user_id] = path
        batrun_state[user_id] = "await_confirm"

        content = read_bat_content(path)
        write_bot_log(f"Пользователь {user_id} готовит к запуску BAT по пути: {path}")

        # Показываем предупреждение и содержимое файла
        await message.answer(
            "⚠️ ВНИМАНИЕ!\n"
            "Сейчас будет выполнен BAT-файл на этом компьютере.\n"
            "Убедись, что ты доверяешь этому содержимому.\n\n"
            f"Файл: `{path}`",
            parse_mode="Markdown",
        )

        # Разбиваем содержимое на части, чтобы влезло в лимит
        if not content:
            await message.answer("Файл пустой или не удалось прочитать его содержимое.")
        else:
            chunk_size = 3500  # чуть меньше 4096, с запасом на ```bat
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                await message.answer(f"```bat\n{chunk}\n```", parse_mode="Markdown")

        keyboard = get_bat_confirm_keyboard()
        await message.answer(
            "Запустить этот BAT-файл?",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "choose_from_list"
    )
    async def bat_choose_from_list(message: types.Message):
        """
        Обработка выбора BAT-файла из папки bat-files.
        """
        user_id = message.from_user.id
        files = batrun_files.get(user_id) or []
        name = message.text.strip()

        if name not in files:
            await message.answer(
                "Такого файла нет в списке. Выбери один из предложенных или нажми "
                "«Назад в BAT-меню».",
            )
            return

        bat_dir = get_bat_dir()
        path = os.path.join(bat_dir, name)

        batrun_selected_file[user_id] = path
        batrun_state[user_id] = "await_confirm"

        content = read_bat_content(path)
        write_bot_log(
            f"Пользователь {user_id} выбрал BAT из папки: {path}"
        )

        await message.answer(
            "⚠️ ВНИМАНИЕ!\n"
            "Сейчас будет выполнен BAT-файл из папки bat-files.\n"
            "Убедись, что ты доверяешь этому содержимому.\n\n"
            f"Файл: `{path}`",
            parse_mode="Markdown",
        )

        if not content:
            await message.answer("Файл пустой или не удалось прочитать его содержимое.")
        else:
            chunk_size = 3500
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                await message.answer(f"```bat\n{chunk}\n```", parse_mode="Markdown")

        keyboard = get_bat_confirm_keyboard()
        await message.answer(
            "Запустить этот BAT-файл?",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "choose_template" and
        message.text in ("След. страница СШК", "Пред. страница СШК")
    )
    async def bat_templates_pagination(message: types.Message):
        """
        Переключение страниц списка стандартных BAT-шаблонов.
        """
        user_id = message.from_user.id

        titles = batrun_template_titles.get(user_id) or []
        if not titles:
            # На всякий случай возвращаемся в главное меню BAT
            batrun_state[user_id] = None
            batrun_template_titles.pop(user_id, None)
            batrun_template_page.pop(user_id, None)
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "Список шаблонов пуст. Возвращаюсь в меню работы с BAT.",
                reply_markup=keyboard,
            )
            return

        # Меняем страницу в зависимости от нажатой кнопки
        direction = 1 if message.text == "След. страница СШК" else -1
        current_page = batrun_template_page.get(user_id, 0) + direction
        batrun_template_page[user_id] = current_page

        page_titles, has_prev, has_next, page, total_pages = get_template_page_data(user_id)
        keyboard = get_bat_templates_keyboard(page_titles, has_prev, has_next)

        await message.answer(
            f"Страница {page + 1} из {total_pages}. Выбери шаблон:",
            reply_markup=keyboard,
        )


    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "choose_template"
    )
    async def bat_choose_template(message: types.Message):
        """
        Обработка выбора стандартного BAT-шаблона.
        """
        user_id = message.from_user.id
        title = message.text.strip()

        if title not in BAT_TEMPLATES:
            await message.answer(
                "Такого шаблона нет в списке. Выбери один из предложенных или "
                "нажми «Назад в BAT-меню».",
            )
            return

        template = BAT_TEMPLATES[title]
        filename = sanitize_filename(template.get("filename") or title) or "template.bat"
        if not filename.lower().endswith(".bat"):
            filename += ".bat"

        bat_dir = get_bat_dir()
        path = ensure_unique_path(bat_dir, filename)

        content = template.get("content") or ""
        description = template.get("description") or "Описание отсутствует."

        try:
            with open(path, "w", encoding="cp1251", errors="ignore") as f:
                f.write(content)
        except Exception as e:
            await message.answer(f"Ошибка при сохранении шаблона в файл: {e}")
            return

        batrun_selected_file[user_id] = path
        batrun_state[user_id] = "await_confirm"

        write_bot_log(
            f"Пользователь {user_id} выбрал стандартный BAT-шаблон '{title}', файл: {path}"
        )

        await message.answer(
            "⚠️ ВНИМАНИЕ!\n"
            "Будет выполнен стандартный BAT-шаблон.\n"
            "Убедись, что понимаешь, что он делает.\n\n"
            f"*Шаблон:* {title}\n"
            f"*Описание:* {description}\n"
            "Файл сохранён в папку bat-files и будет запущен оттуда.\n"
            f"`{path}`",
            parse_mode="Markdown",
        )

        if content:
            chunk_size = 3500
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                await message.answer(f"```bat\n{chunk}\n```", parse_mode="Markdown")
        else:
            await message.answer("Содержимое шаблона пустое.")

        keyboard = get_bat_confirm_keyboard()
        await message.answer(
            "Запустить этот BAT-файл?",
            reply_markup=keyboard,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "await_text_content"
    )
    async def bat_handle_text_content(message: types.Message):
        """
        Получаем текст BAT-скрипта одним сообщением.
        """
        user_id = message.from_user.id
        content = message.text or ""
        content = content.replace("\r\n", "\n")

        if not content.strip():
            await message.answer(
                "Текст пустой. Отправь содержимое BAT-скрипта одним сообщением "
                "или нажми «Назад в BAT-меню».",
            )
            return

        batrun_temp_text[user_id] = content
        batrun_state[user_id] = "await_text_filename"

        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Назад в BAT-меню")
        await message.answer(
            "Принял текст BAT-скрипта.\n"
            "Теперь отправь имя файла (без пути). Можно без расширения, я добавлю `.bat`.",
            reply_markup=kb,
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "await_text_filename"
    )
    async def bat_handle_text_filename(message: types.Message):
        """
        Получаем имя файла для сохранения BAT-скрипта из текста.
        """
        user_id = message.from_user.id
        name_raw = (message.text or "").strip().strip('"')

        name_clean = sanitize_filename(name_raw)
        if not name_clean:
            await message.answer(
                "Имя файла пустое или содержит только недопустимые символы.\n"
                "Попробуй ещё раз, без путей и специальных символов.",
            )
            return

        if not name_clean.lower().endswith(".bat"):
            name_clean += ".bat"

        bat_dir = get_bat_dir()
        path = os.path.join(bat_dir, name_clean)

        content = batrun_temp_text.get(user_id, "")
        if not content:
            batrun_state[user_id] = None
            batrun_temp_text.pop(user_id, None)
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "Текст BAT-скрипта потерян. Начни создание заново.",
                reply_markup=keyboard,
            )
            return

        try:
            with open(path, "w", encoding="cp1251", errors="ignore") as f:
                f.write(content)
        except Exception as e:
            await message.answer(f"Ошибка при сохранении файла: {e}")
            return

        write_bot_log(
            f"Пользователь {user_id} создал BAT из текста: {path}"
        )

        batrun_state[user_id] = None
        batrun_temp_text.pop(user_id, None)

        keyboard = get_bat_main_keyboard()
        await message.answer(
            f"BAT-файл сохранён как `{name_clean}` в папку bat-files.\n"
            f"Полный путь: `{path}`",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    @dp.message_handler(
        lambda message: message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "await_bat_file",
        content_types=types.ContentType.DOCUMENT,
    )
    async def bat_handle_document(message: types.Message):
        """
        Обработка загруженного BAT-файла как документа,
        если активен режим 'Работа с BAT' и ожидается файл.
        """
        user_id = message.from_user.id

        document = message.document
        if not document:
            await message.answer("Не вижу файла. Отправь BAT-файл как документ.")
            return

        filename = document.file_name or "script.bat"
        if not filename.lower().endswith(".bat"):
            await message.answer("Это не .bat файл. Отправь файл с расширением .bat.")
            return

        name_clean = sanitize_filename(filename)
        if not name_clean.lower().endswith(".bat"):
            name_clean += ".bat"

        bat_dir = get_bat_dir()
        path = os.path.join(bat_dir, name_clean)

        try:
            await document.download(destination_file=path)
        except Exception as e:
            await message.answer(f"Ошибка при сохранении файла: {e}")
            return

        write_bot_log(
            f"Пользователь {user_id} загрузил BAT-файл через Telegram: {path}"
        )

        batrun_state[user_id] = None

        keyboard = get_bat_main_keyboard()
        await message.answer(
            f"BAT-файл сохранён как `{name_clean}` в папку bat-files.\n"
            f"Полный путь: `{path}`",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False) and
        batrun_state.get(message.from_user.id) == "await_confirm" and
        message.text in ("✅ Запустить этот BAT", "❌ Отмена запуска BAT")
    )
    async def bat_confirm_run(message: types.Message):
        """
        Подтверждение или отмена запуска выбранного BAT-файла.
        """
        user_id = message.from_user.id
        path = batrun_selected_file.get(user_id)

        if not path:
            batrun_state[user_id] = None
            batrun_selected_file.pop(user_id, None)
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "BAT-файл не выбран. Возвращаюсь в меню работы с BAT.",
                reply_markup=keyboard,
            )
            return

        if message.text == "❌ Отмена запуска BAT":
            write_bot_log(
                f"Пользователь {user_id} отменил запуск BAT-файла: {path}"
            )
            batrun_state[user_id] = None
            batrun_selected_file.pop(user_id, None)
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "Запуск BAT отменён.",
                reply_markup=keyboard,
            )
            return

        # Запускаем BAT асинхронно со стримом вывода
        write_bot_log(f"Пользователь {user_id} подтвердил запуск BAT-файла: {path}")
        await message.answer("🚀 Запускаю BAT-файл, готовлю вывод...")

        await start_bat_and_stream(message, path)

    @dp.message_handler(lambda message:
        message.from_user.id in authorized_users and
        batrun_mode.get(message.from_user.id, False)
    )
    async def bat_fallback(message: types.Message):
        """
        Обработчик любых остальных сообщений в режиме работы с BAT.
        """
        user_id = message.from_user.id
        state = batrun_state.get(user_id)

        if state is None:
            keyboard = get_bat_main_keyboard()
            await message.answer(
                "Сейчас активен режим работы с BAT-файлами.\n"
                "Используй кнопки меню или нажми «Назад в утилиты» для выхода.",
                reply_markup=keyboard,
            )
        elif state == "await_path":
            await message.answer(
                "Ожидаю полный путь к .bat файлу.\n"
                "Или нажми «Назад в BAT-меню».",
            )
        elif state == "choose_from_list":
            await message.answer(
                "Выбирай BAT-файл из списка или нажми «Назад в BAT-меню».",
            )
        elif state == "choose_template":
            await message.answer(
                "Выбирай шаблон из списка, перелистывай страницы кнопками «След. страница СШК»/«Пред. страница СШК» или нажми «Назад в BAT-меню».",
            )
        elif state == "await_confirm":
            keyboard = get_bat_confirm_keyboard()
            await message.answer(
                "Подтверди запуск BAT-файла или нажми отмену.",
                reply_markup=keyboard,
            )
        elif state == "running":
            await message.answer(
                "Сейчас выполняется BAT-файл. Вывод обновляется в отдельном сообщении.\n"
                "Чтобы остановить выполнение, нажми «⏹ Остановить BAT».",
                reply_markup=get_bat_running_keyboard(),
            )
        elif state == "await_bat_file":
            await message.answer(
                "Ожидаю .bat файл как документ.\n"
                "Отправь файл или нажми «Назад в BAT-меню».",
            )
        elif state == "await_text_content":
            await message.answer(
                "Ожидаю текст BAT-скрипта одним сообщением.\n"
                "Или нажми «Назад в BAT-меню».",
            )
        elif state == "await_text_filename":
            await message.answer(
                "Ожидаю имя файла для сохранения BAT-скрипта.\n"
                "Или нажми «Назад в BAT-меню».",
            )