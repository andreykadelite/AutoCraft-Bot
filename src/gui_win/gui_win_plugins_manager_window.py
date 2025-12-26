# -*- coding: utf-8 -*-
"""
GUI: Менеджер плагинов (окно PyQt5).
"""

from __future__ import annotations

import asyncio
import configparser
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import traceback
import venv
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

import weakref


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "Менеджер плагинов"
FUNCTIONS_ENTRYPOINT = "open_plugins_manager_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 40
FUNCTIONS_ICON = "SP_DesktopIcon"
FUNCTIONS_TOOLTIP = "Управление плагинами (установка, запуск, автозапуск)"
FUNCTIONS_ACCESSIBLE_NAME = "Кнопка: Менеджер плагинов"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = "Открывает окно менеджера плагинов"

if TYPE_CHECKING:  # pragma: no cover
    from PyQt5.QtWidgets import QWidget



# -------------------- ленивый импорт PyQt5 --------------------
# (нужно, чтобы functions_window.py мог импортировать модуль без поднятия Qt)

_PYQT_IMPORTED = False
_GUI_BUILT = False

def _get_pyqt() -> None:
    """Лениво импортирует PyQt5 только когда GUI реально нужен."""
    global _PYQT_IMPORTED
    if _PYQT_IMPORTED:
        return

    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QEvent
    from PyQt5.QtGui import QFontDatabase, QTextCursor
    from PyQt5.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QHeaderView,
        QScrollBar,
    )

    g = globals()
    g.update({
        'Qt': Qt,
        'QThread': QThread,
        'pyqtSignal': pyqtSignal,
        'QEvent': QEvent,
        'QFontDatabase': QFontDatabase,
        'QTextCursor': QTextCursor,
        'QApplication': QApplication,
        'QCheckBox': QCheckBox,
        'QDialog': QDialog,
        'QDialogButtonBox': QDialogButtonBox,
        'QHBoxLayout': QHBoxLayout,
        'QLabel': QLabel,
        'QMessageBox': QMessageBox,
        'QPushButton': QPushButton,
        'QPlainTextEdit': QPlainTextEdit,
        'QTableWidget': QTableWidget,
        'QTableWidgetItem': QTableWidgetItem,
        'QVBoxLayout': QVBoxLayout,
        'QWidget': QWidget,
        'QHeaderView': QHeaderView,
        'QScrollBar': QScrollBar,
    })

    _PYQT_IMPORTED = True


def _ensure_gui_built() -> None:
    """Создаёт классы GUI (QDialog и т.п.) только по требованию."""
    global _GUI_BUILT
    if _GUI_BUILT:
        return
    _get_pyqt()

    # Классы GUI ниже завязаны на PyQt5. Держим их внутри, чтобы импорт модуля был лёгким.
    # -------------------- фоновые задачи --------------------


    class BackgroundTask(QThread):
        progress = pyqtSignal(str)
        finished = pyqtSignal(object, object)

        def __init__(self, fn: Callable[[Callable[[str], None]], Any], parent=None):
            super().__init__(parent)
            self._fn = fn

        def run(self):  # type: ignore[override]
            try:
                res = self._fn(self.progress.emit)
                self.finished.emit(res, None)
            except Exception as e:
                self.finished.emit(None, e)


    # -------------------- диалог действий по плагину --------------------


    class PluginActionsDialog(QDialog):
        def __init__(self, plugin: Dict[str, Any], backend: PluginBackend, parent=None):
            super().__init__(parent)
            self.setObjectName("pluginActionsDialog")
            self.setWindowTitle(f"Плагин: {plugin.get('display', plugin['key'])}")
            self.setModal(True)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

            self._backend = backend
            self._plugin = plugin
            self._task: Optional[BackgroundTask] = None

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(10)

            header = QLabel(f"Плагин: {plugin.get('display', plugin['key'])}")
            header.setStyleSheet("font-size: 14pt; font-weight: 600;")
            layout.addWidget(header)

            self.status_lbl = QLabel(self._build_status_text(plugin))
            self.status_lbl.setWordWrap(True)
            self.status_lbl.setAccessibleName("Статус плагина")
            layout.addWidget(self.status_lbl)

            self.autostart_cb = QCheckBox("Автозапуск при старте бота")
            self.autostart_cb.setChecked(bool(plugin.get("autostart")))
            self.autostart_cb.setEnabled(plugin.get("installed"))
            self.autostart_cb.toggled.connect(self._toggle_autostart)
            layout.addWidget(self.autostart_cb)

            btns_layout = QHBoxLayout()
            btns_layout.setSpacing(8)
            layout.addLayout(btns_layout)

            self.install_btn = QPushButton("Установить" if not plugin.get("installed") else "Переустановить")
            self.install_btn.clicked.connect(self._install)
            btns_layout.addWidget(self.install_btn)

            self.run_btn = QPushButton("Запустить")
            self.run_btn.setEnabled(plugin.get("installed"))
            self.run_btn.clicked.connect(self._run)
            btns_layout.addWidget(self.run_btn)

            self.unload_btn = QPushButton("Выгрузить")
            self.unload_btn.setEnabled(plugin.get("installed"))
            self.unload_btn.clicked.connect(self._unload)
            btns_layout.addWidget(self.unload_btn)

            self.delete_btn = QPushButton("Удалить")
            self.delete_btn.setEnabled(plugin.get("available"))
            self.delete_btn.clicked.connect(self._delete)
            btns_layout.addWidget(self.delete_btn)

            self.open_btn = QPushButton("Открыть папку")
            self.open_btn.setEnabled(plugin.get("folder") is not None)
            self.open_btn.clicked.connect(self._open_folder)
            btns_layout.addWidget(self.open_btn)

            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setPlaceholderText("Здесь будет вывод операций с плагином.")
            # Делаем лог удобным для скринридеров: можно читать стрелками, не прыгая в конец.
            self.log_view.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            self.log_view.setFocusPolicy(Qt.StrongFocus)
            self.log_view.installEventFilter(self)
            self._log_user_scrolling = False
            try:
                self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            except Exception:
                pass
            layout.addWidget(self.log_view)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        # ---- helpers ----
        def _append_log(self, text: str) -> None:
            edit = self.log_view
            vsb = edit.verticalScrollBar()

            try:
                saved_scroll = vsb.value()
                was_at_bottom = saved_scroll >= (vsb.maximum() - 1)
            except Exception:
                saved_scroll = None
                was_at_bottom = True

            try:
                saved_cursor = QTextCursor(edit.textCursor())
            except Exception:
                saved_cursor = None

            edit.appendPlainText(text)

            # Если пользователь читает историю (скроллил/жмёт стрелки) — не ломаем позицию каретки.
            if self._log_user_scrolling or (not was_at_bottom):
                if saved_cursor is not None:
                    try:
                        edit.setTextCursor(saved_cursor)
                    except Exception:
                        pass
                if saved_scroll is not None:
                    try:
                        vsb.setValue(saved_scroll)
                    except Exception:
                        pass
            else:
                try:
                    edit.moveCursor(QTextCursor.End)
                except Exception:
                    pass
                try:
                    vsb.setValue(vsb.maximum())
                except Exception:
                    pass

        def _set_status(self, text: str) -> None:
            self.status_lbl.setText(text)

        def _build_status_text(self, plugin: Dict[str, Any]) -> str:
            lines = [
                f"Статус: {_format_status(plugin.get('installed'), plugin.get('available'))}",
                f"Автозапуск: {'включён' if plugin.get('autostart') else 'выключен'}",
            ]
            folder = plugin.get("folder")
            if folder:
                lines.append(f"Папка: {folder}")
            else:
                lines.append("Папка: не найдена")
            desc = plugin.get("meta", {}).get("description")
            if desc:
                lines.append("")
                lines.append(str(desc))
            return "\n".join(lines)

        def eventFilter(self, obj, event):  # type: ignore[override]
            # Пока пользователь читает лог (стрелками/колесом/мышью) — мы перестаём "прилипать" к концу.
            if obj is self.log_view:
                if event.type() == QEvent.KeyPress:
                    key = event.key()
                    if key in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Home, Qt.Key_Left):
                        self._log_user_scrolling = True
                    elif key in (Qt.Key_End,):
                        self._log_user_scrolling = False
                    else:
                        # Если уже в режиме чтения — не сбрасываем его просто так.
                        if not self._log_user_scrolling:
                            vsb = self.log_view.verticalScrollBar()
                            try:
                                self._log_user_scrolling = vsb.value() < vsb.maximum()
                            except Exception:
                                self._log_user_scrolling = True
                elif event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                    vsb = self.log_view.verticalScrollBar()
                    try:
                        self._log_user_scrolling = vsb.value() < vsb.maximum()
                    except Exception:
                        self._log_user_scrolling = True
                elif event.type() == QEvent.FocusOut:
                    self._log_user_scrolling = False

            return super().eventFilter(obj, event)

        def _reload_plugin_info(self):
            key = self._plugin["key"]
            available = self._backend.available_plugins()
            autostart = set(self._backend.load_autostart())
            loaded = self._backend._get_loaded_dict()
            info = available.get(key, {})
            meta = info.get("meta", {}) if info else {}
            folder = self._backend._guess_folder(key, info)
            installed = self._backend.is_installed(key, info, loaded)
            self._plugin.update(
                {
                    "display": meta.get("name", key),
                    "installed": installed,
                    "available": bool(info),
                    "autostart": key in autostart,
                    "folder": str(folder) if folder else None,
                    "meta": meta,
                }
            )
            self.status_lbl.setText(self._build_status_text(self._plugin))
            self.run_btn.setEnabled(self._plugin.get("installed"))
            self.unload_btn.setEnabled(self._plugin.get("installed"))
            self.install_btn.setText("Установить" if not self._plugin.get("installed") else "Переустановить")
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(self._plugin.get("autostart"))
            self.autostart_cb.setEnabled(self._plugin.get("installed"))
            self.autostart_cb.blockSignals(False)
            self.delete_btn.setEnabled(self._plugin.get("available"))
            self.open_btn.setEnabled(self._plugin.get("folder") is not None)

        def _run_task(self, fn: Callable[[Callable[[str], None]], Any], done_msg: Optional[str] = None) -> None:
            if self._task and self._task.isRunning():
                return
            self.install_btn.setEnabled(False)
            self.run_btn.setEnabled(False)
            self.unload_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.autostart_cb.setEnabled(False)

            self._task = BackgroundTask(fn, self)

            def _finished(_, err):
                self.install_btn.setEnabled(True)
                self._reload_plugin_info()
                if err:
                    self._append_log(f"[ОШИБКА] {err}")
                    self._set_status(f"Ошибка: {err}")
                else:
                    if done_msg:
                        self._append_log(done_msg)
                        self._set_status(done_msg)
                try:
                    QApplication.beep()
                except Exception:
                    pass

            self._task.progress.connect(self._append_log)
            self._task.finished.connect(_finished)
            self._task.start()

        # ---- actions ----
        def _install(self):
            key = self._plugin["key"]
            self._run_task(lambda cb: self._backend.install_plugin(key, progress=cb), "Установка завершена.")

        def _run(self):
            key = self._plugin["key"]
            self._run_task(lambda cb: self._backend.run_plugin(key, progress=cb), "Запуск отправлен.")

        def _unload(self):
            key = self._plugin["key"]
            self._run_task(lambda cb: self._backend.unload_plugin(key, progress=cb), "Плагин выгружен.")

        def _delete(self):
            key = self._plugin["key"]
            reply = QMessageBox.question(
                self,
                "Удалить плагин",
                "Удалить папку плагина? Это действие необратимо.",
            )
            if reply != QMessageBox.Yes:
                return
            self._run_task(lambda cb: self._backend.delete_plugin(key, progress=cb), "Плагин удалён.")

        def _open_folder(self):
            folder = self._plugin.get("folder")
            if folder:
                _open_folder(Path(folder))

        def _toggle_autostart(self, checked: bool):
            autostart = set(self._backend.load_autostart())
            key = self._plugin["key"]
            if checked:
                autostart.add(key)
            else:
                autostart.discard(key)
            self._backend.save_autostart(list(autostart))
            self._append_log(f"Автозапуск {'включён' if checked else 'выключен'} для {key}")
            self._set_status(f"Автозапуск {'включён' if checked else 'выключен'}.")
            self._reload_plugin_info()


    # -------------------- главное окно менеджера --------------------


    class PluginsManagerWindow(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("pluginsManagerWindow")
            self.setWindowTitle("Менеджер плагинов")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setMinimumSize(820, 520)
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

            self._backend = PluginBackend()
            self._plugins: List[Dict[str, Any]] = []
            self._task: Optional[BackgroundTask] = None

            layout = QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(10)

            header = QLabel("Менеджер плагинов")
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            header.setAccessibleName("Заголовок менеджера плагинов")
            layout.addWidget(header)

            self.status_lbl = QLabel("Готово. Дважды кликните по плагину для действий.")
            self.status_lbl.setWordWrap(True)
            self.status_lbl.setAccessibleName("Строка статуса")
            layout.addWidget(self.status_lbl)

            self.table = QTableWidget(0, 4, self)
            self.table.setHorizontalHeaderLabels(["Плагин", "Статус", "Автозапуск", "Папка"])
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setSelectionMode(QTableWidget.SingleSelection)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            header_view = self.table.horizontalHeader()
            header_view.setSectionResizeMode(QHeaderView.Stretch)
            header_view.setStretchLastSection(True)
            self.table.verticalHeader().setVisible(False)
            self.table.itemSelectionChanged.connect(self._on_selection_changed)
            self.table.itemDoubleClicked.connect(self._open_selected_dialog)
            self.table.installEventFilter(self)
            layout.addWidget(self.table)

            btns = QHBoxLayout()
            btns.setSpacing(8)
            layout.addLayout(btns)

            self.refresh_btn = QPushButton("Обновить список")
            self.refresh_btn.clicked.connect(self.refresh_table)
            btns.addWidget(self.refresh_btn)

            self.install_btn = QPushButton("Установить/обновить")
            self.install_btn.clicked.connect(self._install_selected)
            btns.addWidget(self.install_btn)

            self.run_btn = QPushButton("Запустить")
            self.run_btn.clicked.connect(self._run_selected)
            btns.addWidget(self.run_btn)

            self.unload_btn = QPushButton("Выгрузить")
            self.unload_btn.clicked.connect(self._unload_selected)
            btns.addWidget(self.unload_btn)

            self.delete_btn = QPushButton("Удалить")
            self.delete_btn.clicked.connect(self._delete_selected)
            btns.addWidget(self.delete_btn)

            self.autostart_cb = QCheckBox("Автозапуск")
            self.autostart_cb.toggled.connect(self._toggle_autostart_selected)
            btns.addWidget(self.autostart_cb)

            self.open_btn = QPushButton("Открыть папку")
            self.open_btn.clicked.connect(self._open_folder_selected)
            btns.addWidget(self.open_btn)

            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setPlaceholderText("Лог операций менеджера плагинов.")
            # Для чтения логов скринридером: разрешаем выделение с клавиатуры и не срываем каретку.
            self.log_view.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            self.log_view.setFocusPolicy(Qt.StrongFocus)
            try:
                self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            except Exception:
                pass
            self.log_view.installEventFilter(self)
            self._log_user_scrolling = False
            layout.addWidget(self.log_view)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self.refresh_table()

        # ---- helpers ----
        def _append_log(self, text: str) -> None:
            edit = self.log_view
            vsb = edit.verticalScrollBar()  # type: QScrollBar

            try:
                saved_scroll = vsb.value()
                was_at_bottom = saved_scroll >= (vsb.maximum() - 1)
            except Exception:
                saved_scroll = None
                was_at_bottom = True

            try:
                saved_cursor = QTextCursor(edit.textCursor())
            except Exception:
                saved_cursor = None

            edit.appendPlainText(text)

            # Если пользователь читает историю (скроллил/стрелки) — сохраняем позицию.
            if self._log_user_scrolling or (not was_at_bottom):
                if saved_cursor is not None:
                    try:
                        edit.setTextCursor(saved_cursor)
                    except Exception:
                        pass
                if saved_scroll is not None:
                    try:
                        vsb.setValue(saved_scroll)
                    except Exception:
                        pass
            else:
                try:
                    edit.moveCursor(QTextCursor.End)
                except Exception:
                    pass
                try:
                    vsb.setValue(vsb.maximum())
                except Exception:
                    pass

        def _set_status(self, text: str) -> None:
            self.status_lbl.setText(text)

        def _current_plugin(self) -> Optional[Dict[str, Any]]:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return None
            row = sel[0].row()
            if 0 <= row < len(self._plugins):
                return self._plugins[row]
            return None

        def _run_task_for_selected(self, fn: Callable[[Callable[[str], None]], Any], success_msg: str = "") -> None:
            if self._task and self._task.isRunning():
                return
            self._task = BackgroundTask(fn, self)

            def _finished(_, err):
                if err:
                    self._append_log(f"[ОШИБКА] {err}")
                    self._set_status(f"Ошибка: {err}")
                else:
                    if success_msg:
                        self._append_log(success_msg)
                        self._set_status(success_msg)
                self.refresh_table()

            self._task.progress.connect(self._append_log)
            self._task.finished.connect(_finished)
            self._task.start()

        def eventFilter(self, obj, event):
            if obj is self.table and event.type() == QEvent.KeyPress:
                key = event.key()
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                    self._open_selected_dialog()
                    return True

            if obj is self.log_view:
                if event.type() == QEvent.KeyPress:
                    key = event.key()
                    # Если пользователь начал читать лог стрелками/страницами — перестаём "прилипать" к концу.
                    if key in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Home, Qt.Key_Left):
                        self._log_user_scrolling = True
                    elif key in (Qt.Key_End,):
                        self._log_user_scrolling = False
                    else:
                        if not self._log_user_scrolling:
                            vsb = self.log_view.verticalScrollBar()
                            try:
                                self._log_user_scrolling = vsb.value() < vsb.maximum()
                            except Exception:
                                self._log_user_scrolling = True

                elif event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                    vsb = self.log_view.verticalScrollBar()
                    try:
                        self._log_user_scrolling = vsb.value() < vsb.maximum()
                    except Exception:
                        self._log_user_scrolling = True

                elif event.type() == QEvent.FocusOut:
                    self._log_user_scrolling = False

            return super().eventFilter(obj, event)

        def _update_buttons_state(self):
            plugin = self._current_plugin()
            has_sel = plugin is not None
            installed = plugin.get("installed") if plugin else False
            available = plugin.get("available") if plugin else False

            self.install_btn.setEnabled(has_sel and available)
            self.run_btn.setEnabled(has_sel and installed)
            self.unload_btn.setEnabled(has_sel and installed)
            self.delete_btn.setEnabled(has_sel and available)
            self.autostart_cb.setEnabled(has_sel and installed)
            self.open_btn.setEnabled(has_sel and plugin.get("folder") is not None if plugin else False)

            if plugin:
                self.autostart_cb.blockSignals(True)
                self.autostart_cb.setChecked(bool(plugin.get("autostart")))
                self.autostart_cb.blockSignals(False)

        # ---- таблица ----
        def refresh_table(self):
            available = self._backend.available_plugins()
            autostart = set(self._backend.load_autostart())
            loaded = self._backend._get_loaded_dict()

            keys = set(available.keys()) | set(loaded.keys()) | autostart
            self._plugins = []
            for key in sorted(keys):
                info = available.get(key, {})
                meta = info.get("meta", {}) if info else {}
                folder = self._backend._guess_folder(key, info)
                installed = self._backend.is_installed(key, info, loaded)
                self._plugins.append(
                    {
                        "key": key,
                        "display": meta.get("name", key),
                        "status": _format_status(installed, bool(info)),
                        "installed": installed,
                        "autostart": key in autostart,
                        "folder": str(folder) if folder else None,
                        "available": bool(info),
                        "meta": meta,
                    }
                )

            self.table.setRowCount(len(self._plugins))
            for row, plugin in enumerate(self._plugins):
                items = [
                    (plugin["display"], False),
                    (plugin["status"], False),
                    ("да" if plugin["autostart"] else "нет", False),
                    (plugin["folder"] or "-", False),
                ]
                for col, (text, editable) in enumerate(items):
                    it = QTableWidgetItem(text)
                    if not editable:
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, col, it)
            self.table.resizeColumnsToContents()
            self._update_buttons_state()

        def _on_selection_changed(self):
            self._update_buttons_state()

        def _open_selected_dialog(self):
            plugin = self._current_plugin()
            if not plugin:
                return
            dlg = PluginActionsDialog(plugin, self._backend, parent=self)
            try:
                dlg.exec_()
            except Exception:
                dlg.show()
            self.refresh_table()

        # ---- кнопки ----
        def _install_selected(self):
            plugin = self._current_plugin()
            if not plugin:
                return
            key = plugin["key"]
            self._run_task_for_selected(
                lambda cb: self._backend.install_plugin(key, progress=cb),
                f"Плагин «{plugin['display']}» установлен.",
            )

        def _run_selected(self):
            plugin = self._current_plugin()
            if not plugin:
                return
            key = plugin["key"]
            self._run_task_for_selected(
                lambda cb: self._backend.run_plugin(key, progress=cb),
                f"Плагин «{plugin['display']}» запущен.",
            )

        def _unload_selected(self):
            plugin = self._current_plugin()
            if not plugin:
                return
            key = plugin["key"]
            self._run_task_for_selected(
                lambda cb: self._backend.unload_plugin(key, progress=cb),
                f"Плагин «{plugin['display']}» выгружен.",
            )

        def _delete_selected(self):
            plugin = self._current_plugin()
            if not plugin or not plugin.get("available"):
                return
            key = plugin["key"]
            reply = QMessageBox.question(
                self,
                "Удалить плагин",
                "Удалить папку плагина? Это действие необратимо.",
            )
            if reply != QMessageBox.Yes:
                return
            self._run_task_for_selected(
                lambda cb: self._backend.delete_plugin(key, progress=cb),
                f"Плагин «{plugin['display']}» удалён.",
            )

        def _toggle_autostart_selected(self, checked: bool):
            plugin = self._current_plugin()
            if not plugin:
                return
            autostart = set(self._backend.load_autostart())
            if checked:
                autostart.add(plugin["key"])
            else:
                autostart.discard(plugin["key"])
            self._backend.save_autostart(list(autostart))
            self._append_log(f"Автозапуск {'включён' if checked else 'выключен'} для {plugin['display']}")
            self._set_status(f"Автозапуск {'включён' if checked else 'выключен'}.")
            self.refresh_table()

        def _open_folder_selected(self):
            plugin = self._current_plugin()
            if plugin and plugin.get("folder"):
                _open_folder(Path(plugin["folder"]))



    # Экспортируем классы наружу (для совместимости и дебага).
    globals()['BackgroundTask'] = BackgroundTask
    globals()['PluginActionsDialog'] = PluginActionsDialog
    globals()['PluginsManagerWindow'] = PluginsManagerWindow
    _GUI_BUILT = True

# -------------------- утилиты --------------------


def _safe_text(obj: object) -> str:
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "<н/д>"


def _open_folder(path: Path) -> None:
    """Открыть папку в проводнике/файловом менеджере (best effort)."""
    try:
        path = path.resolve()
    except Exception:
        path = Path(path)
    if not path.exists():
        return
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def _format_status(installed: bool, available: bool) -> str:
    if installed:
        return "Установлен"
    if available:
        return "Не установлен"
    return "Не найден"


# -------------------- бэкенд --------------------


class PluginBackend:
    """Использует функции из __main__ (bot-ok.py). При их отсутствии включает фолбэки."""

    def __init__(self):
        try:
            import __main__ as main  # type: ignore
        except Exception:
            main = None  # type: ignore
        self._main = main

        base = Path(getattr(main, "base_dir", getattr(main, "BASE_DIR", Path.cwd()))).resolve()
        self.plugin_dir = Path(getattr(main, "PLUGIN_DIR", base / "plugins")).resolve()
        self.config_file = Path(
            getattr(main, "CONFIG_FILE", getattr(main, "CONFIG_PATH", base / "config.ini"))
        ).resolve()
        self._local_loaded: Dict[str, Dict[str, Any]] = {}

    # заглушки: методы ниже будут заполнены дальше
    def log(self, text: str) -> None:
        for attr in ("write_bot_log", "write_plugin_log", "write_log"):
            fn = getattr(self._main, attr, None) if self._main else None
            if callable(fn):
                try:
                    fn(text)
                    return
                except Exception:
                    pass
        try:
            print(text)
        except Exception:
            pass

    def _add_site_packages(self, path: str) -> None:
        fn = getattr(self._main, "add_site_packages", None) if self._main else None
        if callable(fn):
            try:
                fn(path)
                return
            except Exception:
                pass
        if path and path not in sys.path:
            sys.path.insert(0, path)
            import importlib

            importlib.invalidate_caches()

    def _get_dispatcher(self):
        bot = getattr(self._main, "current_bot", None) if self._main else None
        return getattr(bot, "dispatcher", None) if bot is not None else None

    def _get_event_loop(self):
        loop = getattr(self._main, "current_loop", None) if self._main else None
        if loop and getattr(loop, "is_running", lambda: False)():
            return loop
        return None

    def _get_loaded_dict(self) -> Dict[str, Dict[str, Any]]:
        lp = getattr(self._main, "loaded_plugins", None) if self._main else None
        if isinstance(lp, dict):
            return lp
        return self._local_loaded

    def _guess_folder(self, key: str, info: Dict[str, Any]) -> Optional[Path]:
        if info and info.get("folder"):
            try:
                return Path(info["folder"]).resolve()
            except Exception:
                return None
        candidate = self.plugin_dir / key
        return candidate if candidate.exists() else None

    def _venv_exists(self, folder: Optional[Path]) -> bool:
        if not folder:
            return False
        venv_dir = folder / "venv"
        if not venv_dir.exists():
            return False
        markers = [
            venv_dir / "pyvenv.cfg",
            venv_dir / "Scripts" / "python.exe",
            venv_dir / "Scripts" / "pip.exe",
            venv_dir / "bin" / "python",
            venv_dir / "bin" / "pip",
        ]
        return any(p.exists() for p in markers)

    def is_installed(self, key: str, info: Dict[str, Any], loaded: Dict[str, Dict[str, Any]]) -> bool:
        if key in loaded:
            return True
        folder = self._guess_folder(key, info)
        return self._venv_exists(folder)

    # ---- автозапуск ----

    def load_autostart(self) -> List[str]:
        fn = getattr(self._main, "load_autostart_config", None) if self._main else None
        if callable(fn):
            try:
                return list(fn() or [])
            except Exception as e:
                self.log(f"[GUI][плагины] Ошибка чтения автозапуска: {e}")

        cfg = configparser.ConfigParser()
        if self.config_file.exists():
            try:
                cfg.read(self.config_file, encoding="utf-8")
                if cfg.has_section("autostart"):
                    plugins_str = cfg["autostart"].get("plugins", "")
                    return [p.strip() for p in plugins_str.split(",") if p.strip()]
            except Exception:
                pass
        return []

    def save_autostart(self, plugins: List[str]) -> None:
        fn = getattr(self._main, "save_autostart_config", None) if self._main else None
        if callable(fn):
            try:
                fn(plugins)
                return
            except Exception as e:
                self.log(f"[GUI][плагины] Ошибка сохранения автозапуска: {e}")

        cfg = configparser.ConfigParser()
        if self.config_file.exists():
            try:
                cfg.read(self.config_file, encoding="utf-8")
            except Exception:
                cfg = configparser.ConfigParser()
        if "autostart" not in cfg:
            cfg["autostart"] = {}
        cfg["autostart"]["plugins"] = ",".join(plugins)
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with self.config_file.open("w", encoding="utf-8") as f:
                cfg.write(f)
            self.log("Конфигурация автозапуска плагинов сохранена (fallback).")
        except Exception as e:
            self.log(f"[GUI][плагины] Не удалось сохранить автозапуск: {e}")

    # ---- доступные плагины ----

    def available_plugins(self) -> Dict[str, Dict[str, Any]]:
        fn = getattr(self._main, "scan_available_plugins", None) if self._main else None
        if callable(fn):
            try:
                return fn()
            except Exception as e:
                self.log(f"[GUI][плагины] Ошибка scan_available_plugins: {e}")
        return self._fallback_scan_available()

    def _fallback_scan_available(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        try:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if not self.plugin_dir.exists():
            return out

        for item in sorted(self.plugin_dir.iterdir()):
            if not item.is_dir():
                continue
            plugin_name = item.name
            meta = {}
            meta_file = item / f"{plugin_name}.json"
            if meta_file.exists():
                try:
                    import json

                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            meta.setdefault("name", plugin_name)
            out[plugin_name] = {"meta": meta, "folder": str(item)}
        return out

    # ---- операции ----

    def install_plugin(self, plugin_key: str, progress: Optional[Callable[[str], None]] = None) -> None:
        available = self.available_plugins()
        if plugin_key not in available:
            raise ValueError(f"Плагин «{plugin_key}» не найден.")

        info = available[plugin_key]
        folder = Path(info["folder"])
        meta = info.get("meta", {}) or {}
        dp = self._get_dispatcher()

        if progress:
            progress(f"Начинается установка плагина «{meta.get('name', plugin_key)}».")
        self.log(f"[GUI][плагины] Установка {plugin_key}")

        c_venv = getattr(self._main, "create_plugin_venv", None) if self._main else None
        if callable(c_venv):
            c_venv(str(folder), dp, None)
        else:
            venv_path = folder / "venv"
            if not venv_path.exists():
                venv.create(venv_path, with_pip=True)

        pip_exe, _, site_packages = self._get_plugin_venv_paths(folder)

        deps = meta.get("dependencies", []) or []
        inst_dep = getattr(self._main, "install_dependency_for_plugin", None) if self._main else None
        for dep in deps:
            if progress:
                progress(f"Устанавливаю зависимость {dep}...")
            if callable(inst_dep):
                inst_dep(dep, pip_exe, plugin_key, dp, None)
            else:
                self._pip_install(pip_exe, dep)

        modules = []
        py_files_found = False
        if site_packages:
            self._add_site_packages(site_packages)
        for filename in sorted(os.listdir(folder)):
            if not filename.endswith(".py"):
                continue
            py_files_found = True
            file_path = folder / filename
            spec = importlib.util.spec_from_file_location(f"{plugin_key}_{filename}", str(file_path))
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)  # type: ignore
                modules.append(module)
                if progress:
                    progress(f"Импортирован {filename}")
            except Exception as e:
                traceback.print_exc()
                if progress:
                    progress(f"[ОШИБКА] {filename}: {e}")
                self.log(f"[GUI][плагины] Ошибка импорта {filename}: {e}")

        if not py_files_found and progress:
            progress("В папке нет .py файлов.")

        for mod in modules:
            if hasattr(mod, "init_plugin"):
                try:
                    if site_packages:
                        self._add_site_packages(site_packages)
                    if dp is not None:
                        mod.init_plugin(dp)
                    else:
                        mod.init_plugin()
                    if progress:
                        progress(f"init_plugin выполнен: {mod.__name__}")
                except Exception as e:
                    traceback.print_exc()
                    self.log(f"[GUI][плагины] Ошибка init_plugin {mod.__name__}: {e}")
                    if progress:
                        progress(f"[ОШИБКА] init_plugin {mod.__name__}: {e}")

        loaded = self._get_loaded_dict()
        loaded[plugin_key] = {"modules": modules, "meta": meta, "venv_site": site_packages}

        if progress:
            progress("Плагин установлен и загружен.")

    def unload_plugin(self, plugin_key: str, progress: Optional[Callable[[str], None]] = None) -> bool:
        loaded = self._get_loaded_dict()
        info = loaded.pop(plugin_key, None)
        if not info:
            return False

        dp = self._get_dispatcher()
        rm_handlers = getattr(self._main, "remove_handlers_from_module", None) if self._main else None
        if dp is not None and callable(rm_handlers):
            for mod in info.get("modules", []):
                try:
                    rm_handlers(dp, mod.__name__)
                except Exception:
                    pass
        if progress:
            progress("Плагин выгружен.")
        return True

    def delete_plugin(self, plugin_key: str, progress: Optional[Callable[[str], None]] = None) -> None:
        available = self.available_plugins()
        folder = None
        if plugin_key in available:
            folder = Path(available[plugin_key]["folder"])

        self.unload_plugin(plugin_key, progress)

        autostart = set(self.load_autostart())
        if plugin_key in autostart:
            autostart.remove(plugin_key)
            self.save_autostart(list(autostart))

        if folder and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            if progress:
                progress("Папка плагина удалена.")
        else:
            raise ValueError("Папка плагина не найдена, удалять нечего.")

    def run_plugin(self, plugin_key: str, progress: Optional[Callable[[str], None]] = None) -> None:
        loaded = self._get_loaded_dict()
        info = loaded.get(plugin_key)
        if not info:
            available = self.available_plugins()
            if plugin_key in available:
                # Плагин есть на диске (venv/файлы), но не загружен в память — подгружаем.
                self.install_plugin(plugin_key, progress=progress)
                loaded = self._get_loaded_dict()
                info = loaded.get(plugin_key)
            if not info:
                raise ValueError("Плагин не установлен/не загружен.")

        modules = info.get("modules", [])
        if not modules:
            raise RuntimeError("У плагина нет загруженных модулей.")

        msg = self._build_dummy_message()
        launched = False
        errors: List[str] = []
        for mod in modules:
            fn = getattr(mod, "run_plugin", None)
            if not callable(fn):
                continue
            launched = True
            try:
                site_packages = info.get("venv_site")
                if site_packages:
                    self._add_site_packages(site_packages)
                if asyncio.iscoroutinefunction(fn):
                    loop = self._get_event_loop()
                    if loop:
                        asyncio.run_coroutine_threadsafe(fn(msg), loop)
                    else:
                        asyncio.run(fn(msg))
                else:
                    fn(msg)
                if progress:
                    progress(f"run_plugin выполнен: {mod.__name__}")
            except Exception as e:
                traceback.print_exc()
                errors.append(str(e))
                if progress:
                    progress(f"[ОШИБКА] run_plugin {mod.__name__}: {e}")

        if not launched:
            raise RuntimeError("У плагина нет функции run_plugin.")
        if errors:
            raise RuntimeError("; ".join(errors))
        if progress:
            progress("Плагин запущен.")

    def _build_dummy_message(self):
        bot = getattr(self._main, "current_bot", None) if self._main else None

        chat_id = None
        for attr in ("authorized_users", "allowed_accounts"):
            try:
                data = getattr(self._main, attr, None) if self._main else None
                if data:
                    chat_id = next(iter(data))
                    break
            except Exception:
                continue

        backend_log = self.log

        class _Chat:
            def __init__(self, cid):
                self.id = cid

        class _User:
            def __init__(self, uid):
                self.id = uid

        class _DummyMessage:
            def __init__(self, bot_obj, cid, log_fn):
                self.text = "[GUI запуск плагина]"
                self.bot = bot_obj
                self.chat = _Chat(cid)
                self.from_user = _User(cid)
                self._log = log_fn

            async def answer(self, text, **kwargs):
                if callable(self._log):
                    try:
                        self._log(f"[GUI][плагины] Ответ плагина: {text}")
                    except Exception:
                        pass
                if self.bot and self.chat.id:
                    try:
                        await self.bot.send_message(self.chat.id, text, **kwargs)
                    except Exception:
                        pass

        return _DummyMessage(bot, chat_id, backend_log)

    def _get_plugin_venv_paths(self, plugin_folder: Path):
        fn = getattr(self._main, "get_plugin_venv_paths", None) if self._main else None
        if callable(fn):
            return fn(str(plugin_folder))

        venv_path = plugin_folder / "venv"
        if os.name == "nt":
            pip_exe = venv_path / "Scripts" / "pip.exe"
            python_exe = venv_path / "Scripts" / "python.exe"
            site_packages = venv_path / "Lib" / "site-packages"
        else:
            pip_exe = venv_path / "bin" / "pip"
            python_exe = venv_path / "bin" / "python"
            site_packages = venv_path / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        return str(pip_exe), str(python_exe), str(site_packages)

    def _pip_install(self, pip_exe: str, dep: str) -> None:
        try:
            subprocess.check_call([pip_exe, "install", "--upgrade", dep])
        except Exception as e:
            self.log(f"[GUI][плагины] Не удалось установить {dep}: {e}")


# -------------------- открытие окна --------------------

_WINDOWS = weakref.WeakKeyDictionary()


def open_plugins_manager_window(main_window: Optional["QWidget"]) -> None:
    if main_window is None:
        return
    _ensure_gui_built()

    w = _WINDOWS.get(main_window)
    if w is None:
        w = PluginsManagerWindow(parent=main_window)
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
