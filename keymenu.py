from aiogram import types

def get_main_keyboard():
    """
    Возвращает основную клавиатуру с кнопками:
    "Статус сервера", "Статус сети", "Скриншот", "Список плагинов",
    "Дополнительно", "cmd", "утилиты", "консоль python"
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "Статус сервера",
        "Статус сети",
        "Скриншот",
        "Список плагинов",
        "Дополнительно",
        "cmd",
        "утилиты",
        "консоль python"
    ]
    kb.add(*buttons)
    return kb

def get_additional_keyboard():
    """
    Возвращает дополнительную клавиатуру с кнопками:
    "Заметки", "Отправить файлы", "Прием файлов", "Питание",
    "Плагины", "Справка", "лог", "Настройки", "Связь с разработчиком", "Назад"
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "Заметки",
        "Отправить файлы",
        "Прием файлов",
        "Питание",
        "Плагины",
        "Справка",
        "лог",
        "Настройки",
        "Связь с разработчиком",
        "Назад"
    ]
    kb.add(*buttons)
    return kb


def create_plugins_ext_menu():
    """
    Создаёт меню для плагинов с оригинальными и дополнительными кнопками.
    Теперь добавлена кнопка «Полный перезапуск» рядом с «Перезагрузить плагины».
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for btn in [
        "Список плагинов",
        "Перезагрузить плагины",
        "Полный перезапуск",
        "Настроить автозапуск",
        "Установка плагинов",
        "Скачать плагин",
        "Сброс настроек плагинов",
        "Удаление плагинов",
        "Резервные копии"
    ]:
        kb.add(types.KeyboardButton(btn))
    kb.add(types.KeyboardButton("Вернуться"))
    return kb

def backup_main_keyboard():
    """
    Формирует основное меню резервных копий.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for btn in ["Восстановить из резервной копии", "Сделать резервную копию", "Очистить резервные копии", "Назад"]:
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

import inspect
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
    Возвращает основное меню настроек:
    "Авторизация", "Система", "Память", "Сброс",
    "Резервное копирование и восстановление", "Информация", "Назад"
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Авторизация", "Система", "Память", "Сброс", "Резервное копирование и восстановление", "Информация", "Вернуться")
    return kb

