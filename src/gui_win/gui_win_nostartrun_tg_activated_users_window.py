# -*- coding: utf-8 -*-
"""
GUI: Пользователи Telegram, активировавшие бота.

Модуль сделан с ленивыми импортами:
- PyQt5 импортируется только при реальном открытии окна.
- backend (activated_users_store) импортируется только при обновлении данных.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from PyQt5.QtWidgets import QWidget


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "Активированные TG-пользователи"
FUNCTIONS_ENTRYPOINT = "open_tg_activated_users_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 65
FUNCTIONS_ICON = "SP_FileDialogInfoView"
FUNCTIONS_TOOLTIP = "Показать пользователей Telegram, которые активировали бота"
FUNCTIONS_ACCESSIBLE_NAME = "Окно: Активированные TG-пользователи"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = (
    "Показывает пользователей Telegram из data/activated_users.db: id, username и другие доступные поля."
)


_PYQT_CACHE: Optional[Tuple[Any, ...]] = None
_WINDOW_CLASS: Optional[type] = None
_BACKEND_CACHE: Optional[Any] = None


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


def _one_line(text: Any) -> str:
    try:
        s = str(text)
    except Exception:
        return "(unprintable)"
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.splitlines())
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def _resolve_base_dir(main_window: Optional["QWidget"] = None) -> Path:
    candidates: List[Any] = []

    if main_window is not None:
        for attr in ("base_dir", "BASE_DIR"):
            try:
                value = getattr(main_window, attr, None)
                if value:
                    candidates.append(value)
            except Exception:
                pass

    main_mod = sys.modules.get("__main__")
    if main_mod is not None:
        for attr in ("base_dir", "BASE_DIR"):
            try:
                value = getattr(main_mod, attr, None)
                if value:
                    candidates.append(value)
            except Exception:
                pass
        try:
            get_app_dir = getattr(main_mod, "get_app_dir", None)
            if callable(get_app_dir):
                value = get_app_dir()
                if value:
                    candidates.append(value)
        except Exception:
            pass

    candidates.append(Path.cwd())

    for item in candidates:
        try:
            path = Path(item).resolve()
            if path.exists() and path.is_dir():
                return path
        except Exception:
            continue

    try:
        return Path(__file__).resolve().parent.parent
    except Exception:
        return Path.cwd().resolve()


def _import_activated_users_store(main_window: Optional["QWidget"] = None):
    global _BACKEND_CACHE
    if _BACKEND_CACHE is not None:
        return _BACKEND_CACHE

    last_error: Optional[Exception] = None

    for name in ("activated_users_store", "moduls.activated_users_store"):
        try:
            _BACKEND_CACHE = importlib.import_module(name)
            return _BACKEND_CACHE
        except Exception as exc:
            last_error = exc

    try:
        base_dir = _resolve_base_dir(main_window)
        moduls_dir = base_dir / "moduls"
        if moduls_dir.exists():
            moduls_dir_str = str(moduls_dir)
            if moduls_dir_str not in sys.path:
                sys.path.insert(0, moduls_dir_str)
            try:
                _BACKEND_CACHE = importlib.import_module("activated_users_store")
                return _BACKEND_CACHE
            except Exception as exc:
                last_error = exc

        file_path = moduls_dir / "activated_users_store.py"
        if file_path.exists():
            spec = importlib.util.spec_from_file_location("activated_users_store", str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules.setdefault("activated_users_store", module)
                _BACKEND_CACHE = module
                return _BACKEND_CACHE
    except Exception as exc:
        last_error = exc

    if last_error:
        raise last_error
    raise ImportError("Не удалось импортировать activated_users_store")


def _open_folder(path: Path) -> Tuple[bool, str]:
    try:
        path = path.resolve()
    except Exception:
        path = Path(path)

    if not path.exists():
        return False, f"Папка не найдена: {path}"

    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True, f"Открыта папка: {path}"
    except Exception as exc:
        return False, f"Не удалось открыть папку: {path}. Ошибка: {exc}"


def _render_users(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "Пользователи пока не сохранены."

    parts: List[str] = []
    for idx, row in enumerate(rows, 1):
        user_id = _safe_int(row.get("user_id"))
        chat_id = _safe_int(row.get("chat_id"))
        username = _safe_text(row.get("username"), fallback="")
        if username and username != "-" and not username.startswith("@"):
            username = f"@{username}"

        lines = [
            f"[{idx}] user_id={_safe_text(user_id)}  chat_id={_safe_text(chat_id)}",
            f"username={_safe_text(username)}",
            f"first_name={_safe_text(row.get('first_name'))}",
            f"last_name={_safe_text(row.get('last_name'))}",
            f"language_code={_safe_text(row.get('language_code'))}",
            f"is_bot={_safe_text(row.get('is_bot'))}",
            f"activated_at={_safe_text(row.get('activated_at'))}",
            f"last_activated_at={_safe_text(row.get('last_activated_at'))}",
            f"activation_count={_safe_text(row.get('activation_count'))}",
            f"last_source={_safe_text(row.get('last_source'))}",
        ]
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _try_log(log_func: Optional[Callable[[str], None]], text: str) -> None:
    if callable(log_func):
        try:
            log_func(text)
            return
        except Exception:
            pass

    main_mod = sys.modules.get("__main__")
    if main_mod is not None:
        for attr in ("write_bot_log", "write_log", "log"):
            try:
                fn = getattr(main_mod, attr, None)
                if callable(fn):
                    fn(text)
                    return
            except Exception:
                continue


def _get_pyqt() -> Tuple[Any, ...]:
    global _PYQT_CACHE
    if _PYQT_CACHE is not None:
        return _PYQT_CACHE

    from PyQt5.QtCore import Qt  # type: ignore
    from PyQt5.QtWidgets import (  # type: ignore
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )

    _PYQT_CACHE = (
        Qt,
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )
    return _PYQT_CACHE


def _get_window_class():
    global _WINDOW_CLASS
    if _WINDOW_CLASS is not None:
        return _WINDOW_CLASS

    (
        Qt,
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    ) = _get_pyqt()

    class TgActivatedUsersWindow(QDialog):
        def __init__(self, parent: Optional["QWidget"] = None, log_func: Optional[Callable[[str], None]] = None):
            super().__init__(parent)

            self.setObjectName("tgActivatedUsersWindow")
            self.setWindowTitle("Активированные TG-пользователи")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setMinimumSize(860, 620)
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
            self._base_dir = _resolve_base_dir(parent)

            root = QVBoxLayout(self)
            root.setContentsMargins(20, 20, 20, 16)
            root.setSpacing(10)

            header = QLabel("Активированные пользователи Telegram")
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            header.setAccessibleName("Заголовок окна активированных пользователей")
            root.addWidget(header)

            info = QLabel(
                "Окно показывает пользователей, которые прошли активацию бота. "
                "Данные берутся из data/activated_users.db."
            )
            info.setWordWrap(True)
            root.addWidget(info)

            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFrameShadow(QFrame.Sunken)
            root.addWidget(divider)

            self.status_lbl = QLabel("Готово к загрузке данных.")
            self.status_lbl.setWordWrap(True)
            self.status_lbl.setAccessibleName("Строка статуса")
            self.status_lbl.setAccessibleDescription("Озвучивает итог действий: успех или ошибка.")
            root.addWidget(self.status_lbl)

            self.summary_lbl = QLabel("Сохранено пользователей: -")
            self.summary_lbl.setAccessibleName("Сводка по пользователям")
            root.addWidget(self.summary_lbl)

            self.db_path_lbl = QLabel("База данных: -")
            self.db_path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            self.db_path_lbl.setFocusPolicy(Qt.StrongFocus)
            self.db_path_lbl.setAccessibleName("Путь к базе данных активированных пользователей")
            root.addWidget(self.db_path_lbl)

            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            root.addLayout(btn_row)

            self.refresh_btn = QPushButton("Обновить")
            self.refresh_btn.clicked.connect(self._refresh)
            btn_row.addWidget(self.refresh_btn)

            self.copy_btn = QPushButton("Копировать список")
            self.copy_btn.clicked.connect(self._copy_to_clipboard)
            btn_row.addWidget(self.copy_btn)

            self.clear_btn = QPushButton("Очистить всех пользователей")
            self.clear_btn.clicked.connect(self._clear_all_users)
            self.clear_btn.setAccessibleName("Кнопка: Очистить всех пользователей")
            self.clear_btn.setAccessibleDescription(
                "Удаляет всех активированных пользователей после подтверждения."
            )
            btn_row.addWidget(self.clear_btn)

            self.open_data_btn = QPushButton("Открыть папку data")
            self.open_data_btn.clicked.connect(self._open_data_folder)
            btn_row.addWidget(self.open_data_btn)

            self.output = QPlainTextEdit()
            self.output.setReadOnly(True)
            self.output.setPlaceholderText("Здесь будет список пользователей.")
            self.output.setFocusPolicy(Qt.StrongFocus)
            self.output.setAccessibleName("Список активированных пользователей Telegram")
            self.output.setAccessibleDescription(
                "Показывает user_id, chat_id, username, имя, язык и время активации каждого пользователя."
            )
            root.addWidget(self.output, 1)

            box = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = box.button(QDialogButtonBox.Close)
            if close_btn is not None:
                close_btn.setText("Закрыть")
            box.rejected.connect(self.reject)
            root.addWidget(box)

            self._refresh()

        def _set_status(self, text: str, ok: bool = True) -> None:
            prefix = "OK:" if ok else "Ошибка:"
            self.status_lbl.setText(f"{prefix} {text}")
            try:
                self.status_lbl.setAccessibleName(f"Статус: {prefix} {text}")
            except Exception:
                pass

        def _log(self, text: str) -> None:
            _try_log(self._log_func, f"[GUI][TG-USERS] {_one_line(text)}")

        def _refresh(self) -> None:
            self._base_dir = _resolve_base_dir(self.parentWidget())

            try:
                backend = _import_activated_users_store(self.parentWidget())
            except Exception as exc:
                self.summary_lbl.setText("Сохранено пользователей: -")
                self.db_path_lbl.setText("База данных: -")
                self.output.setPlainText(
                    "Не удалось загрузить backend модуль activated_users_store.\n"
                    f"Ошибка: {exc}"
                )
                self._set_status("Не удалось импортировать activated_users_store.", ok=False)
                self._log(f"backend import failed: {exc}")
                return

            base_dir_str = str(self._base_dir)
            db_path = self._base_dir / "data" / "activated_users.db"
            rows: List[Dict[str, Any]] = []

            try:
                ensure_storage = getattr(backend, "ensure_storage", None)
                if callable(ensure_storage):
                    db_path = Path(ensure_storage(base_dir_str))
            except Exception as exc:
                self._log(f"ensure_storage error: {exc}")

            try:
                list_activated_users = getattr(backend, "list_activated_users", None)
                if not callable(list_activated_users):
                    raise RuntimeError("В activated_users_store нет функции list_activated_users")
                rows = list_activated_users(base_dir_str) or []
            except Exception as exc:
                self.summary_lbl.setText("Сохранено пользователей: -")
                self.db_path_lbl.setText(f"База данных: {db_path}")
                self.output.setPlainText(
                    "Не удалось прочитать пользователей из базы.\n"
                    f"Путь: {db_path}\n"
                    f"Ошибка: {exc}"
                )
                self._set_status("Ошибка чтения данных из БД.", ok=False)
                self._log(f"read users failed: {exc}")
                return

            count = len(rows)
            self.summary_lbl.setText(f"Сохранено пользователей: {count}")
            self.db_path_lbl.setText(f"База данных: {db_path}")
            self.output.setPlainText(_render_users(rows))

            if count == 0:
                self._set_status("База доступна, но пользователей пока нет.", ok=True)
            else:
                self._set_status(f"Загружено пользователей: {count}", ok=True)

        def _copy_to_clipboard(self) -> None:
            text = self.output.toPlainText().strip()
            if not text:
                self._set_status("Нечего копировать.", ok=False)
                return

            app = QApplication.instance()
            if app is None:
                self._set_status("QApplication не найден, копирование недоступно.", ok=False)
                return

            try:
                app.clipboard().setText(text)
                self._set_status("Список скопирован в буфер обмена.", ok=True)
            except Exception as exc:
                self._set_status(f"Не удалось скопировать список: {exc}", ok=False)

        def _open_data_folder(self) -> None:
            data_dir = _resolve_base_dir(self.parentWidget()) / "data"
            ok, message = _open_folder(data_dir)
            self._set_status(message, ok=ok)

        def _ask_clear_confirmation(self, count: int) -> bool:
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Warning)
            confirm.setWindowTitle("Подтверждение очистки")
            confirm.setText("Вы действительно хотите удалить всех активированных пользователей?")
            confirm.setInformativeText(
                f"Будет удалено записей: {count}\nЭто действие нельзя отменить."
            )
            confirm.setStandardButtons(QMessageBox.NoButton)

            btn_yes = confirm.addButton("Да", QMessageBox.YesRole)
            btn_no = confirm.addButton("Нет", QMessageBox.NoRole)

            try:
                confirm.setAccessibleName("Подтверждение очистки базы пользователей")
                confirm.setAccessibleDescription("Опасное действие. Выбор: Да или Нет.")
            except Exception:
                pass

            try:
                btn_yes.setAccessibleName("Да, очистить базу пользователей")
                btn_yes.setAccessibleDescription("Подтверждает безвозвратную очистку базы.")
                btn_no.setAccessibleName("Нет, отменить очистку базы")
                btn_no.setAccessibleDescription("Отменяет очистку и возвращает в окно модуля.")
            except Exception:
                pass

            # Безопасное поведение по умолчанию: default и Esc = "Нет"
            try:
                btn_no.setAutoDefault(True)
                btn_no.setDefault(True)
            except Exception:
                pass
            try:
                confirm.setDefaultButton(btn_no)
            except Exception:
                pass
            try:
                confirm.setEscapeButton(btn_no)
            except Exception:
                pass
            try:
                btn_no.setFocus(Qt.OtherFocusReason)
            except Exception:
                try:
                    btn_no.setFocus()
                except Exception:
                    pass

            try:
                confirm.exec_()
            except Exception:
                try:
                    confirm.exec()
                except Exception:
                    return False

            return confirm.clickedButton() is btn_yes

        def _clear_all_users(self) -> None:
            self._base_dir = _resolve_base_dir(self.parentWidget())

            try:
                backend = _import_activated_users_store(self.parentWidget())
            except Exception as exc:
                self._set_status(f"Не удалось загрузить backend: {exc}", ok=False)
                self._log(f"clear failed (import backend): {exc}")
                return

            base_dir_str = str(self._base_dir)

            try:
                list_activated_users = getattr(backend, "list_activated_users", None)
                if callable(list_activated_users):
                    rows = list_activated_users(base_dir_str) or []
                else:
                    rows = []
            except Exception:
                rows = []

            count = len(rows)
            if count <= 0:
                self._set_status("База уже пустая, очищать нечего.", ok=True)
                return

            if not self._ask_clear_confirmation(count):
                self._set_status("Очистка отменена.", ok=True)
                return

            try:
                clear_activated_users = getattr(backend, "clear_activated_users", None)
                if not callable(clear_activated_users):
                    raise RuntimeError("В activated_users_store нет функции clear_activated_users")
                deleted = int(clear_activated_users(base_dir_str))
            except Exception as exc:
                self._set_status(f"Ошибка очистки базы: {exc}", ok=False)
                self._log(f"clear failed: {exc}")
                return

            self._log(f"cleared users: {deleted}")
            self._refresh()
            self._set_status(f"База очищена. Удалено записей: {deleted}", ok=True)

    _WINDOW_CLASS = TgActivatedUsersWindow
    return _WINDOW_CLASS


_WINDOWS = weakref.WeakKeyDictionary()


def open_tg_activated_users_window(main_window: Optional["QWidget"]) -> None:
    """Открыть (или активировать) окно активированных TG-пользователей."""
    if main_window is None:
        return

    try:
        window_cls = _get_window_class()
    except Exception:
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
