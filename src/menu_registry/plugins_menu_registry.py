# -*- coding: utf-8 -*-
"""
menu_registry/plugins_menu_registry.py

Реестр пунктов меню для "Менеджера плагинов" (аналог utilities_registry.py).

Задача:
- Другие модули (например, сам менеджер плагинов) регистрируют пункты меню через
  register_plugin_menu_item(...).
- Меню "Плагины" (plugins_menu.py) читает реестр и строит клавиатуру.

Важно:
- НИКАКИХ дефолтных пунктов тут нет. Если ничего не зарегистрировано, меню покажет
  только кнопку "Вернуться" (см. plugins_menu.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PluginMenuDescriptor:
    key: str
    title: str
    trigger_text: str
    group: str = "plugins_menu"
    order: int = 100
    description: str = ""


_registry: Dict[str, PluginMenuDescriptor] = {}


def register_plugin_menu_item(
    key: str,
    title: str,
    trigger_text: Optional[str] = None,
    group: str = "plugins_menu",
    order: int = 100,
    description: str = "",
) -> None:
    """
    Зарегистрировать пункт меню.

    - key: уникальный ключ (перезаписывает существующий пункт с тем же key)
    - title: текст кнопки
    - trigger_text: текст для поиска/триггера (по умолчанию = title)
    - group: логическая группа (по умолчанию plugins_menu)
    - order: сортировка (меньше -> выше)
    """
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")

    trig = (trigger_text if trigger_text is not None else title).strip()
    grp = (group or "plugins_menu").strip()

    _registry[key.strip()] = PluginMenuDescriptor(
        key=key.strip(),
        title=title.strip(),
        trigger_text=trig,
        group=grp,
        order=int(order),
        description=description or "",
    )


def get_plugin_menu_items(group: Optional[str] = "plugins_menu") -> List[PluginMenuDescriptor]:
    """Получить список пунктов, отсортированный по (order, title)."""
    if group is None:
        items = list(_registry.values())
    else:
        g = group.strip()
        items = [d for d in _registry.values() if d.group == g]

    items.sort(key=lambda d: (d.order, d.title.lower()))
    return items


def get_plugin_menu_item(key: str) -> Optional[PluginMenuDescriptor]:
    """Получить пункт по key."""
    return _registry.get(key)


def find_by_trigger(text: str, group: Optional[str] = None) -> Optional[PluginMenuDescriptor]:
    """Найти пункт по точному совпадению trigger_text."""
    if text is None:
        return None

    for d in get_plugin_menu_items(group=group):
        if d.trigger_text == text:
            return d
    return None


def clear_registry() -> None:
    """Очистить реестр (удобно для hot-reload)."""
    _registry.clear()


def debug_dump() -> str:
    """Текстовый дамп по всем группам."""
    lines = []
    for d in get_plugin_menu_items(group=None):
        lines.append(f"[{d.group}] {d.order:03d} {d.key} -> {d.title} (trigger={d.trigger_text})")
    return "\n".join(lines) if lines else "(registry is empty)"
