
import sys
import os
import threading
import requests
import configparser
import info
import gui_serverapi
import collections  # <-- for recent-log de-dup window
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QTextEdit, QTextBrowser, QSystemTrayIcon, QMenu, QAction, QStyle, QStyleFactory, QSizePolicy,
    QMessageBox, QDialog, QVBoxLayout, QDialogButtonBox, QCheckBox, QFrame, QHBoxLayout, QGroupBox, QRadioButton)
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


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpDialog")
        self.setWindowTitle("Справка")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setAccessibleName("Окно справки")
        self.setAccessibleDescription("Описание возможностей программы и контакты поддержки")
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)

        if parent:
            try:
                self.setPalette(parent.palette())
                if parent.styleSheet():
                    self.setStyleSheet(parent.styleSheet())
            except Exception:
                pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        header = QLabel("Справка")
        header.setObjectName("helpDialogHeader")
        header.setAccessibleName("Заголовок справки")
        header.setAccessibleDescription("Заголовок окна справки")
        header.setAlignment(Qt.AlignLeft)
        header.setStyleSheet("font-size: 16pt; font-weight: 600;")
        layout.addWidget(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setAccessibleName("Разделитель")
        layout.addWidget(divider)

        self.text_view = QTextBrowser()
        self.text_view.setObjectName("helpDialogText")
        self.text_view.setReadOnly(True)
        help_text = info.CONTACT_TEXT.strip() + "\n\n" + info.HELP_TEXT.strip()
        set_markdown = getattr(self.text_view, "setMarkdown", None)
        if callable(set_markdown):
            try:
                set_markdown(help_text)
            except Exception:
                self.text_view.setPlainText(help_text)
        else:
            self.text_view.setPlainText(help_text)
        self.text_view.setAccessibleName("Текст справки")
        self.text_view.setAccessibleDescription("Подробная справка и контакты поддержки")
        self.text_view.setFocusPolicy(Qt.StrongFocus)
        self.text_view.setTabChangesFocus(True)
        self.text_view.setFrameShape(QFrame.NoFrame)
        self.text_view.setTextInteractionFlags(
            Qt.TextSelectableByKeyboard
            | Qt.TextSelectableByMouse
            | Qt.LinksAccessibleByKeyboard
            | Qt.LinksAccessibleByMouse
        )
        self.text_view.setOpenExternalLinks(True)
        self.text_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_view.document().setDefaultFont(QFont("Segoe UI", 10))
        layout.addWidget(self.text_view)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn = button_box.button(QDialogButtonBox.Close)
        close_btn.setText("Закрыть")
        close_btn.setAccessibleName("Закрыть справку")
        close_btn.setAccessibleDescription("Закрывает окно справки")
        close_btn.setDefault(True)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setTabOrder(self.text_view, close_btn)
        self.text_view.moveCursor(QTextCursor.Start)
        self.text_view.ensureCursorVisible()
        self.text_view.setFocus(Qt.TabFocusReason)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        app = QApplication.instance()
        if app:
            app.setQuitOnLastWindowClosed(False)
            app.setStyle(QStyleFactory.create('Fusion'))
            dark_palette = QPalette()
            dark_palette.setColor(QPalette.Window, QColor(45,45,45))
            dark_palette.setColor(QPalette.WindowText, QColor(220,220,220))
            dark_palette.setColor(QPalette.Base, QColor(30,30,30))
            dark_palette.setColor(QPalette.AlternateBase, QColor(45,45,45))
            dark_palette.setColor(QPalette.ToolTipBase, QColor(255,255,220))
            dark_palette.setColor(QPalette.ToolTipText, QColor(0,0,0))
            dark_palette.setColor(QPalette.Text, QColor(220,220,220))
            dark_palette.setColor(QPalette.Button, QColor(45,45,45))
            dark_palette.setColor(QPalette.ButtonText, QColor(220,220,220))
            dark_palette.setColor(QPalette.Highlight, QColor(42,130,218))
            dark_palette.setColor(QPalette.HighlightedText, QColor(255,255,255))
            app.setPalette(dark_palette)
            app.setFont(QFont('Segoe UI', 10))
        screen_geom = QApplication.primaryScreen().availableGeometry()
        self.resize(int(screen_geom.width()*0.8), int(screen_geom.height()*0.8))
        self.setMinimumSize(600, 500)
        self.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: #dddddd;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-size: 10pt;
            }
            QPushButton {
                background-color: #444444;
                color: #ffffff;
                border: none;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QLineEdit, QPlainTextEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px;
                color: #ffffff;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #dddddd;
            }
            QMenu::item:selected {
                background-color: #555555;
            }
        """)
        self.setWindowTitle(f"AutoCraft Bot v{info.VERSION}")
        self.setGeometry(100, 100, 600, 550)
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        layout = QGridLayout(central_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        # Token input
        token_label = QLabel("Токен:")
        token_label.setAccessibleName("Метка токена")
        token_label.setAccessibleDescription("Метка для поля ввода токена бота")
        self.token_edit = QLineEdit()
        self.token_edit.setAccessibleName("Поле ввода токена")
        self.token_edit.setAccessibleDescription("Введите токен для подключения к Telegram-боту")
        self.token_edit.setPlaceholderText("Введите токен")
        self.token_edit.setToolTip("Поле ввода токена")
        self.token_edit.setFocus()

        # PIN input
        pin_label = QLabel("PIN-код:")
        pin_label.setAccessibleName("Метка PIN-кода")
        pin_label.setAccessibleDescription("Метка для поля ввода PIN-кода")
        self.pin_edit = QLineEdit()
        self.pin_edit.setAccessibleName("Поле ввода PIN-кода")
        self.pin_edit.setAccessibleDescription("Введите PIN-код для авторизации в боте")
        self.pin_edit.setEchoMode(QLineEdit.Password)
        self.pin_edit.setPlaceholderText("Введите PIN-код")
        self.pin_edit.setToolTip("Поле ввода PIN-кода")

        # Allowed account IDs input
        account_ids_label = QLabel("ID аккаунтов:")
        account_ids_label.setAccessibleName("Метка ID аккаунтов")
        account_ids_label.setAccessibleDescription("Метка для поля ввода ID аккаунтов (через запятую)")
        self.account_ids_edit = QLineEdit()
        self.account_ids_edit.setAccessibleName("Поле ввода ID аккаунтов")
        self.account_ids_edit.setAccessibleDescription("Введите ID аккаунтов, разделённые запятыми (каждый от 7 до 10 цифр)")
        self.account_ids_edit.setPlaceholderText("Например: 1234567, 1234567890")
        self.account_ids_edit.setToolTip("Поле ввода ID аккаунтов")

        # Address input (Telegram API server)
        address_label = QLabel("Адрес API:")
        address_label.setAccessibleName("Метка адреса API")
        address_label.setAccessibleDescription("Метка для поля ввода адреса Telegram API сервера")
        self.address_edit = QLineEdit()
        self.address_edit.setAccessibleName("Поле ввода адреса API")
        self.address_edit.setAccessibleDescription("Введите адрес Telegram API сервера (IP или домен)")
        self.address_edit.setPlaceholderText("Введите адрес API")
        self.address_edit.setToolTip("Поле ввода адреса API")

        # Port input (Telegram API server)
        port_label = QLabel("Порт API:")
        port_label.setAccessibleName("Метка порта API")
        port_label.setAccessibleDescription("Метка для поля ввода порта Telegram API сервера")
        self.port_edit = QLineEdit()
        # Checkbox to lock API fields
        self.lock_api_checkbox = QCheckBox("Использовать стандартный сервер Telegram API")
        self.lock_api_checkbox.setAccessibleName("Чекбокс: использовать стандартный сервер Telegram API")
        self.lock_api_checkbox.setAccessibleDescription("Если установлен, будут использоваться стандартные адрес и порт Telegram API сервера, а поля адреса и порта будут недоступны для редактирования")
        self.lock_api_checkbox.setToolTip("Использовать стандартный сервер Telegram API")
        lock = load_lock_api_fields()
        self.lock_api_checkbox.setChecked(lock)
        self.address_edit.setEnabled(not lock)
        self.port_edit.setEnabled(not lock)
        self.lock_api_checkbox.stateChanged.connect(self.on_lock_api_checkbox_changed)

        self.port_edit.setAccessibleName("Поле ввода порта API")
        self.port_edit.setAccessibleDescription("Введите порт Telegram API сервера")
        self.port_edit.setPlaceholderText("Введите порт API")
        self.port_edit.setToolTip("Поле ввода порта API")

        # Save and Connect button
        self.save_run_button = QPushButton("Сохранить и подключить")
        self.save_run_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.save_run_button.setAccessibleName("Кнопка: Сохранить и подключить")
        self.save_run_button.setAccessibleDescription("Нажмите для сохранения настроек и подключения бота после проверки корректности ввода")
        self.save_run_button.setToolTip("Сохранить и подключить")
        self.save_run_button.clicked.connect(self.save_and_run_bot)

        # Other control buttons
        self.toggle_button = QPushButton("Перезапустить бота")
        self.toggle_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.toggle_button.setAccessibleName("Кнопка: Перезапустить бота")
        self.toggle_button.setAccessibleDescription("Нажмите для перезапуска бота")
        self.toggle_button.setToolTip("Перезапустить бота")
        self.toggle_button.clicked.connect(self.restart_bot)

        self.reset_button = QPushButton("Сброс")
        self.reset_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.reset_button.setAccessibleName("Кнопка: Сброс")
        self.reset_button.setAccessibleDescription("Сброс настроек бота, очистка логов и перезапись файла с учетными данными")
        self.reset_button.setToolTip("Сброс")
        self.reset_button.clicked.connect(self.reset_bot)

        self.help_button = QPushButton("Справка")
        self.help_button.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.help_button.setAccessibleName("Кнопка: Справка")
        self.help_button.setAccessibleDescription("Нажмите для просмотра справки")
        self.help_button.setToolTip("Справка")
        self.help_button.clicked.connect(self.show_help)

        self.exit_button = QPushButton("Выход")
        self.exit_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCloseButton))
        self.exit_button.setAccessibleName("Кнопка: Выход")
        self.exit_button.setAccessibleDescription("Нажмите для выхода из приложения")
        self.exit_button.setToolTip("Выход")
        self.exit_button.clicked.connect(self.exit_app)

        self.minimize_tray_button = QPushButton("Свернуть в трей")
        self.minimize_tray_button.setIcon(self.style().standardIcon(QStyle.SP_TitleBarMinButton))
        self.minimize_tray_button.setAccessibleName("Кнопка: Свернуть в трей")
        self.minimize_tray_button.setAccessibleDescription("Нажмите для сворачивания приложения в системный трей")
        self.minimize_tray_button.setToolTip("Свернуть в трей")
        self.minimize_tray_button.clicked.connect(self.hide_to_tray)

        self.status_label = QLabel("Бот не запущен.")
        self.status_label.setAccessibleName("Метка состояния")
        self.status_label.setAccessibleDescription("Отображает текущее состояние бота")
        self.status_label.setToolTip("Состояние бота")

        # --- Log panel + duplicate filter ---
        self.monitor_edit = QPlainTextEdit()
        self.monitor_edit.setAccessibleName("Область мониторинга логов")
        self.monitor_edit.setAccessibleDescription("Здесь отображаются логи работы бота")
        self.monitor_edit.setReadOnly(True)
        self.monitor_edit.setPlaceholderText("Логи работы бота.")
        self.monitor_edit.setToolTip("Логи")
        self.monitor_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.monitor_edit.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
        # wire eventFilter + scroll tracking (existed methods, just not connected earlier)
        try:
            self.monitor_edit.installEventFilter(self)
            self.monitor_edit.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        except Exception:
            pass

        # checkbox to enable/disable duplicate filtering
        self.filter_duplicates_checkbox = QCheckBox("Фильтровать дубли логов")
        self.filter_duplicates_checkbox.setChecked(True)
        self.filter_duplicates_checkbox.setAccessibleName("Чекбокс: фильтровать дубли логов")
        self.filter_duplicates_checkbox.setAccessibleDescription("Если включено, повторяющиеся подряд сообщения не будут дублироваться в панели логов")

        # de-dup window: remember last 500 unique lines to catch double-subscribe cases
        self._recent_log_deque = collections.deque(maxlen=500)
        self._recent_log_set = set()
        self._last_appended_line = None
        self._log_user_scrolling = False  # default

        # --- Подписка на логи и первичный дренаж буфера ---
        try:
            # Подключаем сигналы логгера к GUI (guard от двойной подписки)
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
            # Переливаем ранние логи, накопленные до создания GUI
            for _m in list(pending_log_messages):
                self.append_log(_m)
            pending_log_messages.clear()
            import sys as _sys2
            if '__main__' in _sys2.modules:
                setattr(_sys2.modules['__main__'], 'gui_ready', True)
        except Exception:
            pass


        # Load existing credentials and API settings
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

        # If token exists, fill fields and start bot
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


        # Place widgets in layout
        layout.addWidget(token_label, 0, 0)
        layout.addWidget(self.token_edit, 0, 1, 1, 2)
        layout.addWidget(pin_label, 1, 0)
        layout.addWidget(self.pin_edit, 1, 1, 1, 2)
        layout.addWidget(account_ids_label, 2, 0)
        layout.addWidget(self.account_ids_edit, 2, 1, 1, 2)
        layout.addWidget(address_label, 3, 0)
        layout.addWidget(self.address_edit, 3, 1, 1, 2)
        layout.addWidget(port_label, 4, 0)
        layout.addWidget(self.port_edit, 4, 1, 1, 2)
        layout.addWidget(self.lock_api_checkbox, 5, 0, 1, 3)
        layout.addWidget(self.save_run_button, 6, 0, 1, 3)

        button_bar = QHBoxLayout()
        # Button for local Telegram API server settings
        self.api_settings_button = QPushButton("Настройки API сервера")
        self.api_settings_button.setAccessibleName("Кнопка: Настройки API сервера")
        self.api_settings_button.setAccessibleDescription("Открыть окно настроек локального Telegram API сервера")
        self.api_settings_button.clicked.connect(self.open_api_server_settings)

        button_bar.addWidget(self.toggle_button)
        button_bar.addWidget(self.reset_button)
        button_bar.addWidget(self.help_button)
        button_bar.addWidget(self.exit_button)
        button_bar.addWidget(self.api_settings_button)
        button_bar.addWidget(self.minimize_tray_button)
        layout.addLayout(button_bar, 7, 0, 1, 3)

        # --- Windows Autostart controls ---
        self.autorun_checkbox = QCheckBox("Запускать вместе с Windows")
        self.autorun_checkbox.setFocusPolicy(Qt.StrongFocus)
        self.autorun_checkbox.setAccessibleName("Чекбокс: запускать программу вместе с Windows")
        self.autorun_checkbox.setAccessibleDescription("Если включено, программа будет запускаться при входе в Windows. Используйте Tab/Shift+Tab для перехода.")
        self.start_tray_checkbox = QCheckBox("Запуск сразу в трее")

        # --- Method selection ---
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

        self.start_tray_checkbox.setFocusPolicy(Qt.StrongFocus)
        self.start_tray_checkbox.setAccessibleName("Чекбокс: запуск сразу в трее")
        self.start_tray_checkbox.setAccessibleDescription("Если включено, при автозапуске окно не будет показано, приложение стартует в системном трее. Доступно при включённом автозапуске.")

        # Load current settings and reflect them
        autorun_enabled, start_in_tray, method_choice = load_startup_full()
        self.autorun_checkbox.setChecked(autorun_enabled)
        self.start_tray_checkbox.setChecked(start_in_tray)

        # initialize method radios (default to "startup")
        if method_choice == "auto":
            self.method_auto.setChecked(True)
        elif method_choice == "registry":
            self.method_registry.setChecked(True)
        else:
            # fallback for unknown/removed methods (e.g., 'schtask')
            self.method_startup.setChecked(True)

        # disable method group + tray until autorun on
        self.method_group.setEnabled(autorun_enabled)
        self.start_tray_checkbox.setEnabled(autorun_enabled)
        # Не блокируем для доступности NVDA/JAWS: оставляем доступной,
        # а ограничение проверим в обработчике изменения состояния.

        # Connect handlers
        self.autorun_checkbox.stateChanged.connect(self.on_autorun_checkbox_changed)
        self.start_tray_checkbox.stateChanged.connect(self.on_start_tray_checkbox_changed)

        self.method_auto.toggled.connect(self.on_method_radio_changed)
        self.method_startup.toggled.connect(self.on_method_radio_changed)
        self.method_registry.toggled.connect(self.on_method_radio_changed)

        layout.addWidget(self.autorun_checkbox, 8, 0, 1, 3)
        layout.addWidget(self.start_tray_checkbox, 9, 0, 1, 3)
        layout.addWidget(self.method_group, 10, 0, 1, 3)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator, 11, 0, 1, 3)

        # status + filter checkbox in one line
        status_line = QHBoxLayout()
        status_line.addWidget(self.status_label)
        status_line.addStretch(1)
        status_line.addWidget(self.filter_duplicates_checkbox)
        layout.addLayout(status_line, 12, 0, 1, 3)

        layout.addWidget(self.monitor_edit, 13, 0, 1, 3)

        self.token_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pin_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.account_ids_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.address_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.port_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.monitor_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(13, 1)

        # --- Tab order (фиксированный, без дубликатов) ---
        self.setTabOrder(self.token_edit, self.pin_edit)
        self.setTabOrder(self.pin_edit, self.account_ids_edit)
        self.setTabOrder(self.account_ids_edit, self.address_edit)
        self.setTabOrder(self.address_edit, self.port_edit)
        self.setTabOrder(self.port_edit, self.save_run_button)
        self.setTabOrder(self.save_run_button, self.toggle_button)
        self.setTabOrder(self.toggle_button, self.reset_button)
        self.setTabOrder(self.reset_button, self.help_button)
        self.setTabOrder(self.help_button, self.exit_button)
        self.setTabOrder(self.exit_button, self.api_settings_button)
        self.setTabOrder(self.api_settings_button, self.minimize_tray_button)
        self.setTabOrder(self.minimize_tray_button, self.autorun_checkbox)
        self.setTabOrder(self.autorun_checkbox, self.start_tray_checkbox)
        self.setTabOrder(self.start_tray_checkbox, self.method_auto)
        self.setTabOrder(self.method_auto, self.method_startup)
        self.setTabOrder(self.method_startup, self.method_registry)
        self.setTabOrder(self.method_registry, self.monitor_edit)

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

        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.setVisible(False)
        # If launched with --tray flag, auto-hide to tray by timer (10s); otherwise do nothing
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

        # If launched with --tray flag, start hidden to tray (useful for autorun)

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
        Перезапуск бота по кнопке: выполняем ТОЧНО ТУ ЖЕ ПОСЛЕДОВАТЕЛЬНОСТЬ,
        что и в обработчике «Полный перезапуск» из modulpsw, только
        все логи отправляем ТОЛЬКО в GUI (а не в Telegram).
        """
        try:
            log_emitter.log_message.emit("Действие: Перезапустить бота → старт последовательности полного перезапуска (лог в GUI).")
        except Exception:
            pass
        self.status_label.setText("Перезапуск: формирую отчёт и готовлю остановку локального API-сервера…")
        # Запускаем последовательность в фоновом потоке, чтобы не блокировать GUI
        worker = threading.Thread(target=self._full_restart_via_gui, daemon=True)
        worker.start()

    def _full_restart_via_gui(self):
        """
        Полный перезапуск "как в modulpsw":
        1) Сформировать лог-отчёт (среда, пути, argv, frozen, Nuitka vars).
        2) Мягко закрыть управляемый браузер (если есть).
        3) Снять список живых потоков.
        4) Вывести отчёт в GUI (у нас он уже печатается построчно).
        5) Отключить локальный Telegram Bot API сервер (через gui_serverapi и fallback taskkill под Windows).
        6) Вывести хвост лога в GUI.
        7) Короткая пауза и выход с кодом 42 (сигнал вотчдогу).
        """
        import time
        import subprocess
        import traceback
        from datetime import datetime
        import inspect

        def ts():
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def log_line(msg: str):
            try:
                log_emitter.log_message.emit(f"[{ts()}] {msg}")
            except Exception:
                # запасной путь
                try:
                    self.append_log(f"[{ts()}] {msg}")
                except Exception:
                    pass

        # 1) Заголовок и базовая среда
        log_line("Полный перезапуск (GUI): начинаю. Сначала пришлю отчёт в GUI, затем выключу локальный API-сервер.")
        try:
            log_line(f"Рабочая папка: {BASE_DIR}")
        except Exception:
            log_line("Рабочая папка: <недоступна>")
        try:
            log_line(f"Python: {sys.executable}")
        except Exception:
            log_line("Python: <недоступно>")
        try:
            log_line(f"Аргументы: {sys.argv}")
        except Exception:
            log_line("Аргументы: <недоступны>")
        try:
            log_line(f"frozen: {getattr(sys, 'frozen', False)}")
        except Exception:
            pass
        try:
            log_line(f"NUITKA_ONEFILE_PARENT: {os.environ.get('NUITKA_ONEFILE_PARENT', '')}")
        except Exception:
            pass
        try:
            is_child = any(s in sys.argv for s in ("--child", "/child"))
            log_line(f"child-режим (эвристика по argv): {is_child}")
        except Exception:
            pass

        # 2) Мягко закрыть управляемый браузер
        try:
            import modulbrowsrem as _b
            ctrl = getattr(_b, "CTRL", None) or getattr(_b, "BROWSER_CTRL", None)
            if ctrl is not None:
                try:
                    selected = ctrl.get_selected()
                except Exception as e_sel:
                    selected = None
                    log_line(f"Браузер: не получил выбранный экземпляр: {e_sel}")
                if selected:
                    log_line(f"Браузер: найден выбранный контроллер '{selected}'. Пытаюсь закрыть мягко (таймаут 5с).")
                    try:
                        quit_fn = getattr(ctrl, "quit", None)
                        if quit_fn:
                            if inspect.iscoroutinefunction(quit_fn):
                                import asyncio
                                asyncio.run(asyncio.wait_for(quit_fn(selected), timeout=5.0))
                            else:
                                try:
                                    quit_fn(selected, timeout=5)
                                except TypeError:
                                    quit_fn(selected)
                        log_line("Браузер: команда закрытия отправлена.")
                    except Exception as e_quit:
                        log_line(f"Браузер: ошибка при попытке закрытия: {e_quit}")
                else:
                    log_line("Браузер: выбранный контроллер не обнаружен — пропускаю закрытие.")
            else:
                log_line("Браузер: модуль найден, но контроллер BROWSER_CTRL отсутствует — пропускаю закрытие.")
        except Exception as e:
            log_line(f"Браузер: модуль управления отсутствует/ошибка импорта — пропускаю. Детали: {e}")

        # 3) Список живых потоков
        try:
            ths = threading.enumerate()
            log_line(f"Потоки живые: {len(ths)}")
            for t in ths:
                try:
                    log_line(f" - {t.name} (daemon={t.daemon})")
                except Exception:
                    pass
        except Exception:
            pass

        # 4) Отчёт уже весь ушёл в GUI построчно
        log_line("Отчёт отправлен в GUI. Отключаю локальный API-сервер…")

        # 5) Остановка локального Telegram Bot API
        # 5a) Пытаемся через функции gui_serverapi (по аналогии с modulpsw)
        try:
            # Индикатор работы
            is_running = None
            for name in ("is_server_running", "server_is_running", "is_running", "running"):
                val = getattr(gui_serverapi, name, None)
                try:
                    is_running = bool(val() if callable(val) else val)
                except Exception:
                    is_running = None
                if is_running is not None:
                    break
            if is_running is None:
                log_line("API-сервер: не удалось определить состояние через gui_serverapi — продолжаю.")
            else:
                log_line(f"API-сервер: состояние перед остановкой — running={is_running}")

            # Функции остановки
            stopped = False
            for stop_name in ("stop_server_globally", "stop_server", "shutdown"):
                stop_fn = getattr(gui_serverapi, stop_name, None)
                if stop_fn:
                    try:
                        stop_fn()
                        log_line(f"API-сервер: вызвана функция {stop_name}().")
                        stopped = True
                        break
                    except Exception as e_stop:
                        log_line(f"API-сервер: {stop_name}() вернул исключение: {e_stop}")
            if not stopped:
                log_line("API-сервер: подходящая функция остановки через gui_serverapi не найдена или не сработала.")
        except Exception as e:
            log_line(f"API-сервер: ошибка при обращении к gui_serverapi: {e}")

        # 5b) Если у нас есть окно настроек сервера — попросим его остановиться
        try:
            if hasattr(self, "api_server_window") and self.api_server_window:
                try:
                    proc = self.api_server_window.proc
                except Exception:
                    proc = None
                log_line("API-сервер: пробую остановить через окно настроек.")
                try:
                    self.api_server_window.stop_server()
                    time.sleep(0.6)
                except Exception as e:
                    log_line(f"API-сервер: окно/процесс — ошибка при stop_server(): {e}")
        except Exception:
            pass

        # 5c) Fallback: добиваем процесс под Windows (как в modulpsw)
        try:
            if _is_windows():
                import subprocess
                def _run_taskkill(args):
                    try:
                        cp = subprocess.run(args, capture_output=True, text=True, timeout=4)
                        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()
                    except Exception as ex:
                        return -1, "", str(ex)

                rc, so, se = _run_taskkill(["taskkill", "/IM", "telegram-bot-api.exe", "/T"])
                log_line(f"taskkill /IM telegram-bot-api.exe /T → rc={rc}")
                if so: log_line(f"stdout: {so}")
                if se: log_line(f"stderr: {se}")
                if rc != 0:
                    rc2, so2, se2 = _run_taskkill(["taskkill", "/F", "/IM", "telegram-bot-api.exe", "/T"])
                    log_line(f"taskkill /F /IM telegram-bot-api.exe /T → rc={rc2}")
                    if so2: log_line(f"stdout: {so2}")
                    if se2: log_line(f"stderr: {se2}")
        except Exception as e:
            log_line(f"API-сервер: ошибка при taskkill: {e}")

        # 6) Хвостовое сообщение
        log_line("Сервер остановлен. Перезапускаюсь…")
        time.sleep(0.8)
        os._exit(42)

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

    def exit_app(self):
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

    def show_help(self):
        dialog = HelpDialog(self)
        dialog.exec_()

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
