
import sys
import os
import threading
import requests
import configparser
import info
import gui_serverapi
import collections  # <-- for recent-log de-dup window
import importlib
import webbrowser
from urllib.parse import urlsplit, urlunsplit
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QGridLayout,
    QSplitter,
    QScrollArea,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QTextEdit,
    QTextBrowser,
    QSystemTrayIcon,
    QMenu,
    QAction,
    QStyle,
    QStyleFactory,
    QSizePolicy,
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QDialogButtonBox,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QGroupBox,
    QRadioButton)

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QEvent
from PyQt5.QtGui import QIcon, QPalette, QColor, QFont, QTextCursor
from windows_startup import (
    BASE_DIR,
    _debug_log,
    CONFIG_PATH,
    _read_ini,
    _config_to_str,
    _atomic_write,
    load_startup_full,
    load_startup_settings,
    save_startup_settings,
    save_startup_method,
    apply_autorun_selected,
    apply_autorun,
    detect_autorun,
    _is_windows,
)

# --- GUI sub-windows (kept separate for easier editing) ---
from gui_win.functions_window import create_functions_button

# Nuitka/анализатор: модуль справки должен попасть в сборку, но импорт — только по кнопке.
try:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        import gui_win.help_window  # noqa: F401
except Exception:
    pass


# === Accessible radio button: arrows move focus only; Enter/Space selects ===

class A11yRadioButton(QRadioButton):
    """
    Радиокнопка с предсказуемой навигацией для NVDA/JAWS:
    — Стрелки ←/→/↑/↓ только перемещают фокус в группе, НО НЕ выбирают пункт.
    — Enter/Space выполняют выбор (click()).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nav_group = None
        self.setFocusPolicy(Qt.StrongFocus)

    def set_nav_group(self, widgets):
        """Установить последовательность радиокнопок для навигации стрелками."""
        self._nav_group = list(widgets) if widgets is not None else None

    def _move_focus(self, step: int) -> bool:
        """Сместить фокус в группе на step (-1 или +1). Возвращает True если удалось."""
        if not self._nav_group:
            return False
        try:
            lst = self._nav_group
            i = lst.index(self)
        except Exception:
            return False
        j = i + step
        if 0 <= j < len(lst):
            lst[j].setFocus(Qt.TabFocusReason)
            return True
        return False

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Up, Qt.Key_Left):
            if self._move_focus(-1):
                event.accept()
                return
        elif key in (Qt.Key_Down, Qt.Key_Right):
            if self._move_focus(1):
                event.accept()
                return
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            # Выбираем ТОЛЬКО на Enter/Space
            self.click()  # генерирует обычные сигналы clicked/toggled
            event.accept()
            return
        # Остальные клавиши — стандартное поведение
        super().keyPressEvent(event)
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


API_SECTION = 'api_server'

API_USE_STANDARD_KEY = 'use_standard_api'
def load_credentials():
    cfg = _read_ini(CONFIG_PATH)
    token = cfg.get('credentials', 'token', fallback='').strip()
    pin = cfg.get('credentials', 'pin', fallback='').strip()
    allowed_ids = cfg.get('credentials', 'allowed_ids', fallback='').strip()
    return token, pin, allowed_ids


def save_credentials(token, pin, allowed_ids):
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH, encoding='utf-8')
    if 'credentials' not in config:
        config['credentials'] = {}
    config['credentials']['token'] = token
    config['credentials']['pin'] = pin
    config['credentials']['allowed_ids'] = allowed_ids
    _atomic_write(CONFIG_PATH, _config_to_str(config))

def load_api_config():
    cfg = _read_ini(CONFIG_PATH)
    address = cfg.get('telegram_api', 'address', fallback='').strip()
    port = cfg.get('telegram_api', 'port', fallback='').strip()
    return address, port


def load_server_autostart():
    cfg = _read_ini(CONFIG_PATH)
    try:
        return cfg.getboolean("gui_settings", "auto_start", fallback=False)
    except Exception:
        return False


def save_api_config(address, port):
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH, encoding='utf-8')
    if 'telegram_api' not in config:
        config['telegram_api'] = {}
    config['telegram_api']['address'] = address
    config['telegram_api']['port'] = port
    _atomic_write(CONFIG_PATH, _config_to_str(config))

def load_lock_api_fields():
    cfg = _read_ini(CONFIG_PATH)
    try:
        return cfg.getboolean(API_SECTION, API_USE_STANDARD_KEY, fallback=False)
    except Exception:
        return False


def save_lock_api_fields(lock):
    """Save the use_standard_api flag to config.ini api_server section"""
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH, encoding='utf-8')
    if API_SECTION not in config:
        config[API_SECTION] = {}
    config[API_SECTION][API_USE_STANDARD_KEY] = 'true' if lock else 'false'
    _atomic_write(CONFIG_PATH, _config_to_str(config))



# Import necessary functions and global variables from __main__
from __main__ import (
    run_bot,
    current_bot, bot_thread, current_loop, allowed_accounts,
    authorized_users, note_mode, pending_note, file_mode, cmd_mode,
    in_cmd_menu, power_mode, pending_power_action, infiles_mode, plugins_mode,
    log_emitter
,
    pending_log_messages
)

def get_bot_username(token):
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        response = requests.get(url, timeout=5)
        if response.ok:
            data = response.json()
            if data.get("ok"):
                return data["result"].get("username")
    except Exception:
        pass
    return None

class BotNameWorker(QObject):
    bot_name_found = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, token):
        super().__init__()
        self.token = token

    def run(self):
        bot_name = get_bot_username(self.token)
        if bot_name:
            self.bot_name_found.emit(bot_name)
        else:
            self.bot_name_found.emit("неизвестный бот")
        self.finished.emit()

class BotConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подтверждение подключения")
        self.layout = QVBoxLayout(self)

        self.bot_name_field = QLineEdit("Получение имени бота...")
        self.bot_name_field.setReadOnly(True)
        self.bot_name_field.setAccessibleName("Имя бота")
        self.bot_name_field.setAccessibleDescription("Имя бота для подтверждения подключения")
        self.bot_name_field.setFocusPolicy(Qt.StrongFocus)
        self.layout.addWidget(self.bot_name_field)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self.button_box.button(QDialogButtonBox.Yes).setText("Да")
        self.button_box.button(QDialogButtonBox.No).setText("Нет")
        self.layout.addWidget(self.button_box)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.setTabOrder(self.bot_name_field, self.button_box.button(QDialogButtonBox.Yes))
        self.setTabOrder(self.button_box.button(QDialogButtonBox.Yes), self.button_box.button(QDialogButtonBox.No))

    def update_bot_name(self, bot_name):
        self.bot_name_field.setText(f"Подключиться к боту: {bot_name}?")




class ResponsiveButtonGrid(QWidget):
    """Адаптивная сетка кнопок: автоматически раскладывает виджеты по N колонкам.
    Полезно, когда окно становится узким: кнопки не уезжают за край и не превращаются в кашу.
    """
    def __init__(self, buttons, columns=3, parent=None):
        super().__init__(parent)
        self._buttons = list(buttons or [])
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self._cols = 0
        self.relayout(max(1, int(columns)))

    def relayout(self, columns: int):
        columns = max(1, int(columns))
        if columns == self._cols:
            return
        self._cols = columns

        # Снимаем всё из layout, но сами виджеты НЕ удаляем.
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                self._grid.removeWidget(w)

        for i, btn in enumerate(self._buttons):
            r = i // columns
            c = i % columns
            self._grid.addWidget(btn, r, c)

        for c in range(columns):
            self._grid.setColumnStretch(c, 1)

    def buttons(self):
        return list(self._buttons)

class MainWindow(QMainWindow):
    def __init__(self):

        super().__init__()

        # --- App-level look & feel -------------------------------------------------
        app = QApplication.instance()
        if app:
            app.setQuitOnLastWindowClosed(False)
            app.setStyle(QStyleFactory.create('Fusion'))

            dark_palette = QPalette()
            dark_palette.setColor(QPalette.Window, QColor(45, 45, 45))
            dark_palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
            dark_palette.setColor(QPalette.Base, QColor(30, 30, 30))
            dark_palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
            dark_palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
            dark_palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
            dark_palette.setColor(QPalette.Text, QColor(220, 220, 220))
            dark_palette.setColor(QPalette.Button, QColor(45, 45, 45))
            dark_palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
            dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
            dark_palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
            app.setPalette(dark_palette)
            app.setFont(QFont('Segoe UI', 10))

        # --- Window sizing (adaptive by default) -----------------------------------
        try:
            screen_geom = QApplication.primaryScreen().availableGeometry()
            self.resize(int(screen_geom.width() * 0.82), int(screen_geom.height() * 0.82))
        except Exception:
            self.resize(900, 720)

        self.setMinimumSize(620, 520)

        self.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: #dddddd;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-size: 10pt;
            }

            QGroupBox {
                border: 1px solid #555555;
                border-radius: 8px;
                margin-top: 10px;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                font-weight: 600;
            }

            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 30px;
            }
            QPushButton:hover {
                background-color: #555555;
            }

            QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 6px;
                color: #ffffff;
            }

            QMenu {
                background-color: #2d2d2d;
                color: #dddddd;
            }
            QMenu::item:selected {
                background-color: #555555;
            }

            QSplitter::handle {
                background-color: #3a3a3a;
            }
        """)

        self.setWindowTitle(f"AutoCraft Bot v{info.VERSION}")

        # --- Central layout: splitter + scroll (real adaptive UI) ------------------
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.splitter)

        # Top: settings (inside scroll area, so small screens don't suffer)
        self.top_scroll = QScrollArea()
        self.top_scroll.setWidgetResizable(True)
        self.top_scroll.setFrameShape(QFrame.NoFrame)
        self.splitter.addWidget(self.top_scroll)

        top_container = QWidget()
        self.top_scroll.setWidget(top_container)
        top_layout = QVBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        # Bottom: logs/status
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        self.splitter.addWidget(bottom_container)
        self.splitter.setStretchFactor(1, 1)

        # --- Inputs: Connection ----------------------------------------------------
        conn_group = QGroupBox("Подключение")
        conn_group.setAccessibleName("Группа: подключение")
        conn_group.setAccessibleDescription("Настройки подключения к Telegram-боту: токен, PIN и список разрешённых ID")

        conn_form = QFormLayout(conn_group)
        conn_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        try:
            conn_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        except Exception:
            pass
        conn_form.setHorizontalSpacing(12)
        conn_form.setVerticalSpacing(10)

        token_label = QLabel("Токен:")
        token_label.setAccessibleName("Метка токена")
        token_label.setAccessibleDescription("Метка для поля ввода токена бота")
        self.token_edit = QLineEdit()
        self.token_edit.setAccessibleName("Поле ввода токена")
        self.token_edit.setAccessibleDescription("Введите токен для подключения к Telegram-боту")
        self.token_edit.setPlaceholderText("Введите токен")
        self.token_edit.setToolTip("Поле ввода токена")
        self.token_edit.setFocus()

        pin_label = QLabel("PIN-код:")
        pin_label.setAccessibleName("Метка PIN-кода")
        pin_label.setAccessibleDescription("Метка для поля ввода PIN-кода")
        self.pin_edit = QLineEdit()
        self.pin_edit.setAccessibleName("Поле ввода PIN-кода")
        self.pin_edit.setAccessibleDescription("Введите PIN-код для авторизации в боте")
        self.pin_edit.setEchoMode(QLineEdit.Password)
        self.pin_edit.setPlaceholderText("Введите PIN-код")
        self.pin_edit.setToolTip("Поле ввода PIN-кода")

        account_ids_label = QLabel("ID аккаунтов:")
        account_ids_label.setAccessibleName("Метка ID аккаунтов")
        account_ids_label.setAccessibleDescription("Метка для поля ввода ID аккаунтов (через запятую)")
        self.account_ids_edit = QLineEdit()
        self.account_ids_edit.setAccessibleName("Поле ввода ID аккаунтов")
        self.account_ids_edit.setAccessibleDescription("Введите ID аккаунтов, разделённые запятыми (каждый от 7 до 10 цифр)")
        self.account_ids_edit.setPlaceholderText("Например: 1234567, 1234567890")
        self.account_ids_edit.setToolTip("Поле ввода ID аккаунтов")

        conn_form.addRow(token_label, self.token_edit)
        conn_form.addRow(pin_label, self.pin_edit)
        conn_form.addRow(account_ids_label, self.account_ids_edit)
        top_layout.addWidget(conn_group)

        # --- Inputs: Telegram API --------------------------------------------------
        api_group = QGroupBox("Telegram API")
        api_group.setAccessibleName("Группа: Telegram API")
        api_group.setAccessibleDescription("Настройки сервера Telegram API: адрес, порт и выбор стандартного сервера")

        api_form = QFormLayout(api_group)
        api_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        try:
            api_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        except Exception:
            pass
        api_form.setHorizontalSpacing(12)
        api_form.setVerticalSpacing(10)

        address_label = QLabel("Адрес API:")
        address_label.setAccessibleName("Метка адреса API")
        address_label.setAccessibleDescription("Метка для поля ввода адреса Telegram API сервера")
        self.address_edit = QLineEdit()
        self.address_edit.setAccessibleName("Поле ввода адреса API")
        self.address_edit.setAccessibleDescription("Введите адрес Telegram API сервера (IP или домен)")
        self.address_edit.setPlaceholderText("Введите адрес API")
        self.address_edit.setToolTip("Поле ввода адреса API")

        port_label = QLabel("Порт API:")
        port_label.setAccessibleName("Метка порта API")
        port_label.setAccessibleDescription("Метка для поля ввода порта Telegram API сервера")
        self.port_edit = QLineEdit()
        self.port_edit.setAccessibleName("Поле ввода порта API")
        self.port_edit.setAccessibleDescription("Введите порт Telegram API сервера")
        self.port_edit.setPlaceholderText("Введите порт API")
        self.port_edit.setToolTip("Поле ввода порта API")

        self.lock_api_checkbox = QCheckBox("Использовать стандартный сервер Telegram API")
        self.lock_api_checkbox.setAccessibleName("Чекбокс: использовать стандартный сервер Telegram API")
        self.lock_api_checkbox.setAccessibleDescription("Если установлен, будут использоваться стандартные адрес и порт Telegram API сервера, а поля адреса и порта станут недоступны")
        self.lock_api_checkbox.setToolTip("Использовать стандартный сервер Telegram API")
        lock = load_lock_api_fields()
        self.lock_api_checkbox.setChecked(lock)
        self.address_edit.setEnabled(not lock)
        self.port_edit.setEnabled(not lock)
        self.lock_api_checkbox.stateChanged.connect(self.on_lock_api_checkbox_changed)

        self.api_settings_button = QPushButton("Настройки API сервера")
        self.api_settings_button.setAccessibleName("Кнопка: Настройки API сервера")
        self.api_settings_button.setAccessibleDescription("Открыть окно настроек локального Telegram API сервера")
        self.api_settings_button.clicked.connect(self.open_api_server_settings)

        api_form.addRow(address_label, self.address_edit)
        api_form.addRow(port_label, self.port_edit)
        api_form.addRow(self.lock_api_checkbox)

        api_footer = QWidget()
        api_footer_l = QHBoxLayout(api_footer)
        api_footer_l.setContentsMargins(0, 0, 0, 0)
        api_footer_l.setSpacing(8)
        api_footer_l.addWidget(self.api_settings_button)
        api_footer_l.addStretch(1)
        api_form.addRow(api_footer)

        top_layout.addWidget(api_group)

        # --- Buttons: Primary action ----------------------------------------------
        control_group = QGroupBox("Управление")
        control_group.setAccessibleName("Группа: управление")
        control_group.setAccessibleDescription("Основные действия: сохранить и подключить, перезапуск, сброс, справка, трей, выход")

        control_layout = QVBoxLayout(control_group)
        control_layout.setContentsMargins(10, 10, 10, 10)
        control_layout.setSpacing(10)

        self.save_run_button = QPushButton("Сохранить и подключить")
        self.save_run_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.save_run_button.setAccessibleName("Кнопка: Сохранить и подключить")
        self.save_run_button.setAccessibleDescription("Сохранить настройки и подключить бота")
        self.save_run_button.setToolTip("Сохранить и подключить")
        self.save_run_button.clicked.connect(self.save_and_run_bot)

        self.toggle_button = QPushButton("Перезапустить бота")
        self.toggle_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.toggle_button.setAccessibleName("Кнопка: Перезапустить бота")
        self.toggle_button.setAccessibleDescription("Перезапустить бота")
        self.toggle_button.setToolTip("Перезапустить бота")
        self.toggle_button.clicked.connect(self.restart_bot)

        self.reset_button = QPushButton("Сброс")
        self.reset_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.reset_button.setAccessibleName("Кнопка: Сброс")
        self.reset_button.setAccessibleDescription("Сброс настроек бота и очистка логов")
        self.reset_button.setToolTip("Сброс")
        self.reset_button.clicked.connect(self.reset_bot)

        self.help_button = QPushButton("Справка")
        self.help_button.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.help_button.setAccessibleName("Кнопка: Справка")
        self.help_button.setAccessibleDescription("Открыть справку")
        self.help_button.setToolTip("Справка")
        self.help_button.clicked.connect(self.show_help)

        # Кнопка и окно «Функции» вынесены в отдельный файл gui_win/functions_window.py
        self.functions_button = create_functions_button(self)

        self.open_panel_button = QPushButton("Открыть панель")
        self.open_panel_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_panel_button.setAccessibleName("Кнопка: Открыть панель")
        self.open_panel_button.setAccessibleDescription(
            "Открыть веб-панель AutoCraft в браузере, если панель запущена"
        )
        self.open_panel_button.setToolTip("Открыть веб-панель в браузере")
        self.open_panel_button.clicked.connect(self.open_webpanel_in_browser)

        self.minimize_tray_button = QPushButton("Свернуть в трей")
        self.minimize_tray_button.setIcon(self.style().standardIcon(QStyle.SP_TitleBarMinButton))
        self.minimize_tray_button.setAccessibleName("Кнопка: Свернуть в трей")
        self.minimize_tray_button.setAccessibleDescription("Свернуть приложение в системный трей")
        self.minimize_tray_button.setToolTip("Свернуть в трей")
        self.minimize_tray_button.clicked.connect(self.hide_to_tray)

        self.exit_button = QPushButton("Выход")
        self.exit_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCloseButton))
        self.exit_button.setAccessibleName("Кнопка: Выход")
        self.exit_button.setAccessibleDescription("Выйти из приложения")
        self.exit_button.setToolTip("Выход")
        self.exit_button.clicked.connect(self.exit_app)

        control_layout.addWidget(self.save_run_button)

        # Адаптивная сетка вторичных кнопок: 3/2/1 колонки в зависимости от ширины окна
        self._control_buttons_grid = ResponsiveButtonGrid(
            [
                self.toggle_button,
                self.reset_button,
                self.help_button,
                self.functions_button,
                self.open_panel_button,
                self.minimize_tray_button,
                self.exit_button,
            ],
            columns=3
        )
        control_layout.addWidget(self._control_buttons_grid)

        top_layout.addWidget(control_group)

        # --- Autostart -------------------------------------------------------------
        autostart_group = QGroupBox("Автозапуск Windows")
        autostart_group.setAccessibleName("Группа: автозапуск")
        autostart_group.setAccessibleDescription("Настройки автозапуска программы вместе с Windows")

        autostart_layout = QVBoxLayout(autostart_group)
        autostart_layout.setContentsMargins(10, 10, 10, 10)
        autostart_layout.setSpacing(10)

        self.autorun_checkbox = QCheckBox("Запускать вместе с Windows")
        self.autorun_checkbox.setFocusPolicy(Qt.StrongFocus)
        self.autorun_checkbox.setAccessibleName("Чекбокс: запускать программу вместе с Windows")
        self.autorun_checkbox.setAccessibleDescription("Если включено, программа будет запускаться при входе в Windows")

        self.start_tray_checkbox = QCheckBox("Запуск сразу в трее")
        self.start_tray_checkbox.setFocusPolicy(Qt.StrongFocus)
        self.start_tray_checkbox.setAccessibleName("Чекбокс: запуск сразу в трее")
        self.start_tray_checkbox.setAccessibleDescription("Если включено, при автозапуске окно не будет показано")

        self.method_group = QGroupBox("Способ автозапуска")
        self.method_group.setAccessibleName("Группа: способ автозапуска")
        self.method_group.setAccessibleDescription("Выберите способ автозапуска: Автовыбор, Папка Автозагрузка, Реестр.")

        self.method_auto = A11yRadioButton("Автовыбор (каскадом)")
        self.method_auto.setAccessibleName("Переключатель: Автовыбор способа")

        self.method_startup = A11yRadioButton("Папка «Автозагрузка» (ярлык/бат)")
        self.method_startup.setAccessibleName("Переключатель: Папка Автозагрузка")

        self.method_registry = A11yRadioButton("Реестр (HKCU\\Run)")
        self.method_registry.setAccessibleName("Переключатель: Реестр HKCU Run")

        method_layout = QVBoxLayout(self.method_group)
        method_layout.addWidget(self.method_auto)
        method_layout.addWidget(self.method_startup)
        method_layout.addWidget(self.method_registry)

        # Навигационная группа для радиокнопок метода автозапуска
        self.method_radios = [self.method_auto, self.method_startup, self.method_registry]
        for _rb in self.method_radios:
            if hasattr(_rb, "set_nav_group"):
                _rb.set_nav_group(self.method_radios)

        # Ensure consistent accessibility behavior across all radios
        for _rb in self.method_radios:
            try:
                _rb.setAutoExclusive(True)
                _rb.setFocusPolicy(Qt.StrongFocus)
            except Exception:
                pass

        # Load current settings and reflect them
        autorun_enabled, start_in_tray, method_choice = load_startup_full()
        self.autorun_checkbox.setChecked(autorun_enabled)
        self.start_tray_checkbox.setChecked(start_in_tray)

        if method_choice == "auto":
            self.method_auto.setChecked(True)
        elif method_choice == "registry":
            self.method_registry.setChecked(True)
        else:
            self.method_startup.setChecked(True)

        self.method_group.setEnabled(autorun_enabled)
        self.start_tray_checkbox.setEnabled(autorun_enabled)

        # Connect handlers
        self.autorun_checkbox.stateChanged.connect(self.on_autorun_checkbox_changed)
        self.start_tray_checkbox.stateChanged.connect(self.on_start_tray_checkbox_changed)
        self.method_auto.toggled.connect(self.on_method_radio_changed)
        self.method_startup.toggled.connect(self.on_method_radio_changed)
        self.method_registry.toggled.connect(self.on_method_radio_changed)

        autostart_layout.addWidget(self.autorun_checkbox)
        autostart_layout.addWidget(self.start_tray_checkbox)
        autostart_layout.addWidget(self.method_group)

        top_layout.addWidget(autostart_group)
        top_layout.addStretch(1)

        # --- Status + logs ---------------------------------------------------------
        self.status_label = QLabel("Бот не запущен.")
        self.status_label.setAccessibleName("Метка состояния")
        self.status_label.setAccessibleDescription("Отображает текущее состояние бота")
        self.status_label.setToolTip("Состояние бота")

        self.monitor_edit = QPlainTextEdit()
        self.monitor_edit.setAccessibleName("Область мониторинга логов")
        self.monitor_edit.setAccessibleDescription("Здесь отображаются логи работы бота")
        self.monitor_edit.setReadOnly(True)
        self.monitor_edit.setPlaceholderText("Логи работы бота.")
        self.monitor_edit.setToolTip("Логи")
        self.monitor_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.monitor_edit.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
        try:
            self.monitor_edit.installEventFilter(self)
            self.monitor_edit.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        except Exception:
            pass

        self.filter_duplicates_checkbox = QCheckBox("Фильтровать дубли логов")
        self.filter_duplicates_checkbox.setChecked(True)
        self.filter_duplicates_checkbox.setAccessibleName("Чекбокс: фильтровать дубли логов")
        self.filter_duplicates_checkbox.setAccessibleDescription("Если включено, повторяющиеся подряд сообщения не будут дублироваться")

        # de-dup window
        self._recent_log_deque = collections.deque(maxlen=500)
        self._recent_log_set = set()
        self._last_appended_line = None
        self._log_user_scrolling = False

        log_group = QGroupBox("Логи")
        log_group.setAccessibleName("Группа: логи")
        log_group.setAccessibleDescription("Панель логов и состояние бота")

        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(8)

        status_line = QHBoxLayout()
        status_line.addWidget(self.status_label)
        status_line.addStretch(1)
        status_line.addWidget(self.filter_duplicates_checkbox)
        log_layout.addLayout(status_line)
        log_layout.addWidget(self.monitor_edit)

        bottom_layout.addWidget(log_group)

        # Give the log pane most of the space by default
        try:
            self.splitter.setSizes([360, 540])
        except Exception:
            pass

        # --- Subscribe to logs + drain early buffer --------------------------------
        try:
            import sys as _sys
            _main = _sys.modules.get('__main__')
            already = getattr(_main, 'gui_log_connected', False) if _main else False
            if not already:
                log_emitter.log_message.connect(self.append_log)
                if _main is not None:
                    setattr(_main, 'gui_log_connected', True)
        except Exception:
            pass
        try:
            for _m in list(pending_log_messages):
                self.append_log(_m)
            pending_log_messages.clear()
            import sys as _sys2
            if '__main__' in _sys2.modules:
                setattr(_sys2.modules['__main__'], 'gui_ready', True)
        except Exception:
            pass

        # --- Load existing credentials and API settings ----------------------------
        try:
            token, pin, allowed_ids_str = load_credentials()
            address, port = load_api_config()
        except Exception:
            token, pin, allowed_ids_str = "", "", ""
            address, port = "", ""
            save_credentials("", "", "")
            save_api_config("", "")
            try:
                log_emitter.log_message.emit("Файл config.ini поврежден, перезаписываем его. Введите данные для подключения.")
            except Exception:
                pass
        else:
            valid = True
            if allowed_ids_str:
                for id_str in allowed_ids_str.split(","):
                    stripped = id_str.strip()
                    if stripped and (not stripped.isdigit() or not (7 <= len(stripped) <= 10)):
                        valid = False
                        break
            if not valid:
                try:
                    log_emitter.log_message.emit("Внимание: формат ID в config.ini выглядит некорректно. Файл не изменён.")
                except Exception:
                    pass

        # Fill fields
        if token:
            self.token_edit.setText(token)
            self.pin_edit.setText(pin)
            self.account_ids_edit.setText(allowed_ids_str)
            self.address_edit.setText(address)
            self.port_edit.setText(port)

            try:
                _debug_log("GUI: token_present -> start_bot()")
            except Exception:
                pass
            try:
                log_emitter.log_message.emit("Решение: токен найден → запускаю бота.")
            except Exception:
                pass

            self.start_bot()

            # Auto-launch API server GUI if configured
            if load_server_autostart():
                self.api_server_window = gui_serverapi.MainWindow()
                self.api_server_window.showMinimized()
            else:
                self.api_server_window = None
        else:
            self.api_server_window = None

        # --- Adaptive policies ------------------------------------------------------
        for w in (self.token_edit, self.pin_edit, self.account_ids_edit, self.address_edit, self.port_edit):
            try:
                w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            except Exception:
                pass
        self.monitor_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # --- Tab order (fixed, predictable for screen readers) ---------------------
        self.setTabOrder(self.token_edit, self.pin_edit)
        self.setTabOrder(self.pin_edit, self.account_ids_edit)
        self.setTabOrder(self.account_ids_edit, self.address_edit)
        self.setTabOrder(self.address_edit, self.port_edit)
        self.setTabOrder(self.port_edit, self.lock_api_checkbox)
        self.setTabOrder(self.lock_api_checkbox, self.api_settings_button)
        self.setTabOrder(self.api_settings_button, self.save_run_button)
        self.setTabOrder(self.save_run_button, self.toggle_button)
        self.setTabOrder(self.toggle_button, self.reset_button)
        self.setTabOrder(self.reset_button, self.help_button)
        self.setTabOrder(self.help_button, self.functions_button)
        self.setTabOrder(self.functions_button, self.open_panel_button)
        self.setTabOrder(self.open_panel_button, self.minimize_tray_button)
        self.setTabOrder(self.minimize_tray_button, self.exit_button)
        self.setTabOrder(self.exit_button, self.autorun_checkbox)
        self.setTabOrder(self.autorun_checkbox, self.start_tray_checkbox)
        self.setTabOrder(self.start_tray_checkbox, self.method_auto)
        self.setTabOrder(self.method_auto, self.method_startup)
        self.setTabOrder(self.method_startup, self.method_registry)
        self.setTabOrder(self.method_registry, self.monitor_edit)

        # --- Icon + tray ------------------------------------------------------------
        if os.path.exists(os.path.join(BASE_DIR, "icon.png")):
            icon = QIcon(os.path.join(BASE_DIR, "icon.png"))
        else:
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.setWindowIcon(icon)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.windowIcon())
        self.tray_icon.setToolTip(f"AutoCraft Bot v{info.VERSION}")

        tray_menu = QMenu()
        restore_action = QAction("Развернуть", self)
        restore_action.setToolTip("Развернуть окно приложения")
        restore_action.setStatusTip("Развернуть окно приложения")
        restore_action.triggered.connect(self.show_normal)

        exit_action = QAction("Выход", self)
        exit_action.setToolTip("Завершить работу приложения")
        exit_action.setStatusTip("Завершить работу приложения")
        exit_action.triggered.connect(self.exit_app)

        tray_menu.addAction(restore_action)
        tray_menu.addSeparator()

        api_settings_action = QAction("Настройка локального API", self)
        api_settings_action.setToolTip("Открыть настройки локального Telegram API сервера")
        api_settings_action.setStatusTip("Открыть окно настройки локального Telegram API сервера")
        api_settings_action.triggered.connect(self.open_api_server_settings)
        tray_menu.addAction(api_settings_action)

        tray_menu.addSeparator()
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.setVisible(False)

        # Auto-hide to tray when launched with --tray
        args = [a.lower() for a in sys.argv[1:]]
        if ("--tray" in args) or ("/tray" in args):
            try:
                log_emitter.log_message.emit("Запуск с флагом --tray: окно будет автоматически свернуто в трей через 10 секунд.")
            except Exception:
                pass
            QTimer.singleShot(10000, self._auto_hide_to_tray)
        else:
            try:
                log_emitter.log_message.emit("Запуск без флага --tray: автосворачивание отключено.")
            except Exception:
                pass

        # First adaptive layout pass (for the initial size)
        try:
            self._update_responsive_controls()
        except Exception:
            pass


    # --- Adaptive UI helpers ------------------------------------------------
    def _update_responsive_controls(self):
        """Подстройка раскладки кнопок под ширину окна (3/2/1 колонки)."""
        try:
            w = self.centralWidget().width() if self.centralWidget() else self.width()
        except Exception:
            w = self.width()
        if w >= 980:
            cols = 3
        elif w >= 720:
            cols = 2
        else:
            cols = 1

        try:
            if hasattr(self, "_control_buttons_grid") and self._control_buttons_grid:
                self._control_buttons_grid.relayout(cols)
        except Exception:
            pass

    def resizeEvent(self, event):
        try:
            self._update_responsive_controls()
        except Exception:
            pass
        super().resizeEvent(event)


    # --- Enforcement on boot -------------------------------------------------
    def _enforce_autorun_from_config_on_boot(self):
        if not _is_windows():
            return
        cfg_autorun, cfg_tray, cfg_method = load_startup_full()
        # Детектируем текущее состояние
        det_enabled, det_tray, det_method = detect_autorun()
        # Если в конфиге нужно, а фактически не стоит — ставим лучшим доступным способом
        if cfg_autorun and not det_enabled:
            def _log(m): 
                try: log_emitter.log_message.emit(m)
                except Exception: pass
            _log("Проверка автозапуска: требуется по конфигу, но не обнаружен — применяем.")
            apply_autorun_selected(True, cfg_tray, cfg_method, log=_log)
        # Если в конфиге выключено, а что-то стоит — удалим
        elif not cfg_autorun and det_enabled:
            def _log(m): 
                try: log_emitter.log_message.emit(m)
                except Exception: pass
            _log(f"Проверка автозапуска: в конфиге выключен, а обнаружен ({det_method}) — удаляем.")
            apply_autorun_selected(False, False, cfg_method, log=_log)

    # ------------------------------------------------------------------------

    def keyPressEvent(self, event):
        # Do not steal arrow/navigation keys from text widgets (лог, редакторы, инпуты).
        widget = self.focusWidget()
        if isinstance(widget, (QPlainTextEdit, QTextEdit, QLineEdit, QTextBrowser)):
            return super().keyPressEvent(event)
        if event.key() in (Qt.Key_Up, Qt.Key_Left):
            self.focusPreviousChild()
        elif event.key() in (Qt.Key_Down, Qt.Key_Right):
            self.focusNextChild()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if isinstance(widget, QPushButton):
                widget.click()
        else:
            super().keyPressEvent(event)

    # --- Duplicate-aware log appender ---------------------------------------
    def _should_skip_duplicate(self, msg: str) -> bool:
        """Return True if msg is a duplicate we want to hide (when filtering is on)."""
        if not msg:
            return False
        key = msg.rstrip("\n")
        # 1) drop exact same as last appended line (classic double-connect case)
        if self._last_appended_line is not None and key == self._last_appended_line:
            return True
        # 2) drop if we've already seen this within the recent window
        if key in self._recent_log_set:
            return True
        return False

    def _remember_line(self, key: str):
        """Remember that we printed this line (bounded window)."""
        dq = self._recent_log_deque
        st = self._recent_log_set
        if len(dq) == dq.maxlen:
            old = dq.popleft()
            st.discard(old)
        dq.append(key)
        st.add(key)
        self._last_appended_line = key

    def append_log(self, msg):
        """Append log line (optionally filtered) и держим автоскролл адекватным."""
        try:
            vsb = self.monitor_edit.verticalScrollBar()
            at_end = vsb.value() >= (vsb.maximum() - 2)
        except Exception:
            at_end = True

        # --- duplicate filter ---
        if self.filter_duplicates_checkbox.isChecked():
            key = (msg or "").rstrip("\n")
            if self._should_skip_duplicate(key):
                # silently ignore duplicate
                return
        else:
            key = (msg or "").rstrip("\n")

        # Append text safely
        if msg and not msg.endswith("\n"):
            self.monitor_edit.appendPlainText(msg)
        else:
            from PyQt5.QtGui import QTextCursor as _QTextCursor
            self.monitor_edit.moveCursor(_QTextCursor.End)
            self.monitor_edit.insertPlainText(msg)

        # Remember to filter future dups
        self._remember_line(key)

        # Respect user scrolling
        try:
            if at_end and not getattr(self, "_log_user_scrolling", False):
                vsb.setValue(vsb.maximum())
        except Exception:
            pass

    def save_and_run_bot(self):
        token = self.token_edit.text().strip()
        pin = self.pin_edit.text().strip()
        allowed_ids_str = self.account_ids_edit.text().strip()
        address = self.address_edit.text().strip()
        port = self.port_edit.text().strip()

        # Validate token
        if not token or ":" not in token:
            errorBox = QMessageBox()
            errorBox.setIcon(QMessageBox.Warning)
            errorBox.setWindowTitle("Ошибка ввода токена")
            errorBox.setText("Ошибка: введите корректный токен.")
            errorBox.setInformativeText("Пример токена: 123456:ABCdefGhIjKl")
            errorBox.exec_()
            return

        # Validate account IDs
        allowed_accounts.clear()
        if allowed_ids_str:
            valid_ids = []
            for id_str in allowed_ids_str.split(","):
                id_str = id_str.strip()
                if id_str:
                    if not id_str.isdigit() or not (7 <= len(id_str) <= 10):
                        errorBox = QMessageBox()
                        errorBox.setIcon(QMessageBox.Warning)
                        errorBox.setWindowTitle("Ошибка ввода ID аккаунтов")
                        errorBox.setText("Ошибка: неверный формат ID аккаунтов.")
                        errorBox.setInformativeText("ID аккаунта должен содержать от 7 до 10 цифр.")
                        errorBox.exec_()
                        return
                    valid_ids.append(int(id_str))
            valid_ids = list(set(valid_ids))
            if len(valid_ids) > 10:
                errorBox = QMessageBox()
                errorBox.setIcon(QMessageBox.Warning)
                errorBox.setWindowTitle("Ошибка ввода ID аккаунтов")
                errorBox.setText("Ошибка: можно вводить не более 10 ID аккаунтов.")
                errorBox.setInformativeText("Например: 1234567, 1234567890")
                errorBox.exec_()
                return
            for id_num in valid_ids:
                allowed_accounts.add(id_num)
            allowed_ids_str = ", ".join(map(str, sorted(valid_ids)))

        # Validate address/port (optional: ensure port is numeric)
        if address and port and not port.isdigit():
            errorBox = QMessageBox()
            errorBox.setIcon(QMessageBox.Warning)
            errorBox.setWindowTitle("Ошибка ввода порта")
            errorBox.setText("Ошибка: порт должен быть числом.")
            errorBox.exec_()
            return

        # Confirmation dialog for bot connection
        confirmDialog = BotConfirmDialog()
        thread = QThread()
        worker = BotNameWorker(token)
        worker.moveToThread(thread)
        worker.bot_name_found.connect(confirmDialog.update_bot_name)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        thread.start()

        result = confirmDialog.exec_()
        if result == QDialog.Accepted:
            save_credentials(token, pin, allowed_ids_str)
            save_api_config(address, port)
            global TOKEN, PIN_CODE, bot_thread, current_bot, current_loop
            TOKEN = token
            PIN_CODE = pin
            self.status_label.setText("Запуск бота...")
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
            QTimer.singleShot(3000, self.update_bot_name_status)
        else:
            self.status_label.setText("Подключение отменено пользователем.")

    def update_bot_name_status(self):
        if current_bot is not None and hasattr(current_bot, 'username'):
            bot_name = current_bot.username
        else:
            bot_name = "неизвестный бот"
        self.status_label.setText(f"Подключение к боту: {bot_name}. Бот запущен.")

    def restart_bot(self):
        """
        Перезапуск бота по кнопке: вызываем единый полный перезапуск из sys_core/full_restart.py,
        чтобы логика была в одном месте и могла вызываться другими модулями.
        """
        try:
            log_emitter.log_message.emit("Действие: Перезапустить бота → sys_core.full_restart (полный перезапуск).")
        except Exception:
            pass
        try:
            self.status_label.setText("Перезапуск: формирую отчёт и запускаю полный перезапуск…")
        except Exception:
            pass

        # Запускаем в фоновом потоке, чтобы не блокировать GUI
        worker = threading.Thread(target=self._full_restart_via_sys_core, daemon=True)
        worker.start()

    def _full_restart_via_sys_core(self):
        """
        Фоновая задача для кнопки «Перезапустить бота».
        Запускает sys_core.full_restart.full_restart(...) и выводит лог в GUI через log_emitter.
        """
        import asyncio
        import traceback

        # На всякий случай добавим base_dir в sys.path (и для исходников, и для Nuitka EXE)
        try:
            base_dir = BASE_DIR
        except Exception:
            base_dir = os.getcwd()

        try:
            if base_dir and base_dir not in sys.path:
                sys.path.insert(0, base_dir)
        except Exception:
            pass

        # Импортируем единый перезапуск
        try:
            from sys_core.full_restart import full_restart as _full_restart
        except Exception:
            # Попробуем добавить типовые пути проекта и повторить импорт
            try:
                candidates = [
                    os.path.join(base_dir, "src"),
                    os.path.join(base_dir, "src", "moduls"),
                    os.path.join(base_dir, "src", "gui_win"),
                    os.path.join(base_dir, "moduls"),
                    os.path.join(base_dir, "gui_win"),
                ]
                for p in candidates:
                    if os.path.isdir(p) and p not in sys.path:
                        sys.path.insert(0, p)
            except Exception:
                pass

            try:
                from sys_core.full_restart import full_restart as _full_restart
            except Exception:
                try:
                    log_emitter.log_message.emit("[ОШИБКА] Не удалось импортировать sys_core.full_restart.full_restart")
                    log_emitter.log_message.emit(traceback.format_exc())
                except Exception:
                    pass
                return

        async def _send(line: str):
            """Отправка строк в GUI-лог через потокобезопасный PyQt-сигнал."""
            try:
                s = "" if line is None else str(line)
                lines = s.splitlines() or [""]
                for ln in lines:
                    log_emitter.log_message.emit(ln)
            except Exception:
                pass

        try:
            asyncio.run(_full_restart(send=_send))
        except SystemExit:
            # Полный перезапуск штатно завершает процесс (watchdog поднимет заново)
            raise
        except Exception:
            try:
                log_emitter.log_message.emit("[ОШИБКА] Полный перезапуск завершился с ошибкой в GUI-потоке:")
                log_emitter.log_message.emit(traceback.format_exc())
            except Exception:
                pass
            # Фолбэк: всё равно выходим, чтобы внешний вотчдог поднял процесс
            try:
                os._exit(42)
            except Exception:
                pass


    def on_log_scroll_changed(self, value):
        try:
            vsb = self.monitor_edit.verticalScrollBar()
            self._log_user_scrolling = value < (vsb.maximum() - 2)
        except Exception:
            self._log_user_scrolling = False

    def eventFilter(self, obj, event):
        # Mark that user interacts with the log view so we don't auto-jump
        if obj is self.monitor_edit:
            if event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                self._log_user_scrolling = True
            elif event.type() == QEvent.KeyPress:
                key = event.key()
                if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
                    self._log_user_scrolling = True
            elif event.type() == QEvent.FocusOut:
                # When leaving the log, reset the flag so future logs can autoscroll
                self._log_user_scrolling = False
        return super().eventFilter(obj, event)

    def start_bot(self):
        global TOKEN, PIN_CODE, bot_thread, current_bot, current_loop, allowed_accounts
        token = self.token_edit.text().strip()
        pin = self.pin_edit.text().strip()
        allowed_ids_str = self.account_ids_edit.text().strip()
        address = self.address_edit.text().strip()
        port = self.port_edit.text().strip()

        if not token:
            self.status_label.setText("Ошибка: введите токен.")
            return

        TOKEN = token
        PIN_CODE = pin
        allowed_accounts.clear()
        if allowed_ids_str:
            valid_ids = []
            for id_str in allowed_ids_str.split(","):
                id_str = id_str.strip()
                if id_str:
                    if not id_str.isdigit() or not (7 <= len(id_str) <= 10):
                        self.status_label.setText("Ошибка: ID аккаунта должен содержать от 7 до 10 цифр.")
                        return
                    valid_ids.append(int(id_str))
            valid_ids = list(set(valid_ids))
            if len(valid_ids) > 10:
                self.status_label.setText("Ошибка: можно вводить не более 10 ID аккаунтов.")
                return
            for id_num in valid_ids:
                allowed_accounts.add(id_num)
            allowed_ids_str = ", ".join(map(str, sorted(valid_ids)))
        save_credentials(token, pin, allowed_ids_str)
        save_api_config(address, port)
        self.status_label.setText("Бот запускается...")
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        self.status_label.setText("Бот запущен.")
        self.toggle_button.setText("Перезапустить бота")
        self.toggle_button.setAccessibleName("Кнопка: Перезапустить бота")
        self.toggle_button.setAccessibleDescription("Нажмите для перезапуска бота")
        self.bot_running = True

    def stop_bot(self):
        global current_bot, current_loop, bot_thread
        try:
            if current_loop is not None:
                current_loop.call_soon_threadsafe(current_loop.stop)
                if bot_thread is not None:
                    bot_thread.join(timeout=5)
        except Exception:
            pass
        current_bot = None
        current_loop = None
        bot_thread = None
        self.status_label.setText("Бот остановлен.")
        self.toggle_button.setText("Перезапустить бота")
        self.toggle_button.setAccessibleName("Кнопка: Перезапустить бота")
        self.toggle_button.setAccessibleDescription("Нажмите для перезапуска бота")
        self.bot_running = False

    def reset_bot(self):
        global TOKEN, PIN_CODE, current_bot, current_loop, bot_thread, allowed_accounts, authorized_users, note_mode, pending_note, file_mode, cmd_mode, in_cmd_menu, power_mode, pending_power_action, infiles_mode, plugins_mode
        if current_loop is not None:
            try:
                current_loop.call_soon_threadsafe(current_loop.stop)
                if bot_thread is not None:
                    bot_thread.join(timeout=5)
            except Exception:
                pass
        TOKEN = ""
        PIN_CODE = ""
        allowed_accounts.clear()
        authorized_users.clear()
        note_mode.clear()
        pending_note.clear()
        file_mode.clear()
        cmd_mode.clear()
        in_cmd_menu.clear()
        power_mode.clear()
        pending_power_action.clear()
        infiles_mode.clear()
        plugins_mode.clear()
        self.token_edit.setText("")
        self.pin_edit.setText("")
        self.account_ids_edit.setText("")
        self.address_edit.setText("")
        self.port_edit.setText("")
        save_credentials("", "", "")
        save_api_config("", "")
        self.status_label.setText("Бот сброшен. Файл учетных данных очищен.")

    
    def _close_managed_browser_before_shutdown(self):
        """Закрывает браузер ТОЛЬКО если он запущен через модуль управления браузером (moduls/nostartrunmodulbrowsrem)."""
        try:
            import importlib
            import inspect
            import asyncio

            # Приоритетно берём модули из папки moduls рядом с программой/EXE
            moduls_dir = os.path.join(BASE_DIR, "moduls")
            if os.path.isdir(moduls_dir) and moduls_dir not in sys.path:
                sys.path.insert(0, moduls_dir)

            # Важно: если модуль уже загружен, берём его (с сохранением состояния CTRL)
            m = sys.modules.get("nostartrunmodulbrowsrem") or sys.modules.get("moduls.nostartrunmodulbrowsrem")
            if m is None:
                m = importlib.import_module("nostartrunmodulbrowsrem")

            ctrl = getattr(m, "CTRL", None)
            if ctrl is None:
                return

            selected = None
            try:
                get_sel = getattr(ctrl, "get_selected", None)
                if callable(get_sel):
                    selected = get_sel()
                else:
                    selected = getattr(ctrl, "selected", None)
            except Exception:
                selected = None

            if not selected:
                return

            quit_fn = getattr(ctrl, "quit", None) or getattr(ctrl, "close", None) or getattr(ctrl, "shutdown", None)
            if not quit_fn:
                return

            # Поддержка sync/async вариантов с таймаутом
            if inspect.iscoroutinefunction(quit_fn):
                asyncio.run(asyncio.wait_for(quit_fn(selected), timeout=5.0))
            else:
                try:
                    res = quit_fn(selected, timeout=5)
                except TypeError:
                    res = quit_fn(selected)

                if inspect.isawaitable(res):
                    asyncio.run(asyncio.wait_for(res, timeout=5.0))
        except Exception:
            # Выход/закрытие приложения не должен падать из-за браузера
            return


    def exit_app(self):
        # Перед выходом: закрыть управляемый браузер (если он открыт через модуль)
        self._close_managed_browser_before_shutdown()
        # First, stop the local Telegram API server window and process
        if hasattr(self, "api_server_window") and self.api_server_window:
            try:
                proc = self.api_server_window.proc
                # If server is running, stop it and close window after stop
                if proc.state() == gui_serverapi.QProcess.Running:
                    proc.finished.connect(lambda exitCode, exitStatus: self.api_server_window.close())
                    self.api_server_window.stop_server()
                else:
                    # If not running, close the window immediately
                    self.api_server_window.close()
            except Exception:
                pass
        # Then stop the Telegram bot
        self.stop_bot()
        # Quit the main application
        QApplication.quit()

    def closeEvent(self, event):
        # Перед закрытием окна: закрыть управляемый браузер (если он открыт через модуль)
        self._close_managed_browser_before_shutdown()
        # On main window close, ensure server window and bot are stopped
        if hasattr(self, "api_server_window") and self.api_server_window:
            try:
                proc = self.api_server_window.proc
                if proc.state() == gui_serverapi.QProcess.Running:
                    proc.finished.connect(lambda exitCode, exitStatus: self.api_server_window.close())
                    self.api_server_window.stop_server()
                else:
                    self.api_server_window.close()
            except Exception:
                pass
        # Stop the Telegram bot
        self.stop_bot()
        # Accept the close event
        event.accept()
        QApplication.quit()


    def hide_to_tray(self):
        try:
            log_emitter.log_message.emit("Действие: свернуть в трей.")
        except Exception:
            pass
        self.hide()
        self.tray_icon.setVisible(True)

    def _auto_hide_to_tray(self):
        try:
            log_emitter.log_message.emit("Автосворачивание: скрываем окно в трей по настройке через 10 секунд.")
        except Exception:
            pass
        self.hide_to_tray()

    def show_normal(self):
        self.show()
        self.tray_icon.setVisible(False)
        # If launched with --tray flag, start hidden to tray (useful for autorun)


    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_normal()


    def _get_webpanel_backend(self):
        """
        Возвращает backend веб-панели тем же способом, что и gui_win_webpanel_window.py:
        пробуем startrunmodulwebpanel, затем moduls.startrunmodulwebpanel.
        """
        last_err = None
        for name in ("startrunmodulwebpanel", "moduls.startrunmodulwebpanel"):
            try:
                return importlib.import_module(name)
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        raise ImportError("Не удалось импортировать backend веб-панели")

    @staticmethod
    def _normalize_panel_url_for_browser(url: str) -> str:
        """
        Нормализует URL панели для открытия в браузере.
        Если host=0.0.0.0, подменяем на 127.0.0.1 как в окне веб-панели.
        """
        try:
            parts = urlsplit((url or "").strip())
            host = parts.hostname or ""
            if host == "0.0.0.0":
                # Собираем netloc вручную, чтобы сохранить порт.
                port = f":{parts.port}" if parts.port else ""
                netloc = f"127.0.0.1{port}"
                return urlunsplit((parts.scheme or "http", netloc, parts.path, parts.query, parts.fragment))
            return url
        except Exception:
            return url

    def _resolve_webpanel_url(self):
        """
        Возвращает (running, url, err_message)
        running=True только если панель реально запущена.
        url берётся из runtime (srv.url), как в gui_win_webpanel_window.py.
        """
        try:
            backend = self._get_webpanel_backend()
        except Exception as e:
            return False, "", f"Не удалось подключиться к модулю веб-панели: {e}"

        srv = getattr(backend, "_server", None)

        running = False
        try:
            checker = getattr(backend, "_is_panel_running", None)
            if callable(checker):
                running = bool(checker())
            elif srv is not None and hasattr(srv, "is_running"):
                running = bool(srv.is_running())
        except Exception:
            running = False

        if not running:
            return False, "", "Панель не запущена. Сначала нажми «Запустить панель»."

        # URL только от запущенного runtime, чтобы совпадало с логикой окна веб-панели.
        url = ""
        try:
            if srv is not None and hasattr(srv, "url"):
                url = str(srv.url() or "").strip()
        except Exception:
            url = ""

        # Фолбэк на конфиг (на случай, если runtime не отдал URL, но статус running уже true).
        if not url:
            cfg = None
            try:
                if srv is not None:
                    cfg = srv.runtime.config
            except Exception:
                cfg = None
            if cfg is None:
                try:
                    panel_config = getattr(backend, "panel_config", None)
                    load_cfg = getattr(panel_config, "load_config", None) if panel_config else None
                    base_dir = getattr(backend, "base_dir", None)
                    if callable(load_cfg):
                        cfg = load_cfg(base_dir)
                except Exception:
                    cfg = None

            try:
                host = str(getattr(cfg, "host", "") or "").strip()
                port = str(getattr(cfg, "port", "") or "").strip()
                if host and port:
                    if host == "0.0.0.0":
                        host = "127.0.0.1"
                    url = f"http://{host}:{port}"
            except Exception:
                url = ""

        if not url:
            return False, "", "Панель запущена, но URL не удалось определить."

        return True, self._normalize_panel_url_for_browser(url), ""

    def open_webpanel_in_browser(self):
        """
        Кнопка в main GUI: открывает веб-панель в браузере,
        но только если панель запущена.
        """
        running, url, err = self._resolve_webpanel_url()
        if not running:
            QMessageBox.information(self, "Веб-панель", err or "Панель не запущена.")
            return

        try:
            ok = webbrowser.open(url, new=2)
            if ok:
                self.status_label.setText(f"Открываю веб-панель: {url}")
            else:
                self.status_label.setText("Не удалось открыть браузер автоматически.")
                QMessageBox.warning(self, "Веб-панель", f"Не удалось открыть браузер автоматически.\nАдрес панели:\n{url}")
        except Exception as e:
            QMessageBox.warning(self, "Веб-панель", f"Ошибка при открытии панели:\n{e}\n\nАдрес панели:\n{url}")

    def show_help(self):
        """Открыть справку. Модуль подгружается только по нажатию кнопки."""
        try:
            import importlib
            # Ленивая загрузка: не тащим окно справки при старте GUI
            mod = importlib.import_module('gui_win.help_window')
            show_fn = getattr(mod, 'show_help_dialog', None)
            if callable(show_fn):
                show_fn(self)
            else:
                raise AttributeError('show_help_dialog не найден')
        except Exception as e:
            try:
                QMessageBox.warning(self, "Справка", f"Не удалось открыть справку: {e}")
            except Exception:
                pass


    def open_api_server_settings(self):
        """Open the Local Telegram API Server settings window"""
        # If window exists, restore or show it
        if hasattr(self, "api_server_window") and self.api_server_window:
            if self.api_server_window.isMinimized():
                self.api_server_window.showNormal()
                self.api_server_window.activateWindow()
            else:
                self.api_server_window.show()
                self.api_server_window.activateWindow()
        else:
            # First launch: open normally
            self.api_server_window = gui_serverapi.MainWindow()
            self.api_server_window.show()

    def on_lock_api_checkbox_changed(self, state):
        locked = (state == Qt.Checked)
        self.address_edit.setEnabled(not locked)
        self.port_edit.setEnabled(not locked)
        save_lock_api_fields(locked)

    # --- AUTOSTART HANDLERS --------------------------------------------------
    def on_autorun_checkbox_changed(self, state):
        enabled = (state == Qt.Checked) and _is_windows()
        self.method_group.setEnabled(enabled)
        self.start_tray_checkbox.setEnabled(enabled)
        start_in_tray = self.start_tray_checkbox.isChecked() if enabled else False
        save_startup_settings(enabled, start_in_tray)
        method = self._current_method_choice() if enabled else (load_startup_full()[2])
        def _log(m):
            try: log_emitter.log_message.emit(m)
            except Exception: pass
        ok = apply_autorun_selected(enabled, start_in_tray, method, log=_log)
        if not ok and _is_windows():
            QMessageBox.warning(self, "Автозапуск", "Не удалось применить настройку автозапуска.\nПроверьте права пользователя.")


    def _current_method_choice(self) -> str:
        if self.method_auto.isChecked():
            return "auto"
        if self.method_registry.isChecked():
            return "registry"
        return "startup"

    def on_method_radio_changed(self, checked):
        if not checked:
            return
        method = self._current_method_choice()
        save_startup_method(method)
        autorun_enabled = self.autorun_checkbox.isChecked() and _is_windows()
        start_in_tray = self.start_tray_checkbox.isChecked()
        if autorun_enabled:
            def _log(m):
                try: log_emitter.log_message.emit(m)
                except Exception: pass
            ok = apply_autorun_selected(True, start_in_tray, method, log=_log)
            if not ok and _is_windows():
                QMessageBox.warning(self, "Автозапуск", "Не удалось применить выбранный способ автозапуска.")

    def on_start_tray_checkbox_changed(self, state):
            start_in_tray = (state == Qt.Checked)
            autorun_enabled = self.autorun_checkbox.isChecked() and _is_windows()
            if start_in_tray and not autorun_enabled:
                # Держим доступным для фокуса, но не даём включить без автозапуска
                self.start_tray_checkbox.blockSignals(True)
                self.start_tray_checkbox.setChecked(False)
                self.start_tray_checkbox.blockSignals(False)
                QMessageBox.information(self, "Запуск в трее",
                                        "Опция доступна только при включённом автозапуске.")
                start_in_tray = False
            # Сохраняем
            save_startup_settings(autorun_enabled, start_in_tray)
            # Обновляем все механизмы автозапуска, если он включён
            if autorun_enabled:
                def _log(m):
                    try: log_emitter.log_message.emit(m)
                    except Exception: pass
                method = self._current_method_choice()
                ok = apply_autorun_selected(True, start_in_tray, method, log=_log)
                if not ok and _is_windows():
                    QMessageBox.warning(self, "Автозапуск", "Не удалось обновить параметры автозапуска в системе.")


if __name__ == '__main__':
    print("Ошибка: gui.py не предназначен для самостоятельного запуска. Запусти bot-ok.py вместо этого.")
    sys.exit(1)
