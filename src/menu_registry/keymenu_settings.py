from aiogram import types

# Стараемся сначала импортировать из menu_registry (новый путь),
# а если не вышло — пробуем старый путь (на случай совместимости).
try:
    from menu_registry.settings_registry import get_settings
except Exception:
    try:
        from settings_registry import get_settings
    except Exception:
        get_settings = None


def get_settings_keyboard():
    """
    Клавиатура раздела «Настройки» (ReplyKeyboardMarkup).
    Строится динамически из settings_registry.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    items = []
    if get_settings is not None:
        try:
            items = get_settings(group="settings")
        except Exception:
            items = []

    if items:
        for it in items:
            kb.add(it.title)

    kb.add("Назад в главное меню")
    return kb
