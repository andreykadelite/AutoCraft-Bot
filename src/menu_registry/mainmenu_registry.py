from dataclasses import dataclass
from typing import Dict, List, Optional, Iterable

import configparser
import os
import sys


@dataclass
class MainMenuItem:
    """
    Описание пункта главного меню.

    key          — внутренний ключ, должен быть уникален.
    title        — текст кнопки, который видит пользователь.
    trigger_text — текст, по которому модуль-обработчик ловит сообщение.
    group        — логическая группа (обычно "main").
    order        — порядок сортировки (меньше — выше).
    description  — человекочитаемое описание (для отладки/логов/настроек).
    """
    key: str
    title: str
    trigger_text: str
    group: str = "main"
    order: int = 100
    description: str = ""


# Реестр пунктов главного меню: key -> MainMenuItem
_main_registry: Dict[str, MainMenuItem] = {}


def register_main_item(
    key: str,
    title: str,
    trigger_text: str,
    group: str = "main",
    order: int = 100,
    description: str = "",
) -> None:
    """
    Зарегистрировать пункт главного меню.

    Обычно вызывается из модулей (nostartrunmodul_*, modul* и т. д.)
    один раз при их инициализации.
    """
    item = MainMenuItem(
        key=key,
        title=title,
        trigger_text=trigger_text,
        group=group,
        order=order,
        description=description,
    )
    _main_registry[key] = item


# ======= Работа с config.ini: включение/выключение пунктов =======

def _get_base_dir() -> str:
    """
    Базовая папка для config.ini:

    - для EXE (Nuitka) — рядом с sys.executable;
    - для .py — рядом с основным скриптом (sys.argv[0]).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(sys.argv[0]))


_CONFIG_SECTION = "mainmenu_visibility"
_CONFIG_PATH = os.path.join(_get_base_dir(), "config.ini")

# Кэш настроек видимости: {key: True/False}. True = показывать, False = скрыть.
_visibility_cache: Dict[str, bool] = {}


def _load_visibility_config() -> Dict[str, bool]:
    """
    Загрузить конфигурацию видимости пунктов главного меню из config.ini.

    Формат:
    [mainmenu_visibility]
    status_server = true
    status_network = false
    ...
    """
    global _visibility_cache

    if _visibility_cache:
        return _visibility_cache

    cfg = configparser.ConfigParser()
    data: Dict[str, bool] = {}

    if os.path.exists(_CONFIG_PATH):
        try:
            cfg.read(_CONFIG_PATH, encoding="utf-8")
        except Exception:
            cfg = configparser.ConfigParser()

    if cfg.has_section(_CONFIG_SECTION):
        for opt, val in cfg.items(_CONFIG_SECTION):
            try:
                data[opt] = cfg.getboolean(_CONFIG_SECTION, opt, fallback=True)
            except ValueError:
                data[opt] = True

    _visibility_cache = data
    return _visibility_cache


def _save_visibility_config(data: Dict[str, bool]) -> None:
    """
    Сохранить конфигурацию видимости пунктов главного меню в config.ini,
    не затрагивая другие секции.
    """
    cfg = configparser.ConfigParser()

    if os.path.exists(_CONFIG_PATH):
        try:
            cfg.read(_CONFIG_PATH, encoding="utf-8")
        except Exception:
            cfg = configparser.ConfigParser()

    if not cfg.has_section(_CONFIG_SECTION):
        cfg.add_section(_CONFIG_SECTION)

    for key, value in data.items():
        cfg.set(_CONFIG_SECTION, key, "true" if value else "false")

    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)


def is_main_item_visible(key: str) -> bool:
    """
    Проверить, должен ли пункт главного меню с данным key показываться в основном меню.

    Логика по умолчанию:
    - если в конфиге ключа нет — считаем, что он ВКЛЮЧЁН (True);
    - специальные внутренние ключи можно держать всегда скрытыми/видимыми.
    """
    # Встроенную команду "Настройка главного меню" из ГЛАВНОГО МЕНЮ убираем.
    # Она должна жить только в меню бота / меню настроек, а не среди рабочих кнопок.
    if key == "mainmenu_settings":
        return False

    data = _load_visibility_config()
    return data.get(key, True)


def set_main_item_visibility(key: str, visible: bool) -> None:
    """
    Явно включить/выключить пункт главного меню с данным key.
    """
    global _visibility_cache
    data = _load_visibility_config()
    data[key] = bool(visible)
    _visibility_cache = data
    _save_visibility_config(data)


def toggle_main_item_visibility(key: str) -> bool:
    """
    Переключить видимость пункта главного меню с данным key.

    Возвращает новое состояние: True = включён, False = выключен.
    """
    current = is_main_item_visible(key)
    new_state = not current
    set_main_item_visibility(key, new_state)
    return new_state


def get_main_items(
    group: Optional[str] = "main",
    include_disabled: bool = False,
) -> List[MainMenuItem]:
    """
    Получить список элементов главного меню для указанной группы,
    отсортированный по order, title.

    group = None        — вернуть все пункты из всех групп.
    include_disabled    — если False (по умолчанию), применяется фильтр видимости
                          по секции [mainmenu_visibility] в config.ini.
                          Если True — вернуть все пункты, игнорируя настройки
                          видимости (удобно для экрана настроек).
    """
    values: Iterable[MainMenuItem] = _main_registry.values()

    if group is not None:
        values = [u for u in values if u.group == group]

    if not include_disabled:
        values = [u for u in values if is_main_item_visible(u.key)]

    return sorted(
        values,
        key=lambda u: (u.order, u.title.lower()),
    )


def get_main_items_with_visibility(group: Optional[str] = "main"):
    """
    Вернуть список (item, visible) для указанной группы.
    Удобно использовать в интерфейсе 'Настройка главного меню'.

    Специальный пункт 'mainmenu_settings' здесь не отображаем, чтобы
    пользователь не пытался включать/выключать саму настройку.
    """
    items = get_main_items(group=group, include_disabled=True)
    filtered = [item for item in items if item.key != "mainmenu_settings"]
    return [(item, is_main_item_visible(item.key)) for item in filtered]


def debug_dump_main() -> None:
    """
    Отладочный дамп зарегистрированных пунктов главного меню в stdout.
    Показывает ВСЕ пункты (включая выключенные), для проверки реестра.
    """
    print("=== MAIN MENU REGISTRY DUMP ===")
    for u in get_main_items(group=None, include_disabled=True):
        print(
            f"[{u.group}] key={u.key!r} title={u.title!r} "
            f"trigger={u.trigger_text!r} order={u.order} "
            f"visible={is_main_item_visible(u.key)}"
        )
    print("=== END DUMP ===")


# Встроенный пункт 'Настройка главного меню'.
# Регистрируем его при импорте модуля, чтобы он всегда был доступен как команда.
# В ГЛАВНОМ МЕНЮ при этом он не появится из-за is_main_item_visible().
try:
    register_main_item(
        key="mainmenu_settings",
        title="Настройка главного меню",
        trigger_text="Настройка главного меню",
        group="main",
        order=5,
        description="Настройка показа пунктов главного меню",
    )
except Exception:
    # Не мешаем остальной работе, если что-то пойдёт не так при автозаписи.
    pass
# --- Aliases for backwards-compat imports ---
# If this module is imported as menu_registry.mainmenu_registry, old code that later does
# "import mainmenu_registry" (or "from mainmenu_registry import ...") can still work.
if __name__ == "menu_registry.mainmenu_registry":
    sys.modules.setdefault("mainmenu_registry", sys.modules[__name__])
elif __name__ == "mainmenu_registry":
    sys.modules.setdefault("menu_registry.mainmenu_registry", sys.modules[__name__])
