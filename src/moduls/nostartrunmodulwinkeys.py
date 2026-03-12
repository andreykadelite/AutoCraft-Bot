# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, List

from aiogram import types
from aiogram.dispatcher import Dispatcher

from keymenu import get_utilities_keyboard
from __main__ import authorized_users, write_bot_log
from utilities_registry import register_utility

try:
    from moduls.web_dashboard.web_plugins.win_keys import plugin as wk_plugin
except Exception:
    try:
        from web_dashboard.web_plugins.win_keys import plugin as wk_plugin  # type: ignore
    except Exception:
        wk_plugin = None  # type: ignore


BTN_OPEN = "Клавиатура WinKeys"
BTN_BACK = "Назад в утилиты"
BTN_MAIN = "Главное WinKeys"
BTN_CATS = "Категории команд"
BTN_PRESETS = "Пресеты (общие)"
BTN_HISTORY = "История (общая)"
BTN_REPEAT = "Повторить последнюю"
BTN_SAVE_LAST = "Сохранить последнюю в пресеты"
BTN_HELP = "Справка WinKeys"
BTN_GUIDE = "Полная справка команд"
BTN_PREV = "Страница назад"
BTN_NEXT = "Страница вперед"
BTN_TO_CATS = "К категориям"
BTN_DEL_MODE = "Режим удаления пресетов"
BTN_CLEAR_PRESETS = "Очистить пресеты"
BTN_CLEAR_HISTORY = "Очистить историю"
BTN_CONFIRM = "Подтвердить"
BTN_CANCEL = "Отмена"
BTN_RUN_SELECTED = "Выполнить выбранную команду"
BTN_SAVE_SELECTED = "Сохранить выбранную команду"
BTN_BACK_TO_LIST = "Назад к списку команд"

PAGE_CMD = 8
PAGE_PRESET = 8
PAGE_HISTORY = 8

KEY_TYPES = {"hotkey", "key", "text"}
CAT_ORDER = [
    "Windows",
    "Система",
    "Текст",
    "Браузер",
    "Проводник",
    "Скриншоты",
    "Клавиши по отдельности",
]

STATE: Dict[int, Dict[str, Any]] = {}


def _log(text: str) -> None:
    try:
        write_bot_log(text)
    except Exception:
        pass


def _auth(uid: int) -> bool:
    try:
        if isinstance(authorized_users, dict):
            return uid in authorized_users
        return uid in authorized_users
    except Exception:
        return False


def _u(uid: int) -> Dict[str, Any]:
    st = STATE.get(uid)
    if st is None:
        st = {
            "mode": False,
            "screen": "main",
            "page": 0,
            "category": "",
            "map": {},
            "last_action": {},
            "last_name": "",
            "delete_mode": False,
            "confirm_action": "",
            "confirm_return": "main",
            "selected_action": {},
            "selected_name": "",
            "selected_combo": "",
            "selected_category": "",
            "selected_blocked": "",
        }
        STATE[uid] = st
    return st


def _clip(val: Any, size: int = 80) -> str:
    text = str(val or "").replace("\n", " ").strip()
    if len(text) <= size:
        return text
    return text[: size - 3].rstrip() + "..."


def _rows(labels: List[str], width: int) -> List[List[str]]:
    out: List[List[str]] = []
    cur: List[str] = []
    for label in labels:
        cur.append(label)
        if len(cur) >= width:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _kb(rows_data: List[List[str]]) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for row in rows_data:
        if row:
            kb.add(*row)
    return kb


def _paginate(items: List[Dict[str, Any]], page: int, per_page: int) -> tuple[List[Dict[str, Any]], int, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    start = page * per_page
    return items[start : start + per_page], page, total_pages


def _is_key_action(action: Any) -> bool:
    if not isinstance(action, dict):
        return False
    return str(action.get("type") or "").strip().lower() in KEY_TYPES


def _summary(action: Dict[str, Any]) -> str:
    if wk_plugin and hasattr(wk_plugin, "_action_summary"):
        try:
            return str(wk_plugin._action_summary(action))
        except Exception:
            pass
    kind = str(action.get("type") or "").lower()
    if kind == "hotkey":
        keys = action.get("keys") if isinstance(action.get("keys"), list) else []
        return " + ".join(str(k) for k in keys) if keys else "hotkey"
    if kind == "key":
        return str(action.get("key") or "key")
    if kind == "text":
        return _clip(action.get("text") or "", 60)
    return kind or "команда"


def _fmt_ts(ts: Any) -> str:
    try:
        value = int(ts)
        if value <= 0:
            return ""
        return time.strftime("%d.%m %H:%M:%S", time.localtime(value))
    except Exception:
        return ""


def _button_label(prefix: str, value: Any, max_len: int = 30) -> str:
    text = _clip(value, max_len)
    return f"{prefix} {text}".strip()


def _command_meaning(name: str, combo: str, category: str) -> str:
    title = str(name or "Команда").strip()
    combo_text = str(combo or "не указано").strip()
    cat = str(category or "Прочее").strip()
    return (
        f"Команда: {title}\n"
        f"Комбинация: {combo_text}\n"
        f"Категория: {cat}\n"
        f"Что делает: {title}"
    )


def _split_by_limit(lines: List[str], limit: int = 3500) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        line_text = str(line)
        extra = len(line_text) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = [line_text]
            current_len = len(line_text)
        else:
            current.append(line_text)
            current_len += extra

    if current:
        chunks.append("\n".join(current))
    return chunks


def _backend_missing() -> str:
    return "win_keys backend недоступен (plugin.py не импортировался)."


def _storage_path() -> str:
    if not wk_plugin:
        return "не определён"
    try:
        return str(wk_plugin._resolve_storage_file())
    except Exception:
        return "не определён"


def _load_store() -> tuple[Dict[str, Any], str]:
    if not wk_plugin:
        return {}, _backend_missing()
    try:
        store = wk_plugin._load_store()
        return (store if isinstance(store, dict) else {}), ""
    except Exception as exc:
        return {}, f"Ошибка чтения win_keys_store.json: {type(exc).__name__}: {exc}"


def _builtins() -> List[Dict[str, Any]]:
    if not wk_plugin:
        return []
    source = getattr(wk_plugin, "_BUILTIN_ACTIONS", [])
    if not isinstance(source, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        if not _is_key_action(action):
            continue
        out.append(
            {
                "name": str(item.get("name") or "Команда"),
                "category": str(item.get("category") or "Прочее"),
                "combo": str(item.get("combo") or _summary(action)),
                "blocked": str(item.get("blocked_reason") or "").strip(),
                "action": copy.deepcopy(action),
            }
        )
    return out


def _guide_lines(items: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    counts: Dict[str, int] = {}
    for item in items:
        cat = str(item.get("category") or "Прочее").strip() or "Прочее"
        counts[cat] = counts.get(cat, 0) + 1

    order = {name: i for i, name in enumerate(CAT_ORDER)}
    categories = sorted(counts.keys(), key=lambda name: (order.get(name, 999), name.lower()))

    lines.append("Полная справка по клавиатурным командам WinKeys")
    lines.append(f"Всего команд: {len(items)}")
    lines.append("")

    for cat in categories:
        lines.append(f"[{cat}] ({counts.get(cat, 0)})")
        cat_items = [it for it in items if str(it.get("category") or "").strip() == cat]
        for item in cat_items:
            combo = str(item.get("combo") or _summary(item.get("action") or {})).strip()
            name = str(item.get("name") or "Команда").strip()
            lines.append(f"- {combo}: {name}")
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _presets(store: Dict[str, Any]) -> List[Dict[str, Any]]:
    custom = store.get("custom_actions") if isinstance(store, dict) else []
    if not isinstance(custom, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in custom:
        if not isinstance(item, dict):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        if not _is_key_action(action):
            continue
        out.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "Пресет"),
                "updated_at": int(item.get("updated_at") or 0),
                "action": copy.deepcopy(action),
            }
        )
    out.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return out


def _history(store: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = store.get("history") if isinstance(store, dict) else []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        if not _is_key_action(action):
            continue
        out.append(
            {
                "name": str(item.get("name") or "Команда"),
                "summary": str(item.get("summary") or _summary(action)),
                "ok": bool(item.get("ok")),
                "ts": int(item.get("ts") or 0),
                "action": copy.deepcopy(action),
            }
        )
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    return out


def _execute(uid: int, action_payload: Dict[str, Any], source: str, name: str) -> tuple[bool, str]:
    st = _u(uid)
    if not wk_plugin:
        return False, _backend_missing()
    try:
        action, err = wk_plugin._normalize_action_payload(action_payload)
        if err or not action:
            return False, err or "Некорректная команда."
        ok, msg = wk_plugin._execute_action(action)
        try:
            wk_plugin._append_history(action, source=source, name=name, ok=ok, message=msg, user=f"tg:{uid}")
        except Exception:
            pass
        st["last_action"] = copy.deepcopy(action)
        st["last_name"] = name
        return bool(ok), str(msg)
    except Exception as exc:
        return False, f"Ошибка выполнения: {type(exc).__name__}: {exc}"


def _save_preset(action_payload: Dict[str, Any], base_name: str) -> tuple[bool, str]:
    if not wk_plugin:
        return False, _backend_missing()
    try:
        action, err = wk_plugin._normalize_action_payload(action_payload)
        if err or not action:
            return False, err or "Некорректная команда."
        with wk_plugin._STORAGE_LOCK:
            store = wk_plugin._read_store_no_lock()
            custom = list(store.get("custom_actions") or [])
            max_count = int(getattr(wk_plugin, "_MAX_CUSTOM_ACTIONS", 300))
            if len(custom) >= max_count:
                return False, "Достигнут лимит сохранённых пресетов."

            names = {str(item.get("name") or "") for item in custom if isinstance(item, dict)}
            safe_name = wk_plugin._normalize_name(base_name)
            if safe_name in names:
                idx = 2
                while True:
                    candidate = wk_plugin._normalize_name(f"{safe_name} ({idx})")
                    if candidate not in names:
                        safe_name = candidate
                        break
                    idx += 1

            ts = int(time.time())
            custom.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "name": safe_name,
                    "action": action,
                    "created_at": ts,
                    "updated_at": ts,
                }
            )
            store["custom_actions"] = wk_plugin._normalize_custom_items(custom)
            wk_plugin._write_store_no_lock(store)
        return True, f"Пресет сохранён: {safe_name}"
    except Exception as exc:
        return False, f"Ошибка сохранения пресета: {type(exc).__name__}: {exc}"


def _delete_preset(preset_id: str) -> tuple[bool, str]:
    if not wk_plugin:
        return False, _backend_missing()
    pid = str(preset_id or "").strip()
    if not pid:
        return False, "Не указан ID пресета."
    try:
        with wk_plugin._STORAGE_LOCK:
            store = wk_plugin._read_store_no_lock()
            custom = list(store.get("custom_actions") or [])
            after = [item for item in custom if str(item.get("id") or "") != pid]
            if len(after) == len(custom):
                return False, "Пресет не найден."
            store["custom_actions"] = wk_plugin._normalize_custom_items(after)
            wk_plugin._write_store_no_lock(store)
        return True, "Пресет удалён."
    except Exception as exc:
        return False, f"Ошибка удаления пресета: {type(exc).__name__}: {exc}"


def _clear_presets() -> tuple[bool, str]:
    if not wk_plugin:
        return False, _backend_missing()
    try:
        with wk_plugin._STORAGE_LOCK:
            store = wk_plugin._read_store_no_lock()
            store["custom_actions"] = []
            store["example_presets_version"] = int(getattr(wk_plugin, "_EXAMPLE_PRESETS_VERSION", 2))
            wk_plugin._write_store_no_lock(store)
        return True, "Пресеты очищены."
    except Exception as exc:
        return False, f"Ошибка очистки пресетов: {type(exc).__name__}: {exc}"


def _clear_history() -> tuple[bool, str]:
    if not wk_plugin:
        return False, _backend_missing()
    try:
        with wk_plugin._STORAGE_LOCK:
            store = wk_plugin._read_store_no_lock()
            store["history"] = []
            wk_plugin._write_store_no_lock(store)
        return True, "История очищена."
    except Exception as exc:
        return False, f"Ошибка очистки истории: {type(exc).__name__}: {exc}"


def _set_screen(uid: int, screen: str, page: int = 0) -> None:
    st = _u(uid)
    st["screen"] = screen
    st["page"] = max(0, int(page))

async def _render_main(message: types.Message, uid: int, note: str = "") -> None:
    st = _u(uid)
    _set_screen(uid, "main", 0)
    st["map"] = {}
    st["confirm_action"] = ""
    st["confirm_return"] = "main"

    items = _builtins()
    store, err = _load_store()
    presets = _presets(store) if not err else []
    history = _history(store) if not err else []

    lines = [
        "Режим WinKeys: клавиатурные команды на кнопках.",
        f"Команд: {len(items)}",
        f"Пресетов (общих): {len(presets)}",
        f"История (общая): {len(history)}",
        f"Хранилище: {_storage_path()}",
    ]
    if err:
        lines.append(f"Ошибка хранилища: {err}")
    if note:
        lines.append("")
        lines.append(note)

    kb = _kb(
        [
            [BTN_CATS],
            [BTN_PRESETS],
            [BTN_HISTORY],
            [BTN_REPEAT],
            [BTN_SAVE_LAST],
            [BTN_HELP],
            [BTN_GUIDE],
            [BTN_BACK],
        ]
    )
    await message.answer("\n".join(lines), reply_markup=kb)


async def _render_categories(message: types.Message, uid: int, note: str = "") -> None:
    st = _u(uid)
    _set_screen(uid, "categories", 0)

    items = _builtins()
    counts: Dict[str, int] = {}
    for item in items:
        cat = str(item.get("category") or "Прочее").strip() or "Прочее"
        counts[cat] = counts.get(cat, 0) + 1

    order = {name: i for i, name in enumerate(CAT_ORDER)}
    cats = sorted(counts.items(), key=lambda p: (order.get(p[0], 999), p[0].lower()))

    mapping: Dict[str, Dict[str, Any]] = {}
    rows_data: List[List[str]] = []

    all_label = f"Все категории ({len(items)})"
    rows_data.append([all_label])
    mapping[all_label] = {"kind": "category", "category": ""}

    for cat, count in cats:
        label = f"{cat} ({count})"
        rows_data.append([label])
        mapping[label] = {"kind": "category", "category": cat}

    rows_data.append([BTN_MAIN])
    rows_data.append([BTN_BACK])

    st["map"] = mapping

    lines = ["Категории клавиатурных команд:", f"Всего команд: {len(items)}"]
    for cat, count in cats:
        lines.append(f"- {cat}: {count}")
    if note:
        lines.append("")
        lines.append(note)

    await message.answer("\n".join(lines), reply_markup=_kb(rows_data))


async def _render_help(message: types.Message, uid: int) -> None:
    text = (
        "Справка WinKeys:\n"
        "1) Открой 'Категории команд' и выбери раздел.\n"
        "2) Нажатие на кнопку команды показывает её расшифровку и назначение.\n"
        "3) Команда не отправляется сразу; запуск выполняется отдельной кнопкой в карточке.\n"
        "4) 'Пресеты (общие)' и 'История (общая)' используют общее хранилище win_keys.\n"
        "5) Кнопка 'Полная справка команд' отправляет общий список всех комбинаций и их значений.\n"
        "Важно: Ctrl+Alt+Delete отправляется, но результат зависит от политики Windows и контекста запуска."
    )
    await message.answer(text, reply_markup=_kb([[BTN_MAIN], [BTN_GUIDE], [BTN_BACK]]))


async def _send_full_guide(message: types.Message, uid: int) -> None:
    items = _builtins()
    if not items:
        await message.answer("Справочник команд пуст.", reply_markup=_kb([[BTN_MAIN], [BTN_BACK]]))
        return

    lines = _guide_lines(items)
    chunks = _split_by_limit(lines, limit=3400)

    for idx, chunk in enumerate(chunks):
        if idx == len(chunks) - 1:
            await message.answer(chunk, reply_markup=_kb([[BTN_MAIN], [BTN_BACK]]))
        else:
            await message.answer(chunk)


async def _render_command_preview(message: types.Message, uid: int, note: str = "") -> None:
    st = _u(uid)
    _set_screen(uid, "command_preview", st.get("page", 0))

    action = st.get("selected_action") if isinstance(st.get("selected_action"), dict) else {}
    name = str(st.get("selected_name") or "Команда")
    combo = str(st.get("selected_combo") or _summary(action))
    category = str(st.get("selected_category") or "Прочее")
    blocked = str(st.get("selected_blocked") or "").strip()

    lines = [
        "Карточка команды:",
        _command_meaning(name, combo, category),
    ]
    if blocked:
        lines.append(f"Ограничение: {blocked}")
    if note:
        lines.append("")
        lines.append(note)

    rows_data: List[List[str]] = []
    if action:
        if not blocked:
            rows_data.append([BTN_RUN_SELECTED])
        rows_data.append([BTN_SAVE_SELECTED])
    rows_data.extend([[BTN_BACK_TO_LIST], [BTN_MAIN], [BTN_BACK]])

    await message.answer("\n".join(lines), reply_markup=_kb(rows_data))


async def _render_commands(message: types.Message, uid: int, page: int | None = None, note: str = "") -> None:
    st = _u(uid)
    cat = str(st.get("category") or "")

    items = _builtins()
    if cat:
        items = [item for item in items if str(item.get("category") or "") == cat]

    page_val = st.get("page", 0) if page is None else page
    page_items, page_safe, total_pages = _paginate(items, int(page_val), PAGE_CMD)
    _set_screen(uid, "commands", page_safe)

    mapping: Dict[str, Dict[str, Any]] = {}
    labels: List[str] = []
    lines = [
        "Список команд:",
        f"Категория: {cat or 'Все категории'}",
        f"Страница: {page_safe + 1}/{total_pages}",
        f"Команд в выборке: {len(items)}",
        "Нажми кнопку команды, чтобы посмотреть её значение (без мгновенной отправки).",
    ]

    if page_items:
        lines.append("")
        for i, item in enumerate(page_items, start=1):
            label = _button_label(f"{i}.", item.get("combo") or _summary(item.get("action") or {}), max_len=26)
            labels.append(label)
            mapping[label] = {
                "kind": "builtin",
                "name": str(item.get("name") or "Команда"),
                "combo": str(item.get("combo") or _summary(item.get("action") or {})),
                "category": str(item.get("category") or cat or "Прочее"),
                "blocked": str(item.get("blocked") or "").strip(),
                "action": copy.deepcopy(item.get("action") or {}),
            }
            blocked = str(item.get("blocked") or "").strip()
            mark = "[X]" if blocked else "[ ]"
            lines.append(f"{i}. {mark} {_clip(item.get('name'), 70)} | {_clip(item.get('combo'), 70)}")
            if blocked:
                lines.append(f"   Блокировка: {_clip(blocked, 130)}")
    else:
        lines.append("")
        lines.append("Команды не найдены.")

    if note:
        lines.append("")
        lines.append(note)

    st["map"] = mapping

    rows_data: List[List[str]] = _rows(labels, 4)
    nav: List[str] = []
    if page_safe > 0:
        nav.append(BTN_PREV)
    if page_safe < total_pages - 1:
        nav.append(BTN_NEXT)
    if nav:
        rows_data.append(nav)

    rows_data.extend([[BTN_SAVE_LAST], [BTN_TO_CATS], [BTN_MAIN], [BTN_GUIDE], [BTN_BACK]])

    await message.answer("\n".join(lines), reply_markup=_kb(rows_data))


async def _render_presets(message: types.Message, uid: int, page: int | None = None, note: str = "") -> None:
    st = _u(uid)
    store, err = _load_store()
    presets = _presets(store) if not err else []

    page_val = st.get("page", 0) if page is None else page
    page_items, page_safe, total_pages = _paginate(presets, int(page_val), PAGE_PRESET)
    _set_screen(uid, "presets", page_safe)

    mapping: Dict[str, Dict[str, Any]] = {}
    labels: List[str] = []

    del_mode = bool(st.get("delete_mode", False))
    lines = [
        "Общие пресеты WinKeys:",
        f"Режим удаления: {'ВКЛ' if del_mode else 'ВЫКЛ'}",
        f"Страница: {page_safe + 1}/{total_pages}",
        f"Всего пресетов: {len(presets)}",
    ]
    if err:
        lines.append(f"Ошибка хранилища: {err}")

    if page_items:
        lines.append("")
        for i, item in enumerate(page_items, start=1):
            label = _button_label(f"{i}.", item.get("name"), max_len=24)
            labels.append(label)
            mapping[label] = {
                "kind": "preset",
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "Пресет"),
                "action": copy.deepcopy(item.get("action") or {}),
            }
            ts = _fmt_ts(item.get("updated_at"))
            suffix = f" | {ts}" if ts else ""
            lines.append(f"{i}. {_clip(item.get('name'), 62)} | {_clip(_summary(item.get('action') or {}), 62)}{suffix}")
    else:
        lines.append("")
        lines.append("Пресетов пока нет.")

    if note:
        lines.append("")
        lines.append(note)

    st["map"] = mapping

    rows_data: List[List[str]] = _rows(labels, 4)
    nav: List[str] = []
    if page_safe > 0:
        nav.append(BTN_PREV)
    if page_safe < total_pages - 1:
        nav.append(BTN_NEXT)
    if nav:
        rows_data.append(nav)

    rows_data.extend(
        [
            [f"{BTN_DEL_MODE}: {'ВКЛ' if del_mode else 'ВЫКЛ'}"],
            [BTN_CLEAR_PRESETS],
            [BTN_MAIN],
            [BTN_GUIDE],
            [BTN_BACK],
        ]
    )

    await message.answer("\n".join(lines), reply_markup=_kb(rows_data))

async def _render_history(message: types.Message, uid: int, page: int | None = None, note: str = "") -> None:
    st = _u(uid)
    store, err = _load_store()
    items = _history(store) if not err else []

    page_val = st.get("page", 0) if page is None else page
    page_items, page_safe, total_pages = _paginate(items, int(page_val), PAGE_HISTORY)
    _set_screen(uid, "history", page_safe)

    mapping: Dict[str, Dict[str, Any]] = {}
    labels: List[str] = []
    lines = [
        "Общая история WinKeys:",
        f"Страница: {page_safe + 1}/{total_pages}",
        f"Записей: {len(items)}",
    ]
    if err:
        lines.append(f"Ошибка хранилища: {err}")

    if page_items:
        lines.append("")
        for i, item in enumerate(page_items, start=1):
            label = _button_label(f"{i}.", item.get("summary"), max_len=24)
            labels.append(label)
            mapping[label] = {
                "kind": "history",
                "name": str(item.get("name") or "Команда"),
                "action": copy.deepcopy(item.get("action") or {}),
            }
            status = "OK" if item.get("ok") else "ERR"
            ts = _fmt_ts(item.get("ts"))
            suffix = f" | {ts}" if ts else ""
            lines.append(f"{i}. [{status}] {_clip(item.get('name'), 56)} | {_clip(item.get('summary'), 56)}{suffix}")
    else:
        lines.append("")
        lines.append("История пуста.")

    if note:
        lines.append("")
        lines.append(note)

    st["map"] = mapping

    rows_data: List[List[str]] = _rows(labels, 4)
    nav: List[str] = []
    if page_safe > 0:
        nav.append(BTN_PREV)
    if page_safe < total_pages - 1:
        nav.append(BTN_NEXT)
    if nav:
        rows_data.append(nav)

    rows_data.extend([[BTN_CLEAR_HISTORY], [BTN_MAIN], [BTN_GUIDE], [BTN_BACK]])

    await message.answer("\n".join(lines), reply_markup=_kb(rows_data))


async def _render_confirm(message: types.Message, uid: int, action: str, return_screen: str) -> None:
    st = _u(uid)
    _set_screen(uid, "confirm", 0)
    st["confirm_action"] = action
    st["confirm_return"] = return_screen
    st["map"] = {}

    if action == "clear_presets":
        text = "Подтверди очистку всех общих пресетов WinKeys."
    elif action == "clear_history":
        text = "Подтверди очистку общей истории WinKeys."
    else:
        text = "Подтверди действие."

    await message.answer(text, reply_markup=_kb([[BTN_CONFIRM, BTN_CANCEL], [BTN_BACK]]))


async def _rerender(message: types.Message, uid: int, note: str = "") -> None:
    st = _u(uid)
    screen = str(st.get("screen") or "main")
    if screen == "categories":
        await _render_categories(message, uid, note)
    elif screen == "commands":
        await _render_commands(message, uid, note=note)
    elif screen == "command_preview":
        await _render_command_preview(message, uid, note=note)
    elif screen == "presets":
        await _render_presets(message, uid, note=note)
    elif screen == "history":
        await _render_history(message, uid, note=note)
    else:
        await _render_main(message, uid, note)


def register_handlers(dp: Dispatcher):
    register_utility(
        key="winkeys_reply",
        title=BTN_OPEN,
        trigger_text=BTN_OPEN,
        group="utilities",
        order=21,
        description="Клавиатурные команды WinKeys с общими пресетами и историей",
    )

    @dp.message_handler(lambda m: _auth(m.from_user.id) and m.text == BTN_OPEN)
    async def winkeys_entry(message: types.Message):
        uid = message.from_user.id
        st = _u(uid)
        st["mode"] = True
        st["delete_mode"] = False
        st["category"] = ""
        _log(f"Пользователь {uid} открыл модуль '{BTN_OPEN}'.")
        await _render_main(message, uid)

    @dp.message_handler(
        lambda m: _auth(m.from_user.id)
        and _u(m.from_user.id).get("mode", False)
        and m.text in {BTN_BACK, "Назад"}
    )
    async def winkeys_back(message: types.Message):
        uid = message.from_user.id
        st = _u(uid)
        st["mode"] = False
        st["map"] = {}
        st["confirm_action"] = ""
        st["confirm_return"] = "main"
        _log(f"Пользователь {uid} вышел из модуля '{BTN_OPEN}'.")
        await message.answer("Возврат в раздел утилит.", reply_markup=get_utilities_keyboard())

    @dp.message_handler(lambda m: _auth(m.from_user.id) and _u(m.from_user.id).get("mode", False))
    async def winkeys_router(message: types.Message):
        uid = message.from_user.id
        st = _u(uid)
        text = str(message.text or "").strip()
        screen = str(st.get("screen") or "main")
        mapping = st.get("map") if isinstance(st.get("map"), dict) else {}

        if text == BTN_MAIN:
            await _render_main(message, uid)
            return
        if text == BTN_CATS:
            await _render_categories(message, uid)
            return
        if text == BTN_PRESETS:
            st["delete_mode"] = False
            await _render_presets(message, uid, page=0)
            return
        if text == BTN_HISTORY:
            await _render_history(message, uid, page=0)
            return
        if text == BTN_TO_CATS:
            await _render_categories(message, uid)
            return
        if text == BTN_HELP:
            await _render_help(message, uid)
            return
        if text == BTN_GUIDE:
            await _send_full_guide(message, uid)
            return
        if text == BTN_BACK_TO_LIST:
            await _render_commands(message, uid)
            return

        if text == BTN_RUN_SELECTED:
            if screen != "command_preview":
                await _rerender(message, uid, "Сначала открой карточку команды.")
                return
            action = st.get("selected_action") if isinstance(st.get("selected_action"), dict) else {}
            name = str(st.get("selected_name") or "Команда")
            blocked = str(st.get("selected_blocked") or "").strip()
            if blocked:
                await _render_command_preview(message, uid, note=f"[ERR] {name}: {blocked}")
                return
            ok, res = _execute(uid, action, "builtin_preview", name)
            _log(f"Пользователь {uid} WinKeys preview run: {name!r}, успех={ok}")
            await _render_command_preview(message, uid, note=f"[{'OK' if ok else 'ERR'}] {name}: {res}")
            return

        if text == BTN_SAVE_SELECTED:
            if screen != "command_preview":
                await _rerender(message, uid, "Сначала открой карточку команды.")
                return
            action = st.get("selected_action") if isinstance(st.get("selected_action"), dict) else {}
            name = str(st.get("selected_name") or "Команда")
            if not action:
                await _render_command_preview(message, uid, note="Не удалось определить команду для сохранения.")
                return
            ok, res = _save_preset(action, f"TG: {name}")
            _log(f"Пользователь {uid} сохранение выбранной команды: успех={ok}, msg={res!r}")
            await _render_command_preview(message, uid, note=f"[{'OK' if ok else 'ERR'}] {res}")
            return

        if text in {BTN_PREV, BTN_NEXT}:
            delta = -1 if text == BTN_PREV else 1
            page = max(0, int(st.get("page", 0)) + delta)
            if screen == "commands":
                await _render_commands(message, uid, page=page)
            elif screen == "presets":
                await _render_presets(message, uid, page=page)
            elif screen == "history":
                await _render_history(message, uid, page=page)
            else:
                await _render_main(message, uid, note="Постраничная навигация доступна только в списках.")
            return

        if text == BTN_REPEAT:
            last_action = st.get("last_action") if isinstance(st.get("last_action"), dict) else {}
            if not last_action:
                await _render_main(message, uid, note="Последняя команда отсутствует.")
                return
            name = str(st.get("last_name") or "Последняя команда")
            ok, res = _execute(uid, last_action, "repeat", name)
            _log(f"Пользователь {uid} повтор команды WinKeys: {name!r}, успех={ok}")
            await _render_main(message, uid, note=f"[{'OK' if ok else 'ERR'}] {name}: {res}")
            return

        if text == BTN_SAVE_LAST:
            last_action = st.get("last_action") if isinstance(st.get("last_action"), dict) else {}
            if not last_action:
                await _rerender(message, uid, "Нет последней команды для сохранения.")
                return
            base_name = f"TG: {st.get('last_name') or 'Команда'}"
            ok, res = _save_preset(last_action, base_name)
            _log(f"Пользователь {uid} сохранение пресета WinKeys: успех={ok}, msg={res!r}")
            await _rerender(message, uid, f"[{'OK' if ok else 'ERR'}] {res}")
            return

        if text.startswith(BTN_DEL_MODE):
            if screen != "presets":
                await _render_main(message, uid, note="Режим удаления доступен только в разделе пресетов.")
                return
            st["delete_mode"] = not bool(st.get("delete_mode", False))
            await _render_presets(message, uid, note=f"Режим удаления {'включён' if st['delete_mode'] else 'выключен'}.")
            return

        if text == BTN_CLEAR_PRESETS:
            await _render_confirm(message, uid, "clear_presets", "presets")
            return
        if text == BTN_CLEAR_HISTORY:
            await _render_confirm(message, uid, "clear_history", "history")
            return

        if text in {BTN_CONFIRM, BTN_CANCEL}:
            if screen != "confirm":
                await _rerender(message, uid, "Подтверждение сейчас не требуется.")
                return
            action = str(st.get("confirm_action") or "")
            ret = str(st.get("confirm_return") or "main")
            st["confirm_action"] = ""
            st["confirm_return"] = "main"

            if text == BTN_CANCEL:
                if ret == "presets":
                    await _render_presets(message, uid, note="Действие отменено.")
                elif ret == "history":
                    await _render_history(message, uid, note="Действие отменено.")
                else:
                    await _render_main(message, uid, note="Действие отменено.")
                return

            if action == "clear_presets":
                ok, res = _clear_presets()
                st["delete_mode"] = False
                await _render_presets(message, uid, note=f"[{'OK' if ok else 'ERR'}] {res}")
                return
            if action == "clear_history":
                ok, res = _clear_history()
                await _render_history(message, uid, note=f"[{'OK' if ok else 'ERR'}] {res}")
                return

            await _render_main(message, uid, note="Неизвестное действие подтверждения.")
            return

        if screen == "categories" and text in mapping:
            payload = mapping[text]
            st["category"] = str(payload.get("category") or "")
            await _render_commands(message, uid, page=0)
            return

        if screen == "commands" and text in mapping:
            payload = mapping[text]
            name = str(payload.get("name") or "Команда")
            combo = str(payload.get("combo") or "")
            category = str(payload.get("category") or st.get("category") or "Прочее")
            blocked = str(payload.get("blocked") or "").strip()
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            st["selected_action"] = copy.deepcopy(action)
            st["selected_name"] = name
            st["selected_combo"] = combo
            st["selected_category"] = category
            st["selected_blocked"] = blocked
            await _render_command_preview(message, uid)
            return

        if screen == "presets" and text in mapping:
            payload = mapping[text]
            name = str(payload.get("name") or "Пресет")
            if st.get("delete_mode", False):
                ok, res = _delete_preset(str(payload.get("id") or ""))
                _log(f"Пользователь {uid} удаление пресета WinKeys: успех={ok}, msg={res!r}")
                await _render_presets(message, uid, note=f"[{'OK' if ok else 'ERR'}] {res}")
                return
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            ok, res = _execute(uid, action, "custom", name)
            _log(f"Пользователь {uid} WinKeys preset: {name!r}, успех={ok}")
            await _render_presets(message, uid, note=f"[{'OK' if ok else 'ERR'}] {name}: {res}")
            return

        if screen == "history" and text in mapping:
            payload = mapping[text]
            name = str(payload.get("name") or "Команда")
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            ok, res = _execute(uid, action, "history", name)
            _log(f"Пользователь {uid} WinKeys history replay: {name!r}, успех={ok}")
            await _render_history(message, uid, note=f"[{'OK' if ok else 'ERR'}] {name}: {res}")
            return

        await _rerender(message, uid, "Команда не распознана. Используй кнопки текущего раздела.")
