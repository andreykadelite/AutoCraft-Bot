import sys
import os
import threading
import requests
import configparser
import info
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel, QLineEdit, QPushButton,
    QPlainTextEdit, QSystemTrayIcon, QMenu, QAction, QStyle, QStyleFactory, QSizePolicy,
    QMessageBox, QDialog, QVBoxLayout, QDialogButtonBox, QFrame, QHBoxLayout
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QIcon, QPalette, QColor, QFont

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
# Enable High DPI scaling and high DPI pixmaps
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

# Определяем единый каталог для файла config.ini
if "NUITKA_ONEFILE_PARENT" in os.environ:
    BASE_DIR = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
elif getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
def load_credentials():
    config = configparser.ConfigParser()
    # Если файла нет — создаём его с секцией credentials и возвращаем пустые значения
    if not os.path.exists(CONFIG_PATH):
        config['credentials'] = {'token': '', 'pin': '', 'allowed_ids': ''}
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            config.write(f)
        return "", "", ""
    # Читаем существующий файл
    config.read(CONFIG_PATH, encoding='utf-8')
    # Если нет секции — добавляем её и сохраняем
    if 'credentials' not in config:
        config['credentials'] = {'token': '', 'pin': '', 'allowed_ids': ''}
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            config.write(f)
    # Возвращаем токен, pin и строку ID, используя fallback
    token = config.get('credentials', 'token', fallback='')
    pin = config.get('credentials', 'pin', fallback='')
    allowed_ids = config.get('credentials', 'allowed_ids', fallback='')
    return token, pin, allowed_ids

def save_credentials(token, pin, allowed_ids):
    config = configparser.ConfigParser()
    # Если файл уже есть, читаем его, чтобы не затирать другие разделы
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH, encoding='utf-8')
    if 'credentials' not in config:
        config['credentials'] = {}
    config['credentials']['token'] = token
    config['credentials']['pin'] = pin
    config['credentials']['allowed_ids'] = allowed_ids
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

# Импорт необходимых функций и глобальных переменных из модуля, запущенного как __main__
from __main__ import (
    run_bot,
    current_bot, bot_thread, current_loop, allowed_accounts,
    authorized_users, note_mode, pending_note, file_mode, cmd_mode,
    in_cmd_menu, power_mode, pending_power_action, infiles_mode, plugins_mode,
    log_emitter
)

def get_bot_username(token):
    """
    Функция для получения имени бота через метод getMe Telegram API.
    При корректном токене возвращает имя (username) бота, иначе None.
    """
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
        
        # Создаем read-only поле для имени бота, чтобы оно было фокусируемым
        self.bot_name_field = QLineEdit("Получение имени бота...")
        self.bot_name_field.setReadOnly(True)
        self.bot_name_field.setAccessibleName("Имя бота")
        self.bot_name_field.setAccessibleDescription("Имя бота для подтверждения подключения")
        self.bot_name_field.setFocusPolicy(Qt.StrongFocus)
        self.layout.addWidget(self.bot_name_field)
        
        # Диалоговые кнопки с переводом на русский
        self.button_box = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self.button_box.button(QDialogButtonBox.Yes).setText("Да")
        self.button_box.button(QDialogButtonBox.No).setText("Нет")
        self.layout.addWidget(self.button_box)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Устанавливаем порядок табуляции: сначала поле с именем бота, потом кнопки
        self.setTabOrder(self.bot_name_field, self.button_box.button(QDialogButtonBox.Yes))
        self.setTabOrder(self.button_box.button(QDialogButtonBox.Yes), self.button_box.button(QDialogButtonBox.No))

    def update_bot_name(self, bot_name):
        self.bot_name_field.setText(f"Подключиться к боту: {bot_name}?")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Apply modern Fusion style for a clean look
        app = QApplication.instance()
        if app:
            app.setStyle(QStyleFactory.create('Fusion'))
            # Apply dark theme palette
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
            # Set global font
            app.setFont(QFont('Segoe UI', 10))
        # Dynamic window sizing: 80% of available screen
        screen_geom = QApplication.primaryScreen().availableGeometry()
        self.resize(int(screen_geom.width()*0.8), int(screen_geom.height()*0.8))
        self.setMinimumSize(600, 400)
        # Set dark stylesheet for modern UI
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
        self.setWindowTitle("AutoCraft Bot v1.1")
        self.setGeometry(100, 100, 600, 500)
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        layout = QGridLayout(central_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

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

        # Кнопка "Сохранить и подключить"
        self.save_run_button = QPushButton("Сохранить и подключить")
        self.save_run_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.save_run_button.setAccessibleName("Кнопка: Сохранить и подключить")
        self.save_run_button.setAccessibleDescription("Нажмите для сохранения настроек и подключения бота после проверки корректности ввода")
        self.save_run_button.setToolTip("Сохранить и подключить")
        self.save_run_button.clicked.connect(self.save_and_run_bot)

        self.toggle_button = QPushButton("Перезапустить бота")
        self.toggle_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.toggle_button.setAccessibleName("Кнопка: Перезапустить бота")
        self.toggle_button.setAccessibleDescription("Нажмите для перезапуска бота")
        self.toggle_button.setToolTip("Перезапустить бота")
        self.toggle_button.clicked.connect(self.restart_bot)

        self.reset_button = QPushButton("Сброс")
        self.reset_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        # Кнопка Справка
        self.help_button = QPushButton("Справка")
        self.help_button.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.help_button.setAccessibleName("Кнопка: Справка")
        self.help_button.setAccessibleDescription("Нажмите для просмотра справки")
        self.help_button.setToolTip("Справка")
        self.help_button.clicked.connect(self.show_help)
        self.reset_button.setAccessibleName("Кнопка: Сброс")
        self.reset_button.setAccessibleDescription("Сброс настроек бота, очистка логов и перезапись файла с учетными данными")
        self.reset_button.setToolTip("Сброс")
        self.reset_button.clicked.connect(self.reset_bot)

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

        self.monitor_edit = QPlainTextEdit()
        self.monitor_edit.setAccessibleName("Область мониторинга логов")
        self.monitor_edit.setAccessibleDescription("Здесь отображаются логи работы бота")
        self.monitor_edit.setReadOnly(True)
        self.monitor_edit.setPlaceholderText("Логи работы бота.")
        self.monitor_edit.setToolTip("Логи")
        self.monitor_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.monitor_edit.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
        self.log_buffer = []

        # Попытка загрузки файла config.ini
        try:
            token, pin, allowed_ids_str = load_credentials()
        except Exception:
            token, pin, allowed_ids_str = "", "", ""
            save_credentials("", "", "")
            log_emitter.log_message.emit("Файл config.ini поврежден, перезаписываем его. Введите данные для подключения.")
        else:
            valid = True
            if token and ":" not in token:
                valid = False
            if allowed_ids_str:
                for id_str in allowed_ids_str.split(","):
                    stripped = id_str.strip()
                    if stripped:
                        if not stripped.isdigit() or not (7 <= len(stripped) <= 10):
                            valid = False
                            break
            if not valid:
                token, pin, allowed_ids_str = "", "", ""
                save_credentials("", "", "")
                log_emitter.log_message.emit("Файл config.ini поврежден, перезаписываем его. Введите данные для подключения.")

        if token:
            self.token_edit.setText(token)
            self.pin_edit.setText(pin)
            self.account_ids_edit.setText(allowed_ids_str)
            self.start_bot()

        # Размещение виджетов
        layout.addWidget(token_label, 0, 0)
        layout.addWidget(self.token_edit, 0, 1, 1, 2)
        layout.addWidget(pin_label, 1, 0)
        layout.addWidget(self.pin_edit, 1, 1, 1, 2)
        layout.addWidget(account_ids_label, 2, 0)
        layout.addWidget(self.account_ids_edit, 2, 1, 1, 2)
        layout.addWidget(self.save_run_button, 3, 0, 1, 3)
        # Группировка кнопок действий для логичности интерфейса
        button_bar = QHBoxLayout()
        button_bar.addWidget(self.toggle_button)
        button_bar.addWidget(self.reset_button)
        button_bar.addWidget(self.help_button)
        button_bar.addWidget(self.exit_button)
        button_bar.addWidget(self.minimize_tray_button)
        layout.addLayout(button_bar, 4, 0, 1, 3)

        # Разделитель перед логами
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator, 5, 0, 1, 3)
        layout.addWidget(self.status_label, 6, 0, 1, 3)
        layout.addWidget(self.monitor_edit, 7, 0, 1, 3)
        # Make widgets resize nicely
        self.token_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pin_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.account_ids_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.monitor_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Stretch layout columns and rows
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(7, 1)

        self.setTabOrder(self.token_edit, self.pin_edit)
        self.setTabOrder(self.pin_edit, self.account_ids_edit)
        self.setTabOrder(self.account_ids_edit, self.save_run_button)
        self.setTabOrder(self.save_run_button, self.toggle_button)
        self.setTabOrder(self.toggle_button, self.reset_button)
        self.setTabOrder(self.reset_button, self.exit_button)
        self.setTabOrder(self.exit_button, self.minimize_tray_button)
        self.setTabOrder(self.minimize_tray_button, self.monitor_edit)

        if os.path.exists(os.path.join(BASE_DIR, "icon.png")):
            icon = QIcon(os.path.join(BASE_DIR, "icon.png"))
        else:
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.setWindowIcon(icon)

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.windowIcon())
        self.tray_icon.setToolTip("AutoCraft Bot v1.1")

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
        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.setVisible(False)

        log_emitter.log_message.connect(self.append_log)
        self.bot_running = False

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Left):
            self.focusPreviousChild()
        elif event.key() in (Qt.Key_Down, Qt.Key_Right):
            self.focusNextChild()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            widget = self.focusWidget()
            if isinstance(widget, QPushButton):
                widget.click()
        else:
            super().keyPressEvent(event)

    def append_log(self, msg):
        self.log_buffer.append(msg)
        if len(self.log_buffer) > 1000:
            self.log_buffer = self.log_buffer[-1000:]
        self.monitor_edit.setPlainText("\n".join(self.log_buffer))
        self.monitor_edit.verticalScrollBar().setValue(self.monitor_edit.verticalScrollBar().maximum())

    def save_and_run_bot(self):
        token = self.token_edit.text().strip()
        pin = self.pin_edit.text().strip()
        allowed_ids_str = self.account_ids_edit.text().strip()
        # Проверка токена
        if not token:
            errorBox = QMessageBox()
            errorBox.setIcon(QMessageBox.Warning)
            errorBox.setWindowTitle("Ошибка ввода")
            errorBox.setText("Ошибка: введите токен.")
            errorBox.setInformativeText("Пример корректного токена: 123456:ABCdefGhIjKl")
            errorBox.setAccessibleName("Ошибка ввода токена")
            errorBox.setAccessibleDescription("Введите корректный токен, например 123456:ABCdefGhIjKl")
            errorBox.exec_()
            return
        if ":" not in token:
            errorBox = QMessageBox()
            errorBox.setIcon(QMessageBox.Warning)
            errorBox.setWindowTitle("Ошибка ввода токена")
            errorBox.setText("Ошибка: некорректный формат токена.")
            errorBox.setInformativeText("Пример корректного токена: 123456:ABCdefGhIjKl")
            errorBox.setAccessibleName("Ошибка формата токена")
            errorBox.setAccessibleDescription("Введите токен в формате 123456:ABCdefGhIjKl")
            errorBox.exec_()
            return
        allowed_accounts.clear()
        # Проверка ID аккаунтов (поле может быть пустым)
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
                        errorBox.setInformativeText(
                            "ID аккаунта должен содержать от 7 до 10 цифр. Пример: 1234567, 1234567890"
                        )
                        errorBox.setAccessibleName("Ошибка формата ID аккаунтов")
                        errorBox.setAccessibleDescription(
                            "Введите ID аккаунтов (каждый от 7 до 10 цифр), разделённые запятыми"
                        )
                        errorBox.exec_()
                        return
                    valid_ids.append(int(id_str))
            # Удаляем дубликаты
            valid_ids = list(set(valid_ids))
            if len(valid_ids) > 10:
                errorBox = QMessageBox()
                errorBox.setIcon(QMessageBox.Warning)
                errorBox.setWindowTitle("Ошибка ввода ID аккаунтов")
                errorBox.setText("Ошибка: можно вводить не более 10 ID аккаунтов.")
                errorBox.setInformativeText("Пример корректного ввода: 1234567, 1234567890")
                errorBox.setAccessibleName("Ошибка количества ID аккаунтов")
                errorBox.setAccessibleDescription("Введите не более 10 ID аккаунтов, разделённых запятыми")
                errorBox.exec_()
                return
            for id_num in valid_ids:
                allowed_accounts.add(id_num)
            # Формируем нормализованную строку для записи в файл
            allowed_ids_str = ", ".join(map(str, sorted(valid_ids)))
        
        # Создаем диалог подтверждения и запускаем worker в отдельном потоке для получения имени бота
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
        self.status_label.setText("Бот перезапускается...")
        QTimer.singleShot(2000, self.perform_full_restart_wrapper)

    def perform_full_restart_wrapper(self):
        from modulpsw import perform_full_restart
        perform_full_restart()

    def start_bot(self):
        global TOKEN, PIN_CODE, bot_thread, current_bot, current_loop, allowed_accounts
        token = self.token_edit.text().strip()
        pin = self.pin_edit.text().strip()
        allowed_ids_str = self.account_ids_edit.text().strip()
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
        save_credentials("", "", "")
        self.status_label.setText("Бот сброшен. Файл учетных данных очищен.")

    def exit_app(self):
        self.stop_bot()
        QApplication.quit()

    def hide_to_tray(self):
        self.hide()
        self.tray_icon.setVisible(True)

    def show_normal(self):
        self.show()
        self.tray_icon.setVisible(False)

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_normal()

# Защита: если модуль запускается напрямую, выводим сообщение и завершаем выполнение.

    def show_help(self):
        """Показывает окно справки с контактами и инструкцией."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Справка")
        dlg_layout = QVBoxLayout(dlg)
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(info.CONTACT_TEXT + "\n\n" + info.HELP_TEXT)
        dlg_layout.addWidget(text_edit)
        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        dlg_layout.addWidget(btns)
        dlg.exec_()

if __name__ == '__main__':
    print("Ошибка: gui.py не предназначен для самостоятельного запуска. Запусти 6.9.py вместо этого.")
    sys.exit(1)
