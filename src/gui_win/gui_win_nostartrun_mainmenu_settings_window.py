# -*- coding: utf-8 -*-
"""
GUI: Настройки отображения кнопок главного меню бота (через config.ini).

Окно повторяет логику nostartrunmodul_mainmenu_settings: показывает список
кнопок главного меню и позволяет включать/выключать их, не создавая новых
секций в конфиге. Кнопка «Вернуть по умолчанию» снимает все галочки.

⚠️ Доступность (важно именно для Windows Narrator / «Экранный диктор»):
- Вместо виртуальных элементов QListWidget используется набор РЕАЛЬНЫХ QCheckBox,
  размещённых в прокрутке. Для Narrator это обычно читается стабильнее, чем
  элементы item-view (QListWidgetItem), которые в Qt5 на Windows могут
  озвучиваться нестабильно.
- Стрелки ВВЕРХ/ВНИЗ перемещают фокус между чекбоксами.
- Space переключает чекбокс (стандартно). Enter/Return также переключают.

ЛЕНИВЫЙ ИМПОРТ:
Этот модуль безопасно импортируется без PyQt5 (важно для functions_window.py,
сканеров модулей и сборок Nuitka). PyQt5 грузится только при реальном открытии окна.
"""

from __future__ import annotations

import weakref
import os
import sys
import importlib
import inspect
import configparser
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING, List, Any, Tuple

from mainmenu_registry import get_main_items_with_visibility, set_main_item_visibility

# -------------------- lazy PyQt5 import --------------------

_PYQT_CACHE: Optional[Tuple[Any, ...]] = None
_WINDOW_CLASS: Optional[type] = None


def _get_pyqt() -> Tuple[Any, ...]:
    """
    Ленивая загрузка PyQt5.
    Возвращает кортеж с нужными классами/объектами, чтобы не раздувать globals.
    """
    global _PYQT_CACHE
    if _PYQT_CACHE is not None:
        return _PYQT_CACHE

    from PyQt5.QtCore import Qt, QTimer, QEvent  # type: ignore
    from PyQt5.QtWidgets import (  # type: ignore
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QDialogButtonBox,
        QPushButton,
        QHBoxLayout,
        QMessageBox,
        QScrollArea,
        QWidget,
        QCheckBox,
        QSizePolicy,
    )

    _PYQT_CACHE = (
        Qt,
        QTimer,
        QEvent,
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QDialogButtonBox,
        QPushButton,
        QHBoxLayout,
        QMessageBox,
        QScrollArea,
        QWidget,
        QCheckBox,
        QSizePolicy,
    )
    return _PYQT_CACHE


# -------------------- utilities -> mainmenu sync --------------------
# Этот GUI может работать как самостоятельный способ управлять главным меню.
# Поэтому здесь мы:
# 1) подтягиваем утилиты из utilities_registry и регистрируем их в mainmenu_registry;
# 2) по умолчанию делаем утилиты ВЫКЛЮЧЕННЫМИ, чтобы они не появлялись в главном меню без ручного включения;
# 3) ставим "хук" на register_utility, чтобы поздние регистрации тоже попадали в главное меню.

_UTIL_SYNC_DONE_KEYS = set()
_UTIL_HOOK_INSTALLED = False


def _try_get_utilities():
    try:
        from utilities_registry import get_utilities  # type: ignore
        return get_utilities
    except Exception:
        return None


def _find_config_ini_path() -> Optional[Path]:
    """
    Пытаемся найти config.ini так же, как это делает остальная система.

    Приоритет:
    1) Рядом с .exe (Nuitka/pyinstaller-like) через sys.executable.
    2) Путь/функция из mainmenu_registry (если есть).
    3) base_dir/__file__ из __main__.
    4) Рядом с sys.argv[0].
    5) Текущая рабочая папка.

    Возвращает Path (может не существовать, если ещё не создан).
    """
    candidates: List[Path] = []

    # 0) при запуске как .exe (Nuitka) чаще всего правильный config.ini рядом с exe
    try:
        exe = getattr(sys, "executable", None)
        if exe and str(exe).lower().endswith(".exe"):
            candidates.append((Path(str(exe)).resolve().parent / "config.ini").resolve())
    except Exception:
        pass

    # 1) если mainmenu_registry сам умеет говорить, где у него config.ini
    try:
        mm = importlib.import_module("mainmenu_registry")
        for attr in ("get_config_path", "get_config_ini_path", "get_ini_path", "CONFIG_PATH", "CONFIG_INI"):
            v = getattr(mm, attr, None)
            try:
                p = v() if callable(v) else v
                if isinstance(p, str) and p.strip():
                    candidates.append(Path(p.strip()).resolve())
                elif isinstance(p, Path):
                    candidates.append(p.resolve())
            except Exception:
                continue
    except Exception:
        pass

    # 2) __main__ подсказки
    try:
        import __main__  # type: ignore

        for attr in ("base_dir", "BASE_DIR", "base_path", "APP_DIR"):
            bd = getattr(__main__, attr, None)
            if isinstance(bd, str) and bd:
                candidates.append((Path(bd).resolve() / "config.ini").resolve())
            elif isinstance(bd, Path):
                candidates.append((bd.resolve() / "config.ini").resolve())

        mf = getattr(__main__, "__file__", None)
        if isinstance(mf, str) and mf:
            candidates.append((Path(mf).resolve().parent / "config.ini").resolve())
    except Exception:
        pass

    # 3) argv[0]
    try:
        if sys.argv and sys.argv[0]:
            candidates.append((Path(sys.argv[0]).resolve().parent / "config.ini").resolve())
    except Exception:
        pass

    # 4) cwd
    try:
        candidates.append((Path(os.getcwd()).resolve() / "config.ini").resolve())
    except Exception:
        pass

    # Ищем существующий config.ini
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue

    # Если нигде не найден, вернём самый вероятный путь (первый кандидат),
    # чтобы запись всё равно попадала "рядом с программой".
    for p in candidates:
        if p:
            return p
    return None


def _cfg_read(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str  # не ломаем регистр ключей
    try:
        if path.exists():
            cfg.read(path, encoding="utf-8")
    except Exception:
        pass
    return cfg


def _cfg_write(path: Path, cfg: configparser.ConfigParser) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            cfg.write(f)
    except Exception:
        pass


def _read_visibility_override_from_config(key: str) -> Optional[bool]:
    """
    Читает явное значение из [mainmenu_visibility], если оно есть.
    Возвращает True/False или None (если записи нет / config не найден).
    """
    cfg_path = _find_config_ini_path()
    if cfg_path is None:
        return None

    cfg = _cfg_read(cfg_path)
    sect = "mainmenu_visibility"
    try:
        if not cfg.has_section(sect):
            return None
        if not cfg.has_option(sect, key):
            return None
        raw = (cfg.get(sect, key, fallback="") or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    except Exception:
        return None
    return None


def _ensure_default_visibility_in_config(key: str, default_on: bool) -> None:
    """
    Если для key нет записи в [mainmenu_visibility], проставим дефолт.
    ВАЖНО: просто "записать 0 в ini" иногда недостаточно, потому что mainmenu_registry
    может держать кэш видимости в памяти. Поэтому, когда записи нет, мы:
      1) выставляем видимость через set_main_item_visibility (обновляет и память, и ini),
      2) если не получилось, пишем ini вручную (как запасной вариант).
    """
    # Есть ли уже явная настройка? Тогда НИЧЕГО не трогаем.
    existing = _read_visibility_override_from_config(key)
    if existing is not None:
        # На всякий случай синхронизируем кэш, если он вдруг не совпадает.
        try:
            set_main_item_visibility(key, bool(existing))
        except Exception:
            pass
        return

    # Если явной настройки нет, ставим дефолт через API реестра (это ключевой фикс).
    try:
        set_main_item_visibility(key, bool(default_on))
        return
    except Exception:
        pass

    # Фолбэк: руками в ini.
    cfg_path = _find_config_ini_path()
    if cfg_path is None:
        return
    cfg = _cfg_read(cfg_path)
    sect = "mainmenu_visibility"
    try:
        if not cfg.has_section(sect):
            cfg.add_section(sect)
        if not cfg.has_option(sect, key):
            cfg.set(sect, key, "1" if default_on else "0")
            _cfg_write(cfg_path, cfg)
    except Exception:
        pass


def _get_mainmenu_register_fn():
    """
    Подбираем функцию регистрации пункта главного меню из mainmenu_registry.
    Мы не знаем точное имя в сборке пользователя, поэтому пробуем несколько.
    """
    try:
        mmr = importlib.import_module("mainmenu_registry")
    except Exception:
        return None

    for name in (
        "register_main_item",
        "register_item",
        "register_menu_item",
        "register_mainmenu_item",
        "add_main_item",
        "add_item",
    ):
        fn = getattr(mmr, name, None)
        if callable(fn):
            return fn
    return None


def _register_mainmenu_item_safely(
    key: str,
    title: str,
    trigger_text: str,
    group: str = "main",
    order: int = 100,
    description: str = "",
    default_visible: Optional[bool] = None,
) -> bool:
    fn = _get_mainmenu_register_fn()
    if fn is None:
        return False

    try:
        mmr = importlib.import_module("mainmenu_registry")
    except Exception:
        mmr = None

    # 1) Если есть dataclass/descriptor - используем его
    desc_cls = getattr(mmr, "MainMenuItemDescriptor", None) if mmr is not None else None
    if desc_cls is not None:
        try:
            kwargs = dict(
                key=key,
                title=title,
                trigger_text=trigger_text,
                group=group,
                order=order,
                description=description,
            )
            # Поддержка разных версий: пытаемся прокинуть "дефолтную видимость", если поле есть
            if default_visible is not None:
                try:
                    sig = inspect.signature(desc_cls)
                    if "default_visible" in sig.parameters:
                        kwargs["default_visible"] = bool(default_visible)
                    elif "default_on" in sig.parameters:
                        kwargs["default_on"] = bool(default_visible)
                    elif "enabled_by_default" in sig.parameters:
                        kwargs["enabled_by_default"] = bool(default_visible)
                except Exception:
                    pass

            desc = desc_cls(**kwargs)
            fn(desc)
            return True
        except Exception:
            pass

    # 2) Пробуем kwargs (и аккуратно пытаемся передать дефолтную видимость)
    base_kwargs = dict(
        key=key,
        title=title,
        trigger_text=trigger_text,
        group=group,
        order=order,
        description=description,
    )
    if default_visible is not None:
        # Не используем "visible" как текущую видимость, чтобы не перетирать настройки пользователя.
        # Пробуем только "дефолтные" имена.
        for kname in ("default_visible", "default_on", "enabled_by_default"):
            base_kwargs[kname] = bool(default_visible)

    try:
        fn(**base_kwargs)
        return True
    except Exception:
        pass

    # 3) Пробуем более короткие сигнатуры
    for kwargs in (
        dict(key=key, title=title, trigger_text=trigger_text, group=group, order=order),
        dict(key=key, title=title, trigger_text=trigger_text, group=group),
        dict(key=key, title=title, trigger_text=trigger_text),
        dict(key=key, title=title),
    ):
        if default_visible is not None:
            for kname in ("default_visible", "default_on", "enabled_by_default"):
                kwargs[kname] = bool(default_visible)
        try:
            fn(**kwargs)
            return True
        except Exception:
            continue

    # 4) Позиционные, на всякий
    try:
        fn(key, title, trigger_text, group, order, description)
        return True
    except Exception:
        pass
    try:
        fn(key, title, trigger_text)
        return True
    except Exception:
        pass

    return False


def _sync_utilities_into_mainmenu_registry(default_on: bool = False) -> None:
    """
    Регистрирует утилиты (utilities_registry) как элементы главного меню.
    Делает это безопасно и без дублей.
    """
    global _UTIL_SYNC_DONE_KEYS

    get_utilities = _try_get_utilities()
    if get_utilities is None:
        return

    # Что уже есть в реестре главного меню
    existing_keys = set()
    existing_titles = set()
    try:
        for item_obj, _vis in get_main_items_with_visibility(group="main"):
            k = getattr(item_obj, "key", None)
            t = getattr(item_obj, "title", None)
            if k:
                existing_keys.add(str(k))
            if t:
                existing_titles.add(str(t))
    except Exception:
        pass

    # Подтягиваем утилиты
    try:
        utilities = list(get_utilities(group=None))  # type: ignore
    except Exception:
        return

    # Стабильная сортировка
    def _sort_key(u):
        return (
            str(getattr(u, "group", "")),
            int(getattr(u, "order", 100) or 100),
            str(getattr(u, "title", "")),
            str(getattr(u, "key", "")),
        )

    utilities.sort(key=_sort_key)

    for u in utilities:
        ukey = str(getattr(u, "key", "") or "").strip()
        if not ukey:
            continue

        main_key = f"util__{ukey}"

        # уже зарегистрировано
        if main_key in existing_keys:
            _UTIL_SYNC_DONE_KEYS.add(main_key)
            continue
        if main_key in _UTIL_SYNC_DONE_KEYS:
            continue

        title = str(getattr(u, "title", main_key) or main_key)
        trigger = str(getattr(u, "trigger_text", title) or title)
        desc = str(getattr(u, "description", "") or "")

        # Чтобы не было коллизий по title
        if title in existing_titles:
            title = f"{title} ({ukey})"

        ok = _register_mainmenu_item_safely(
            key=main_key,
            title=title,
            trigger_text=trigger,
            group="main",
            order=1000 + int(getattr(u, "order", 100) or 100),
            description=desc,
            default_visible=default_on,
        )
        if ok:
            existing_keys.add(main_key)
            existing_titles.add(title)
            _UTIL_SYNC_DONE_KEYS.add(main_key)

            # По умолчанию выключаем, чтобы утилиты не появлялись в главном меню без ручного включения.
            _ensure_default_visibility_in_config(main_key, default_on=default_on)


def _install_utilities_register_hook(default_on: bool = False) -> None:
    """
    Если утилиты регистрируются ПОСЛЕ того, как уже открылось/инициализировалось окно,
    мы хотим, чтобы они тоже попали в главное меню.
    """
    global _UTIL_HOOK_INSTALLED
    if _UTIL_HOOK_INSTALLED:
        return

    try:
        import utilities_registry as ur  # type: ignore
    except Exception:
        return

    orig = getattr(ur, "register_utility", None)
    if not callable(orig):
        return

    # Если уже хуканули, не трогаем
    if getattr(orig, "__name__", "") == "_register_utility_hooked":
        _UTIL_HOOK_INSTALLED = True
        return

    def _register_utility_hooked(*args, **kwargs):  # type: ignore
        res = orig(*args, **kwargs)
        try:
            _sync_utilities_into_mainmenu_registry(default_on=default_on)
        except Exception:
            pass
        return res

    try:
        _register_utility_hooked.__name__ = "_register_utility_hooked"
    except Exception:
        pass

    try:
        ur.register_utility = _register_utility_hooked  # type: ignore
        _UTIL_HOOK_INSTALLED = True
    except Exception:
        pass


# Пытаемся поставить хук и подтянуть утилиты сразу при импорте модуля.
# Это не тянет PyQt5 и нужно для "поздних" регистраций утилит.
try:
    _install_utilities_register_hook(default_on=False)
    _sync_utilities_into_mainmenu_registry(default_on=False)
except Exception:
    pass


if TYPE_CHECKING:  # pragma: no cover
    from PyQt5.QtWidgets import QWidget as _QWidget  # noqa: F401


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "Главное меню (видимость)"
FUNCTIONS_ENTRYPOINT = "open_mainmenu_settings_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 45
FUNCTIONS_ICON = "SP_FileDialogListView"
FUNCTIONS_TOOLTIP = "Включение/выключение кнопок главного меню бота"
FUNCTIONS_ACCESSIBLE_NAME = "Окно: настройки видимости главного меню"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = (
    "Переключает показ кнопок главного меню бота, используя существующие настройки config.ini."
)


def _try_log(log_func: Optional[Callable[[str], None]], text: str) -> None:
    if callable(log_func):
        try:
            log_func(text)
            return
        except Exception:
            pass
    try:
        from __main__ import write_bot_log  # type: ignore

        if callable(write_bot_log):
            write_bot_log(text)
    except Exception:
        pass


def _get_window_class():
    """
    Возвращает (и кэширует) класс окна.
    Класс создаётся только после ленивого импорта PyQt5.
    """
    global _WINDOW_CLASS
    if _WINDOW_CLASS is not None:
        return _WINDOW_CLASS

    (
        Qt,
        QTimer,
        QEvent,
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QDialogButtonBox,
        QPushButton,
        QHBoxLayout,
        QMessageBox,
        QScrollArea,
        QWidget,
        QCheckBox,
        QSizePolicy,
    ) = _get_pyqt()

    class MainMenuSettingsWindow(QDialog):
        def __init__(self, parent: Optional["QWidget"] = None, log_func: Optional[Callable[[str], None]] = None):
            super().__init__(parent)
            self.setObjectName("mainMenuSettingsWindow")
            self.setWindowTitle("Настройки главного меню бота")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setMinimumSize(620, 420)
            self.setSizeGripEnabled(True)

            if parent is not None:
                try:
                    self.setPalette(parent.palette())
                except Exception:
                    pass
                try:
                    ss = parent.styleSheet()
                    if ss:
                        self.setStyleSheet(ss)
                except Exception:
                    pass

            self._log_func = log_func
            self._updating = False

            # Текущий набор чекбоксов (для навигации стрелками / массовых операций)
            self._checkboxes: List[QCheckBox] = []

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(10)

            header = QLabel("Настройки видимости кнопок главного меню")
            header.setObjectName("mainMenuSettingsHeader")
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            header.setAccessibleName("Заголовок окна настроек главного меню бота")
            layout.addWidget(header)

            info = QLabel(
                "Стрелками вверх/вниз выберите кнопку. "
                "Space или Enter переключают галочку. "
                "Изменения сразу записываются в config.ini (секция [mainmenu_visibility]). "
                "Кнопка «Вернуть по умолчанию» снимает все галочки."
            )
            info.setWordWrap(True)
            info.setAccessibleName("Подсказка по управлению видимостью главного меню")
            layout.addWidget(info)

            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFrameShadow(QFrame.Sunken)
            divider.setAccessibleName("Разделитель")
            layout.addWidget(divider)

            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setAccessibleName("Статус изменения настроек")
            layout.addWidget(self.status)

            # --- Список чекбоксов в прокрутке (реальные контролы, лучше для Narrator) ---
            self.scroll_area = QScrollArea(self)
            self.scroll_area.setObjectName("mainMenuSettingsScroll")
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setFrameShape(QFrame.StyledPanel)
            self.scroll_area.setAccessibleName("Список кнопок главного меню с флажками видимости")
            self.scroll_area.setFocusPolicy(Qt.StrongFocus)

            self.scroll_container = QWidget(self.scroll_area)
            self.scroll_container.setObjectName("mainMenuSettingsScrollContainer")
            self.scroll_container.setAccessibleName("Контейнер списка кнопок главного меню")
            self.scroll_layout = QVBoxLayout(self.scroll_container)
            self.scroll_layout.setContentsMargins(10, 10, 10, 10)
            self.scroll_layout.setSpacing(6)

            # «растяжка» внизу, чтобы элементы не прилипали и проще читались
            self.scroll_layout.addStretch(1)

            self.scroll_area.setWidget(self.scroll_container)
            layout.addWidget(self.scroll_area)

            btns = QHBoxLayout()
            btns.setSpacing(8)
            layout.addLayout(btns)

            self.refresh_btn = QPushButton("Обновить")
            self.refresh_btn.setAccessibleName("Обновить список кнопок главного меню")
            self.refresh_btn.setToolTip("Перечитать список кнопок из реестра меню")
            self.refresh_btn.setShortcut("Alt+O")
            self.refresh_btn.clicked.connect(self._reload)
            btns.addWidget(self.refresh_btn)

            self.reset_btn = QPushButton("Вернуть по умолчанию")
            self.reset_btn.setAccessibleName("Снять все галочки видимости")
            self.reset_btn.setToolTip("Снять все галочки и скрыть все кнопки главного меню")
            self.reset_btn.setShortcut("Alt+V")
            self.reset_btn.clicked.connect(self._reset_all)
            btns.addWidget(self.reset_btn)

            btns.addStretch(1)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            close_btn.setAccessibleName("Закрыть окно настроек главного меню")
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            # Таб-цепочка: гарантируем, что Tab попадёт в список.
            try:
                self.setTabOrder(self.scroll_area, self.refresh_btn)
                self.setTabOrder(self.refresh_btn, self.reset_btn)
                self.setTabOrder(self.reset_btn, close_btn)
            except Exception:
                pass

            # Стрелки ВВЕРХ/ВНИЗ между блоками (когда фокус НЕ на чекбоксах).
            # На самих чекбоксах стрелки листают чекбоксы, поэтому туда не лезем.
            self._nav_controls = (self.refresh_btn, self.reset_btn, close_btn, self.scroll_area)
            for w in self._nav_controls:
                try:
                    w.installEventFilter(self)
                except Exception:
                    pass

            self._reload()
            # Добиваем фокус на первом пункте уже после показа окна.
            self._focus_first_checkbox_deferred()

        # ---- helpers ----

        @staticmethod
        def _format_item_text(title: str, is_on: bool) -> str:
            # В тексте дублируем состояние, чтобы диктор озвучивал его даже если
            # по каким-то причинам не озвучивает «checked/unchecked».
            return f"{title} — {'включена' if is_on else 'выключена'}"

        def _set_status(self, text: str) -> None:
            self.status.setText(text)

        def _clear_checkboxes(self) -> None:
            self._checkboxes = []
            # Удаляем все виджеты из scroll_layout, кроме последнего stretch.
            try:
                while self.scroll_layout.count() > 0:
                    item = self.scroll_layout.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)
                        w.deleteLater()
            except Exception:
                pass
            # Вернём stretch вниз.
            try:
                self.scroll_layout.addStretch(1)
            except Exception:
                pass

        def _focus_first_checkbox(self) -> None:
            """Поставить фокус на первый чекбокс, чтобы стрелки сразу работали."""
            try:
                if not self._checkboxes:
                    self.scroll_area.setFocus(Qt.OtherFocusReason)
                    return
                self._checkboxes[0].setFocus(Qt.OtherFocusReason)
            except Exception:
                pass

        def _focus_first_checkbox_deferred(self) -> None:
            """Фокус после входа в event loop (важно для Windows/Narrator)."""
            try:
                QTimer.singleShot(0, self._focus_first_checkbox)
            except Exception:
                self._focus_first_checkbox()

        def _reload(self) -> None:
            self._updating = True
            try:
                self._clear_checkboxes()
                # Подтягиваем утилиты в реестр главного меню, чтобы ими можно было управлять отсюда.
                try:
                    _install_utilities_register_hook(default_on=False)
                    _sync_utilities_into_mainmenu_registry(default_on=False)
                except Exception:
                    pass

                items = get_main_items_with_visibility(group="main")

                created = 0
                for item_obj, visible in items:
                    key = getattr(item_obj, "key", "") or ""
                    title = getattr(item_obj, "title", key) or key
                    desc = getattr(item_obj, "description", "") or ""

                    cb = QCheckBox(self._format_item_text(str(title), bool(visible)), self.scroll_container)
                    cb.setProperty("mainmenu_key", str(key))
                    cb.setProperty("mainmenu_title", str(title))
                    cb.setChecked(bool(visible))
                    cb.setFocusPolicy(Qt.StrongFocus)

                    # Подсказка/описание для диктора
                    if desc:
                        cb.setToolTip(desc)
                        cb.setAccessibleDescription(desc)
                    else:
                        cb.setAccessibleDescription(f"Кнопка главного меню: {title}")

                    # Чуть расширяем, чтобы текст не резался, и диктор его не обрывал.
                    cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

                    cb.stateChanged.connect(self._on_checkbox_state_changed)
                    cb.installEventFilter(self)

                    # Вставляем перед stretch (последний элемент)
                    idx = max(0, self.scroll_layout.count() - 1)
                    self.scroll_layout.insertWidget(idx, cb)

                    self._checkboxes.append(cb)
                    created += 1

                if created > 0:
                    self._focus_first_checkbox_deferred()

                self._set_status(f"Найдено {len(items)} кнопок главного меню.")
            finally:
                self._updating = False

        def _checkbox_index(self, cb: QCheckBox) -> int:
            try:
                return self._checkboxes.index(cb)
            except Exception:
                return -1

        def _focus_checkbox_by_index(self, idx: int) -> None:
            if not self._checkboxes:
                return
            idx = max(0, min(idx, len(self._checkboxes) - 1))
            try:
                self._checkboxes[idx].setFocus(Qt.OtherFocusReason)
            except Exception:
                pass

        def eventFilter(self, obj, event):  # type: ignore[override]
            # 1) Навигация по чекбоксам стрелками + Enter
            try:
                if isinstance(obj, QCheckBox):
                    if event.type() == QEvent.FocusIn:
                        title = obj.property("mainmenu_title") or ""
                        is_on = obj.isChecked()
                        self._set_status(f"Выбрано: «{title}». Состояние: {'включена' if is_on else 'выключена'}.")

                    if event.type() == QEvent.KeyPress:
                        key = event.key()
                        if key in (Qt.Key_Return, Qt.Key_Enter):
                            obj.toggle()
                            event.accept()
                            return True

                        if key in (Qt.Key_Down, Qt.Key_Up):
                            cur = self._checkbox_index(obj)
                            if cur >= 0:
                                nxt = cur + (1 if key == Qt.Key_Down else -1)
                                self._focus_checkbox_by_index(nxt)
                                event.accept()
                                return True

                # 2) Если фокус на кнопках/скролле, стрелки кидают фокус в первый чекбокс
                if event.type() == QEvent.KeyPress:
                    key = event.key()
                    if key in (Qt.Key_Down, Qt.Key_Up):
                        if obj in getattr(self, "_nav_controls", ()):
                            self._focus_first_checkbox_deferred()
                            event.accept()
                            return True
            except Exception:
                pass

            return super().eventFilter(obj, event)

        def _on_checkbox_state_changed(self, state: int) -> None:
            if self._updating:
                return
            cb = self.sender()
            if not isinstance(cb, QCheckBox):
                return

            key = cb.property("mainmenu_key") or ""
            title = cb.property("mainmenu_title") or key
            new_state = cb.isChecked()

            # Чтобы не словить рекурсию при смене текста, закрываемся флагом.
            self._updating = True
            try:
                set_main_item_visibility(str(key), bool(new_state))
                cb.setText(self._format_item_text(str(title), bool(new_state)))
            finally:
                self._updating = False

            _try_log(self._log_func, f"[GUI][MAINMENU] {key} -> {'on' if new_state else 'off'} ({title})")
            self._set_status(f"Кнопка «{title}» {'включена' if new_state else 'выключена'}.")

        def _reset_all(self) -> None:
            reply = QMessageBox.question(
                self,
                "Сбросить видимость",
                "Снять все галочки и скрыть все кнопки главного меню?",
            )
            if reply != QMessageBox.Yes:
                return

            self._updating = True
            try:
                for cb in list(self._checkboxes):
                    try:
                        key = cb.property("mainmenu_key") or ""
                        title = cb.property("mainmenu_title") or key
                        cb.setChecked(False)
                        cb.setText(self._format_item_text(str(title), False))
                        if key:
                            set_main_item_visibility(str(key), False)
                    except Exception:
                        continue
            finally:
                self._updating = False

            _try_log(self._log_func, "[GUI][MAINMENU] reset to defaults (all off)")
            self._set_status("Все галочки сняты. Кнопки главного меню скрыты.")

        def showEvent(self, event) -> None:  # type: ignore[override]
            super().showEvent(event)
            # Гарантируем фокус на первом пункте.
            self._focus_first_checkbox_deferred()

    _WINDOW_CLASS = MainMenuSettingsWindow
    return _WINDOW_CLASS


# -------------------- открытие окна --------------------

_WINDOWS = weakref.WeakKeyDictionary()


def open_mainmenu_settings_window(main_window: Optional["QWidget"]) -> None:
    """Открыть (или активировать) окно настроек главного меню."""
    if main_window is None:
        return

    # Подтягиваем Qt только в момент реального открытия окна.
    try:
        window_cls = _get_window_class()
    except Exception as e:
        # Падаем мягко: без Qt окно не открыть, но и приложение не валим.
        try:
            _try_log(getattr(main_window, "write_bot_log", None), f"[GUI][MAINMENU] PyQt5 import failed: {e}")
        except Exception:
            pass
        return

    w = _WINDOWS.get(main_window)
    if w is None:
        log_func = None
        for attr in ("write_bot_log", "write_log", "log", "append_log", "add_log_line"):
            try:
                candidate = getattr(main_window, attr, None)
                if callable(candidate):
                    log_func = candidate
                    break
            except Exception:
                continue
        w = window_cls(parent=main_window, log_func=log_func)
        _WINDOWS[main_window] = w

    try:
        w.show()
        w.raise_()
        w.activateWindow()
    except Exception:
        try:
            w.show()
        except Exception:
            pass
