# -*- coding: utf-8 -*-
"""
GUI: Веб-панель AutoCraft.

Окно управления веб-панелью (аналог startrunmodulwebpanel.py):
- запуск/остановка
- статус и URL
- логин/пароль
- смена пароля
- настройки (host/port/debug/retention)
- автозапуск
- последние логи

Упор на доступность:
- минимум "болтливых" AccessibleName/Description (только там, где реально помогает)
- предсказуемый Tab / Shift+Tab (явный таб-ордер)
- при открытии фокус сразу на кнопку "Запустить/Остановить панель"
- окна "Показать ..." всегда с текстом + кнопка "Закрыть", таб: текст -> закрыть
"""

from __future__ import annotations

import importlib
import time
import webbrowser
import weakref
from typing import Optional, Callable, TYPE_CHECKING, Tuple, Any

if TYPE_CHECKING:  # pragma: no cover
    from PyQt5.QtWidgets import QWidget


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "Веб-панель AutoCraft"
FUNCTIONS_ENTRYPOINT = "open_webpanel_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 55
FUNCTIONS_ICON = "SP_ComputerIcon"
FUNCTIONS_TOOLTIP = "Управление веб-панелью AutoCraft"
FUNCTIONS_ACCESSIBLE_NAME = "Окно: Веб-панель AutoCraft"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = (
    "Запуск/остановка веб-панели, управление пользователями, настройки и просмотр логов."
)


# -------------------- lazy PyQt5 import --------------------

_PYQT_CACHE: Optional[Tuple[Any, ...]] = None
_WINDOW_CLASS: Optional[type] = None
_BACKEND_CACHE: Optional[Any] = None


def _get_pyqt() -> Tuple[Any, ...]:
    """Ленивая загрузка PyQt5."""
    global _PYQT_CACHE
    if _PYQT_CACHE is not None:
        return _PYQT_CACHE

    from PyQt5.QtCore import Qt, QTimer  # type: ignore
    from PyQt5.QtGui import QTextCursor  # type: ignore
    from PyQt5.QtWidgets import (  # type: ignore
        QApplication,
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QGroupBox,
        QGridLayout,
        QLineEdit,
        QCheckBox,
        QPushButton,
        QHBoxLayout,
        QSpinBox,
        QPlainTextEdit,
        QDialogButtonBox,
        QComboBox,
        QTableWidget,
        QTableWidgetItem,
        QAbstractItemView,
        QMenu,
        QShortcut,
        QMessageBox,
    )

    _PYQT_CACHE = (
        Qt,
        QTimer,
        QTextCursor,
        QApplication,
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QGroupBox,
        QGridLayout,
        QLineEdit,
        QCheckBox,
        QPushButton,
        QHBoxLayout,
        QSpinBox,
        QPlainTextEdit,
        QDialogButtonBox,
        QComboBox,
        QTableWidget,
        QTableWidgetItem,
        QAbstractItemView,
        QMenu,
        QShortcut,
        QMessageBox,
    )
    return _PYQT_CACHE


def _get_backend():
    """Лениво импортируем backend модуля веб-панели."""
    global _BACKEND_CACHE
    if _BACKEND_CACHE is not None:
        return _BACKEND_CACHE

    last_err: Optional[Exception] = None
    for name in ("startrunmodulwebpanel", "moduls.startrunmodulwebpanel"):
        try:
            _BACKEND_CACHE = importlib.import_module(name)
            return _BACKEND_CACHE
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise ImportError("Не удалось импортировать startrunmodulwebpanel")


def _try_log(log_func: Optional[Callable[[str], None]], text: str, backend: Optional[Any] = None) -> None:
    """Пробуем писать в лог куда получится, без падений."""
    delivered = False
    if backend is not None:
        try:
            wb = getattr(backend, "write_bot_log", None)
            if callable(wb):
                wb(text)
                delivered = True
        except Exception:
            pass
    if not delivered and callable(log_func):
        try:
            log_func(text)
            delivered = True
        except Exception:
            pass
    if delivered:
        return
    try:
        import __main__  # type: ignore

        wb = getattr(__main__, "write_bot_log", None)
        if callable(wb):
            wb(text)
    except Exception:
        pass


def _get_window_class():
    """Возвращает (и кэширует) класс окна."""
    global _WINDOW_CLASS
    if _WINDOW_CLASS is not None:
        return _WINDOW_CLASS

    (
        Qt,
        QTimer,
        QTextCursor,
        QApplication,
        QDialog,
        QVBoxLayout,
        QLabel,
        QFrame,
        QGroupBox,
        QGridLayout,
        QLineEdit,
        QCheckBox,
        QPushButton,
        QHBoxLayout,
        QSpinBox,
        QPlainTextEdit,
        QDialogButtonBox,
        QComboBox,
        QTableWidget,
        QTableWidgetItem,
        QAbstractItemView,
        QMenu,
        QShortcut,
        QMessageBox,
    ) = _get_pyqt()

    class WebPanelWindow(QDialog):
        def __init__(self, parent: Optional["QWidget"] = None, log_func: Optional[Callable[[str], None]] = None):
            super().__init__(parent)

            self.setObjectName("webPanelWindow")
            self.setWindowTitle("Веб-панель AutoCraft")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setMinimumSize(720, 640)
            self.setSizeGripEnabled(True)

            if parent is not None:
                # наследуем оформление главного окна (особенно важно в EXE)
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
            self._backend = None
            self._backend_error: Optional[Exception] = None
            self._updating = False
            self._logs_last_text: str = ""
            self._logs_last_error: Optional[str] = None
            self._status_last_render: Optional[tuple] = None
            self._restart_busy = False
            self._config_cache: Optional[Any] = None
            self._config_cache_ts: float = 0.0
            self._config_cache_ttl_sec: float = 3.0
            self._autostart_cache_value: Optional[bool] = None
            self._autostart_cache_ts: float = 0.0
            self._autostart_cache_ttl_sec: float = 2.5
            self._logs_poll_ms: int = 1600
            self._logs_poll_reader_ms: int = 3200
            self._status_poll_ms: int = 1500

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(10)

            header = QLabel("Веб-панель AutoCraft")
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            layout.addWidget(header)

            info = QLabel("Запуск/остановка, пользователи, настройки и логи веб-панели.")
            info.setWordWrap(True)
            layout.addWidget(info)

            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFrameShadow(QFrame.Sunken)
            layout.addWidget(divider)

            # ---- helpers: поля "только чтение" ----
            def ro_field(accessible_name: str) -> "QLineEdit":
                w = QLineEdit()
                w.setReadOnly(True)
                # QLineEdit надёжно читается скринридером, но не заставляем его говорить лишнее
                try:
                    w.setFrame(False)
                except Exception:
                    pass
                try:
                    w.setFocusPolicy(Qt.StrongFocus)
                except Exception:
                    pass
                try:
                    w.setAccessibleName(accessible_name)
                except Exception:
                    pass
                return w

            def ro_status(accessible_name: str) -> "QLabel":
                """Только чтение, но НЕ поле ввода: скринридер читает как статический текст."""
                w = QLabel("—")
                # Визуально как "поле", но по роли остаётся текстом.
                try:
                    w.setFrameShape(QFrame.Panel)
                    w.setFrameShadow(QFrame.Sunken)
                except Exception:
                    pass
                try:
                    w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                except Exception:
                    pass
                try:
                    w.setMargin(6)
                except Exception:
                    pass
                # Не включаем TextSelectableByKeyboard: Qt может сбросить focusPolicy до ClickFocus,
                # и тогда до виджета нельзя дойти Tab'ом (плохо для скринридера).
                try:
                    w.setTextInteractionFlags(Qt.NoTextInteraction)
                except Exception:
                    pass
                try:
                    w.setFocusPolicy(Qt.StrongFocus)
                except Exception:
                    pass
                try:
                    w.setAccessibleName(accessible_name)
                except Exception:
                    pass
                return w


            # --- Состояние ---
            self.status_group = QGroupBox("Состояние панели")
            status_layout = QGridLayout(self.status_group)
            status_layout.setHorizontalSpacing(12)
            status_layout.setVerticalSpacing(6)

            self.status_value = ro_status("Статус панели")
            self.url_value = ro_field("URL панели")
            self.host_value = ro_field("Хост панели")
            self.port_value = ro_field("Порт панели")

            lbl_status = QLabel("&Статус:")
            lbl_status.setBuddy(self.status_value)
            lbl_url = QLabel("&URL:")
            lbl_url.setBuddy(self.url_value)
            lbl_host = QLabel("&Хост:")
            lbl_host.setBuddy(self.host_value)
            lbl_port = QLabel("&Порт:")
            lbl_port.setBuddy(self.port_value)

            status_layout.addWidget(lbl_status, 0, 0)
            status_layout.addWidget(self.status_value, 0, 1, 1, 3)
            status_layout.addWidget(lbl_url, 1, 0)
            status_layout.addWidget(self.url_value, 1, 1, 1, 3)
            status_layout.addWidget(lbl_host, 2, 0)
            status_layout.addWidget(self.host_value, 2, 1)
            status_layout.addWidget(lbl_port, 2, 2)
            status_layout.addWidget(self.port_value, 2, 3)

            self.autostart_cb = QCheckBox("Автозапуск панели при старте AutoCraft")
            self.autostart_cb.toggled.connect(self._on_autostart_toggled)
            status_layout.addWidget(self.autostart_cb, 3, 0, 1, 4)

            self.autostart_note = QLabel("Применится при следующем запуске AutoCraft.")
            self.autostart_note.setWordWrap(True)
            status_layout.addWidget(self.autostart_note, 4, 0, 1, 4)

            controls_row1 = QHBoxLayout()
            self.toggle_btn = QPushButton("Запустить панель")
            self.toggle_btn.clicked.connect(self._on_toggle_panel)
            controls_row1.addWidget(self.toggle_btn)

            self.restart_btn = QPushButton("Перезапустить панель")
            self.restart_btn.clicked.connect(self._on_restart_panel)
            controls_row1.addWidget(self.restart_btn)

            self.open_btn = QPushButton("Открыть панель")
            self.open_btn.clicked.connect(self._on_open_panel)
            self.open_btn.setEnabled(False)
            controls_row1.addWidget(self.open_btn)

            self.url_btn = QPushButton("Показать адрес")
            self.url_btn.clicked.connect(self._on_show_url)
            controls_row1.addWidget(self.url_btn)

            controls_row1.addStretch(1)
            status_layout.addLayout(controls_row1, 5, 0, 1, 4)

            controls_row2 = QHBoxLayout()
            self.creds_btn = QPushButton("Менеджер пользователей")
            self.creds_btn.clicked.connect(self._on_open_user_manager)
            controls_row2.addWidget(self.creds_btn)

            self.api_btn = QPushButton("API панели")
            self.api_btn.clicked.connect(self._on_show_api)
            controls_row2.addWidget(self.api_btn)

            controls_row2.addStretch(1)
            status_layout.addLayout(controls_row2, 6, 0, 1, 4)

            layout.addWidget(self.status_group)

            # --- Настройки ---
            self.settings_group = QGroupBox("Настройки панели")
            settings_layout = QGridLayout(self.settings_group)
            settings_layout.setHorizontalSpacing(12)
            settings_layout.setVerticalSpacing(6)

            self.host_edit = QLineEdit()
            self.host_edit.setAccessibleName("Хост (ввод)")
            self.port_spin = QSpinBox()
            self.port_spin.setRange(1, 65535)
            self.port_spin.setAccessibleName("Порт (выбор)")
            self.debug_cb = QCheckBox("Debug режим")
            self.retention_spin = QSpinBox()
            self.retention_spin.setRange(1, 3650)
            self.retention_spin.setAccessibleName("Хранение логов, дней")

            lbl_host2 = QLabel("Х&ост:")
            lbl_host2.setBuddy(self.host_edit)
            lbl_port2 = QLabel("П&орт:")
            lbl_port2.setBuddy(self.port_spin)
            lbl_ret = QLabel("&Хранение логов (дней):")
            lbl_ret.setBuddy(self.retention_spin)

            settings_layout.addWidget(lbl_host2, 0, 0)
            settings_layout.addWidget(self.host_edit, 0, 1)
            settings_layout.addWidget(lbl_port2, 0, 2)
            settings_layout.addWidget(self.port_spin, 0, 3)

            settings_layout.addWidget(lbl_ret, 1, 0)
            settings_layout.addWidget(self.retention_spin, 1, 1)
            settings_layout.addWidget(self.debug_cb, 1, 2, 1, 2)

            settings_note = QLabel("После сохранения перезапустите панель для применения.")
            settings_note.setWordWrap(True)
            settings_layout.addWidget(settings_note, 2, 0, 1, 4)

            settings_buttons = QHBoxLayout()
            self.save_settings_btn = QPushButton("Сохранить настройки")
            self.save_settings_btn.clicked.connect(self._on_save_settings)
            settings_buttons.addWidget(self.save_settings_btn)

            self.reload_settings_btn = QPushButton("Перечитать из config.ini")
            self.reload_settings_btn.clicked.connect(self._load_config)
            settings_buttons.addWidget(self.reload_settings_btn)

            settings_buttons.addStretch(1)
            settings_layout.addLayout(settings_buttons, 3, 0, 1, 4)

            layout.addWidget(self.settings_group)

            # --- Пользователи ---
            self.password_group = QGroupBox("Пользователи панели")
            password_layout = QGridLayout(self.password_group)
            password_layout.setHorizontalSpacing(12)
            password_layout.setVerticalSpacing(6)

            users_hint = QLabel(
                "Добавление пользователей, смена пароля, удаление и просмотр сохранённых учётных данных."
            )
            users_hint.setWordWrap(True)
            password_layout.addWidget(users_hint, 0, 0, 1, 4)

            users_note = QLabel("Все операции выполняются через менеджер пользователей.")
            users_note.setWordWrap(True)
            password_layout.addWidget(users_note, 1, 0, 1, 4)

            self.user_manager_btn = QPushButton("Открыть менеджер пользователей")
            self.user_manager_btn.clicked.connect(self._on_open_user_manager)
            password_layout.addWidget(self.user_manager_btn, 2, 0, 1, 4)

            layout.addWidget(self.password_group)

            # --- Логи ---
            self.logs_group = QGroupBox("Логи панели")
            logs_layout = QVBoxLayout(self.logs_group)
            logs_layout.setSpacing(6)

            self.logs_view = QPlainTextEdit()
            self.logs_view.setReadOnly(True)
            self.logs_view.setAccessibleName("Логи веб-панели")
            self.logs_view.setMinimumHeight(140)
            # Tab уходит дальше по форме, чтение логов стрелками
            try:
                self.logs_view.setTabChangesFocus(True)
            except Exception:
                pass
            try:
                self.logs_view.setUndoRedoEnabled(False)
            except Exception:
                pass
            try:
                self.logs_view.setLineWrapMode(QPlainTextEdit.NoWrap)
            except Exception:
                pass
            try:
                self.logs_view.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            except Exception:
                pass
            try:
                self.logs_view.setAccessibleDescription("Чтение стрелками. Tab перейти дальше.")
            except Exception:
                pass

            logs_layout.addWidget(self.logs_view)

            self.logs_note = QLabel("Обновление логов: автоматически.")
            self.logs_note.setWordWrap(True)
            logs_layout.addWidget(self.logs_note)

            layout.addWidget(self.logs_group)

            self.status_msg = QLabel("")
            self.status_msg.setWordWrap(True)
            layout.addWidget(self.status_msg)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            # Таб-ордер: максимально прямой и стабильный
            try:
                self.setTabOrder(self.status_value, self.url_value)
                self.setTabOrder(self.url_value, self.host_value)
                self.setTabOrder(self.host_value, self.port_value)
                self.setTabOrder(self.port_value, self.autostart_cb)

                self.setTabOrder(self.autostart_cb, self.toggle_btn)
                self.setTabOrder(self.toggle_btn, self.restart_btn)
                self.setTabOrder(self.restart_btn, self.open_btn)
                self.setTabOrder(self.open_btn, self.url_btn)
                self.setTabOrder(self.url_btn, self.creds_btn)
                self.setTabOrder(self.creds_btn, self.api_btn)

                self.setTabOrder(self.api_btn, self.host_edit)
                self.setTabOrder(self.host_edit, self.port_spin)
                self.setTabOrder(self.port_spin, self.retention_spin)
                self.setTabOrder(self.retention_spin, self.debug_cb)
                self.setTabOrder(self.debug_cb, self.save_settings_btn)
                self.setTabOrder(self.save_settings_btn, self.reload_settings_btn)

                self.setTabOrder(self.reload_settings_btn, self.user_manager_btn)

                self.setTabOrder(self.user_manager_btn, self.logs_view)
                self.setTabOrder(self.logs_view, close_btn)
            except Exception:
                pass

            self._load_backend()
            self._load_config()
            self._refresh_status()

            # Первичная загрузка логов + автообновление (реальное время, но без "фокуса-воровства")
            self._refresh_logs(silent=True)

            self._logs_timer = QTimer(self)
            self._logs_timer.timeout.connect(self._refresh_logs_soft)
            self._logs_timer.start(self._logs_poll_ms)

            self._status_timer = QTimer(self)
            self._status_timer.timeout.connect(self._refresh_status_soft)
            self._status_timer.start(self._status_poll_ms)

        # ---- accessibility helpers ----

        def showEvent(self, event) -> None:  # type: ignore[override]
            """При открытии окна сразу ставим фокус на кнопку запуска/остановки."""
            try:
                super().showEvent(event)  # type: ignore[misc]
            except Exception:
                pass

            try:
                self._refresh_status_soft()
            except Exception:
                pass

            try:
                QTimer.singleShot(0, self._focus_to_toggle)
            except Exception:
                pass

        def _focus_to_toggle(self) -> None:
            try:
                self.toggle_btn.setFocus(Qt.OtherFocusReason)
                # Подстрахуем чтение: некоторые скринридеры любят, когда у кнопки явно задано имя.
                try:
                    self.toggle_btn.setAccessibleName(self.toggle_btn.text())
                except Exception:
                    pass
            except Exception:
                pass

        def _popup_text(self, title: str, text: str) -> None:
            """Доступное окно сообщения: текст + кнопка 'Закрыть'."""
            dlg = QDialog(self)
            dlg.setWindowTitle(title)
            dlg.setModal(True)
            try:
                dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            except Exception:
                pass

            # наследуем стиль
            try:
                dlg.setPalette(self.palette())
            except Exception:
                pass
            try:
                ss = self.styleSheet()
                if ss:
                    dlg.setStyleSheet(ss)
            except Exception:
                pass

            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(16, 16, 16, 12)
            lay.setSpacing(10)

            head = QLabel(title)
            head.setStyleSheet("font-size: 12pt; font-weight: 600;")
            lay.addWidget(head)

            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText(text)
            view.setAccessibleName("Текст сообщения")
            view.setMinimumHeight(160)
            try:
                view.setTabChangesFocus(True)
            except Exception:
                pass
            try:
                view.setUndoRedoEnabled(False)
            except Exception:
                pass
            try:
                view.setFocusPolicy(Qt.StrongFocus)
            except Exception:
                pass
            try:
                # Чтобы можно было читать стрелками и выделять клавиатурой (важно для NVDA/JAWS).
                view.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            except Exception:
                pass
            try:
                # Читается понятнее, когда переносы только по \n, а не по ширине виджета.
                view.setLineWrapMode(QPlainTextEdit.NoWrap)
            except Exception:
                pass
            try:
                view.setAccessibleDescription("Чтение стрелками. Tab перейти на кнопку Закрыть.")
            except Exception:
                pass
            try:
                cur = view.textCursor()
                cur.movePosition(QTextCursor.Start)
                view.setTextCursor(cur)
            except Exception:
                pass
            lay.addWidget(view)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            buttons.rejected.connect(dlg.reject)
            lay.addWidget(buttons)

            # Таб: текст -> закрыть
            try:
                dlg.setTabOrder(view, close_btn)
            except Exception:
                pass

            try:
                QTimer.singleShot(0, lambda: view.setFocus(Qt.OtherFocusReason))
            except Exception:
                pass

            try:
                dlg.exec_()
            except Exception:
                try:
                    dlg.exec()
                except Exception:
                    pass

        def _ask_yes_no(
            self,
            title: str,
            text: str,
            *,
            default_yes: bool = True,
            parent: Optional["QWidget"] = None,
        ) -> bool:
            box = QMessageBox(parent if parent is not None else self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle(title)
            box.setText(text)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes if default_yes else QMessageBox.No)
            try:
                box.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            except Exception:
                pass
            try:
                yes_btn = box.button(QMessageBox.Yes)
                if yes_btn is not None:
                    yes_btn.setText("Да")
                no_btn = box.button(QMessageBox.No)
                if no_btn is not None:
                    no_btn.setText("Нет")
            except Exception:
                pass
            try:
                answer = box.exec_()
            except Exception:
                answer = box.exec()
            return answer == QMessageBox.Yes

        # ---- backend helpers ----

        def _remember_config(self, cfg: Any) -> None:
            self._config_cache = cfg
            self._config_cache_ts = time.monotonic()

        def _get_cached_or_loaded_config(self, backend: Any, srv: Any) -> Any:
            now = time.monotonic()

            cfg = None
            if srv is not None:
                try:
                    cfg = srv.runtime.config
                except Exception:
                    cfg = None
            if cfg is not None:
                self._remember_config(cfg)
                return cfg

            if (
                self._config_cache is not None
                and (now - float(self._config_cache_ts)) < float(self._config_cache_ttl_sec)
            ):
                return self._config_cache

            try:
                cfg = backend.panel_config.load_config(backend.base_dir)
            except Exception:
                cfg = None
            if cfg is not None:
                self._remember_config(cfg)
            return cfg

        def _get_cached_autostart_enabled(self, backend: Any) -> bool:
            now = time.monotonic()
            if (
                self._autostart_cache_value is None
                or (now - float(self._autostart_cache_ts)) >= float(self._autostart_cache_ttl_sec)
            ):
                try:
                    self._autostart_cache_value = bool(backend._load_autostart_setting())
                except Exception:
                    self._autostart_cache_value = False
                self._autostart_cache_ts = now
            return bool(self._autostart_cache_value)

        def _load_backend(self) -> None:
            try:
                self._backend = _get_backend()
                self._backend_error = None
            except Exception as e:
                self._backend = None
                self._backend_error = e
                self._set_status_msg(f"Ошибка импорта модуля веб-панели: {e}")
                try:
                    self.status_group.setEnabled(False)
                    self.settings_group.setEnabled(False)
                    self.password_group.setEnabled(False)
                    self.logs_group.setEnabled(False)
                except Exception:
                    pass

        def _format_error(self, action: str, exc: Exception) -> str:
            if self._backend is not None:
                try:
                    return self._backend._format_error(action, exc)
                except Exception:
                    pass
            detail = str(exc).strip() or repr(exc)
            return f"Не удалось {action}. Ошибка: {detail}"

        def _set_status_msg(self, text: str) -> None:
            try:
                self.status_msg.setText(text)
            except Exception:
                pass

        def _require_backend(self) -> bool:
            if self._backend is None:
                msg = "Модуль управления веб-панелью недоступен."
                if self._backend_error:
                    msg += f" Ошибка: {self._backend_error}"
                self._set_status_msg(msg)
                return False
            return True

        def _get_server(self):
            if not self._require_backend():
                return None
            try:
                return self._backend._ensure_server()
            except Exception as e:
                msg = self._format_error("инициализировать веб-панель", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return None

        def _get_existing_server(self):
            if not self._require_backend():
                return None
            try:
                return getattr(self._backend, "_server", None)
            except Exception:
                return None

        # ---- refresh ----

        def _refresh_status_soft(self) -> None:
            try:
                if not self.isVisible():
                    return
            except Exception:
                pass
            try:
                self._refresh_status()
            except Exception:
                pass

        def _refresh_logs_soft(self) -> None:
            try:
                if not self.isVisible():
                    return
            except Exception:
                pass
            try:
                self._refresh_logs(silent=True)
            except Exception:
                pass

        def _refresh_status(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend

            running = False
            try:
                running = bool(backend._is_panel_running())
            except Exception:
                running = False
            restart_in_progress = bool(getattr(backend, "_restart_in_progress", False) or self._restart_busy)

            srv = getattr(backend, "_server", None)
            cfg = self._get_cached_or_loaded_config(backend, srv)

            host = str(cfg.host) if cfg is not None else ""
            port = str(cfg.port) if cfg is not None else ""

            url = ""
            if running and srv is not None:
                try:
                    url = srv.url()
                except Exception:
                    url = ""

            if restart_in_progress:
                status_text = "Перезапускается"
            else:
                status_text = "Запущена" if running else "Остановлена"

            # URL показываем даже если панель ещё не запущена (по конфигу), чтобы было понятно, куда заходить.
            planned_url = ""
            if host and port:
                planned_url = f"http://{host}:{port}"
                # если host=0.0.0.0, открывать в браузере лучше через 127.0.0.1 или реальный IP
                if host.strip() == "0.0.0.0":
                    planned_url = f"http://127.0.0.1:{port}"

            shown_url = url or planned_url or ""
            rendered = (
                status_text,
                (shown_url or "—"),
                (host or "—"),
                (port or "—"),
                bool(running),
                bool(restart_in_progress),
            )
            if rendered != self._status_last_render:
                self._status_last_render = rendered
                try:
                    self.status_value.setAccessibleName(f"Статус панели: {rendered[0]}")
                except Exception:
                    pass
                self.status_value.setText(rendered[0])
                self.url_value.setText(rendered[1])
                self.host_value.setText(rendered[2])
                self.port_value.setText(rendered[3])
                self.toggle_btn.setText("Остановить панель" if running else "Запустить панель")
                if restart_in_progress:
                    self.restart_btn.setText("Перезапускается...")
                else:
                    self.restart_btn.setText("Перезапустить панель")
                self.toggle_btn.setEnabled(not restart_in_progress)
                self.restart_btn.setEnabled(bool(running) and (not restart_in_progress))
                self.open_btn.setEnabled(bool(running) and (not restart_in_progress))
                try:
                    self.toggle_btn.setAccessibleName(self.toggle_btn.text())
                except Exception:
                    pass
                try:
                    self.restart_btn.setAccessibleName(self.restart_btn.text())
                except Exception:
                    pass
                try:
                    self.open_btn.setAccessibleName(self.open_btn.text())
                except Exception:
                    pass

            enabled = self._get_cached_autostart_enabled(backend)

            self._updating = True
            try:
                # Важно: не дергаем сигнал, если значение уже такое же.
                if bool(self.autostart_cb.isChecked()) != bool(enabled):
                    self.autostart_cb.setChecked(bool(enabled))
            finally:
                self._updating = False

        def _load_config(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend
            try:
                cfg = backend.panel_config.load_config(backend.base_dir)
            except Exception as e:
                msg = self._format_error("загрузить настройки веб-панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            self._remember_config(cfg)
            self._updating = True
            try:
                self.host_edit.setText(str(cfg.host))
                self.port_spin.setValue(int(cfg.port))
                self.debug_cb.setChecked(bool(cfg.debug))
                self.retention_spin.setValue(int(cfg.retention_days))
            finally:
                self._updating = False

        def _set_logs_timer_interval(self, interval_ms: int) -> None:
            try:
                timer = getattr(self, "_logs_timer", None)
                if timer is None:
                    return
                next_interval = max(400, int(interval_ms))
                if int(timer.interval()) != next_interval:
                    timer.setInterval(next_interval)
            except Exception:
                pass

        def _is_logs_reader_active(self) -> bool:
            try:
                if not bool(self.logs_view.hasFocus()):
                    return False
            except Exception:
                return False

            has_sel = False
            try:
                has_sel = bool(self.logs_view.textCursor().hasSelection())
            except Exception:
                has_sel = False

            at_bottom = True
            try:
                sb = self.logs_view.verticalScrollBar()
                if sb is not None:
                    at_bottom = (int(sb.value()) + int(sb.pageStep())) >= (int(sb.maximum()) - 2)
            except Exception:
                at_bottom = True
            return has_sel or (not at_bottom)

        def _refresh_logs(self, silent: bool = False) -> None:
            """Обновляет окно логов.

            silent=True: без всплывающих окон (используется таймером).
            Важно: не крадём фокус и не сбиваем чтение.
            """
            if not self._require_backend():
                return
            backend = self._backend
            if silent and self._is_logs_reader_active():
                try:
                    self.logs_note.setText("Обновление логов: приостановлено, пока вы читаете.")
                except Exception:
                    pass
                self._set_logs_timer_interval(self._logs_poll_reader_ms)
                return
            self._set_logs_timer_interval(self._logs_poll_ms)
            try:
                self.logs_note.setText("Обновление логов: автоматически.")
            except Exception:
                pass
            text, error = backend._tail_db_logs(backend.base_dir, limit=120)

            if error:
                err_text = str(error).strip() or repr(error)
                if self._logs_last_error != err_text:
                    self._logs_last_error = err_text
                    self._set_status_msg(f"Не удалось получить логи: {err_text}")
                    if not silent:
                        self._popup_text("Веб-панель", f"Не удалось получить логи: {err_text}")
                new_text = f"Не удалось получить логи. Ошибка: {err_text}"
            else:
                self._logs_last_error = None
                new_text = text if text else "Логи пустые."

            if new_text == self._logs_last_text:
                return
            self._logs_last_text = new_text

            # Если пользователь читает середину/выделяет текст, сохраняем позицию.
            focus = False
            try:
                focus = bool(self.logs_view.hasFocus())
            except Exception:
                focus = False

            sb = None
            try:
                sb = self.logs_view.verticalScrollBar()
            except Exception:
                sb = None

            at_bottom = True
            sb_value = 0
            try:
                if sb is not None:
                    sb_value = int(sb.value())
                    at_bottom = (sb_value + int(sb.pageStep())) >= (int(sb.maximum()) - 2)
            except Exception:
                at_bottom = True

            anchor = 0
            pos = 0
            has_sel = False
            try:
                cur = self.logs_view.textCursor()
                anchor = int(cur.anchor())
                pos = int(cur.position())
                has_sel = bool(cur.hasSelection())
            except Exception:
                pass

            try:
                self.logs_view.setUpdatesEnabled(False)
            except Exception:
                pass
            try:
                self.logs_view.setPlainText(new_text)
            finally:
                try:
                    self.logs_view.setUpdatesEnabled(True)
                except Exception:
                    pass

            try:
                if sb is not None:
                    if focus and (not at_bottom or has_sel):
                        sb.setValue(min(sb_value, sb.maximum()))
                        doc_len = int(self.logs_view.document().characterCount())
                        if doc_len > 0:
                            a = max(0, min(anchor, doc_len - 1))
                            p = max(0, min(pos, doc_len - 1))
                            cur2 = self.logs_view.textCursor()
                            cur2.setPosition(a)
                            cur2.setPosition(p, QTextCursor.KeepAnchor)
                            self.logs_view.setTextCursor(cur2)
                    else:
                        sb.setValue(sb.maximum())
                        if focus:
                            cur3 = self.logs_view.textCursor()
                            cur3.movePosition(QTextCursor.End)
                            self.logs_view.setTextCursor(cur3)
            except Exception:
                pass

        # ---- actions ----

        def _on_autostart_toggled(self, checked: bool) -> None:
            if self._updating:
                return
            if not self._require_backend():
                return
            try:
                self._backend._set_autostart_setting(
                    bool(checked),
                    actor="gui",
                    source="gui",
                )
            except Exception as e:
                msg = self._format_error("изменить автозапуск веб-панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            running = False
            try:
                running = bool(self._backend._is_panel_running())
            except Exception:
                running = False

            note = (
                "Панель сейчас запущена (это её не останавливает)."
                if running
                else "Панель сейчас остановлена (это её не запускает)."
            )
            msg = ("Автозапуск включён. " if checked else "Автозапуск выключен. ") + note
            self._set_status_msg(msg)
            self._autostart_cache_value = bool(checked)
            self._autostart_cache_ts = time.monotonic()
            _try_log(self._log_func, f"[GUI][WEBPANEL] autostart={'on' if checked else 'off'}", self._backend)

        def _on_toggle_panel(self) -> None:
            if self._restart_busy or bool(getattr(self._backend, "_restart_in_progress", False)):
                self._set_status_msg("Панель перезапускается. Дождитесь завершения операции.")
                self._refresh_status()
                return

            srv = self._get_server()
            if srv is None:
                return

            running = False
            try:
                running = bool(self._backend._is_panel_running())
            except Exception:
                running = False

            if running:
                try:
                    srv.stop()
                    self._backend._safe_audit(srv, "gui", "panel_stop", True, source="gui")
                    self._set_status_msg("Панель остановлена.")
                    _try_log(self._log_func, "[GUI][WEBPANEL] panel stopped", self._backend)
                except Exception as e:
                    msg = self._format_error("остановить веб-панель", e)
                    self._set_status_msg(msg)
                    self._popup_text("Веб-панель", msg)
            else:
                if not self._ensure_panel_start_ready(interactive=True):
                    self._refresh_status()
                    return
                try:
                    srv.start()
                    self._backend._safe_audit(srv, "gui", "panel_start", True, source="gui")
                    text = f"Панель запущена. Адрес: {srv.url()}"
                    self._set_status_msg(text)
                    _try_log(self._log_func, "[GUI][WEBPANEL] panel started", self._backend)
                except Exception as e:
                    msg = self._format_error("запустить веб-панель", e)
                    self._set_status_msg(msg)
                    self._popup_text("Веб-панель", msg)

            self._refresh_status()

        def _on_restart_panel(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend

            if self._restart_busy or bool(getattr(backend, "_restart_in_progress", False)):
                text = "Панель уже перезапускается. Подождите завершения операции."
                self._set_status_msg(text)
                self._refresh_status()
                return

            running = False
            try:
                running = bool(backend._is_panel_running())
            except Exception:
                running = False
            if not running:
                text = "Панель не запущена. Сначала запустите панель."
                self._set_status_msg(text)
                self._refresh_status()
                return

            self._restart_busy = True
            self._set_status_msg("Панель перезапускается. Подождите несколько секунд...")
            self._refresh_status()
            try:
                ok, text = backend.restart_panel_sync(actor="gui", source="gui")
            except Exception as e:
                ok = False
                text = self._format_error("перезапустить веб-панель", e)
            finally:
                self._restart_busy = False

            if ok:
                self._set_status_msg(text)
                _try_log(self._log_func, "[GUI][WEBPANEL] panel restarted", backend)
            else:
                self._set_status_msg(text)
                self._popup_text("Веб-панель", text)
                _try_log(self._log_func, f"[GUI][WEBPANEL] panel restart failed: {text}", backend)

            self._refresh_status()

        def _get_openable_url(self, srv: Any) -> str:
            """URL, который удобно открывать в локальном браузере."""
            try:
                url = (srv.url() or "").strip()
            except Exception:
                url = ""

            if not url:
                try:
                    cfg = srv.runtime.config
                    host = str(getattr(cfg, "host", "") or "").strip()
                    port = str(getattr(cfg, "port", "") or "").strip()
                    if host and port:
                        url = f"http://{host}:{port}"
                except Exception:
                    url = ""

            # 0.0.0.0 не открывается как клиентский адрес; для локального ПК подставляем localhost.
            if url.startswith("http://0.0.0.0:"):
                url = "http://127.0.0.1:" + url.split(":", 2)[2]
            elif url.startswith("https://0.0.0.0:"):
                url = "https://127.0.0.1:" + url.split(":", 2)[2]
            return url

        def _on_open_panel(self) -> None:
            srv = self._get_server()
            if srv is None:
                return

            running = False
            try:
                running = bool(self._backend._is_panel_running())
            except Exception:
                running = False

            if not running:
                msg = "Панель сейчас не запущена. Сначала нажмите «Запустить панель»."
                self._set_status_msg(msg)
                self._popup_text("Открыть панель", msg)
                self._refresh_status()
                return

            url = self._get_openable_url(srv)
            if not url:
                msg = "Не удалось определить адрес панели."
                self._set_status_msg(msg)
                self._popup_text("Открыть панель", msg)
                return

            try:
                opened = bool(webbrowser.open(url, new=2))
            except Exception as e:
                msg = self._format_error("открыть веб-панель в браузере", e)
                self._set_status_msg(msg)
                self._popup_text("Открыть панель", msg)
                return

            if opened:
                self._set_status_msg(f"Открываю панель в браузере: {url}")
            else:
                self._set_status_msg(f"Не удалось открыть браузер автоматически. Адрес панели: {url}")
                self._popup_text("Открыть панель", f"Не удалось открыть браузер автоматически.\nАдрес панели: {url}")

            _try_log(self._log_func, f"[GUI][WEBPANEL] open panel url={url}", self._backend)

        def _on_show_url(self) -> None:
            srv = self._get_server()
            if srv is None:
                return
            try:
                url = srv.url()
            except Exception as e:
                msg = self._format_error("получить адрес веб-панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            running = False
            try:
                running = bool(self._backend._is_panel_running())
            except Exception:
                running = False

            status = "Панель запущена." if running else "Панель не запущена."
            self._popup_text("Адрес панели", f"{status}\nURL: {url}")
            _try_log(self._log_func, f"[GUI][WEBPANEL] show panel url={url}", self._backend)

        def _on_show_api(self) -> None:
            text = (
                "Ключевые эндпоинты:\n"
                "- /api/health\n"
                "- /api/overview, /api/metrics?minutes=...\n"
                "- /api/processes, /api/services, /api/network\n"
                "- /api/logs/tail, /api/windows/events\n"
                "- /api/alerts (GET), /api/alerts/mute/<id>\n"
                "- /api/autocraft/status, /api/autocraft/plugins, /api/autocraft/logs\n"
                "- /api/audit, /api/settings (GET/PUT), /api/actions/diagnostic-bundle\n"
                "Авторизация: Bearer-токен после /api/login или X-Panel-Token."
            )
            self._popup_text("API панели", text)
            _try_log(self._log_func, "[GUI][WEBPANEL] show api help", self._backend)

        def _load_panel_roles(self) -> list:
            if not self._require_backend():
                return []
            backend = self._backend
            try:
                roles = backend.panel_config.list_panel_roles(backend.base_dir)
            except Exception:
                roles = []
            cleaned = []
            for item in roles or []:
                role_name = str(item or "").strip()
                if role_name and role_name not in cleaned:
                    cleaned.append(role_name)
            if not cleaned:
                cleaned = ["Super Admin", "Admin", "Operator", "Viewer", "Auditor"]
            return cleaned

        def _load_panel_users(self) -> list:
            if not self._require_backend():
                return []
            backend = self._backend
            return backend.panel_config.list_panel_users(backend.base_dir)

        def _get_panel_bootstrap_state(self) -> dict:
            if not self._require_backend():
                return {}
            backend = self._backend
            getter = getattr(backend.panel_config, "get_panel_bootstrap_state", None)
            if callable(getter):
                payload = getter(backend.base_dir)
                if isinstance(payload, dict):
                    return payload
            users = self._load_panel_users()
            super_admin_users = []
            for item in users:
                roles = [str(role).strip() for role in (item.get("roles") or []) if str(role).strip()]
                if "Super Admin" in roles:
                    super_admin_users.append(str(item.get("username") or "").strip())
            return {
                "has_users": bool(users),
                "has_super_admin": bool(super_admin_users),
                "super_admin_users": super_admin_users,
                "user_count": len(users),
            }

        def _get_panel_start_block_reason(self) -> str:
            if not self._require_backend():
                return "Панель недоступна: backend не инициализирован."
            backend = self._backend
            getter = getattr(backend.panel_config, "get_panel_start_block_reason", None)
            if callable(getter):
                return str(getter(backend.base_dir) or "").strip()
            state = self._get_panel_bootstrap_state()
            if not bool(state.get("has_users")):
                return (
                    "Панель не может быть запущена: не найдено ни одного пользователя. "
                    "Создайте первого пользователя с ролью Super Admin."
                )
            if not bool(state.get("has_super_admin")):
                return (
                    "Панель не может быть запущена: отсутствует пользователь с ролью Super Admin. "
                    "Создайте первого Super Admin или назначьте роль существующему пользователю."
                )
            return ""

        def _on_create_first_super_admin(self) -> str:
            if not self._require_backend():
                return ""
            backend = self._backend

            dlg = QDialog(self)
            dlg.setWindowTitle("Первый запуск: создать Super Admin")
            dlg.setModal(True)
            form = QGridLayout(dlg)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(8)

            info = QLabel(
                "Для запуска панели нужно создать первого пользователя с ролью Super Admin."
            )
            info.setWordWrap(True)
            form.addWidget(info, 0, 0, 1, 4)

            login_edit = QLineEdit("admin")
            password_edit = QLineEdit()
            password_edit.setEchoMode(QLineEdit.Password)
            password2_edit = QLineEdit()
            password2_edit.setEchoMode(QLineEdit.Password)
            role_value = QLabel("Super Admin")

            show_password_cb = QCheckBox("Показать пароль")
            def _set_password_visibility(checked: bool) -> None:
                mode = QLineEdit.Normal if checked else QLineEdit.Password
                password_edit.setEchoMode(mode)
                password2_edit.setEchoMode(mode)
            show_password_cb.toggled.connect(_set_password_visibility)

            lbl_login = QLabel("Логин:")
            lbl_login.setBuddy(login_edit)
            lbl_pass = QLabel("Пароль:")
            lbl_pass.setBuddy(password_edit)
            lbl_pass2 = QLabel("Повторите пароль:")
            lbl_pass2.setBuddy(password2_edit)

            form.addWidget(lbl_login, 1, 0)
            form.addWidget(login_edit, 1, 1, 1, 3)
            form.addWidget(QLabel("Роль:"), 2, 0)
            form.addWidget(role_value, 2, 1, 1, 3)
            form.addWidget(lbl_pass, 3, 0)
            form.addWidget(password_edit, 3, 1, 1, 3)
            form.addWidget(lbl_pass2, 4, 0)
            form.addWidget(password2_edit, 4, 1, 1, 3)
            form.addWidget(show_password_cb, 5, 1, 1, 3)

            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Save).setText("Создать Super Admin")
            buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            form.addWidget(buttons, 6, 0, 1, 4)

            while True:
                try:
                    accepted = bool(dlg.exec_())
                except Exception:
                    accepted = bool(dlg.exec())
                if not accepted:
                    return ""

                username = (login_edit.text() or "").strip() or "admin"
                user_password = (password_edit.text() or "").strip()
                user_password2 = (password2_edit.text() or "").strip()

                if len(username) < 3 or (" " in username):
                    self._popup_text("Пользователи", "Логин должен быть от 3 символов и без пробелов.")
                    continue
                if len(user_password) < 6:
                    self._popup_text("Пользователи", "Пароль должен быть минимум 6 символов.")
                    continue
                if user_password != user_password2:
                    self._popup_text("Пользователи", "Пароли не совпадают.")
                    continue

                try:
                    backend.panel_config.create_panel_user(
                        backend.base_dir,
                        username,
                        username,
                        user_password,
                        "Super Admin",
                    )
                    srv = self._get_existing_server()
                    if srv is not None:
                        backend._safe_audit(
                            srv,
                            "gui",
                            "create_first_super_admin_via_gui",
                            True,
                            source="gui",
                            target=username,
                        )
                except Exception as e:
                    try:
                        srv = self._get_existing_server()
                        if srv is not None:
                            backend._safe_audit(
                                srv,
                                "gui",
                                "create_first_super_admin_via_gui",
                                False,
                                source="gui",
                                target=username,
                                details=str(e),
                            )
                    except Exception:
                        pass
                    msg = self._format_error("создать первого Super Admin", e)
                    self._set_status_msg(msg)
                    self._popup_text("Пользователи", msg)
                    continue

                self._set_status_msg(f"Создан первый Super Admin: {username}.")
                self._popup_text(
                    "Super Admin создан",
                    f"Логин: {username}\nПароль: {user_password}\nРоль: Super Admin",
                )
                _try_log(self._log_func, f"[GUI][WEBPANEL] first super admin created: {username}", backend)
                return username

        def _ensure_panel_start_ready(self, interactive: bool = True) -> bool:
            try:
                block_reason = self._get_panel_start_block_reason()
            except Exception as e:
                msg = self._format_error("проверить готовность запуска веб-панели", e)
                self._set_status_msg(msg)
                if interactive:
                    self._popup_text("Веб-панель", msg)
                return False

            if not block_reason:
                return True

            self._set_status_msg(block_reason)
            if not interactive:
                return False

            if not self._ask_yes_no(
                "Первый запуск веб-панели",
                f"{block_reason}\n\nСоздать первого Super Admin сейчас?",
                default_yes=True,
            ):
                return False

            created_username = self._on_create_first_super_admin()
            if not created_username:
                return False
            try:
                return not bool(self._get_panel_start_block_reason())
            except Exception:
                return False

        def _on_add_user(self) -> str:
            if not self._require_backend():
                return ""
            backend = self._backend
            roles = self._load_panel_roles()
            if not roles:
                self._popup_text("Пользователи", "Не удалось загрузить роли пользователей.")
                return ""

            dlg = QDialog(self)
            dlg.setWindowTitle("Добавить пользователя")
            dlg.setModal(True)
            form = QGridLayout(dlg)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(8)

            login_edit = QLineEdit()
            name_edit = QLineEdit()
            password_edit = QLineEdit()
            password_edit.setEchoMode(QLineEdit.Password)
            role_combo = QComboBox()
            role_combo.addItems(roles)
            preferred_role = "Viewer"
            try:
                state = self._get_panel_bootstrap_state()
                if not bool(state.get("has_super_admin")):
                    preferred_role = "Super Admin"
            except Exception:
                preferred_role = "Viewer"
            for idx, role_name in enumerate(roles):
                if role_name == preferred_role:
                    role_combo.setCurrentIndex(idx)
                    break

            show_password_cb = QCheckBox("Показать пароль")
            show_password_cb.toggled.connect(
                lambda checked: password_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
            )

            lbl_login = QLabel("Логин:")
            lbl_login.setBuddy(login_edit)
            lbl_name = QLabel("Имя:")
            lbl_name.setBuddy(name_edit)
            lbl_pass = QLabel("Пароль:")
            lbl_pass.setBuddy(password_edit)
            lbl_role = QLabel("Роль:")
            lbl_role.setBuddy(role_combo)

            form.addWidget(lbl_login, 0, 0)
            form.addWidget(login_edit, 0, 1, 1, 3)
            form.addWidget(lbl_name, 1, 0)
            form.addWidget(name_edit, 1, 1, 1, 3)
            form.addWidget(lbl_pass, 2, 0)
            form.addWidget(password_edit, 2, 1, 1, 3)
            form.addWidget(show_password_cb, 3, 1, 1, 3)
            form.addWidget(lbl_role, 4, 0)
            form.addWidget(role_combo, 4, 1, 1, 3)

            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Save).setText("Сохранить")
            buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            form.addWidget(buttons, 5, 0, 1, 4)

            while True:
                try:
                    accepted = bool(dlg.exec_())
                except Exception:
                    accepted = bool(dlg.exec())
                if not accepted:
                    return ""

                username = (login_edit.text() or "").strip()
                display_name = (name_edit.text() or "").strip()
                user_password = (password_edit.text() or "").strip()
                role_name = (role_combo.currentText() or "").strip()

                if len(username) < 3 or (" " in username):
                    self._popup_text("Пользователи", "Логин должен быть от 3 символов и без пробелов.")
                    continue
                if not display_name:
                    self._popup_text("Пользователи", "Имя пользователя не может быть пустым.")
                    continue
                if len(user_password) < 6:
                    self._popup_text("Пользователи", "Пароль должен быть минимум 6 символов.")
                    continue
                if not role_name:
                    self._popup_text("Пользователи", "Выберите роль пользователя.")
                    continue

                try:
                    backend.panel_config.create_panel_user(
                        backend.base_dir,
                        username,
                        display_name,
                        user_password,
                        role_name,
                    )
                    srv = self._get_existing_server()
                    if srv is not None:
                        backend._safe_audit(
                            srv,
                            "gui",
                            "create_user_via_gui",
                            True,
                            source="gui",
                            target=username,
                        )
                except Exception as e:
                    try:
                        srv = self._get_existing_server()
                        if srv is not None:
                            backend._safe_audit(
                                srv,
                                "gui",
                                "create_user_via_gui",
                                False,
                                source="gui",
                                target=username,
                                details=str(e),
                            )
                    except Exception:
                        pass
                    msg = self._format_error("добавить пользователя панели", e)
                    self._set_status_msg(msg)
                    self._popup_text("Пользователи", msg)
                    continue

                self._set_status_msg(f"Пользователь {username} добавлен.")
                self._popup_text(
                    "Пользователь добавлен",
                    f"Логин: {username}\nПароль: {user_password}\nРоль: {role_name}",
                )
                _try_log(self._log_func, f"[GUI][WEBPANEL] user created: {username}", backend)
                return username

        def _on_open_user_manager(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend

            dlg = QDialog(self)
            dlg.setWindowTitle("Менеджер пользователей")
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(12, 12, 12, 10)
            layout.setSpacing(8)

            users_hint = QLabel(
                "Выберите пользователя стрелками в таблице. Действия доступны кнопками ниже "
                "или через контекстное меню (ПКМ, клавиша контекста, Shift+F10)."
            )
            users_hint.setWordWrap(True)
            layout.addWidget(users_hint)

            table = QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["Логин", "Имя", "Роли", "Пароль"])
            try:
                table.setSelectionBehavior(QAbstractItemView.SelectRows)
                table.setSelectionMode(QAbstractItemView.SingleSelection)
                table.setEditTriggers(QAbstractItemView.NoEditTriggers)
                table.setTabKeyNavigation(False)
                table.setContextMenuPolicy(Qt.CustomContextMenu)
                table.setFocusPolicy(Qt.StrongFocus)
                table.setAccessibleName("Список пользователей веб-панели")
                table.setAccessibleDescription(
                    "Таблица пользователей. Стрелками выбор пользователя. "
                    "Tab переводит фокус на кнопки управления."
                )
            except Exception:
                pass
            layout.addWidget(table)

            buttons_row1 = QHBoxLayout()
            add_btn = QPushButton("Добавить пользователей")
            refresh_btn = QPushButton("Обновить")
            show_btn = QPushButton("Показать логин/пароль")
            change_btn = QPushButton("Сменить пароль")
            role_btn = QPushButton("Сменить роль")
            buttons_row1.addWidget(add_btn)
            buttons_row1.addWidget(refresh_btn)
            buttons_row1.addWidget(show_btn)
            buttons_row1.addWidget(change_btn)
            buttons_row1.addWidget(role_btn)
            buttons_row1.addStretch(1)
            layout.addLayout(buttons_row1)

            buttons_row2 = QHBoxLayout()
            copy_login_btn = QPushButton("Копировать логин")
            delete_btn = QPushButton("Удалить")
            close_btn = QPushButton("Закрыть")
            buttons_row2.addWidget(copy_login_btn)
            buttons_row2.addWidget(delete_btn)
            buttons_row2.addStretch(1)
            buttons_row2.addWidget(close_btn)
            layout.addLayout(buttons_row2)

            for btn, title in (
                (add_btn, "Добавить пользователей"),
                (refresh_btn, "Обновить список пользователей"),
                (show_btn, "Показать логин и пароль"),
                (change_btn, "Сменить пароль"),
                (role_btn, "Сменить роль"),
                (copy_login_btn, "Копировать логин"),
                (delete_btn, "Удалить пользователя"),
                (close_btn, "Закрыть менеджер пользователей"),
            ):
                try:
                    btn.setAccessibleName(title)
                    btn.setFocusPolicy(Qt.StrongFocus)
                except Exception:
                    pass

            try:
                refresh_btn.setShortcut("F5")
                add_btn.setShortcut("Ctrl+N")
                show_btn.setShortcut("Ctrl+I")
                change_btn.setShortcut("Ctrl+P")
                role_btn.setShortcut("Ctrl+R")
                delete_btn.setShortcut("Del")
            except Exception:
                pass

            state = {"users": []}

            def selected_row_index() -> int:
                try:
                    return int(table.currentRow())
                except Exception:
                    return -1

            def selected_user() -> dict:
                row_idx = selected_row_index()
                if row_idx < 0:
                    return {}
                users = state.get("users") or []
                if row_idx >= len(users):
                    return {}
                payload = users[row_idx]
                if isinstance(payload, dict):
                    return payload
                return {}

            def selected_username() -> str:
                row_idx = selected_row_index()
                if row_idx < 0:
                    return ""
                item = table.item(row_idx, 0)
                if item is None:
                    return ""
                return str(item.text() or "").strip()

            def selected_roles() -> list:
                payload = selected_user()
                roles_raw = payload.get("roles") if isinstance(payload, dict) else []
                return [str(x).strip() for x in (roles_raw or []) if str(x).strip()]

            def select_row_by_username(username: str) -> None:
                if table.rowCount() <= 0:
                    return
                target = str(username or "").strip()
                target_row = 0
                if target:
                    for idx in range(table.rowCount()):
                        cell = table.item(idx, 0)
                        if cell and str(cell.text() or "").strip() == target:
                            target_row = idx
                            break
                try:
                    table.setCurrentCell(target_row, 0)
                    table.selectRow(target_row)
                except Exception:
                    pass

            def update_actions_state() -> None:
                has_user = bool(selected_username())
                for btn in (show_btn, change_btn, role_btn, copy_login_btn, delete_btn):
                    try:
                        btn.setEnabled(has_user)
                    except Exception:
                        pass

            def refresh_users(preferred_username: str = "") -> bool:
                keep_username = str(preferred_username or "").strip() or selected_username()
                try:
                    users = self._load_panel_users()
                except Exception as e:
                    self._popup_text("Пользователи", self._format_error("получить список пользователей", e))
                    return False
                state["users"] = users or []
                table.setRowCount(len(state["users"]))
                for row_idx, item in enumerate(state["users"]):
                    username = str(item.get("username") or "")
                    name = str(item.get("name") or username)
                    roles = ", ".join([str(x) for x in (item.get("roles") or []) if str(x).strip()])
                    pwd_state = str(item.get("password_state") or "").strip().lower()
                    if pwd_state == "saved":
                        pwd_flag = "сохранён"
                    elif pwd_state == "hash_only":
                        pwd_flag = "только хэш"
                    elif pwd_state == "missing":
                        pwd_flag = "не задан"
                    else:
                        pwd_flag = "сохранён" if bool(item.get("password_saved")) else "не сохранён"
                    table.setItem(row_idx, 0, QTableWidgetItem(username))
                    table.setItem(row_idx, 1, QTableWidgetItem(name))
                    table.setItem(row_idx, 2, QTableWidgetItem(roles))
                    table.setItem(row_idx, 3, QTableWidgetItem(pwd_flag))
                if table.rowCount() > 0:
                    select_row_by_username(keep_username)
                try:
                    table.resizeColumnsToContents()
                except Exception:
                    pass
                update_actions_state()
                return True

            def on_refresh() -> None:
                if refresh_users(selected_username()):
                    self._set_status_msg("Список пользователей обновлён.")

            def on_add_users() -> None:
                current_username = selected_username()
                created_username = str(self._on_add_user() or "").strip()
                refresh_users(created_username or current_username)
                try:
                    table.setFocus(Qt.OtherFocusReason)
                except Exception:
                    pass

            def on_show_credentials() -> None:
                username = selected_username()
                if not username:
                    self._popup_text("Пользователи", "Сначала выберите пользователя в списке.")
                    return
                try:
                    payload = backend.panel_config.get_panel_user_credentials(backend.base_dir, username)
                except Exception as e:
                    self._popup_text("Пользователи", self._format_error("получить логин и пароль пользователя", e))
                    return
                user_password = str(payload.get("password") or "").strip()
                password_state = str(payload.get("password_state") or "").strip().lower()
                if not user_password:
                    if password_state == "hash_only":
                        user_password = "(в БД хранится только хэш, текущий пароль нельзя показать; задайте новый через «Сменить пароль»)"
                    else:
                        user_password = "(пароль не задан или не сохранён в открытом виде; задайте новый через «Сменить пароль»)"
                self._popup_text(
                    "Учётные данные пользователя",
                    f"Логин: {payload.get('username')}\nПароль: {user_password}",
                )

            def on_change_password() -> None:
                username = selected_username()
                if not username:
                    self._popup_text("Пользователи", "Сначала выберите пользователя в списке.")
                    return
                pass_dlg = QDialog(dlg)
                pass_dlg.setWindowTitle(f"Смена пароля: {username}")
                pass_layout = QGridLayout(pass_dlg)
                pass_edit = QLineEdit()
                pass_edit.setEchoMode(QLineEdit.Password)
                show_cb = QCheckBox("Показать пароль")
                show_cb.toggled.connect(
                    lambda checked: pass_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                )
                pass_layout.addWidget(QLabel("Новый пароль:"), 0, 0)
                pass_layout.addWidget(pass_edit, 0, 1)
                pass_layout.addWidget(show_cb, 1, 1)
                pass_buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
                pass_buttons.button(QDialogButtonBox.Save).setText("Сохранить")
                pass_buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
                pass_buttons.accepted.connect(pass_dlg.accept)
                pass_buttons.rejected.connect(pass_dlg.reject)
                pass_layout.addWidget(pass_buttons, 2, 0, 1, 2)
                try:
                    accepted = bool(pass_dlg.exec_())
                except Exception:
                    accepted = bool(pass_dlg.exec())
                if not accepted:
                    return
                new_password = (pass_edit.text() or "").strip()
                if len(new_password) < 6:
                    self._popup_text("Пользователи", "Пароль должен быть минимум 6 символов.")
                    return
                try:
                    backend.panel_config.set_panel_user_password(
                        backend.base_dir,
                        username,
                        new_password,
                    )
                    srv = self._get_existing_server()
                    if srv is not None:
                        backend._safe_audit(
                            srv,
                            "gui",
                            "change_user_password_via_gui",
                            True,
                            source="gui",
                            target=username,
                        )
                except Exception as e:
                    try:
                        srv = self._get_existing_server()
                        if srv is not None:
                            backend._safe_audit(
                                srv,
                                "gui",
                                "change_user_password_via_gui",
                                False,
                                source="gui",
                                target=username,
                                details=str(e),
                            )
                    except Exception:
                        pass
                    self._popup_text("Пользователи", self._format_error("сменить пароль пользователя", e))
                    return
                self._set_status_msg(f"Пароль пользователя {username} обновлён.")
                self._popup_text(
                    "Пароль обновлён",
                    f"Логин: {username}\nПароль: {new_password}",
                )
                refresh_users(username)

            def on_change_role() -> None:
                username = selected_username()
                if not username:
                    self._popup_text("Пользователи", "Сначала выберите пользователя в списке.")
                    return
                roles = self._load_panel_roles()
                if not roles:
                    self._popup_text("Пользователи", "Не удалось загрузить доступные роли.")
                    return

                role_dlg = QDialog(dlg)
                role_dlg.setWindowTitle(f"Смена роли: {username}")
                role_layout = QGridLayout(role_dlg)
                role_layout.setHorizontalSpacing(10)
                role_layout.setVerticalSpacing(8)

                role_info = QLabel(
                    "Выберите новую роль пользователя. Текущий набор ролей будет заменён выбранной ролью."
                )
                role_info.setWordWrap(True)
                role_layout.addWidget(role_info, 0, 0, 1, 2)

                role_layout.addWidget(QLabel("Новая роль:"), 1, 0)
                role_combo = QComboBox()
                role_combo.addItems(roles)
                role_layout.addWidget(role_combo, 1, 1)

                for role_name in selected_roles():
                    if role_name in roles:
                        role_combo.setCurrentText(role_name)
                        break
                if role_combo.currentIndex() < 0 and roles:
                    role_combo.setCurrentIndex(0)

                role_buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
                role_buttons.button(QDialogButtonBox.Save).setText("Сохранить")
                role_buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
                role_buttons.accepted.connect(role_dlg.accept)
                role_buttons.rejected.connect(role_dlg.reject)
                role_layout.addWidget(role_buttons, 2, 0, 1, 2)

                try:
                    accepted = bool(role_dlg.exec_())
                except Exception:
                    accepted = bool(role_dlg.exec())
                if not accepted:
                    return

                new_role = str(role_combo.currentText() or "").strip()
                if not new_role:
                    self._popup_text("Пользователи", "Выберите роль пользователя.")
                    return

                try:
                    set_role_func = getattr(backend.panel_config, "set_panel_user_role", None)
                    if not callable(set_role_func):
                        raise RuntimeError("Операция смены роли недоступна в текущей версии панели.")
                    try:
                        set_role_func(backend.base_dir, username, new_role, replace_existing=True)
                    except TypeError:
                        set_role_func(backend.base_dir, username, new_role)
                    srv = self._get_existing_server()
                    if srv is not None:
                        backend._safe_audit(
                            srv,
                            "gui",
                            "change_user_role_via_gui",
                            True,
                            source="gui",
                            target=username,
                            details=f"role={new_role}",
                        )
                except Exception as e:
                    try:
                        srv = self._get_existing_server()
                        if srv is not None:
                            backend._safe_audit(
                                srv,
                                "gui",
                                "change_user_role_via_gui",
                                False,
                                source="gui",
                                target=username,
                                details=str(e),
                            )
                    except Exception:
                        pass
                    self._popup_text("Пользователи", self._format_error("сменить роль пользователя", e))
                    return

                self._set_status_msg(f"Роль пользователя {username} изменена на {new_role}.")
                refresh_users(username)

            def on_copy_login() -> None:
                username = selected_username()
                if not username:
                    self._popup_text("Пользователи", "Сначала выберите пользователя в списке.")
                    return
                try:
                    clipboard = QApplication.clipboard()
                    if clipboard is None:
                        raise RuntimeError("Буфер обмена недоступен.")
                    clipboard.setText(username)
                except Exception as e:
                    self._popup_text("Пользователи", self._format_error("скопировать логин пользователя", e))
                    return
                self._set_status_msg(f"Логин {username} скопирован в буфер обмена.")

            def on_delete_user() -> None:
                username = selected_username()
                if not username:
                    self._popup_text("Пользователи", "Сначала выберите пользователя в списке.")
                    return
                if not self._ask_yes_no(
                    "Удаление пользователя",
                    f"Удалить пользователя {username}?",
                    default_yes=False,
                    parent=dlg,
                ):
                    return
                try:
                    backend.panel_config.delete_panel_user(backend.base_dir, username)
                    srv = self._get_existing_server()
                    if srv is not None:
                        backend._safe_audit(
                            srv,
                            "gui",
                            "delete_user_via_gui",
                            True,
                            source="gui",
                            target=username,
                        )
                except Exception as e:
                    try:
                        srv = self._get_existing_server()
                        if srv is not None:
                            backend._safe_audit(
                                srv,
                                "gui",
                                "delete_user_via_gui",
                                False,
                                source="gui",
                                target=username,
                                details=str(e),
                            )
                    except Exception:
                        pass
                    self._popup_text("Пользователи", self._format_error("удалить пользователя", e))
                    return
                self._set_status_msg(f"Пользователь {username} удалён.")
                refresh_users()

            def context_anchor_global_pos():
                row_idx = selected_row_index()
                if row_idx >= 0:
                    item = table.item(row_idx, 0)
                    if item is not None:
                        rect = table.visualItemRect(item)
                        if rect.isValid():
                            return table.viewport().mapToGlobal(rect.center())
                return table.viewport().mapToGlobal(table.viewport().rect().center())

            def build_context_menu():
                menu = QMenu(dlg)
                add_action = menu.addAction("Добавить пользователей")
                menu.addSeparator()
                show_action = menu.addAction("Показать логин/пароль")
                change_password_action = menu.addAction("Сменить пароль")
                change_role_action = menu.addAction("Сменить роль")
                copy_login_action = menu.addAction("Копировать логин")
                menu.addSeparator()
                delete_action = menu.addAction("Удалить пользователя")
                menu.addSeparator()
                refresh_action = menu.addAction("Обновить список")

                has_user = bool(selected_username())
                show_action.setEnabled(has_user)
                change_password_action.setEnabled(has_user)
                change_role_action.setEnabled(has_user)
                copy_login_action.setEnabled(has_user)
                delete_action.setEnabled(has_user)

                add_action.triggered.connect(on_add_users)
                refresh_action.triggered.connect(on_refresh)
                show_action.triggered.connect(on_show_credentials)
                change_password_action.triggered.connect(on_change_password)
                change_role_action.triggered.connect(on_change_role)
                copy_login_action.triggered.connect(on_copy_login)
                delete_action.triggered.connect(on_delete_user)
                return menu

            def open_context_menu(global_pos=None) -> None:
                if not selected_username() and table.rowCount() > 0:
                    select_row_by_username("")
                menu = build_context_menu()
                target_pos = global_pos if global_pos is not None else context_anchor_global_pos()
                try:
                    menu.exec_(target_pos)
                except Exception:
                    menu.exec(target_pos)

            def on_custom_context_menu(pos) -> None:
                row_idx = int(table.rowAt(pos.y()))
                if row_idx >= 0:
                    try:
                        table.setCurrentCell(row_idx, 0)
                        table.selectRow(row_idx)
                    except Exception:
                        pass
                open_context_menu(table.viewport().mapToGlobal(pos))

            add_btn.clicked.connect(on_add_users)
            refresh_btn.clicked.connect(on_refresh)
            show_btn.clicked.connect(on_show_credentials)
            change_btn.clicked.connect(on_change_password)
            role_btn.clicked.connect(on_change_role)
            copy_login_btn.clicked.connect(on_copy_login)
            delete_btn.clicked.connect(on_delete_user)
            close_btn.clicked.connect(dlg.reject)
            table.customContextMenuRequested.connect(on_custom_context_menu)
            table.itemSelectionChanged.connect(update_actions_state)
            table.itemDoubleClicked.connect(lambda _item: on_show_credentials())

            menu_key_shortcut = QShortcut(Qt.Key_Menu, table)
            shift_f10_shortcut = QShortcut("Shift+F10", table)
            for shortcut in (menu_key_shortcut, shift_f10_shortcut):
                try:
                    shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                except Exception:
                    pass
                shortcut.activated.connect(lambda: open_context_menu())
            try:
                table._context_shortcuts = [menu_key_shortcut, shift_f10_shortcut]
            except Exception:
                pass

            try:
                dlg.setTabOrder(table, add_btn)
                dlg.setTabOrder(add_btn, refresh_btn)
                dlg.setTabOrder(refresh_btn, show_btn)
                dlg.setTabOrder(show_btn, change_btn)
                dlg.setTabOrder(change_btn, role_btn)
                dlg.setTabOrder(role_btn, copy_login_btn)
                dlg.setTabOrder(copy_login_btn, delete_btn)
                dlg.setTabOrder(delete_btn, close_btn)
            except Exception:
                pass

            refresh_users()
            update_actions_state()
            try:
                QTimer.singleShot(0, lambda: table.setFocus(Qt.OtherFocusReason))
            except Exception:
                pass
            try:
                dlg.exec_()
            except Exception:
                dlg.exec()

        # Совместимость со старыми именами обработчиков.
        def _on_show_credentials(self) -> None:
            self._on_open_user_manager()

        def _on_change_password(self) -> None:
            self._on_add_user()

        def _on_save_settings(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend

            try:
                cfg = backend.panel_config.load_config(backend.base_dir)
            except Exception as e:
                msg = self._format_error("загрузить настройки веб-панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            host = (self.host_edit.text() or "").strip() or cfg.host
            port = int(self.port_spin.value())
            retention = int(self.retention_spin.value())
            debug = bool(self.debug_cb.isChecked())

            cfg.host = host
            cfg.port = port
            cfg.retention_days = retention
            cfg.debug = debug

            try:
                backend.panel_config.save_config(backend.base_dir, cfg)
            except Exception as e:
                msg = self._format_error("сохранить настройки веб-панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            self._remember_config(cfg)
            srv_err: Optional[Exception] = None
            srv = None
            try:
                srv = backend._ensure_server()
            except Exception as e:
                srv_err = e

            if srv is not None:
                try:
                    srv.runtime.config = cfg
                except Exception:
                    pass
                backend._safe_audit(srv, "gui", "panel_settings_update", True, source="gui")

            self._set_status_msg("Настройки сохранены. Для применения перезапустите панель.")
            _try_log(self._log_func, "[GUI][WEBPANEL] settings saved", backend)

            if srv_err is not None:
                msg = self._format_error("применить настройки веб-панели", srv_err)
                self._popup_text("Веб-панель", msg)

    _WINDOW_CLASS = WebPanelWindow
    return _WINDOW_CLASS


# -------------------- открытие окна --------------------

_WINDOWS = weakref.WeakKeyDictionary()


def open_webpanel_window(main_window: Optional["QWidget"]) -> None:
    """Открыть (или активировать) окно управления веб-панелью."""
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
