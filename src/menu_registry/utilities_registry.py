# -*- coding: utf-8 -*-
"""
menu_registry/utilities_registry.py

Простой реестр утилит для динамических меню (аналог прежнего utilities_registry.py).

Идея:
- Любой модуль, который хочет кнопку в меню, вызывает register_utility().
- Клавиатура (например, "Утилиты") берёт список через get_utilities()
  и строит кнопки на основе title.

Важно:
- Файл перенесён в папку menu_registry.
- Для совместимости ничего "пакетного" тут не требуется: это чистый реестр.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Iterable
import sys as _sys


@dataclass
class UtilityDescriptor:
    """
    Описание одной утилиты / пункта меню.
    """
    key: str                  # Внутреннее имя, уникальное: 'winlogs', 'bat_tools' и т.п.
    title: str                # Надпись на кнопке в клавиатуре
    trigger_text: str         # Текст сообщения, который ловит handler (message.text)
    group: str = "utilities"  # Группа/раздел меню: 'utilities', 'special', 'network' и т.д.
    order: int = 100          # Порядок сортировки внутри группы
    description: str = ""     # Необязательно, подпись для отладки / логов


# Внутреннее хранилище
_registry: Dict[str, UtilityDescriptor] = {}


def register_utility(
    key: str,
    title: str,
    trigger_text: Optional[str] = None,
    group: str = "utilities",
    order: int = 100,
    description: str = "",
) -> None:
    """
    Зарегистрировать утилиту в реестре.

    key         — уникальный идентификатор (для кода, не для пользователя)
    title       — текст на кнопке
    trigger_text— текст, который должно прислать сообщение (message.text),
                  чтобы сработал handler. Если не указан — берётся title.
    group       — раздел меню ('utilities', 'special', 'network' и т.п.)
    order       — порядок сортировки
    description — текстовое описание (для логов, отладки)

    Если key уже есть — запись перезапишется (последняя победила).
    """
    if not key:
        raise ValueError("register_utility: key is required")
    if not title:
        raise ValueError("register_utility: title is required")

    desc = UtilityDescriptor(
        key=key,
        title=title,
        trigger_text=trigger_text or title,
        group=group,
        order=order,
        description=description,
    )
    _registry[key] = desc


def get_utilities(group: Optional[str] = "utilities") -> List[UtilityDescriptor]:
    """
    Получить список утилит для указанной группы, отсортированный по order, title.

    group = None — вернуть все утилиты из всех групп.
    """
    values: Iterable[UtilityDescriptor] = _registry.values()

    if group is not None:
        values = [u for u in values if u.group == group]

    return sorted(
        values,
        key=lambda u: (u.order, u.title.lower()),
    )


def get_utility(key: str) -> Optional[UtilityDescriptor]:
    """
    Получить утилиту по её key, либо None, если нет.
    """
    return _registry.get(key)


def find_by_trigger(text: str, group: Optional[str] = None) -> Optional[UtilityDescriptor]:
    """
    Найти утилиту по тексту кнопки/сообщения (trigger_text).

    group = None — поиск по всем утилитам, иначе только в заданной группе.
    """
    if not text:
        return None

    for u in get_utilities(group):
        if u.trigger_text == text:
            return u
    return None


def clear_registry() -> None:
    """
    Полностью очистить реестр.

    Полезно при горячей перезагрузке модулей:
    перед повторным импортом — чистишь, потом модули снова сами регистрируются.
    """
    _registry.clear()


def debug_dump() -> str:
    """
    Вернуть человекочитаемый дамп реестра (для логов / отладки).
    """
    if not _registry:
        return "Реестр утилит пуст."

    lines: List[str] = ["Реестр утилит:"]
    for u in get_utilities(group=None):
        lines.append(
            f"- [{u.group}] {u.key}: '{u.title}' "
            f"(trigger='{u.trigger_text}', order={u.order})"
        )
    return "\n".join(lines)

# --- Aliases for backwards-compat imports ---
# Чтобы оба пути импорта (menu_registry.utilities_registry и utilities_registry)
# указывали на один и тот же модуль и общий _registry.
if __name__ == "menu_registry.utilities_registry":
    _sys.modules.setdefault("utilities_registry", _sys.modules[__name__])
elif __name__ == "utilities_registry":
    _sys.modules.setdefault("menu_registry.utilities_registry", _sys.modules[__name__])
