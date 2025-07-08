#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accessible and Debug-enabled Local Telegram Bot API Config GUI


"""

import sys
import os
import configparser
from PyQt5.QtCore import Qt, QProcess, QTimer
import locale
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFileDialog, QMessageBox, QSpinBox,
    QHBoxLayout, QGroupBox, QVBoxLayout, QListWidget, QListWidgetItem
)
from PyQt5.QtWidgets import QStyleFactory
from PyQt5.QtGui import QPalette, QColor, QFont

import serverextrbot

# Globals for external control
_proc_global = None
_win_global = None

# Global process handle accessible to external scripts
_proc_global = None

# Determine BASE_DIR dynamically for both development and frozen/exe usage
if "NUITKA_ONEFILE_PARENT" in os.environ:
    BASE_DIR = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
elif getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_ROOT = os.path.join(BASE_DIR, 'serverapibot')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.ini')
SECTION = 'gui_settings'

def load_config():
    config = configparser.ConfigParser()
    defaults = {
        'api_id': '',
        'api_hash': '',
        'local_mode': 'True',
        'http_ip': '0.0.0.0',
        'http_port': '8081',
        'max_webhook_connections': '100000',
        'verbosity': '0',
        'data_dir': os.path.join(SERVER_ROOT, 'data'),
        'temp_dir': os.path.join(SERVER_ROOT, 'temp'),
        'exe_path': os.path.join(SERVER_ROOT, 'telegram-bot-api.exe'),
        'auto_start': 'False',
        'log_max_size': '1',
    }
    if not os.path.exists(CONFIG_PATH):
        config[SECTION] = defaults
        os.makedirs(defaults['data_dir'], exist_ok=True)
        os.makedirs(defaults['temp_dir'], exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            config.write(f)
        return defaults
    config.read(CONFIG_PATH, encoding='utf-8')
    # Если секция GUI отсутствует, создаём с дефолтами и сохраняем
    if SECTION not in config:
        config[SECTION] = defaults
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            config.write(f)
        return defaults
    s = config[SECTION] if SECTION in config else defaults
    return {k: s.get(k, defaults[k]) for k in defaults}

def save_config(settings):
    config = configparser.ConfigParser()
    # Читаем существующий config.ini, чтобы не терять другие секции
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH, encoding='utf-8')
    config[SECTION] = {
        'api_id': settings['api_id'],
        'api_hash': settings['api_hash'],
        'local_mode': str(settings['local_mode']),
        'http_ip': settings['http_ip'],
        'http_port': settings['http_port'],
        'max_webhook_connections': settings['max_webhook_connections'],
        'verbosity': str(settings['verbosity']),
        'data_dir': settings['data_dir'],
        'temp_dir': settings['temp_dir'],
        'exe_path': settings['exe_path'] or os.path.join(SERVER_ROOT, 'telegram-bot-api.exe'),
        'auto_start': str(settings['auto_start']),
        'log_max_size': str(settings['log_max_size'])
    }
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Visual styling to match gui.py
        app = QApplication.instance()
        if app:
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

        # Register global window handle for external stop
        global _win_global
        _win_global = self
        self.proc = QProcess(self)
        # Register global process handle for external stop
        global _proc_global
        _proc_global = self.proc
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.encoding = locale.getpreferredencoding(False)
        self.user_initiated_stop = False  # флаг для пользовательской остановки
        self.proc.readyReadStandardOutput.connect(self.handle_stdout)
        self.proc.readyReadStandardError.connect(self.handle_stderr)
        self.proc.errorOccurred.connect(self.handle_error)
        self.proc.finished.connect(self.handle_finished)
        self.init_ui()
        self.load_settings()
        # Автостарт сервера при старте приложения
        if self.auto_start_cb.isChecked():
            QTimer.singleShot(0, self.start_server)

        # UI log файл
        self.log_file_path = os.path.join(self.data_edit.text(), 'telegram-bot-api-ui.log')

    def init_ui(self):
        self.setWindowTitle("telegram-bot-api-server")
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Description
        desc = QLabel("Настройте и запустите локальный сервер Telegram Bot API\n(Логи и ошибки внизу)")
        desc.setAccessibleName("Описание")
        desc.setAccessibleDescription("Описание окна настройки локального Telegram Bot API")
        desc.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(desc)

        # Server parameters group
        group = QGroupBox("Параметры сервера")
        grid = QGridLayout(group)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(8)
        row = 0

        # API ID
        lbl = QLabel("API ID:")
        lbl.setToolTip("Ваш api_id (число), получить на https://core.telegram.org/api/obtaining_api_id")
        grid.addWidget(lbl, row, 0)
        self.api_id_edit = QLineEdit()
        self.api_id_edit.setAccessibleName("Поле ввода API ID")
        self.api_id_edit.setAccessibleDescription("Введите ваш API ID (число), получить на core.telegram.org")
        self.api_id_edit.setPlaceholderText("Например: 123456")
        grid.addWidget(self.api_id_edit, row, 1, 1, 2)
        row += 1

        # API Hash
        lbl = QLabel("API Hash:")
        lbl.setToolTip("Ваш api_hash (строка), получить на https://core.telegram.org/api/obtaining_api_id")
        grid.addWidget(lbl, row, 0)
        self.api_hash_edit = QLineEdit()
        self.api_hash_edit.setAccessibleName("Поле ввода API Hash")
        self.api_hash_edit.setAccessibleDescription("Введите ваш API Hash (строка), получить на core.telegram.org")
        self.api_hash_edit.setPlaceholderText("Например: abcd1234efgh5678ijkl90mn")
        grid.addWidget(self.api_hash_edit, row, 1, 1, 2)
        row += 1

        # Local Mode
        self.local_cb = QCheckBox("Local Mode")
        self.local_cb.setAccessibleName("Флажок Локальный режим")
        self.local_cb.setAccessibleDescription("Включить локальный режим сервера (--local)")
        self.local_cb.setToolTip("Включить локальный режим сервера (--local)")
        grid.addWidget(self.local_cb, row, 0, 1, 3)
        row += 1

        # Listen IP
        lbl = QLabel("Listen IP:")
        lbl.setToolTip("IP-адрес для прослушивания (--http-ip-address)")
        grid.addWidget(lbl, row, 0)
        self.ip_edit = QLineEdit()
        self.ip_edit.setAccessibleName("Поле ввода IP")
        self.ip_edit.setAccessibleDescription("Введите IP-адрес для прослушивания, например 0.0.0.0")
        self.ip_edit.setPlaceholderText("0.0.0.0")
        grid.addWidget(self.ip_edit, row, 1, 1, 2)
        row += 1

        # HTTP Port
        lbl = QLabel("Port:")
        lbl.setToolTip("Порт для HTTP запросов (--http-port)")
        grid.addWidget(lbl, row, 0)
        self.port_edit = QLineEdit()
        self.port_edit.setAccessibleName("Поле ввода порта HTTP")
        self.port_edit.setAccessibleDescription("Введите порт для HTTP запросов, например 8081")
        self.port_edit.setPlaceholderText("8081")
        grid.addWidget(self.port_edit, row, 1, 1, 2)
        row += 1

        # Max Webhook Connections
        lbl = QLabel("Max Webhook Connections:")
        lbl.setToolTip("Максимум параллельных соединений (--max-webhook-connections)")
        grid.addWidget(lbl, row, 0)
        self.max_conn = QSpinBox()
        self.max_conn.setAccessibleName("Поле ввода максимального количества соединений")
        self.max_conn.setAccessibleDescription("Укажите максимальное число параллельных webhook соединений")
        self.max_conn.setRange(1, 1000000)
        grid.addWidget(self.max_conn, row, 1, 1, 2)
        row += 1

        # Logging Level
        lbl = QLabel("Уровень логирования:")
        lbl.setToolTip("Уровень логирования (--verbosity)")
        grid.addWidget(lbl, row, 0)
        self.verbosity_spin = QSpinBox()
        self.verbosity_spin.setAccessibleName("Поле ввода уровня логирования")
        self.verbosity_spin.setAccessibleDescription("Укажите уровень логирования (0 - нет, >0 - подробность)")
        self.verbosity_spin.setRange(0, 5)
        grid.addWidget(self.verbosity_spin, row, 1, 1, 2)
        row += 1

        # Max UI log size (МБ)
        lbl = QLabel("Макс. размер UI-логов (МБ):")
        lbl.setToolTip("Максимальный размер файла логов UI в МБ, при превышении файл будет обнуляться")
        grid.addWidget(lbl, row, 0)
        self.log_size_spin = QSpinBox()
        self.log_size_spin.setAccessibleName("Поле ввода максимального размера логов")
        self.log_size_spin.setAccessibleDescription("Укажите максимальный размер файла логов UI в МБ")
        self.log_size_spin.setRange(1, 1024)
        grid.addWidget(self.log_size_spin, row, 1, 1, 1)
        lbl2 = QLabel("МБ")
        grid.addWidget(lbl2, row, 2)
        row += 1

        # Data Directory
        lbl = QLabel("Data Dir:")
        lbl.setToolTip("Каталог для файлов сервера (создаётся автоматически)")
        grid.addWidget(lbl, row, 0)
        self.data_edit = QLineEdit()
        self.data_edit.setAccessibleName("Поле ввода каталога данных")
        self.data_edit.setAccessibleDescription("Путь к каталогу для файлов сервера")
        self.data_edit.setPlaceholderText(os.path.join(SERVER_ROOT, 'data'))
        grid.addWidget(self.data_edit, row, 1)
        btn = QPushButton("...")
        btn.setAccessibleName("Кнопка: Выбор каталога данных")
        btn.setAccessibleDescription("Открыть диалог выбора каталога данных")
        btn.clicked.connect(self.browse_data)
        grid.addWidget(btn, row, 2)
        row += 1

        # Temp Directory
        lbl = QLabel("Temp Dir:")
        lbl.setToolTip("Каталог для временных файлов (создаётся автоматически)")
        grid.addWidget(lbl, row, 0)
        self.temp_edit = QLineEdit()
        self.temp_edit.setAccessibleName("Поле ввода каталога временных файлов")
        self.temp_edit.setAccessibleDescription("Путь к каталогу для временных файлов")
        self.temp_edit.setPlaceholderText(os.path.join(SERVER_ROOT, 'temp'))
        grid.addWidget(self.temp_edit, row, 1)
        btn2 = QPushButton("...")
        btn2.setAccessibleName("Кнопка: Выбор каталога temp")
        btn2.setAccessibleDescription("Открыть диалог выбора каталога временных файлов")
        btn2.clicked.connect(self.browse_temp)
        grid.addWidget(btn2, row, 2)
        row += 1

        # Executable Path
        lbl = QLabel("Server EXE:")
        lbl.setToolTip("Путь к telegram-bot-api исполняемому файлу")
        grid.addWidget(lbl, row, 0)
        self.exe_edit = QLineEdit()
        self.exe_edit.setAccessibleName("Поле ввода пути к исполняемому файлу")
        self.exe_edit.setAccessibleDescription("Путь к telegram-bot-api исполняемому файлу")
        self.exe_edit.setPlaceholderText(os.path.join(SERVER_ROOT, 'telegram-bot-api.exe'))
        grid.addWidget(self.exe_edit, row, 1)
        btn3 = QPushButton("...")
        btn3.setAccessibleName("Кнопка: Выбор исполняемого файла")
        btn3.setAccessibleDescription("Открыть диалог выбора telegram-bot-api исполняемого файла")
        btn3.clicked.connect(self.browse_exe)
        grid.addWidget(btn3, row, 2)
        row += 1

        main_layout.addWidget(group)

        # Auto-Start Checkbox
        self.auto_start_cb = QCheckBox("Автостарт при запуске")
        self.auto_start_cb.setAccessibleName("Флажок автозапуска сервера при старте программы")
        self.auto_start_cb.setAccessibleDescription("Если включено, сервер будет автоматически запущен при запуске программы")
        self.auto_start_cb.setToolTip("Автоматически запускать сервер при старте программы")
        main_layout.addWidget(self.auto_start_cb)

        # Control Buttons
        hbox = QHBoxLayout()
        self.start_btn = QPushButton("Старт")
        self.start_btn.setAccessibleName("Кнопка Старт")
        self.start_btn.setAccessibleDescription("Запустить сервер Telegram Bot API")
        self.start_btn.clicked.connect(self.start_server)
        hbox.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setAccessibleName("Кнопка Стоп")
        self.stop_btn.setAccessibleDescription("Остановить сервер Telegram Bot API")
        self.stop_btn.clicked.connect(self.stop_server)
        hbox.addWidget(self.stop_btn)
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setAccessibleName("Кнопка Сохранить")
        self.save_btn.setAccessibleDescription("Сохранить настройки в config.ini")
        self.save_btn.clicked.connect(self.save_settings)
        hbox.addWidget(self.save_btn)
        main_layout.addLayout(hbox)

        # Log Output as list for screen readers and arrow navigation
        main_layout.addWidget(QLabel("Логи:"))
        # Dual log panels: UI logs and telegram-bot-api logs side by side
        dual_layout = QHBoxLayout()
        # UI logs panel
        ui_group = QGroupBox("UI Логи")
        ui_group.setAccessibleName("Группа: UI логов")
        ui_group.setAccessibleDescription("Панель логов пользовательского интерфейса, навигация стрелками вверх и вниз")
        ui_layout = QVBoxLayout(ui_group)
        self.log_output = QListWidget()
        self.log_output.setAccessibleName("Панель логов UI")
        self.log_output.setAccessibleDescription("Журнал UI, навигация стрелками вверх и вниз")
        self.log_output.setToolTip("Журнал пользовательского интерфейса")
        self.log_output.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.log_output.setFocusPolicy(Qt.StrongFocus)
        self.log_output.setSelectionMode(QListWidget.SingleSelection)
        self.log_output.setMinimumHeight(150)
        ui_layout.addWidget(self.log_output)
        dual_layout.addWidget(ui_group)
        # API logs panel
        api_group = QGroupBox("API Логи")
        api_group.setAccessibleName("Группа: API логов")
        api_group.setAccessibleDescription("Панель логов процесса telegram-bot-api, навигация стрелками вверх и вниз")
        api_layout = QVBoxLayout(api_group)
        self.api_log_output = QListWidget()
        self.api_log_output.setAccessibleName("Панель логов telegram-bot-api")
        self.api_log_output.setAccessibleDescription("Журнал вывода telegram-bot-api, навигация стрелками вверх и вниз")
        self.api_log_output.setToolTip("Журнал вывода telegram-bot-api")
        self.api_log_output.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.api_log_output.setFocusPolicy(Qt.StrongFocus)
        self.api_log_output.setSelectionMode(QListWidget.SingleSelection)
        self.api_log_output.setMinimumHeight(150)
        api_layout.addWidget(self.api_log_output)
        dual_layout.addWidget(api_group)
        main_layout.addLayout(dual_layout)

        # Status Label
        self.status_lbl = QLabel("Статус: Остановлен")
        self.status_lbl.setAccessibleName("Метка статуса сервера")
        self.status_lbl.setAccessibleDescription("Отображает текущий статус сервера (запущен/остановлен)")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.status_lbl)

        self.resize(700, 650)
        self.update_buttons()
        # Gracefully stop API server when main application is exiting (e.g., restart bot)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.handle_app_quit)

    def add_log(self, text):
        item = QListWidgetItem(text)
        self.log_output.addItem(item)
        self.log_output.setCurrentItem(item)
        self.log_output.scrollToItem(item)
        # Запись логов UI в файл
        # Rotate UI log if exceeds max size
        try:
            if os.path.exists(self.log_file_path) and os.path.getsize(self.log_file_path) > self.log_size_spin.value() * 1024 * 1024:
                with open(self.log_file_path, 'w', encoding='utf-8'):
                    pass
        except Exception:
            pass

        # Запись логов UI в файл
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as lf:
                lf.write(text + '\n')
        except Exception:
            pass

    def browse_data(self):
        d = QFileDialog.getExistingDirectory(self, "Выбрать каталог данных", SERVER_ROOT)
        if d:
            self.data_edit.setText(d)

    def browse_temp(self):
        t = QFileDialog.getExistingDirectory(self, "Выбрать каталог temp", SERVER_ROOT)
        if t:
            self.temp_edit.setText(t)

    def browse_exe(self):
        p, _ = QFileDialog.getOpenFileName(self, "Выбрать исполняемый файл", SERVER_ROOT)
        if p:
            self.exe_edit.setText(p)

    def add_api_log(self, text):
        """Добавляет строку в панель логов telegram-bot-api."""
        item = QListWidgetItem(text)
        self.api_log_output.addItem(item)
        self.api_log_output.scrollToItem(item)

    def load_settings(self):
        cfg = load_config()
        self.api_id_edit.setText(cfg['api_id'])
        self.api_hash_edit.setText(cfg['api_hash'])
        self.local_cb.setChecked(cfg['local_mode']=='True')
        self.ip_edit.setText(cfg['http_ip'])
        self.port_edit.setText(cfg['http_port'])
        self.max_conn.setValue(int(cfg['max_webhook_connections']))
        self.verbosity_spin.setValue(int(cfg.get('verbosity', '0')))
        self.data_edit.setText(cfg['data_dir'])
        self.temp_edit.setText(cfg['temp_dir'])
        self.exe_edit.setText(cfg['exe_path'])
        self.auto_start_cb.setChecked(cfg['auto_start']=='True')
        self.log_size_spin.setValue(int(cfg.get('log_max_size', '1')))

    def save_settings(self):
        settings = {
            'api_id': self.api_id_edit.text().strip(),
            'api_hash': self.api_hash_edit.text().strip(),
            'local_mode': self.local_cb.isChecked(),
            'http_ip': self.ip_edit.text().strip(),
            'http_port': self.port_edit.text().strip(),
            'max_webhook_connections': str(self.max_conn.value()),
            'verbosity': str(self.verbosity_spin.value()),
            'data_dir': self.data_edit.text().strip() or os.path.join(SERVER_ROOT, 'data'),
            'temp_dir': self.temp_edit.text().strip() or os.path.join(SERVER_ROOT, 'temp'),
            'exe_path': self.exe_edit.text().strip(),
            'auto_start': self.auto_start_cb.isChecked(),
            'log_max_size': str(self.log_size_spin.value()),
        }
        os.makedirs(settings['data_dir'], exist_ok=True)
        os.makedirs(settings['temp_dir'], exist_ok=True)
        save_config(settings)
        QMessageBox.information(self, "Сохранено", "Настройки сохранены в config.ini")

    def start_server(self):
        if self.proc.state() == QProcess.Running:
            QMessageBox.warning(self, "Внимание", "Сервер уже запущен.")
            return
        settings = load_config()
        exe = settings['exe_path']
        if not os.path.exists(exe):
            QMessageBox.critical(self, "Ошибка", "Неверный путь к EXE.")
            return
        settings = {
            'api_id': self.api_id_edit.text().strip(),
            'api_hash': self.api_hash_edit.text().strip(),
            'local_mode': self.local_cb.isChecked(),
            'http_ip': self.ip_edit.text().strip(),
            'http_port': self.port_edit.text().strip(),
            'max_webhook_connections': str(self.max_conn.value()),
            'verbosity': str(self.verbosity_spin.value()),
            'data_dir': self.data_edit.text().strip() or os.path.join(SERVER_ROOT, 'data'),
            'temp_dir': self.temp_edit.text().strip() or os.path.join(SERVER_ROOT, 'temp'),
            'exe_path': self.exe_edit.text().strip(),
            'auto_start': self.auto_start_cb.isChecked(),
            'log_max_size': str(self.log_size_spin.value()),
        }
        os.makedirs(settings['data_dir'], exist_ok=True)
        os.makedirs(settings['temp_dir'], exist_ok=True)
        save_config(settings)
        self.add_log("Настройки сохранены")
        settings = load_config()
        # Путь для записи логов сервера в файл
        log_path = os.path.join(settings['data_dir'], 'telegram-bot-api.log')
        # Формируем аргументы для запуска сервера
        # Формируем аргументы для запуска сервера
        args = [
            f"--api-id={settings['api_id']}",
            f"--api-hash={settings['api_hash']}",
            "--local" if settings['local_mode'] else "",
            f"--http-ip-address={settings['http_ip']}",
            f"--http-port={settings['http_port']}",
            f"--max-webhook-connections={settings['max_webhook_connections']}",
            f"--dir={settings['data_dir']}",
            f"--temp-dir={settings['temp_dir']}",
        ]
        # Убираем пустые аргументы
        args = [a for a in args if a]
        # Устанавливаем уровень логирования на основе выбранного уровня
        verbosity = int(settings.get('verbosity', '0'))
        if verbosity > 0:
            args.append(f"--verbosity={verbosity}")
        # Убираем пустые аргументы
        args = [a for a in args if a]
        # Ротация логов telegram-bot-api
        log_max_size = int(settings.get('log_max_size', '1')) * 1024 * 1024
        args.append(f"--log-max-file-size={log_max_size}")
        # Убираем пустые аргументы после ротации логов
        args = [a for a in args if a]
        self.add_log(f"Запуск: {exe} {' '.join(args)}")
        # Логирование версии перед запуском
        version_proc = QProcess(self)
        version_proc.setProcessChannelMode(QProcess.MergedChannels)
        version_proc.start(exe, ["--version"])
        if version_proc.waitForFinished(2000):
            version_out = bytes(version_proc.readAllStandardOutput()).decode(self.encoding, errors='replace').strip()
            self.add_log(f"Версия: {version_out}")
        else:
            self.add_log("Версия: не удалось получить версию")
        self.proc.setWorkingDirectory(SERVER_ROOT)
        self.proc.start(exe, args)
        if not self.proc.waitForStarted(3000):
            QMessageBox.critical(self, "Ошибка", "Не удалось запустить процесс.")
        else:
            self.status_lbl.setText("Статус: Запущен")
            self.update_buttons()
            # Gracefully stop API server when main application is exiting (e.g., restart bot)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.handle_app_quit)

    def stop_server(self):
        if self.proc.state() == QProcess.Running:
            # помечаем, что остановка инициирована пользователем
            self.user_initiated_stop = True
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
            # обновление UI будет в handle_finished
        else:
            # Сервер уже остановлен. Ничего не делаем.
            return
    def handle_stdout(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(self.encoding, errors='replace')
        for line in text.splitlines():
            self.add_api_log(line)

    def handle_stderr(self):
        text = bytes(self.proc.readAllStandardError()).decode(self.encoding, errors='replace')
        for line in text.splitlines():
            self.add_api_log(f"<Ошибка> {line}")

    def handle_error(self, error):
        if getattr(self, 'user_initiated_stop', False):
            # Игнорируем ошибку, вызванную ручной остановкой
            return
        QMessageBox.critical(self, "Ошибка процесса", f"Процесс завершился с ошибкой: {error}")
        self.add_log(f"<QProcess.Error> {error}")

    def handle_finished(self, exitCode, exitStatus):
        if getattr(self, 'user_initiated_stop', False):
            self.user_initiated_stop = False
            self.status_lbl.setText("Статус: Остановлен")
            self.add_log("Сервер остановлен пользователем.")
            self.update_buttons()
            # Gracefully stop API server when main application is exiting (e.g., restart bot)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.handle_app_quit)
        else:
            status = "Нормальный выход" if exitStatus == QProcess.NormalExit else "Аварийный выход"
            self.status_lbl.setText(f"Статус: {status} (код {exitCode})")
            self.add_log(f"Процесс завершён: {status}, код={exitCode}")
            self.update_buttons()
            # Gracefully stop API server when main application is exiting (e.g., restart bot)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.handle_app_quit)
    def update_buttons(self):
        running = self.proc.state() == QProcess.Running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def handle_app_quit(self):
        """Handle application quit or restart: stop server gracefully."""
        self.user_initiated_stop = True
        if self.proc.state() == QProcess.Running:
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def closeEvent(self, event):
        if self.proc.state() == QProcess.Running:
            self.user_initiated_stop = True
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
        event.accept()



def stop_server_globally():
    """
    Stops the Telegram Bot API server the same way as the Stop button.
    Can be called from other scripts by importing this module.
    """
    global _win_global, _proc_global
    # Prefer calling the window stop to set UI flags correctly
    if _win_global is not None:
        _win_global.stop_server()
    elif _proc_global is not None and _proc_global.state() == QProcess.Running:
        _proc_global.terminate()
        if not _proc_global.waitForFinished(3000):
            _proc_global.kill()
    else:
        print("Server process is not running or already stopped.")

if __name__ == '__main__':

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())