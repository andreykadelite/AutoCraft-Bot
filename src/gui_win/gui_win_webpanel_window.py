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
    "Запуск/остановка веб-панели, смена пароля, настройки и просмотр логов."
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
    )

    _PYQT_CACHE = (
        Qt,
        QTimer,
        QTextCursor,
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

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(10)

            header = QLabel("Веб-панель AutoCraft")
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            layout.addWidget(header)

            info = QLabel("Запуск/остановка, пароль, настройки и логи веб-панели.")
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
            self.creds_btn = QPushButton("Показать логин/пароль")
            self.creds_btn.clicked.connect(self._on_show_credentials)
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

            # --- Пароль ---
            self.password_group = QGroupBox("Пароль администратора")
            password_layout = QGridLayout(self.password_group)
            password_layout.setHorizontalSpacing(12)
            password_layout.setVerticalSpacing(6)

            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setAccessibleName("Новый пароль (ввод)")

            lbl_newpass = QLabel("&Новый пароль:")
            lbl_newpass.setBuddy(self.password_edit)

            self.show_password_cb = QCheckBox("Показать пароль")
            self.show_password_cb.toggled.connect(self._on_show_password_toggled)

            password_layout.addWidget(lbl_newpass, 0, 0)
            password_layout.addWidget(self.password_edit, 0, 1, 1, 3)
            password_layout.addWidget(self.show_password_cb, 1, 1, 1, 3)

            self.change_pass_btn = QPushButton("Сменить пароль")
            self.change_pass_btn.clicked.connect(self._on_change_password)
            password_layout.addWidget(self.change_pass_btn, 2, 0, 1, 4)

            password_hint = QLabel("Минимум 6 символов.")
            password_layout.addWidget(password_hint, 3, 0, 1, 4)

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

                self.setTabOrder(self.reload_settings_btn, self.password_edit)
                self.setTabOrder(self.password_edit, self.show_password_cb)
                self.setTabOrder(self.show_password_cb, self.change_pass_btn)

                self.setTabOrder(self.change_pass_btn, self.logs_view)
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
            self._logs_timer.start(1100)

            self._status_timer = QTimer(self)
            self._status_timer.timeout.connect(self._refresh_status_soft)
            self._status_timer.start(900)

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

        def _on_show_password_toggled(self, checked: bool) -> None:
            try:
                self.password_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
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

        # ---- backend helpers ----

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

        # ---- refresh ----

        def _refresh_status_soft(self) -> None:
            try:
                self._refresh_status()
            except Exception:
                pass

        def _refresh_logs_soft(self) -> None:
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
            cfg = None
            if srv is not None:
                try:
                    cfg = srv.runtime.config
                except Exception:
                    cfg = None
            if cfg is None:
                try:
                    cfg = backend.panel_config.load_config(backend.base_dir)
                except Exception:
                    cfg = None

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

            try:
                enabled = backend._load_autostart_setting()
            except Exception:
                enabled = False

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

            self._updating = True
            try:
                self.host_edit.setText(str(cfg.host))
                self.port_spin.setValue(int(cfg.port))
                self.debug_cb.setChecked(bool(cfg.debug))
                self.retention_spin.setValue(int(cfg.retention_days))
            finally:
                self._updating = False

        def _refresh_logs(self, silent: bool = False) -> None:
            """Обновляет окно логов.

            silent=True: без всплывающих окон (используется таймером).
            Важно: не крадём фокус и не сбиваем чтение.
            """
            if not self._require_backend():
                return
            backend = self._backend
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
                try:
                    srv.start()
                    self._backend._safe_audit(srv, "gui", "panel_start", True, source="gui")
                    text = f"Панель запущена. Адрес: {srv.url()}"
                    if getattr(self._backend, "_last_generated_password", None):
                        text += f"\nПароль по умолчанию: {self._backend._last_generated_password}"
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

        def _on_show_credentials(self) -> None:
            if not self._require_backend():
                return
            backend = self._backend
            try:
                cfg = backend.panel_config.load_config(backend.base_dir)
            except Exception as e:
                msg = self._format_error("получить логин и пароль панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            login = cfg.admin_login or "admin"
            password = cfg.admin_password or getattr(backend, "_last_generated_password", "") or ""
            if not cfg.admin_password and getattr(backend, "_last_generated_password", None):
                cfg.admin_login = login
                cfg.admin_password = backend._last_generated_password
                try:
                    backend.panel_config.save_config(backend.base_dir, cfg)
                except Exception:
                    pass

            if not password:
                text = (
                    f"Логин панели: {login}\n"
                    "Пароль не сохранён. Смените пароль, чтобы сохранить его."
                )
            else:
                text = f"Логин панели: {login}\nПароль панели: {password}"
            self._popup_text("Логин и пароль", text)
            _try_log(self._log_func, "[GUI][WEBPANEL] show credentials", self._backend)

        def _on_change_password(self) -> None:
            if not self._require_backend():
                return
            new_password = (self.password_edit.text() or "").strip()
            if len(new_password) < 6:
                msg = "Пароль слишком короткий. Минимум 6 символов."
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            srv = self._get_server()
            if srv is None:
                return

            try:
                self._backend.panel_config.update_user_password(
                    srv.runtime.config,
                    "admin",
                    new_password,
                    role="Super Admin",
                    base_dir=self._backend.base_dir,
                )
                self._backend.panel_config.save_config(self._backend.base_dir, srv.runtime.config)
                self._backend._safe_audit(srv, "gui", "change_password_via_gui", True, source="gui")
            except Exception as e:
                self._backend._safe_audit(
                    srv,
                    "gui",
                    "change_password_via_gui",
                    False,
                    source="gui",
                    details=str(e),
                )
                msg = self._format_error("сменить пароль панели", e)
                self._set_status_msg(msg)
                self._popup_text("Веб-панель", msg)
                return

            try:
                self._backend._last_generated_password = None
            except Exception:
                pass

            self.password_edit.clear()
            self._set_status_msg("Пароль обновлён.")
            _try_log(self._log_func, "[GUI][WEBPANEL] password changed", self._backend)

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
