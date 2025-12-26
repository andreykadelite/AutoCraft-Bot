"""menu_registry/additional_registry.py

Простой реестр для меню «Дополнительно» (динамическое меню).

Идея ровно такая же, как в utilities_registry.py / settings_registry.py:
- Любой модуль, который хочет кнопку в меню «Дополнительно», вызывает register_additional().
- Клавиатура (например, «Дополнительно») берёт список через get_additionals()
  и строит кнопки.

Важно (совместимость импортов):
- Этот файл может лежать в пакете menu_registry и импортироваться как
  `from menu_registry.additional_registry import ...`
- Старый код может импортировать как `from additional_registry import ...`
  Поэтому в конце файла добавлены алиасы sys.modules, чтобы оба пути
  указывали на один и тот же модуль (и один общий _registry).

Пример регистрации в модуле:

    # Новый путь (если файлы лежат в папке menu_registry)
    from menu_registry.additional_registry import register_additional

    register_additional(
        key="about",
        title="Информация",
        trigger_text="Информация",
        order=10,
        description="Справка/инфо о боте"
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import sys as _sys


@dataclass
class AdditionalDescriptor:
    """Описание одного пункта меню «Дополнительно»."""
    key: str
    title: str
    trigger_text: str
    group: str = "additional"
    order: int = 100
    description: str = ""


_registry: Dict[str, AdditionalDescriptor] = {}


def register_additional(
    key: str,
    title: str,
    trigger_text: Optional[str] = None,
    group: str = "additional",
    order: int = 100,
    description: str = "",
) -> None:
    """Зарегистрировать пункт в реестре меню «Дополнительно».

    Если key уже есть — запись перезапишется (последняя победила).
    """
    if not key:
        raise ValueError("register_additional: key is required")
    if not title:
        raise ValueError("register_additional: title is required")

    desc = AdditionalDescriptor(
        key=key,
        title=title,
        trigger_text=trigger_text or title,
        group=group,
        order=order,
        description=description,
    )
    _registry[key] = desc


def get_additionals(group: Optional[str] = "additional") -> List[AdditionalDescriptor]:
    """Получить список пунктов для указанной группы, отсортированный по order, title.

    group = None — вернуть все пункты из всех групп.
    """
    values: Iterable[AdditionalDescriptor] = _registry.values()

    if group is not None:
        values = [u for u in values if u.group == group]

    return sorted(values, key=lambda u: (u.order, u.title.lower()))


def get_additional(key: str) -> Optional[AdditionalDescriptor]:
    """Получить пункт по key, либо None."""
    return _registry.get(key)


def find_by_trigger(text: str, group: Optional[str] = None) -> Optional[AdditionalDescriptor]:
    """Найти пункт меню по trigger_text.

    group = None — поиск по всем пунктам.
    """
    if not text:
        return None

    for u in get_additionals(group):
        if u.trigger_text == text:
            return u
    return None


def clear_registry() -> None:
    """Полностью очистить реестр (полезно при горячей перезагрузке модулей)."""
    _registry.clear()


def debug_dump() -> str:
    """Вернуть человекочитаемый дамп реестра (для логов / отладки)."""
    if not _registry:
        return "Реестр «Дополнительно» пуст."

    lines: List[str] = ["Реестр «Дополнительно»:"]
    for u in get_additionals(group=None):
        lines.append(
            f"- [{u.group}] {u.key}: '{u.title}' "
            f"(trigger='{u.trigger_text}', order={u.order})"
        )
    return "\n".join(lines)


# --- Aliases for backwards-compat imports ---
# Если модуль импортирован как menu_registry.additional_registry, старый импорт additional_registry
# должен указывать на тот же объект модуля (и общий _registry).
if __name__ == "menu_registry.additional_registry":
    _sys.modules.setdefault("additional_registry", _sys.modules[__name__])
elif __name__ == "additional_registry":
    _sys.modules.setdefault("menu_registry.additional_registry", _sys.modules[__name__])
