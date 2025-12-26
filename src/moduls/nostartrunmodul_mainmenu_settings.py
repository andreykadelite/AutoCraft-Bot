# -*- coding: utf-8 -*-
"""
nostartrunmodul_mainmenu_settings.py

✅ Настройка показа пунктов главного меню (main).

Открытие:
- Кнопка (Reply): "Настройка главного меню"
- Команда (Bot Menu): /mainmenu

⭐ Новое:
- Модуль также смотрит реестр утилит (utilities_registry) и может добавлять утилиты
  как отдельные кнопки в ГЛАВНОЕ меню.
- По умолчанию такие утилиты ВЫКЛЮЧЕНЫ (их можно включить здесь, если нужны в главном меню).

aiogram 2.x
"""

from __future__ import annotations

import configparser
import importlib
import inspect
import os
import sys
from typing import Iterable, List, Optional, Set, Tuple

from aiogram import types
from aiogram.dispatcher import Dispatcher

from mainmenu_registry import (
    get_main_items_with_visibility,
    toggle_main_item_visibility,
)
from keymenu import get_main_settings_keyboard

# --- optional imports (утилиты могут отсутствовать в каких-то сборках) ---
try:
    from utilities_registry import get_utilities  # type: ignore
except Exception:
    get_utilities = None  # type: ignore

try:
    from __main__ import authorized_users, write_bot_log  # type: ignore
except Exception:
    authorized_users = None

    def write_bot_log(*args, **kwargs):  # type: ignore
        return



def _parse_bool(v: str) -> bool:
    v = (v or "").strip().lower()
    return v in ("1", "true", "yes", "on", "y", "да", "вкл", "включено")


def _get_visibility_from_config(key: str) -> Optional[bool]:
    """Вернуть видимость пункта из config.ini, либо None если ключа там нет."""
    if not key:
        return None
    cfg_path = _get_config_path()
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    try:
        if os.path.isfile(cfg_path):
            cfg.read(cfg_path, encoding="utf-8")
    except Exception:
        return None
    if not cfg.has_section("mainmenu_visibility"):
        return None
    try:
        if not cfg.has_option("mainmenu_visibility", key):
            return None
        return _parse_bool(cfg.get("mainmenu_visibility", key))
    except Exception:
        return None

# --- sync cache ---
_UTIL_SYNC_DONE_KEYS: Set[str] = set()


def _is_authorized(user_id: int) -> bool:
    if authorized_users is None:
        return True
    try:
        return user_id in authorized_users
    except Exception:
        return True


def _get_config_path() -> str:
    """Пытаемся найти config.ini так же, как это делает остальная система.

    Приоритет:
    1) Путь/функция из mainmenu_registry (если есть).
    2) base_dir/__file__ из __main__.
    3) Рядом с exe (Nuitka/pyinstaller-like).
    4) Текущая рабочая папка.
    """
    candidates: List[str] = []

    # 0) при запуске как .exe (Nuitka) чаще всего правильный config.ini рядом с exe
    try:
        exe = getattr(sys, "executable", None)
        if exe and str(exe).lower().endswith(".exe"):
            candidates.append(os.path.join(os.path.dirname(str(exe)), "config.ini"))
    except Exception:
        pass

    # 0) если mainmenu_registry сам умеет говорить, где у него config.ini
    try:
        mm = importlib.import_module("mainmenu_registry")
        for attr in ("get_config_path", "get_config_ini_path", "get_ini_path", "CONFIG_PATH", "CONFIG_INI"):
            v = getattr(mm, attr, None)
            try:
                if callable(v):
                    p = v()
                else:
                    p = v
                if isinstance(p, str) and p.strip():
                    candidates.append(p.strip())
            except Exception:
                continue
    except Exception:
        pass

    # 1) рядом с base_dir/__main__.__file__
    try:
        import __main__  # type: ignore

        base_dir = getattr(__main__, "base_dir", None) or getattr(__main__, "BASE_DIR", None)
        if isinstance(base_dir, str) and base_dir:
            candidates.append(os.path.join(os.path.abspath(base_dir), "config.ini"))

        main_file = getattr(__main__, "__file__", None)
        if isinstance(main_file, str) and main_file:
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(main_file)), "config.ini"))
    except Exception:
        pass

    # 2) Nuitka/pyinstaller-like
    try:
        if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
            candidates.append(os.path.join(os.path.dirname(sys.executable), "config.ini"))
        # Nuitka иногда не ставит sys.frozen, но sys.executable всё равно указывает на exe
        if getattr(sys, "executable", None) and str(sys.executable).lower().endswith(".exe"):
            candidates.append(os.path.join(os.path.dirname(sys.executable), "config.ini"))
    except Exception:
        pass

    # 3) рабочая папка
    candidates.append(os.path.join(os.getcwd(), "config.ini"))

    # выбираем первый реально существующий
    for p in candidates:
        try:
            if p and os.path.isfile(p):
                return p
        except Exception:
            continue

    # если файла ещё нет, вернём самый вероятный путь и дадим системе создать файл
    return candidates[0]



def _set_visibility_in_config(key: str, visible: bool) -> bool:
    """Жёстко сохраняем видимость пункта в config.ini (fallback на случай, если реестр не пишет сам)."""
    if not key:
        return False
    cfg_path = _get_config_path()
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    try:
        if os.path.isfile(cfg_path):
            cfg.read(cfg_path, encoding="utf-8")
    except Exception:
        pass
    if not cfg.has_section("mainmenu_visibility"):
        cfg.add_section("mainmenu_visibility")
    try:
        cfg.set("mainmenu_visibility", key, "1" if visible else "0")
        with open(cfg_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        return True
    except Exception:
        return False


def _wrap_utilities_register_utility_once() -> None:
    """Подхватываем утилиты, которые зарегистрируются ПОЗЖЕ (после загрузки этого модуля)."""
    if get_utilities is None:
        return
    try:
        ur = importlib.import_module("utilities_registry")
        orig_reg = getattr(ur, "register_utility", None)
        if not callable(orig_reg):
            return
        if getattr(orig_reg, "_mainmenu_sync_wrapped", False):
            return

        def wrapped_register_utility(*args, **kwargs):
            res = orig_reg(*args, **kwargs)
            try:
                _sync_utilities_into_mainmenu_registry()
            except Exception:
                pass
            return res

        wrapped_register_utility._mainmenu_sync_wrapped = True  # type: ignore
        setattr(ur, "register_utility", wrapped_register_utility)
    except Exception:
        return

def _try_set_visibility_direct_in_registry(key: str, visible: bool) -> bool:
    """
    Пытается выставить видимость пункта через mainmenu_registry *явным* set-методом.
    Не использует toggle, чтобы не словить случайный инверт.
    """
    if not key:
        return False

    try:
        mm = importlib.import_module("mainmenu_registry")
    except Exception:
        return False

    for name in (
        "set_main_item_visibility",
        "set_item_visibility",
        "set_visibility",
        "set_main_visibility",
    ):
        fn = getattr(mm, name, None)
        if not callable(fn):
            continue
        try:
            # Некоторые реализации принимают group, некоторые нет.
            try:
                fn(key, bool(visible), group="main")  # type: ignore
            except TypeError:
                fn(key, bool(visible))  # type: ignore
            return True
        except Exception:
            continue

    return False


def _ensure_default_visibility_in_config(key: str, default_on: bool = False) -> None:
    """
    Если для key нет записи в [mainmenu_visibility], проставим дефолт.
    Как и в GUI-скрипте: сперва пробуем выставить видимость через API реестра
    (чтобы синхронизировались и память, и ini), а если не вышло, пишем ini вручную.
    Если запись уже есть, не трогаем значение, но стараемся синхронизировать кэш.
    """
    if not key:
        return

    # Уже настроено пользователем
    existing = _get_visibility_from_config(key)
    if existing is not None:
        # На всякий случай синхронизируем кэш в памяти
        try:
            _try_set_visibility_direct_in_registry(key, bool(existing))
        except Exception:
            pass
        return

    # 1) Пытаемся через API (обновит кэш и часто сам запишет ini)
    try:
        _try_set_visibility_direct_in_registry(key, bool(default_on))
    except Exception:
        pass

    # 2) Фолбэк: руками в config.ini
    try:
        cfg_path = _get_config_path()
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        if os.path.isfile(cfg_path):
            cfg.read(cfg_path, encoding="utf-8")
        sect = "mainmenu_visibility"
        if not cfg.has_section(sect):
            cfg.add_section(sect)
        if not cfg.has_option(sect, key):
            cfg.set(sect, key, "1" if default_on else "0")
            with open(cfg_path, "w", encoding="utf-8") as f:
                cfg.write(f)
    except Exception:
        pass


def _ensure_default_visibility_on(keys: Iterable[str]) -> None:
    """
    Для новых пунктов (в т.ч. утилитных util__*) по умолчанию ставим выключено,
    но НЕ трогаем то, что пользователь уже настраивал.
    """
    for k in list(keys or []):
        try:
            _ensure_default_visibility_in_config(str(k), default_on=False)
        except Exception:
            continue

def _get_mainmenu_register_fn():
    """Ищем в mainmenu_registry функцию регистрации пункта (на случай разных версий)."""
    try:
        mm = importlib.import_module("mainmenu_registry")
    except Exception:
        return None

    for name in (
        "register_main_item",
        "register_mainmenu_item",
        "register_item",
        "register_menu_item",
        "register_button",
    ):
        fn = getattr(mm, name, None)
        if callable(fn):
            return fn
    return None




def _get_visibility_from_registry(key: str) -> Optional[bool]:
    """Возвращает текущую видимость пункта (по состоянию реестра mainmenu_registry), либо None если не нашли."""
    if not key:
        return None
    try:
        items = get_main_items_with_visibility(group="main")
        for item, vis in items:
            if getattr(item, "key", None) == key:
                return bool(vis)
    except Exception:
        return None
    return None


def _set_visibility_in_registry(key: str, visible: bool) -> bool:
    """
    Пытается выставить видимость пункта прямо в mainmenu_registry (чтобы главное меню не показывало кнопку),
    не полагаясь только на config.ini.
    """
    if not key:
        return False

    # 1) Если в mainmenu_registry есть явная функция set_*.
    try:
        mm = importlib.import_module("mainmenu_registry")
        for name in (
            "set_main_item_visibility",
            "set_item_visibility",
            "set_visibility",
            "set_main_visibility",
        ):
            fn = getattr(mm, name, None)
            if callable(fn):
                # пробуем разные варианты вызова
                for call in (
                    lambda: fn(key, visible, group="main"),
                    lambda: fn(key, visible),
                    lambda: fn(key=key, visible=visible, group="main"),
                    lambda: fn(key=key, visible=visible),
                ):
                    try:
                        call()
                        return True
                    except TypeError:
                        continue
                    except Exception:
                        return False
    except Exception:
        pass

    # 2) Fallback через toggle (если знаем текущее состояние).
    cur = _get_visibility_from_registry(key)
    if cur is None:
        return False
    if cur == bool(visible):
        return True

    try:
        # toggle_main_item_visibility может принимать либо только key, либо (key, group=...)
        try:
            toggle_main_item_visibility(key, group="main")  # type: ignore
        except TypeError:
            toggle_main_item_visibility(key)  # type: ignore
        return True
    except Exception:
        return False


def _apply_visibility_from_config_to_registry(key: str) -> None:
    """
    Синхронизирует состояние видимости в памяти (mainmenu_registry) с config.ini.
    Если ключ в config отсутствует, ничего не делает.
    """
    cfg_vis = _get_visibility_from_config(key)
    if cfg_vis is None:
        return
    desired = bool(cfg_vis)
    cur = _get_visibility_from_registry(key)
    if cur is None or cur != desired:
        # Сначала пробуем явный set-метод (как в GUI), чтобы обновить кэш в памяти.
        if not _try_set_visibility_direct_in_registry(key, desired):
            _set_visibility_in_registry(key, desired)

def _call_register_fn(register_fn, **kwargs) -> bool:
    """
    Пробуем разные сигнатуры регистрации, максимально безопасно.

    Поддерживает:
    - именованные параметры
    - функции с **kwargs (тогда передаём всё)
    - несколько позиционных вариантов (на случай, если функция не принимает kwargs)
    """
    if register_fn is None:
        return False

    # 1) Именованные параметры.
    # Если функция принимает **kwargs, можно передать всё без фильтра.
    try:
        sig = inspect.signature(register_fn)
        params = sig.parameters
        accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if accepts_varkw:
            register_fn(**kwargs)
            return True

        accepted = set(params.keys())
        call_kwargs = {k: v for k, v in kwargs.items() if k in accepted}
        if call_kwargs:
            register_fn(**call_kwargs)
            return True
    except Exception:
        pass

    # 2) Позиционные варианты (key, title, trigger_text, group, order, description).
    key = kwargs.get("key")
    title = kwargs.get("title")
    trigger_text = kwargs.get("trigger_text")
    group = kwargs.get("group")
    order = kwargs.get("order")
    description = kwargs.get("description")

    variants = [
        (key, title),
        (key, title, trigger_text),
        (key, title, trigger_text, group),
        (key, title, trigger_text, group, order),
        (key, title, trigger_text, group, order, description),
    ]

    for args in variants:
        try:
            args2 = list(args)
            while args2 and args2[-1] is None:
                args2.pop()
            register_fn(*args2)
            return True
        except TypeError:
            continue
        except Exception:
            return False

    return False

def _sync_utilities_into_mainmenu_registry() -> None:
    """
    Берём все утилиты из utilities_registry и регистрируем их как пункты главного меню.
    По умолчанию они выключены (mainmenu_visibility=0), чтобы не лезли в главное меню сами.
    """
    global _UTIL_SYNC_DONE_KEYS

    if get_utilities is None:
        return

    try:
        utilities = list(get_utilities(group=None))  # type: ignore
    except Exception:
        return

    if not utilities:
        return

    # Считаем текущие названия, чтобы не плодить дубли (и не сломать переключатель по title).
    try:
        existing_items = get_main_items_with_visibility(group="main")
    except Exception:
        existing_items = []

    existing_titles: Set[str] = set()
    try:
        for item, _vis in existing_items:
            t = getattr(item, "title", None)
            if t:
                existing_titles.add(str(t))
    except Exception:
        pass

    register_fn = _get_mainmenu_register_fn()
    if register_fn is None:
        # Без функции регистрации мы не сможем реально добавить кнопки в главное меню.
        return

    new_keys: List[str] = []
    all_keys: List[str] = []
    seen_titles: Set[str] = set()

    for u in utilities:
        try:
            u_key = getattr(u, "key", None)
            if not u_key:
                continue
            # Ключ в главном меню делаем с префиксом, чтобы не конфликтовал с обычными пунктами.
            main_key = f"util__{u_key}"
            all_keys.append(main_key)
            if main_key in _UTIL_SYNC_DONE_KEYS:
                continue

            # Важно: текст кнопки должен совпасть с тем, что ловят handlers утилиты.
            title = getattr(u, "trigger_text", None) or getattr(u, "title", None) or str(u_key)

            # Защита от дублей по отображаемому тексту.
            if title in existing_titles:
                _UTIL_SYNC_DONE_KEYS.add(main_key)
                continue
            if title in seen_titles:
                _UTIL_SYNC_DONE_KEYS.add(main_key)
                continue
            seen_titles.add(title)

            order = getattr(u, "order", 100)
            description = getattr(u, "description", "")

            # Пытаемся зарегистрировать с флагом «по умолчанию скрыто», если поддерживается.
            ok = _call_register_fn(
                register_fn,
                key=main_key,
                title=title,
                trigger_text=title,
                group="main",
                order=int(order) + 1000,
                description=description,
                default_visible=False,
                visible_by_default=False,
                visible=False,
                default_on=False,
                enabled_by_default=False,
            )

            if ok:
                new_keys.append(main_key)
                _UTIL_SYNC_DONE_KEYS.add(main_key)
        except Exception:
            continue

        # Проставим дефолт: выключено. Не трогаем уже настроенные ключи.
    try:
        _ensure_default_visibility_on(all_keys)
    except Exception:
        return

    # После того как выставили дефолт в config.ini, обязательно синхронизируем состояние в памяти реестра.
    # Иначе главное меню может показывать кнопку, даже если в ini стоит 0.
    try:
        for k in all_keys:
            _apply_visibility_from_config_to_registry(k)
    except Exception:
        pass


def _build_visibility_keyboard():
    """
    Для каждого пункта главного меню:
      - если включён:  "Выключить: <Название>"
      - если выключен: "Включить: <Название>"
    В конце: "Назад в настройки"

    Перед построением:
      - синхронизируем утилиты в главное меню (по умолчанию выключено).
    """
    _sync_utilities_into_mainmenu_registry()

    items_with_vis = get_main_items_with_visibility(group="main")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    any_items = False
    for item, visible in items_with_vis:
        # Не даём отключить саму настройку.
        if getattr(item, "key", "") == "mainmenu_settings":
            continue

        any_items = True
        title = getattr(item, "title", str(item))
        key = getattr(item, "key", "")
        cfg_vis = _get_visibility_from_config(key)
        real_visible = cfg_vis if cfg_vis is not None else visible
        label = f"{'Выключить' if real_visible else 'Включить'}: {title}"
        kb.add(label)

    kb.add("Назад в настройки")
    return kb, any_items


async def _show_mainmenu_settings(message: types.Message) -> None:
    kb, any_items = _build_visibility_keyboard()

    if not any_items:
        text = (
            "В реестре главного меню пока нет пунктов, кроме самой настройки.\n\n"
            "Добавь модули, которые регистрируют свои кнопки в главном меню, "
            "или утилиты в utilities_registry, и они появятся здесь для управления (и для добавления в главное меню)."
        )
    else:
        text = (
            "Настройка показа главного меню.\n\n"
            "Нажимай «Включить/Выключить», чтобы управлять показом пунктов.\n"
            "Изменения сохраняются в config.ini (секция [mainmenu_visibility]).\n\n"
            "Подсказка: утилиты из utilities_registry по умолчанию включены, "
            "их можно включить здесь, чтобы они появились в главном меню."
        )

    await message.answer(text, reply_markup=kb)


def register_handlers(dp: Dispatcher) -> None:
    # На старте: (1) подхватываем поздние register_utility, (2) синхронизируем утилиты в главное меню
    _wrap_utilities_register_utility_once()
    _sync_utilities_into_mainmenu_registry()

    # ✅ Открытие по команде из Bot Menu
    @dp.message_handler(commands=["mainmenu"], state="*")
    async def open_from_botmenu(message: types.Message):
        if not _is_authorized(message.from_user.id):
            await message.answer("Недостаточно прав для изменения настроек главного меню.")
            return
        write_bot_log(f"Открыта 'Настройка главного меню' через /mainmenu (user={message.from_user.id}).")
        await _show_mainmenu_settings(message)

    # ✅ Открытие по кнопке (Reply)
    @dp.message_handler(lambda m: (m.text or "").strip() == "Настройка главного меню")
    async def open_from_button(message: types.Message):
        if not _is_authorized(message.from_user.id):
            await message.answer("Недостаточно прав для изменения настроек главного меню.")
            return
        write_bot_log(f"Открыта 'Настройка главного меню' через кнопку (user={message.from_user.id}).")
        await _show_mainmenu_settings(message)

    # Переключатели
    @dp.message_handler(
        lambda m: (m.text or "").startswith("Включить: ")
        or (m.text or "").startswith("Выключить: ")
    )
    async def toggle_mainmenu_item(message: types.Message):
        if not _is_authorized(message.from_user.id):
            return

        # перед переключением ещё раз синхронизируем утилиты (на всякий случай)
        _sync_utilities_into_mainmenu_registry()

        text = (message.text or "").strip()
        if text.startswith("Включить: "):
            title = text[len("Включить: ") :]
        elif text.startswith("Выключить: "):
            title = text[len("Выключить: ") :]
        else:
            return

        items_with_vis = get_main_items_with_visibility(group="main")
        target_item = None
        for item, _visible in items_with_vis:
            if getattr(item, "key", "") == "mainmenu_settings":
                continue
            if getattr(item, "title", None) == title:
                target_item = item
                break

        if target_item is None:
            await message.answer(
                "Не удалось найти соответствующий пункт главного меню.\n"
                "Возможно, он был удалён, переименован или дублируется по названию."
            )
            return

        new_state = toggle_main_item_visibility(getattr(target_item, "key"))
        # Fallback: гарантируем сохранение в config.ini
        _set_visibility_in_config(getattr(target_item, "key"), bool(new_state))
        write_bot_log(
            f"Переключена видимость пункта '{getattr(target_item,'title','?')}' "
            f"(key={getattr(target_item,'key','?')}) -> {new_state} (user={message.from_user.id})."
        )
        await _show_mainmenu_settings(message)

    @dp.message_handler(lambda m: (m.text or "").strip() == "Назад в настройки")
    async def back_to_settings(message: types.Message):
        if not _is_authorized(message.from_user.id):
            return
        kb = get_main_settings_keyboard()
        await message.answer("Настройки:", reply_markup=kb)
