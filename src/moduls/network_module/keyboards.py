from aiogram import types


def get_network_main_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Мониторинг трафика", "Активные соединения")
    kb.add("Порты и процессы", "Брандмауэр")
    kb.add("Сетевые адаптеры", "Диагностика")
    kb.add("Сканер сети", "Информация о сети")
    kb.add("Информация о модуле")
    kb.add("Назад в утилиты")
    return kb


def get_monitor_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Обновить трафик")
    kb.add("Назад в модуль сети")
    return kb


def get_connections_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Обновить соединения")
    kb.add("Назад в модуль сети")
    return kb


def get_ports_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Информация по порту", "Завершить процесс на порту")
    kb.add("Блокировать порт", "Разблокировать порт")
    kb.add("Информация о портах")
    kb.add("Обновить список портов")
    kb.add("Назад в модуль сети")
    return kb


def get_firewall_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Блокировать приложение", "Разблокировать приложение")
    kb.add("Блокировать порт", "Разблокировать порт")
    kb.add("Обновить список процессов")
    kb.add("Правила бота в брандмауэре")
    kb.add("Назад в модуль сети")
    return kb


def get_adapters_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Состояние адаптеров")
    kb.add("Отключить адаптер", "Включить адаптер")
    kb.add("Обновить список адаптеров")
    kb.add("Назад в модуль сети")
    return kb


def get_diag_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Ping 8.8.8.8", "Ping 1.1.1.1")
    kb.add("Ping ya.ru")
    kb.add("Traceroute 8.8.8.8", "Traceroute ya.ru")
    kb.add("Сброс DNS", "Обновить IP (renew)")
    kb.add("Сброс IP (release)")
    kb.add("IPConfig /all")
    kb.add("Назад в модуль сети")
    return kb


def get_scan_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Сканировать localhost")
    kb.add("Сканировать 8.8.8.8", "Сканировать 1.1.1.1")
    kb.add("Сканировать ya.ru")
    kb.add("Назад в модуль сети")
    return kb
