# -*- coding: utf-8 -*-
"""
startrunmodul_botmenu.py

✅ Модуль для "Меню бота" (кнопка Menu в Telegram).
Ставит команды в Bot Menu и (только для одной команды) показывает главное меню.

Команды в Bot Menu (порядок важен, первая будет сверху):
- /menu       — Главное меню (показывает Reply-клавиатуру главного меню из keymenu.py)
- /screenshot — Скриншот
- /volume     — Громкость
- /mainmenu   — Настройка главного меню

Важно:
- Скриншот/Громкость/Настройка главного меню должны обрабатываться их модулями
  (мы их НЕ перехватываем, чтобы не ломать логику).
- ЭТОТ модуль регистрирует ТОЛЬКО обработчик /menu (главное меню).
- /menu дополнительно "сбрасывает режимы" активных модулей (best-effort),
  как будто нажали "Отмена" внутри модуля.
- aiogram 2.x
"""

from __future__ import annotations

import asyncio
import sys
from typing import List, Tuple, Optional, Any, Dict

from aiogram import types
from aiogram.dispatcher import Dispatcher, FSMContext

try:
    from __main__ import write_bot_log  # type: ignore
except Exception:
    def write_bot_log(*args, **kwargs):  # type: ignore
        return


# Подтягиваем клавиатуру главного меню из keymenu.py
try:
    from keymenu import get_main_keyboard  # type: ignore
except Exception:
    get_main_keyboard = None  # type: ignore



# --- Динамический пункт в меню «Настройки» (аналог utilities_registry) ---
# Этот модуль добавляет ОДНУ кнопку в раздел «Настройки»:
# «Настройка главного меню».
# Важно: мы только регистрируем пункт. Обработчик должен быть в модуле,
# который уже отвечает за «Настройку главного меню» (чтобы ничего не ломать).
try:
    from settings_registry import register_setting  # type: ignore
except Exception:
    register_setting = None  # type: ignore

_settings_registered = False


def _register_settings_entry_once() -> None:
    global _settings_registered
    if _settings_registered:
        return
    _settings_registered = True

    if not register_setting:
        return

    try:
        register_setting(
            key="botmenu_mainmenu_cfg",
            title="Настройка главного меню",
            trigger_text="Настройка главного меню",
            group="settings",
            order=5,
            description="Настройка отображения кнопок главного меню",
        )
    except Exception as e:
        write_bot_log(f"❌ Не удалось зарегистрировать пункт настроек Bot Menu: {e}")


# Порядок важен: первое будет "выше" в Bot Menu
BOT_MENU: List[Tuple[str, str]] = [
    ("menu", "Главное меню"),
    ("screenshot", "Скриншот"),
    ("volume", "Громкость"),
    ("mainmenu", "Настройка главного меню"),
]

_SCOPES = [
    types.BotCommandScopeDefault(),
    types.BotCommandScopeAllPrivateChats(),
    types.BotCommandScopeAllGroupChats(),
]

_LANGS: List[Optional[str]] = [None, "ru"]

_started = False


def _build_commands() -> List[types.BotCommand]:
    return [types.BotCommand(command=c, description=d) for c, d in BOT_MENU]


async def _apply_menu(bot: types.Bot) -> None:
    # 1) очистим старые команды (чтобы не оставалось лишнего в меню)
    for scope in _SCOPES:
        for lang in _LANGS:
            try:
                await bot.delete_my_commands(scope=scope, language_code=lang)
            except Exception:
                pass

    # 2) поставим новые
    cmds = _build_commands()
    for scope in _SCOPES:
        for lang in _LANGS:
            try:
                await bot.set_my_commands(cmds, scope=scope, language_code=lang)
            except Exception as e:
                write_bot_log(f"❌ set_my_commands не сработал (scope={type(scope).__name__}, lang={lang}): {e}")

    # 3) попросим Telegram показывать именно Commands в кнопке Menu
    try:
        await bot.set_chat_menu_button(menu_button=types.MenuButtonCommands())
    except Exception:
        pass

    write_bot_log("✅ Bot Menu обновлено: " + ", ".join([f"/{c}={d}" for c, d in BOT_MENU]))


def _should_scan_module(mod_name: str) -> bool:
    name = (mod_name or "").lower()
    # Сканируем только "наши" модули, чтобы случайно не трогать чужие библиотеки.
    return any(token in name for token in ("moduls", "modul", "plugin", "plugins", "startrun", "nostartrun"))


def _should_scan_attr(attr_name: str) -> bool:
    n = (attr_name or "").lower()

    # Частые имена состояний в проекте
    if n in {"voice_mode", "tts_state", "video_state", "snapshot_state", "playback_state"}:
        return True

    # Общие паттерны
    if n.endswith("_mode") or n.endswith("_modes"):
        # исключаем model / models
        if n.endswith("_model") or n.endswith("_models"):
            return False
        return True

    if n.endswith("_state") or n.endswith("_states"):
        return True

    if n.endswith("_waiting") or "waiting" in n:
        return True

    # Иногда встречается is_*_mode / mode_enabled и т.п. (но не трогаем всё подряд)
    if n.startswith("is_") and n.endswith("_mode"):
        return True

    return False


def _try_set_cancel_flags(state_dict: Dict[str, Any]) -> None:
    """Пробуем выставить типичные флаги отмены, если это похоже на активную работу."""
    try:
        # Для video/recording/timelife и похожих циклов
        if "timelife" in state_dict:
            state_dict["timelife"] = False
        if "stop" in state_dict:
            state_dict["stop"] = True
        if "cancelled" in state_dict:
            state_dict["cancelled"] = True
        if "state" in state_dict and isinstance(state_dict.get("state"), str):
            # Выведем из под-стейта, если это FSM-подобное
            state_dict["state"] = None
    except Exception:
        pass


def _reset_module_states(chat_id: Optional[int], user_id: Optional[int]) -> List[str]:
    """
    Best-effort сброс активных "режимов модулей" для пользователя/чата.

    Делает максимально аккуратно:
    - проходит только по загруженным "нашим" модулям
    - трогает только атрибуты-подозреваемые на состояния (VOICE_MODE/TTS_STATE/xxx_mode/xxx_state/xxx_waiting)
    - для set: удаляет chat_id/user_id
    - для dict:
        - bool True -> False
        - dict-стейт -> выставляет cancel flags (если возможно) и удаляет запись
        - прочие значения -> удаляет запись по ключу только если значение похоже на активное (truthy)
    """
    reset_items: List[str] = []

    keys_to_try: List[int] = []
    if isinstance(chat_id, int):
        keys_to_try.append(chat_id)
    if isinstance(user_id, int) and user_id not in keys_to_try:
        keys_to_try.append(user_id)

    if not keys_to_try:
        return reset_items

    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not _should_scan_module(mod_name):
            continue

        try:
            attrs = dir(mod)
        except Exception:
            continue

        for attr in attrs:
            if not _should_scan_attr(attr):
                continue

            try:
                obj = getattr(mod, attr)
            except Exception:
                continue

            # set: удаляем chat_id/user_id
            if isinstance(obj, set):
                changed = False
                for k in keys_to_try:
                    if k in obj:
                        try:
                            obj.remove(k)
                            changed = True
                        except Exception:
                            pass
                if changed:
                    reset_items.append(f"{mod_name}.{attr}")
                continue

            # dict: сброс по ключу
            if isinstance(obj, dict):
                changed_any = False
                for k in keys_to_try:
                    if k not in obj:
                        continue
                    try:
                        val = obj.get(k)
                        # bool-флаг
                        if isinstance(val, bool):
                            if val is True:
                                obj[k] = False
                                changed_any = True
                        # dict-стейт
                        elif isinstance(val, dict):
                            _try_set_cancel_flags(val)
                            obj.pop(k, None)
                            changed_any = True
                        else:
                            # Прочие типы:
                            # сбрасываем только если значение похоже на "активное" (truthy),
                            # чтобы случайно не вычищать пользовательские настройки/кэш.
                            try:
                                if val is None:
                                    continue
                                if val is False:
                                    continue
                                if isinstance(val, (int, float)) and val == 0:
                                    continue
                                if isinstance(val, str) and not val.strip():
                                    continue
                            except Exception:
                                pass
                            obj.pop(k, None)
                            changed_any = True
                    except Exception:
                        continue
                if changed_any:
                    reset_items.append(f"{mod_name}.{attr}")
                continue

    return reset_items


def _reset_modulsound(chat_id: Optional[int]) -> List[str]:
    """
    Точный сброс режимов modulsound.py (как "Отмена"), если модуль загружен.
    """
    if not isinstance(chat_id, int):
        return []

    items: List[str] = []
    try:
        ms = sys.modules.get("modulsound")
        if ms is None:
            # может быть как moduls.modulsound в зависимости от импорта
            ms = sys.modules.get("moduls.modulsound")
        if ms is None:
            return items
    except Exception:
        return items

    # PLAYBACK_STATE -> остановить воспроизведение
    try:
        ps = getattr(ms, "PLAYBACK_STATE", None)
        if isinstance(ps, dict) and chat_id in ps:
            stopper = getattr(ms, "_stop_playback", None)
            if callable(stopper):
                try:
                    stopper(chat_id, silent=True)  # type: ignore
                except TypeError:
                    stopper(chat_id)  # type: ignore
            else:
                ps.pop(chat_id, None)
            items.append("modulsound: воспроизведение")
    except Exception:
        pass

    # VOICE_MODE set
    try:
        vm = getattr(ms, "VOICE_MODE", None)
        if isinstance(vm, set) and chat_id in vm:
            try:
                vm.discard(chat_id)
            except Exception:
                try:
                    vm.remove(chat_id)
                except Exception:
                    pass
            items.append("modulsound: отправка голоса")
    except Exception:
        pass

    # TTS_STATE dict
    try:
        ts = getattr(ms, "TTS_STATE", None)
        if isinstance(ts, dict) and chat_id in ts:
            ts.pop(chat_id, None)
            items.append("modulsound: синтез речи")
    except Exception:
        pass

    # SNAPSHOT_STATE dict
    try:
        ss = getattr(ms, "SNAPSHOT_STATE", None)
        if isinstance(ss, dict) and chat_id in ss:
            ss.pop(chat_id, None)
            items.append("modulsound: снимок с камеры")
    except Exception:
        pass

    # VIDEO_STATE dict (нужно корректно остановить циклы)
    try:
        vs = getattr(ms, "VIDEO_STATE", None)
        if isinstance(vs, dict) and chat_id in vs:
            state = vs.get(chat_id)
            if isinstance(state, dict):
                _try_set_cancel_flags(state)
                # на всякий случай, если не было ключей:
                state["timelife"] = False
                state["stop"] = True
                state["cancelled"] = True
            vs.pop(chat_id, None)
            items.append("modulsound: видео с камеры/экрана")
    except Exception:
        pass

    return items


def _reset_moduldptools_modes(user_id: Optional[int]) -> List[str]:
    """
    Точный сброс режимов из moduldptools.py (если они реально активны).

    В moduldptools режимы хранятся в dict'ах, которые создаются снаружи и передаются в
    register_dptools_handlers(...). Чаще всего это __main__, но в некоторых сборках
    хранилища могут жить в другом модуле.

    Поэтому:
    - сначала смотрим в __main__
    - затем (очень осторожно) ищем по загруженным модулям dict'ы, где уже есть ключ user_id

    Сбрасываем (по user_id):
    - note_mode + pending_note
    - file_mode
    - infiles_mode
    - power_mode + pending_power_action

    Возвращает человекочитаемые строки, которые покажем пользователю.
    """
    if not isinstance(user_id, int):
        return []

    KNOWN = (
        "file_mode",
        "infiles_mode",
        "note_mode",
        "pending_note",
        "power_mode",
        "pending_power_action",
        "note_read_mode",
        "note_view_state",
        "note_button_map",
        "note_menu_active",
    )

    containers: List[Any] = []
    main_mod = sys.modules.get("__main__")
    if main_mod is not None:
        containers.append(main_mod)

    # Фолбэк: ищем модуль, где явно есть user_id в нужных dict'ах
    for mod in list(sys.modules.values()):
        if mod is None or mod is main_mod:
            continue
        try:
            found = False
            for name in KNOWN:
                obj = getattr(mod, name, None)
                if isinstance(obj, dict) and user_id in obj:
                    found = True
                    break
            if found:
                containers.append(mod)
        except Exception:
            continue

    if not containers:
        return []

    def _get_dict(name: str) -> Optional[Dict[Any, Any]]:
        for c in containers:
            try:
                obj = getattr(c, name, None)
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    closed: List[str] = []

    file_mode = _get_dict("file_mode")
    if isinstance(file_mode, dict) and file_mode.get(user_id) is True:
        file_mode[user_id] = False
        closed.append("Режим отправки файлов завершён.")

    infiles_mode = _get_dict("infiles_mode")
    if isinstance(infiles_mode, dict) and infiles_mode.get(user_id) is True:
        infiles_mode[user_id] = False
        closed.append("Режим приёма файлов завершён.")

    note_mode = _get_dict("note_mode")
    if isinstance(note_mode, dict) and note_mode.get(user_id) is True:
        note_mode[user_id] = False
        closed.append("Режим заметок отменён.")

    pending_note = _get_dict("pending_note")
    if isinstance(pending_note, dict) and user_id in pending_note:
        pending_note.pop(user_id, None)

    power_mode = _get_dict("power_mode")
    if isinstance(power_mode, dict) and power_mode.get(user_id) is True:
        power_mode[user_id] = False
        closed.append("Меню «Питание» закрыто.")

    pending_power_action = _get_dict("pending_power_action")
    if isinstance(pending_power_action, dict) and user_id in pending_power_action:
        pending_power_action.pop(user_id, None)

    # Если эти словари вынесены наружу (редко, но бывает) — подчистим тоже
    for extra in ("note_read_mode", "note_view_state", "note_button_map", "note_menu_active"):
        d = _get_dict(extra)
        if isinstance(d, dict) and user_id in d:
            try:
                d.pop(user_id, None)
            except Exception:
                pass

    return closed



def _format_closed_msgs(msgs: List[str]) -> str:
    if not msgs:
        return ""
    return "\n".join([f"• {m}" for m in msgs])

def _format_reset_items(items: List[str], limit: int = 7) -> str:
    """Коротко форматирует список сброшенных режимов."""
    if not items:
        return ""

    def _short(x: str) -> str:
        # "moduls.nostartrunmodulwinrun.winrun_mode" -> "nostartrunmodulwinrun: winrun_mode"
        parts = x.split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}: {parts[-1]}"
        return x

    shown = items[:limit]
    tail = len(items) - len(shown)

    lines = "\n".join([f"• {_short(s)}" for s in shown])
    if tail > 0:
        lines += f"\n• …и ещё {tail}"
    return lines



async def _cmd_show_main_menu(message: types.Message, state: FSMContext = None) -> None:
    """
    /menu -> показывает главное меню (ReplyKeyboardMarkup) из keymenu.get_main_keyboard()

    Дополнительно:
    - сбрасывает aiogram FSM (если какой-то модуль использовал state)
    - закрывает активные режимы moduldptools (файлы/заметки/питание), если они включены
    - делает best-effort сброс активных режимов других модулей
    """
    # 1) Сброс FSM (если использовался)
    try:
        if state is not None:
            await state.finish()
    except Exception:
        pass

    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)

    closed_msgs: List[str] = []
    reset_verbose: List[str] = []

    # 2) Точный сброс режимов moduldptools.py (файлы/заметки/питание) — только если активны
    try:
        closed_msgs.extend(_reset_moduldptools_modes(user_id))
    except Exception as e:
        write_bot_log(f"❌ Ошибка при сбросе режимов moduldptools: {e}")

    # 3) Точный сброс modulsound.py (как "Отмена"), если модуль загружен
    try:
        sound_items = _reset_modulsound(chat_id)
        reset_verbose.extend(sound_items)

        for s in sound_items:
            t = (s or "").lower()
            if "воспроизведение" in t:
                closed_msgs.append("Воспроизведение остановлено.")
            elif "отправка голоса" in t:
                closed_msgs.append("Режим отправки голоса завершён.")
            elif "синтез речи" in t:
                closed_msgs.append("Режим синтеза речи завершён.")
            elif "снимок" in t:
                closed_msgs.append("Режим снимка с камеры завершён.")
            elif "видео" in t:
                closed_msgs.append("Режим видео завершён.")
    except Exception as e:
        write_bot_log(f"❌ Ошибка при сбросе modulsound: {e}")

    # 4) Общий best-effort сброс остальных модулей (без фанатизма)
    try:
        reset_verbose.extend(_reset_module_states(chat_id, user_id))
    except Exception as e:
        write_bot_log(f"❌ Ошибка при общем сбросе режимов модулей: {e}")

    # Логируем (в лог), но не спамим пользователю техническими деталями
    try:
        if reset_verbose:
            write_bot_log("🧹 /menu: сброшены режимы: " + ", ".join(reset_verbose))
    except Exception:
        pass

    # Уберём дубли сообщений
    if closed_msgs:
        seen = set()
        uniq: List[str] = []
        for m in closed_msgs:
            if m and m not in seen:
                uniq.append(m)
                seen.add(m)
        closed_msgs = uniq

    # 5) Собираем клавиатуру главного меню
    kb = None
    if get_main_keyboard is not None:
        try:
            kb = get_main_keyboard()
        except Exception as e:
            write_bot_log(f"❌ Не удалось собрать главное меню из keymenu.get_main_keyboard(): {e}")
            kb = None


    # Уберём дубли технических меток (для логов/отчёта пользователю)
    if reset_verbose:
        seen2 = set()
        uniq2: List[str] = []
        for it in reset_verbose:
            if not it:
                continue
            if it in seen2:
                continue
            uniq2.append(it)
            seen2.add(it)
        reset_verbose = uniq2

    # Для пользователя: показываем "понятные" режимы + (опционально) внутренние флаги прочих модулей
    other_reset = [x for x in (reset_verbose or []) if "modulsound" not in (x or "").lower()]

    # 6) Текст ответа
    parts: List[str] = []
    if closed_msgs:
        parts.append("✅ Сбросил активные режимы:\n" + _format_closed_msgs(closed_msgs))

    if other_reset:
        extra = _format_reset_items(other_reset, limit=9)
        if extra:
            parts.append("🔧 Дополнительно сброшены внутренние состояния модулей:\n" + extra)

    if not parts:
        parts.append("⚪ Активных режимов не было (сбрасывать нечего).")

    text = "\n\n".join(parts) + "\n\n➡️ Переход в главное меню."


    # 7) Показ главного меню
    if kb is None:
        await message.answer(text, reply_markup=types.ReplyKeyboardRemove())
    else:
        await message.answer(text, reply_markup=kb)



def register_handlers(dp: Dispatcher) -> None:
    """
    В твоей системе менеджер модулей вызывает register_handlers(dp).

    Мы:
    - ставим команды Bot Menu (в фоне, с небольшой задержкой)
    - регистрируем ТОЛЬКО /menu -> показ главного меню
    """
    global _started
    if _started:
        return
    _started = True

    # Регистрируем ОДНУ кнопку в разделе «Настройки» (динамическое меню)
    try:
        _register_settings_entry_once()
    except Exception:
        pass

    # Обработчик /menu (главное меню)
    try:
        dp.register_message_handler(_cmd_show_main_menu, commands=["menu"], state="*")
    except Exception as e:
        write_bot_log(f"❌ Не удалось зарегистрировать обработчик /menu: {e}")


    # Обработчик текстовой кнопки "Главное меню" (если она есть в Reply-клавиатурах)
    # Не мешает /menu: просто ловит ровно текст.
    try:
        dp.register_message_handler(
            _cmd_show_main_menu,
            lambda m: (getattr(m, "text", "") or "").strip().lower() == "главное меню",
            state="*",
        )
    except Exception as e:
        write_bot_log(f"❌ Не удалось зарегистрировать обработчик кнопки 'Главное меню': {e}")

    async def _delayed_apply():
        await asyncio.sleep(0.8)
        await _apply_menu(dp.bot)

    try:
        loop = getattr(dp, "loop", None) or asyncio.get_event_loop()
        loop.create_task(_delayed_apply())
    except Exception as e:
        write_bot_log(f"❌ Не удалось запланировать обновление Bot Menu: {e}")
