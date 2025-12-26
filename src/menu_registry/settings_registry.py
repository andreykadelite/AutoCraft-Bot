"""
Простой реестр настроек для динамических меню.

Идея такая же, как у utilities_registry.py:
- Любой модуль, который хочет кнопку/пункт в разделе «Настройки», вызывает register_setting().
- Клавиатура «Настройки» берёт список через get_settings() и строит кнопки из title.

Пример регистрации в модуле:

    # Новый путь (если файлы лежат в папке menu_registry)
    from menu_registry.settings_registry import register_setting

    register_setting(
        key="mainmenu_cfg",
        title="Настройка главного меню",
        trigger_text="Настройка главного меню",
        group="settings",
        order=10,
        description="Включение/выключение кнопок главного меню"
    )

Примечание про совместимость:
- Этот модуль может быть импортирован как menu_registry.settings_registry.
- Для удобства старого кода он также старается выставить алиас в sys.modules под именем 'settings_registry'
  (если сначала был импортирован через menu_registry.settings_registry).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import sys as _sys


@dataclass
class SettingDescriptor:
    """Описание одного пункта настроек."""
    key: str
    title: str
    trigger_text: str
    group: str = "settings"
    order: int = 100
    description: str = ""


_registry: Dict[str, SettingDescriptor] = {}


def register_setting(
    key: str,
    title: str,
    trigger_text: Optional[str] = None,
    group: str = "settings",
    order: int = 100,
    description: str = "",
) -> None:
    """
    Зарегистрировать пункт настроек.

    key          — уникальный идентификатор (для кода)
    title        — текст на кнопке
    trigger_text — текст, по которому ловит handler (message.text).
                   Если не указан — берётся title.
    group        — раздел меню ('settings', 'security', 'network', ...)
    order        — сортировка внутри группы
    description  — необязательное описание (для логов/отладки)

    Если key уже есть — запись перезапишется (последняя победила).
    """
    if not key:
        raise ValueError("register_setting: key is required")
    if not title:
        raise ValueError("register_setting: title is required")

    desc = SettingDescriptor(
        key=key,
        title=title,
        trigger_text=trigger_text or title,
        group=group,
        order=order,
        description=description,
    )
    _registry[key] = desc


def get_settings(group: Optional[str] = "settings") -> List[SettingDescriptor]:
    """
    Получить список пунктов настроек.

    group=None — вернуть все пункты из всех групп.
    """
    values: Iterable[SettingDescriptor] = _registry.values()
    if group is not None:
        values = [s for s in values if s.group == group]

    return sorted(values, key=lambda s: (s.order, s.title.lower()))


def get_setting(key: str) -> Optional[SettingDescriptor]:
    """Получить пункт по key, либо None."""
    return _registry.get(key)


def find_by_trigger(text: str, group: Optional[str] = None) -> Optional[SettingDescriptor]:
    """
    Найти пункт настроек по trigger_text.

    group=None — поиск по всем группам.
    """
    if not text:
        return None

    for s in get_settings(group):
        if s.trigger_text == text:
            return s
    return None


def clear_registry() -> None:
    """Полностью очистить реестр (удобно при горячей перезагрузке модулей)."""
    _registry.clear()


def debug_dump() -> str:
    """Человекочитаемый дамп реестра (для логов)."""
    if not _registry:
        return "Реестр настроек пуст."

    lines: List[str] = ["Реестр настроек:"]
    for s in get_settings(group=None):
        lines.append(
            f"- [{s.group}] {s.key}: '{s.title}' (trigger='{s.trigger_text}', order={s.order})"
        )
    return "\n".join(lines)


# --- Aliases for backwards-compat imports ---
# If this module is imported as menu_registry.settings_registry, old code that later does
# "import settings_registry" (or "from settings_registry import ...") can still work.
if __name__ == "menu_registry.settings_registry":
    _sys.modules.setdefault("settings_registry", _sys.modules[__name__])
elif __name__ == "settings_registry":
    _sys.modules.setdefault("menu_registry.settings_registry", _sys.modules[__name__])
