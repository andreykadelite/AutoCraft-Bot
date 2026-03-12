# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sys
import platform
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set


LogFn = Optional[Callable[[str], None]]
PostAuthReportSender = Optional[Callable[[int], Awaitable[None]]]


@dataclass(frozen=True)
class StartupTarget:
    user_id: int
    chat_id: int
    source: str


def _log(write_log: LogFn, message: str) -> None:
    if callable(write_log):
        try:
            write_log(message)
            return
        except Exception:
            pass
    try:
        print(message)
    except Exception:
        pass


def _safe_int(value: Any) -> Optional[int]:
    try:
        ivalue = int(value)
    except Exception:
        return None
    return ivalue


def _normalize_allowed_ids(raw_ids: Iterable[Any]) -> Set[int]:
    result: Set[int] = set()
    for raw in raw_ids or []:
        user_id = _safe_int(raw)
        if user_id is None or user_id <= 0:
            continue
        result.add(user_id)
    return result


def _keyboard_has_buttons(reply_markup: Any) -> bool:
    if reply_markup is None:
        return False
    try:
        rows = getattr(reply_markup, "keyboard", None)
        if not rows:
            return False
        for row in rows:
            if row:
                return True
    except Exception:
        return False
    return False


async def _wait_for_mainmenu_items(
    *,
    timeout: float = 15.0,
    interval: float = 0.5,
    stable_checks: int = 3,
) -> bool:
    try:
        from menu_registry.mainmenu_registry import get_main_items
    except Exception:
        try:
            from mainmenu_registry import get_main_items  # type: ignore
        except Exception:
            return False

    end_ts = asyncio.get_event_loop().time() + timeout
    last_count: Optional[int] = None
    stable_steps = 0

    while asyncio.get_event_loop().time() < end_ts:
        try:
            items = get_main_items(group="main") or []
            count = len(items)
        except Exception:
            items = []
            count = 0

        if count > 0:
            if last_count is None or count != last_count:
                last_count = count
                stable_steps = 1
            else:
                stable_steps += 1
            if stable_steps >= stable_checks:
                return True

        await asyncio.sleep(interval)

    try:
        items = get_main_items(group="main") or []
        return len(items) > 0
    except Exception:
        return False


async def _wait_for_post_auth_imports(
    *,
    timeout: float = 30.0,
    interval: float = 0.4,
    write_log: LogFn = None,
) -> bool:
    """
    Ждём, пока менеджеры модулей закончат импорт модулей после авторизации.

    Оба менеджера при завершении импорта сбрасывают внутренний список результатов в None.
    Пока значение не None, импорт ещё идёт (или ожидает авторизацию).
    """
    module_names = (
        "Moduls_manager_ext",
        "moduls.Moduls_manager_sys_ext",
    )
    watchers: List[tuple[str, Callable[[], Any]]] = []
    for mod_name in module_names:
        module_obj = sys.modules.get(mod_name)
        if module_obj is None:
            continue
        getter = getattr(module_obj, "_get_import_results_list", None)
        if callable(getter):
            watchers.append((mod_name, getter))

    if not watchers:
        return True

    end_ts = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end_ts:
        pending_any = False
        for mod_name, getter in watchers:
            try:
                state = getter()
            except Exception:
                state = None
            if state is not None:
                pending_any = True
                break

        if not pending_any:
            return True

        await asyncio.sleep(interval)

    _log(write_log, "[STARTUP] Таймаут ожидания завершения пост-авторизационного импорта модулей.")
    return False


def _load_activated_map(
    *,
    activated_users_store_module: Any,
    base_dir: str,
    write_log: LogFn,
) -> Dict[int, int]:
    if activated_users_store_module is None:
        return {}

    rows: List[Dict[str, Any]] = []
    try:
        lister = getattr(activated_users_store_module, "list_activated_users", None)
        if callable(lister):
            loaded = lister(base_dir)
            if isinstance(loaded, list):
                rows = loaded
    except Exception as exc:
        _log(write_log, f"[STARTUP] Не удалось получить список активированных пользователей: {exc}")
        rows = []

    if not rows:
        try:
            ids_lister = getattr(activated_users_store_module, "list_activated_user_ids", None)
            if callable(ids_lister):
                for uid in ids_lister(base_dir) or []:
                    user_id = _safe_int(uid)
                    if user_id is not None and user_id > 0:
                        rows.append({"user_id": user_id, "chat_id": user_id})
        except Exception as exc:
            _log(write_log, f"[STARTUP] Не удалось получить ID активированных пользователей: {exc}")

    activated_map: Dict[int, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        user_id = _safe_int(row.get("user_id"))
        if user_id is None or user_id <= 0:
            continue
        chat_id = _safe_int(row.get("chat_id"))
        if chat_id is None or chat_id <= 0:
            chat_id = user_id
        activated_map[user_id] = chat_id
    return activated_map


def resolve_startup_targets(
    *,
    allowed_accounts: Iterable[Any],
    activated_users_store_module: Any,
    base_dir: str,
    write_log: LogFn,
) -> List[StartupTarget]:
    allowed_ids = _normalize_allowed_ids(allowed_accounts)
    activated_map = _load_activated_map(
        activated_users_store_module=activated_users_store_module,
        base_dir=base_dir,
        write_log=write_log,
    )

    targets: List[StartupTarget] = []
    if allowed_ids:
        for user_id in sorted(allowed_ids):
            chat_id = activated_map.get(user_id, user_id)
            targets.append(StartupTarget(user_id=user_id, chat_id=chat_id, source="allowed_ids"))
    else:
        for user_id in sorted(activated_map):
            chat_id = activated_map[user_id]
            targets.append(StartupTarget(user_id=user_id, chat_id=chat_id, source="activated_users"))

    return targets


def build_startup_status_text(*, now: Optional[datetime] = None, device_name: Optional[str] = None) -> str:
    now = now or datetime.now()
    dn = (device_name or platform.node() or platform.system() or "unknown").strip() or "unknown"
    return (
        "Статус: бот запущен.\n"
        f"Устройство: {dn}\n"
        f"Дата: {now.strftime('%d.%m.%Y')}\n"
        f"Время: {now.strftime('%H:%M:%S')}"
    )


async def _safe_send_message(
    *,
    bot: Any,
    chat_id: int,
    text: str,
    write_log: LogFn,
    reply_markup: Any = None,
) -> bool:
    try:
        if reply_markup is not None:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id, text)
        return True
    except Exception as exc:
        _log(write_log, f"[STARTUP] Не удалось отправить сообщение chat_id={chat_id}: {exc}")
        return False


async def _save_auto_activation(
    *,
    bot: Any,
    target: StartupTarget,
    activated_users_store_module: Any,
    base_dir: str,
    write_log: LogFn,
) -> None:
    if activated_users_store_module is None:
        return

    saver = getattr(activated_users_store_module, "save_activated_user", None)
    if not callable(saver):
        return

    user_obj: Any = None
    try:
        user_obj = await bot.get_chat(target.user_id)
    except Exception as exc:
        _log(write_log, f"[STARTUP] Не удалось получить данные пользователя {target.user_id}: {exc}")

    if user_obj is None:
        user_obj = SimpleNamespace(
            id=target.user_id,
            username="",
            first_name="",
            last_name="",
            language_code="",
            is_bot=False,
        )

    try:
        saver(
            base_dir=base_dir,
            user=user_obj,
            chat_id=target.chat_id,
            source="startup_auto_auth",
        )
    except Exception as exc:
        _log(write_log, f"[STARTUP] Не удалось сохранить auto-auth для {target.user_id}: {exc}")


async def run_startup_sequence(
    *,
    bot: Any,
    base_dir: str,
    pin_code: str,
    allowed_accounts: Iterable[Any],
    authorized_users: Set[int],
    activated_users_store_module: Any = None,
    get_main_keyboard: Optional[Callable[[], Any]] = None,
    post_auth_report_sender: PostAuthReportSender = None,
    write_log: LogFn = None,
) -> Dict[str, int]:
    pin_required = bool((pin_code or "").strip())
    startup_text = build_startup_status_text()

    targets = resolve_startup_targets(
        allowed_accounts=allowed_accounts,
        activated_users_store_module=activated_users_store_module,
        base_dir=base_dir,
        write_log=write_log,
    )

    stats = {
        "targets": len(targets),
        "status_sent": 0,
        "pin_prompted": 0,
        "auto_authorized": 0,
        "post_auth_reported": 0,
    }

    if not targets:
        _log(
            write_log,
            "[STARTUP] Нет получателей для стартового сообщения: список allowed_ids пуст и БД активированных пользователей пустая.",
        )
        return stats

    auto_targets: List[StartupTarget] = []

    for target in targets:
        sent = await _safe_send_message(
            bot=bot,
            chat_id=target.chat_id,
            text=startup_text,
            write_log=write_log,
        )
        if sent:
            stats["status_sent"] += 1

        if pin_required:
            prompted = await _safe_send_message(
                bot=bot,
                chat_id=target.chat_id,
                text="Введите PIN-код:",
                write_log=write_log,
            )
            if prompted:
                stats["pin_prompted"] += 1
            await asyncio.sleep(0.05)
            continue

        authorized_users.add(target.user_id)
        await _save_auto_activation(
            bot=bot,
            target=target,
            activated_users_store_module=activated_users_store_module,
            base_dir=base_dir,
            write_log=write_log,
        )
        auto_targets.append(target)
        stats["auto_authorized"] += 1
        await asyncio.sleep(0.05)

    if pin_required or not auto_targets:
        return stats

    # 1) Сначала дожидаемся завершения импорта post-auth модулей.
    await _wait_for_post_auth_imports(timeout=30.0, interval=0.4, write_log=write_log)

    # 2) Потом ждём стабилизацию реестра главного меню.
    keyboard = None
    has_menu = False
    if callable(get_main_keyboard):
        try:
            keyboard = get_main_keyboard()
        except Exception as exc:
            _log(write_log, f"[STARTUP] Не удалось сформировать клавиатуру главного меню: {exc}")
            keyboard = None
    if keyboard is not None and not _keyboard_has_buttons(keyboard):
        keyboard = None

    if keyboard is not None:
        has_menu = True
    else:
        try:
            has_menu = await _wait_for_mainmenu_items(timeout=20.0, interval=0.5, stable_checks=4)
        except Exception:
            has_menu = False
        if has_menu and callable(get_main_keyboard):
            try:
                keyboard = get_main_keyboard()
            except Exception:
                keyboard = None
            if keyboard is not None and not _keyboard_has_buttons(keyboard):
                has_menu = False
                keyboard = None

    # 3) После завершения регистрации отправляем авторизацию и меню всем автоавторизованным.
    for target in auto_targets:
        if keyboard is not None:
            await _safe_send_message(
                bot=bot,
                chat_id=target.chat_id,
                text="Вы авторизовались.",
                reply_markup=keyboard,
                write_log=write_log,
            )
            if callable(post_auth_report_sender):
                try:
                    await post_auth_report_sender(target.chat_id)
                    stats["post_auth_reported"] += 1
                except Exception as exc:
                    _log(write_log, f"[STARTUP] Failed to send post-auth report chat_id={target.chat_id}: {exc}")
            if has_menu:
                await _safe_send_message(
                    bot=bot,
                    chat_id=target.chat_id,
                    text="Главное меню ✅",
                    reply_markup=keyboard,
                    write_log=write_log,
                )
            else:
                await _safe_send_message(
                    bot=bot,
                    chat_id=target.chat_id,
                    text=(
                        "Главное меню пока пустое: модули ещё не зарегистрировали свои кнопки. "
                        "Если через пару секунд ничего не появится, просто напиши /start."
                    ),
                    write_log=write_log,
                )
        else:
            await _safe_send_message(
                bot=bot,
                chat_id=target.chat_id,
                text="Вы авторизовались.",
                write_log=write_log,
            )
            if callable(post_auth_report_sender):
                try:
                    await post_auth_report_sender(target.chat_id)
                    stats["post_auth_reported"] += 1
                except Exception as exc:
                    _log(write_log, f"[STARTUP] Failed to send post-auth report chat_id={target.chat_id}: {exc}")
            await _safe_send_message(
                bot=bot,
                chat_id=target.chat_id,
                text=(
                    "Главное меню пока пустое: модули ещё не зарегистрировали свои кнопки. "
                    "Если через пару секунд ничего не появится, просто напиши /start."
                ),
                write_log=write_log,
            )
        await asyncio.sleep(0.05)

    return stats
