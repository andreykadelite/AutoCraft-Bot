"""menu_registry/keymenu_additional.py

Клавиатура раздела «Дополнительно».
Пункты берутся из additional_registry (динамический реестр).
"""

from aiogram import types

# Сначала новый путь (menu_registry), затем старый (для совместимости).
try:
    from menu_registry.additional_registry import get_additionals
except Exception:
    try:
        from additional_registry import get_additionals
    except Exception:
        get_additionals = None


def get_additional_keyboard() -> types.ReplyKeyboardMarkup:
    """Клавиатура для меню «Дополнительно».

    - Кнопки берутся из additional_registry.py (если он есть)
    - Внизу всегда добавляется «Назад в главное меню»
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    items = []
    if get_additionals is not None:
        try:
            items = get_additionals(group="additional")
        except Exception:
            items = []

    if items:
        for item in items:
            # Чтобы не было рассинхрона (title vs trigger_text), отправляем именно trigger_text.
            kb.add(item.trigger_text or item.title)

    kb.add("Назад в главное меню")
    return kb
