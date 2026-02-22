from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import types
from aiogram.dispatcher import Dispatcher

try:
    from keymenu import get_utilities_keyboard  # type: ignore
except Exception:
    try:
        from moduls.keymenu import get_utilities_keyboard  # type: ignore
    except Exception:
        def get_utilities_keyboard() -> types.ReplyKeyboardMarkup:
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add("Назад")
            return kb

try:
    from utilities_registry import register_utility  # type: ignore
except Exception:
    try:
        from menu_registry.utilities_registry import register_utility  # type: ignore
    except Exception:
        register_utility = None  # type: ignore


TRIGGER_TEXT = "Пользователи TG (база)"
SHOW_USERS_TEXT = "Показать всех пользователей"
CLEAR_USERS_TEXT = "Очистить базу пользователей"
CONFIRM_CLEAR_TEXT = "Подтвердить очистку"
CANCEL_CLEAR_TEXT = "Отмена очистки"
BACK_TO_UTILITIES_TEXT = "Назад в утилиты"

_MODE: Dict[int, bool] = {}
_WAITING_CLEAR_CONFIRM: Dict[int, bool] = {}


def _main_module():
    return sys.modules.get("__main__")


def _is_authorized(user_id: int) -> bool:
    main_mod = _main_module()
    if main_mod is None:
        return False
    try:
        authorized_users = getattr(main_mod, "authorized_users", set())
        return user_id in authorized_users
    except Exception:
        return False


def _log(text: str) -> None:
    main_mod = _main_module()
    if main_mod is not None:
        for attr in ("write_bot_log", "write_log", "log"):
            try:
                fn = getattr(main_mod, attr, None)
                if callable(fn):
                    fn(text)
                    return
            except Exception:
                continue
    try:
        print(text)
    except Exception:
        pass


def _resolve_base_dir() -> Path:
    main_mod = _main_module()
    if main_mod is not None:
        for attr in ("base_dir", "BASE_DIR"):
            try:
                value = getattr(main_mod, attr, None)
                if value:
                    p = Path(value).resolve()
                    if p.exists() and p.is_dir():
                        return p
            except Exception:
                pass
        try:
            get_app_dir = getattr(main_mod, "get_app_dir", None)
            if callable(get_app_dir):
                value = get_app_dir()
                if value:
                    p = Path(value).resolve()
                    if p.exists() and p.is_dir():
                        return p
        except Exception:
            pass
    return Path.cwd().resolve()


def _import_store():
    last_error: Optional[Exception] = None
    for name in ("activated_users_store", "moduls.activated_users_store"):
        try:
            return importlib.import_module(name)
        except Exception as exc:
            last_error = exc

    try:
        base_dir = _resolve_base_dir()
        moduls_dir = base_dir / "moduls"
        if moduls_dir.exists():
            moduls_dir_str = str(moduls_dir)
            if moduls_dir_str not in sys.path:
                sys.path.insert(0, moduls_dir_str)
            try:
                return importlib.import_module("activated_users_store")
            except Exception as exc:
                last_error = exc
            file_path = moduls_dir / "activated_users_store.py"
            if file_path.exists():
                spec = importlib.util.spec_from_file_location("activated_users_store", str(file_path))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules.setdefault("activated_users_store", module)
                    return module
    except Exception as exc:
        last_error = exc

    if last_error:
        raise last_error
    raise ImportError("Не удалось импортировать activated_users_store")


def _safe_text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _ensure_user_state(user_id: int) -> None:
    if user_id not in _MODE:
        _MODE[user_id] = False
    if user_id not in _WAITING_CLEAR_CONFIRM:
        _WAITING_CLEAR_CONFIRM[user_id] = False


def _set_mode(user_id: int, enabled: bool) -> None:
    _ensure_user_state(user_id)
    _MODE[user_id] = bool(enabled)
    if not enabled:
        _WAITING_CLEAR_CONFIRM[user_id] = False


def _menu_keyboard(waiting_confirm: bool = False) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if waiting_confirm:
        kb.add(CONFIRM_CLEAR_TEXT)
        kb.add(CANCEL_CLEAR_TEXT)
        kb.add(SHOW_USERS_TEXT)
        kb.add(BACK_TO_UTILITIES_TEXT)
    else:
        kb.add(SHOW_USERS_TEXT)
        kb.add(CLEAR_USERS_TEXT)
        kb.add(BACK_TO_UTILITIES_TEXT)
    return kb


def _chunk_text(text: str, limit: int = 3500) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in text.splitlines():
        add_len = len(line) + 1
        if current and (current_len + add_len > limit):
            parts.append("\n".join(current))
            current = [line]
            current_len = len(line) + 1
        else:
            current.append(line)
            current_len += add_len
    if current:
        parts.append("\n".join(current))
    return parts


def _format_users(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "Пользователи не найдены. База пока пустая."

    lines: List[str] = [f"Активированные пользователи: {len(rows)}", ""]
    for idx, row in enumerate(rows, 1):
        user_id = _safe_int(row.get("user_id"))
        chat_id = _safe_int(row.get("chat_id"))
        username = _safe_text(row.get("username"), fallback="")
        if username and username != "-" and not username.startswith("@"):
            username = f"@{username}"

        line = (
            f"{idx}. id={_safe_text(user_id)}; "
            f"chat={_safe_text(chat_id)}; "
            f"user={_safe_text(username)}; "
            f"name={_safe_text(row.get('first_name'))} {_safe_text(row.get('last_name'))}; "
            f"lang={_safe_text(row.get('language_code'))}; "
            f"is_bot={_safe_text(row.get('is_bot'))}; "
            f"first={_safe_text(row.get('activated_at'))}; "
            f"last={_safe_text(row.get('last_activated_at'))}; "
            f"count={_safe_text(row.get('activation_count'))}; "
            f"source={_safe_text(row.get('last_source'))}"
        )
        lines.append(line)
    return "\n".join(lines)


def register_handlers(dp: Dispatcher):
    if callable(register_utility):
        try:
            register_utility(
                key="tg_activated_users_db",
                title=TRIGGER_TEXT,
                trigger_text=TRIGGER_TEXT,
                group="utilities",
                order=24,
                description="Просмотр и очистка БД активированных пользователей Telegram.",
            )
        except Exception:
            pass

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and message.text.strip() == TRIGGER_TEXT
        )
    )
    async def users_db_entry(message: types.Message):
        user_id = message.from_user.id
        _set_mode(user_id, True)
        _log(f"[USERS-DB] user {user_id} opened module")
        await message.answer(
            "Меню базы активированных пользователей.\n"
            "Выбери действие: показать список или очистить базу.",
            reply_markup=_menu_keyboard(waiting_confirm=False),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
            and message.text.strip().lower() in {BACK_TO_UTILITIES_TEXT.lower(), "назад в утилиты"}
        )
    )
    async def users_db_back_to_utilities(message: types.Message):
        user_id = message.from_user.id
        _set_mode(user_id, False)
        await message.answer(
            "Возвращаю в меню утилит.",
            reply_markup=get_utilities_keyboard(),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
            and message.text.strip() == SHOW_USERS_TEXT
        )
    )
    async def users_db_show_all(message: types.Message):
        user_id = message.from_user.id
        _ensure_user_state(user_id)
        _WAITING_CLEAR_CONFIRM[user_id] = False

        try:
            store = _import_store()
            base_dir = str(_resolve_base_dir())
            rows = store.list_activated_users(base_dir) or []
            db_path = (
                str(store.get_db_path(base_dir))
                if callable(getattr(store, "get_db_path", None))
                else str(_resolve_base_dir() / "data" / "activated_users.db")
            )
        except Exception as exc:
            await message.answer(
                "Не удалось прочитать базу активированных пользователей.\n"
                f"Ошибка: {exc}",
                reply_markup=_menu_keyboard(waiting_confirm=False),
            )
            _log(f"[USERS-DB] show users failed: {exc}")
            return

        report = _format_users(rows)
        chunks = _chunk_text(report, limit=3500)
        for index, chunk in enumerate(chunks, 1):
            prefix = f"Часть {index}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
            await message.answer(prefix + chunk)

        await message.answer(
            f"Путь к БД: {db_path}",
            reply_markup=_menu_keyboard(waiting_confirm=False),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
            and message.text.strip() == CLEAR_USERS_TEXT
        )
    )
    async def users_db_clear_request(message: types.Message):
        user_id = message.from_user.id
        _ensure_user_state(user_id)

        try:
            store = _import_store()
            base_dir = str(_resolve_base_dir())
            rows = store.list_activated_users(base_dir) or []
            count = len(rows)
        except Exception as exc:
            await message.answer(
                "Не удалось подготовить очистку.\n"
                f"Ошибка: {exc}",
                reply_markup=_menu_keyboard(waiting_confirm=False),
            )
            _log(f"[USERS-DB] clear prepare failed: {exc}")
            return

        if count <= 0:
            _WAITING_CLEAR_CONFIRM[user_id] = False
            await message.answer(
                "База уже пустая, очищать нечего.",
                reply_markup=_menu_keyboard(waiting_confirm=False),
            )
            return

        _WAITING_CLEAR_CONFIRM[user_id] = True
        await message.answer(
            "Подтверждение очистки базы.\n"
            f"Будет удалено записей: {count}\n"
            "Нажми «Подтвердить очистку» или «Отмена очистки».",
            reply_markup=_menu_keyboard(waiting_confirm=True),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
            and _WAITING_CLEAR_CONFIRM.get(message.from_user.id, False)
            and message.text.strip() == CANCEL_CLEAR_TEXT
        )
    )
    async def users_db_clear_cancel(message: types.Message):
        user_id = message.from_user.id
        _WAITING_CLEAR_CONFIRM[user_id] = False
        await message.answer(
            "Очистка отменена.",
            reply_markup=_menu_keyboard(waiting_confirm=False),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
            and _WAITING_CLEAR_CONFIRM.get(message.from_user.id, False)
            and message.text.strip() == CONFIRM_CLEAR_TEXT
        )
    )
    async def users_db_clear_confirm(message: types.Message):
        user_id = message.from_user.id
        _WAITING_CLEAR_CONFIRM[user_id] = False

        try:
            store = _import_store()
            base_dir = str(_resolve_base_dir())
            clear_fn = getattr(store, "clear_activated_users", None)
            if not callable(clear_fn):
                raise RuntimeError("В activated_users_store отсутствует clear_activated_users")
            deleted = int(clear_fn(base_dir))
        except Exception as exc:
            await message.answer(
                "Не удалось очистить базу.\n"
                f"Ошибка: {exc}",
                reply_markup=_menu_keyboard(waiting_confirm=False),
            )
            _log(f"[USERS-DB] clear failed: {exc}")
            return

        _log(f"[USERS-DB] user {user_id} cleared users db, deleted={deleted}")
        await message.answer(
            f"База очищена. Удалено записей: {deleted}",
            reply_markup=_menu_keyboard(waiting_confirm=False),
        )

    @dp.message_handler(
        lambda message: (
            bool(message and message.from_user and message.text)
            and _is_authorized(message.from_user.id)
            and _MODE.get(message.from_user.id, False)
        )
    )
    async def users_db_fallback(message: types.Message):
        user_id = message.from_user.id
        waiting = _WAITING_CLEAR_CONFIRM.get(user_id, False)
        if waiting:
            await message.answer(
                "Ожидается подтверждение очистки.\n"
                "Нажми «Подтвердить очистку» или «Отмена очистки».",
                reply_markup=_menu_keyboard(waiting_confirm=True),
            )
            return

        await message.answer(
            "Доступные действия:\n"
            f"- {SHOW_USERS_TEXT}\n"
            f"- {CLEAR_USERS_TEXT}\n"
            f"- {BACK_TO_UTILITIES_TEXT}",
            reply_markup=_menu_keyboard(waiting_confirm=False),
        )
