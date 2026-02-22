import os
import sys
import importlib
from typing import Optional, Any, Dict

# aiogram может быть не нужен в момент раннего импорта (когда бота ещё нет),
# но Dispatcher используется в сигнатурах.
try:
    from aiogram import Dispatcher
except Exception:  # pragma: no cover
    Dispatcher = object  # type: ignore

"""manager_web_dashboard.py

Упрощённый менеджер модулей (папка: moduls).

✅ Теперь делает ТОЛЬКО одно:
- импортирует **startrunmodulwebpanel.py** сразу при импорте этого менеджера.

❌ Больше он НЕ импортирует никакие другие модули:
- нет автопоиска startrun*/nostartrun*
- нет импортов после авторизации
- нет потоков
- нет debug-репортов

При наличии dp (когда бот уже поднялся) можно вызвать register_handlers(dp),
и тогда, если в startrunmodulwebpanel есть функция register_handlers(dp),
она будет вызвана.

Совместимо с:
- запуском из исходников
- запуском из Nuitka onefile (при условии, что moduls включена в сборку)
"""

# ---------------------------------------------------------------------------
# НАСТРОЙКА: какой модуль импортируем сразу
# ---------------------------------------------------------------------------

_PREIMPORT_SHORT_NAME = "startrunmodulwebpanel"
_PREIMPORT_CANDIDATES = (
    f"moduls.{_PREIMPORT_SHORT_NAME}",
    _PREIMPORT_SHORT_NAME,
)

# Статус раннего импорта
_preimport_done: bool = False
_preimport_success: bool = False
_preimport_used_name: str = _PREIMPORT_CANDIDATES[-1]
_preimport_error: Optional[BaseException] = None


def _ensure_own_dir_in_sys_path() -> str:
    """Добавляем пути так, чтобы importlib мог найти moduls.<name> и <name> рядом."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        here = os.getcwd()

    parent = os.path.dirname(here) or here

    # Родитель нужен для импорта вида: moduls.xxx
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)

    # Папка moduls нужна для импорта вида: xxx
    if here and here not in sys.path:
        sys.path.insert(0, here)

    return here


def _get_loaded_webpanel_module() -> Optional[Any]:
    """Вернёт уже загруженный модуль webpanel из sys.modules, если он там есть."""
    for name in _PREIMPORT_CANDIDATES:
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
    return None


def _import_webpanel(force_retry: bool = False) -> Optional[Any]:
    """Импортирует startrunmodulwebpanel.

    force_retry=True полезен, если ранний импорт провалился и нужно попробовать снова,
    но всё равно импортируется только этот модуль.
    """
    global _preimport_done, _preimport_success, _preimport_used_name, _preimport_error

    if _preimport_done and not force_retry:
        return _get_loaded_webpanel_module()

    _ensure_own_dir_in_sys_path()

    # Если просили retry после неудачи: очищаем возможные «полумодули» из sys.modules
    if force_retry and not _preimport_success:
        for name in _PREIMPORT_CANDIDATES:
            try:
                sys.modules.pop(name, None)
            except Exception:
                pass

    _preimport_done = True
    last_exc: Optional[BaseException] = None

    for module_name in _PREIMPORT_CANDIDATES:
        try:
            mod = importlib.import_module(module_name)
            _preimport_success = True
            _preimport_used_name = module_name
            _preimport_error = None
            return mod
        except BaseException as e:
            last_exc = e

    _preimport_success = False
    _preimport_used_name = _PREIMPORT_CANDIDATES[-1]
    _preimport_error = last_exc
    return None


# ✅ Ранний импорт выполняется сразу при импорте менеджера
try:
    _import_webpanel(force_retry=False)
except Exception:
    # Менеджер не должен падать вообще никогда
    pass


def get_preimport_status() -> Dict[str, Any]:
    """Можно дернуть где угодно (даже без бота), чтобы узнать статус."""
    return {
        "done": _preimport_done,
        "success": _preimport_success,
        "used_name": _preimport_used_name,
        "error": f"{type(_preimport_error).__name__}: {_preimport_error}" if _preimport_error else None,
    }


def register_handlers(dp: Dispatcher) -> None:
    """Вызывается из основного кода бота.

    Ничего, кроме startrunmodulwebpanel, не импортирует.
    Если модуль уже загружен, просто вызывает его register_handlers(dp), если она есть.
    """

    # Берём уже загруженный модуль, либо делаем 1 ретрай (только для него)
    mod = _get_loaded_webpanel_module()
    if mod is None:
        mod = _import_webpanel(force_retry=not _preimport_success)

    if mod is None:
        return

    try:
        fn = getattr(mod, "register_handlers", None)
        if callable(fn):
            fn(dp)
    except Exception:
        # Не даём менеджеру падать, даже если webpanel внутри накосячит
        pass
