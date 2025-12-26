from aiogram import types
import inspect

# Пытаемся аккуратно подтянуть реестр утилит.
# Если реестр перенесён в menu_registry — подхватываем оттуда.
# Если его нет (или старый вариант без реестра) — всё продолжит работать
# со статическим меню.
try:
    from menu_registry.utilities_registry import get_utilities
except ImportError:
    try:
        from utilities_registry import get_utilities
    except ImportError:
        get_utilities = None


# Пытаемся аккуратно подтянуть реестр меню «Дополнительно».
# Скрипты/реестр могли быть перенесены в menu_registry — сначала пробуем новый путь,
# затем fallback на старый (для совместимости).
try:
    from menu_registry.additional_registry import get_additionals
except ImportError:
    try:
        from additional_registry import get_additionals
    except ImportError:
        get_additionals = None

# Реестр главного меню (динамическое "Главное меню").
# Скрипты/реестр перенесены в папку menu_registry — сначала пробуем новый путь,
# затем fallback на старый (для совместимости).
try:
    from menu_registry.mainmenu_registry import get_main_items
except ImportError:
    try:
        from mainmenu_registry import get_main_items
    except ImportError:
        get_main_items = None


# Реестр настроек (динамическое меню 'Настройки').
# Скрипты перенесены в папку menu_registry — сначала пробуем новый путь,
# затем fallback на старый (для совместимости).
try:
    from menu_registry.settings_registry import get_settings
except ImportError:
    try:
        from settings_registry import get_settings
    except ImportError:
        get_settings = None




# Пытаемся аккуратно подтянуть реестр меню менеджера плагинов.
# Если его нет — create_plugins_ext_menu() останется со статическим меню.
try:
    # предпочтительно как пакет: menu_registry/plugins_menu_registry.py
    from menu_registry.plugins_menu_registry import get_plugin_menu_items
except ImportError:
    try:
        # fallback, если импортируют без пакета
        from plugins_menu_registry import get_plugin_menu_items
    except ImportError:
        get_plugin_menu_items = None

def get_main_keyboard():
    """
    Возвращает основную клавиатуру.

    Главное меню теперь полностью динамическое:
    - Кнопки берутся из mainmenu_registry.get_main_items("main").
    - Если в реестре ничего нет, клавиатура будет пустой.
    Внешний интерфейс (имя функции) не меняется, другие модули
    продолжают вызывать get_main_keyboard() как и раньше.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    dynamic_buttons = []

    if get_main_items is not None:
        try:
            items = get_main_items(group="main")
            if items:
                dynamic_buttons = [item.title for item in items]
        except Exception:
            dynamic_buttons = []

    for title in dynamic_buttons:
        kb.add(title)

    return kb



def get_additional_keyboard():
    """
    Клавиатура для раздела «Дополнительно».

    ВНЕШНЕ:
    - Функция и её название остаются прежними — другие модули менять не нужно.

    ВНУТРИ:
    - Если доступен additional_registry.get_additionals и в группе "additional"
      что-то зарегистрировано — строим меню из реестра.
    - Если реестр пуст или модуль не найден — показываем только кнопку «Назад».
      (Текст «пусто» должен отправлять вызывающий обработчик.)
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    dynamic_buttons = []
    if get_additionals is not None:
        try:
            items = get_additionals(group="additional")
            dynamic_buttons = [it.title for it in items]
        except Exception:
            dynamic_buttons = []

    if dynamic_buttons:
        for title in dynamic_buttons:
            kb.add(title)
        kb.add("Назад")
    else:
        kb.add("Назад")

    return kb



def get_utilities_keyboard():
    """
    Клавиатура для раздела 'утилиты'.

    ВНЕШНЕ:
    - Функция и её название остаются прежними.
    - Для utilites.py и остальных модулей всё выглядит как статическое меню.

    ВНУТРИ:
    - Если есть utilities_registry.get_utilities и в группе "utilities"
      что-то зарегистрировано — строим меню из реестра.
    - Если реестр пуст или модуль не найден — показываем только кнопку "Назад".
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    dynamic_buttons = []

    # Пытаемся взять список утилит из реестра
    if get_utilities is not None:
        try:
            utilities = get_utilities(group="utilities")
            if utilities:
                dynamic_buttons = [u.title for u in utilities]
        except Exception:
            # Если что-то пошло не так — просто считаем, что утилит нет
            dynamic_buttons = []

    if dynamic_buttons:
        # Динамический вариант через реестр
        for title in dynamic_buttons:
            kb.add(title)
        # Кнопка "Назад" — фиксированная, как и раньше
        kb.add("Назад")
    else:
        # В реестре нет утилит или реестр недоступен — только выход назад
        kb.add("Назад")

    return kb


def create_plugins_ext_menu():
    """
    Создаёт меню для менеджера плагинов.

    Логика как у 'утилит':
    - Если доступен реестр menu_registry.plugins_menu_registry и там в группе
      "plugins_menu" что-то зарегистрировано — строим меню из реестра.
    - Если реестр пуст (или недоступен) — показываем только кнопку «Вернуться».

    Внешний интерфейс (имя функции) не меняется — другие модули продолжают
    вызывать create_plugins_ext_menu() как и раньше.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    dynamic_buttons = []

    # Пытаемся взять список пунктов меню из реестра
    if get_plugin_menu_items is not None:
        try:
            items = get_plugin_menu_items(group="plugins_menu")
            if items:
                dynamic_buttons = [it.title for it in items if getattr(it, "title", None)]
        except Exception:
            dynamic_buttons = []

    if dynamic_buttons:
        for title in dynamic_buttons:
            title = (title or "").strip()
            if title:
                kb.add(types.KeyboardButton(title))

        # Кнопка "Вернуться" — фиксированная. Если её уже зарегистрировали в реестре,
        # повторно не добавляем.
        has_back = any((t or "").strip().lower() == "вернуться" for t in dynamic_buttons)
        if not has_back:
            kb.add(types.KeyboardButton("Вернуться"))
    else:
        # Пока ничего не зарегистрировано — только "Вернуться"
        kb.add(types.KeyboardButton("Вернуться"))

    return kb


def backup_main_keyboard():
    """
    Формирует основное меню резервных копий.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for btn in [
        "Восстановить из резервной копии",
        "Сделать резервную копию",
        "Очистить резервные копии",
        "Назад"
    ]:
        kb.add(types.KeyboardButton(btn))
    return kb


def create_list_keyboard(items, add_back=True):
    """
    Универсальная функция для создания клавиатуры из списка кнопок.
    Если add_back=True, в конец добавляется кнопка "Назад".
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for item in items:
        kb.add(types.KeyboardButton(item))
    if add_back:
        kb.add(types.KeyboardButton("Назад"))
    return kb


# Патчим метод add у ReplyKeyboardMarkup для автоматического добавления кнопки "Настройки"
_original_add = types.ReplyKeyboardMarkup.add


def patched_add(self, *buttons):
    stack = inspect.stack()
    if stack and len(stack) > 1:
        caller_function = stack[1].function
        if caller_function == "additional_menu":
            btns = list(buttons)
            if "Настройки" not in btns:
                if "Назад" in btns:
                    idx = btns.index("Назад")
                    btns.insert(idx, "Настройки")
                else:
                    btns.append("Настройки")
            buttons = tuple(btns)
    return _original_add(self, *buttons)


types.ReplyKeyboardMarkup.add = patched_add


def get_main_settings_keyboard():
    """
    Возвращает основное меню настроек.

    ВНЕШНЕ:
    - Функция и её название остаются прежними.
    - Остальные модули продолжают вызывать get_main_settings_keyboard() как и раньше.

    ВНУТРИ:
    - Если есть settings_registry.get_settings и в группе "settings"
      что-то зарегистрировано — строим меню из реестра.
    - Если реестр пуст или модуль не найден — показываем только кнопку "Вернуться".
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    dynamic_buttons = []

    if get_settings is not None:
        try:
            settings_items = get_settings(group="settings")
            if settings_items:
                dynamic_buttons = [s.title for s in settings_items]
        except Exception:
            dynamic_buttons = []

    if dynamic_buttons:
        for title in dynamic_buttons:
            kb.add(types.KeyboardButton(title))
        kb.add(types.KeyboardButton("Вернуться"))
    else:
        kb.add(types.KeyboardButton("Вернуться"))

    return kb

