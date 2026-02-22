# -*- coding: utf-8 -*-
"""
GUI: Диагностика окружения

Это GUI-эквивалент возможностей из nostartrunmoduldiagnos.py:
- Открыть временную папку (распаковка/корень runtime)
- Открыть папку с EXE
- Показать диагностику окружения
- Тестовая запись в лог (в log/diagnostics_gui.log рядом с EXE/скриптом)
- Аварийное завершение (os._exit) с подтверждением двойным нажатием
- Имитировать зависание (намеренно блокирует поток) с подтверждением двойным нажатием

ВАЖНО: модуль поддерживает ленивый импорт.
PyQt5 подтягивается только когда реально открывают окно,
чтобы functions_window.py мог читать метаданные и не тянуть лишнее заранее.
"""

from __future__ import annotations

import os
import sys
import time
import platform
import subprocess
import traceback
import re
from pathlib import Path
from typing import Callable, Optional, Tuple, TYPE_CHECKING

import weakref

if TYPE_CHECKING:
    # Только для типов, чтобы не тянуть PyQt5 при импорте
    from PyQt5.QtWidgets import QWidget  # pragma: no cover


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "Диагностика окружения"
FUNCTIONS_ENTRYPOINT = "open_env_diagnostics_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 50
FUNCTIONS_ICON = "SP_MessageBoxInformation"
FUNCTIONS_TOOLTIP = "Открыть окно диагностики окружения"
FUNCTIONS_ACCESSIBLE_NAME = "Кнопка: Диагностика окружения"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = (
    "Открывает окно диагностики окружения (папки, диагностика, тест логов, тест watchdog)"
)

# Подтверждение опасных действий: повторное нажатие в течение тайм-аута
CONFIRM_TTL_SECONDS = 10


def _safe_text(s: object) -> str:
    try:
        return str(s)
    except Exception:
        return repr(s)


def _one_line(text: object) -> str:
    """Упрощает текст для логов: в одну строку, без переносов и мусорных пробелов."""
    try:
        s = str(text)
    except Exception:
        return "(unprintable)"
    # Переносы строк ломают читаемость в некоторых лог-вьюерах и скринридерах.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.splitlines())
    # Схлопываем повторяющиеся пробелы
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def _get_argv0() -> str:
    """Безопасно возвращает sys.argv[0] как строку (или адекватный фолбэк)."""
    try:
        argv = getattr(sys, "argv", None)
        if isinstance(argv, (list, tuple)) and argv:
            v = argv[0]
            if v is None:
                return ""
            # sys.argv[0] иногда бывает PathLike
            try:
                s = os.fspath(v)
            except Exception:
                s = str(v)
            return s
    except Exception:
        pass

    try:
        # Встраиваемые/нестандартные рантаймы иногда дают пустой argv,
        # тогда берём sys.executable, если он есть.
        s = getattr(sys, "executable", "") or ""
        return str(s)
    except Exception:
        return ""


def _open_folder(path: Path) -> Tuple[bool, str]:
    """Открытие папки в проводнике/файловом менеджере. Возвращает (ok, message)."""
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
        # Сообщение оставляем с переносом строк (для удобства в статусе GUI),
        # а для логов оно будет нормализовано в одну строку через _one_line().
        return True, f"Открыл папку:\n{path}"
    except Exception as e:
        return False, f"Не удалось открыть папку {path}:\n{e}"


def _get_windows_startup_folders() -> Tuple[Optional[Path], Optional[Path]]:
    """Возвращает пути к папкам автозагрузки Windows: (пользовательская, общая).

    В Windows есть две стандартные папки Startup:
    - Пользовательская (только текущий пользователь)
    - Общая (для всех пользователей)

    Возвращает (user_startup, common_startup). Для не-Windows вернёт (None, None).
    """
    if os.name != "nt":
        return None, None

    # %APPDATA% обычно: C:\Users\<user>\AppData\Roaming
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", "")

    user_startup = None
    common_startup = None

    try:
        if appdata:
            user_startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    except Exception:
        user_startup = None

    try:
        if programdata:
            common_startup = (
                Path(programdata)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "StartUp"
            )
    except Exception:
        common_startup = None

    return user_startup, common_startup


def _is_nuitka_onefile() -> bool:
    """True, если похоже на Nuitka onefile (есть onefile env-переменные)."""
    try:
        return any(k.upper().startswith("NUITKA_ONEFILE") for k in os.environ.keys())
    except Exception:
        return False


def _guess_onefile_extract_dir() -> Optional[Path]:
    """
    Пытаемся определить папку распаковки Nuitka onefile (temp/cache).

    В Nuitka onefile обычно:
    - sys.argv[0]   -> путь к оригинальному EXE (где он лежит на диске)
    - __file__      -> путь внутри папки распаковки (куда Nuitka распаковал .dist)
    """
    if not _is_nuitka_compiled():
        return None

    exe_dir = _get_exe_dir()

    # 1) Попробовать env-переменные onefile (если там вдруг есть путь)
    try:
        for k, v in os.environ.items():
            ku = k.upper()
            if not ku.startswith("NUITKA_ONEFILE"):
                continue
            if not v:
                continue
            # Иногда там PID/роль процесса, это не путь.
            # Берём только то, что похоже на существующий путь.
            try:
                p = Path(v)
                if p.exists():
                    return p if p.is_dir() else p.parent
            except Exception:
                continue
    except Exception:
        pass

    # 2) __main__.__file__ обычно указывает внутрь распаковки
    try:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None)
        if main_file:
            p = Path(main_file).resolve()
            if p.parent.exists() and p.parent != exe_dir:
                return p.parent
    except Exception:
        pass

    # 3) sys.executable в onefile часто указывает на "дочерний" EXE внутри распаковки
    try:
        p = Path(sys.executable).resolve()
        if p.exists() and p.parent != exe_dir:
            return p.parent
    except Exception:
        pass

    # 4) На крайний: текущий модуль
    try:
        p = Path(__file__).resolve()
        if p.parent.exists() and p.parent != exe_dir:
            return p.parent
    except Exception:
        pass

    return None


def _find_runtime_root() -> Path:
    """
    Определяем «папку распаковки / runtime root».

    - Для Nuitka onefile: возвращаем папку распаковки (temp/cache).
    - Для standalone/.py: возвращаем папку, где лежит EXE/скрипт запуска.
    """
    onefile_dir = _guess_onefile_extract_dir()
    if onefile_dir is not None:
        return onefile_dir

    # Фолбэк: попытка найти "корень проекта" рядом с этим файлом (на случай source/standalone)
    try:
        current = Path(__file__).resolve()
        for parent in [current.parent] + list(current.parents):
            if (parent / "config.ini").exists():
                return parent
            if (parent / "moduls").exists() or (parent / "plugins").exists() or (parent / "gui_win").exists():
                return parent
    except Exception:
        pass

    return _get_exe_dir()


def _get_exe_dir() -> Path:
    """Папка, где лежит EXE (или файл запуска в .py режиме)."""
    try:
        argv0 = _get_argv0()
        if argv0:
            return Path(os.path.abspath(argv0)).resolve().parent
    except Exception:
        pass
    return Path(os.getcwd()).resolve()


def _is_nuitka_compiled() -> bool:
    """Пытаемся определить, что мы в скомпилированной сборке."""
    try:
        module = sys.modules.get(__name__)
        if bool(getattr(module, "__compiled__", False)):
            return True
    except Exception:
        pass

    try:
        argv0 = os.path.abspath(_get_argv0() or "").lower()
        if argv0.endswith(".exe") and os.name == "nt":
            return True
    except Exception:
        pass
    return False


def _get_log_file() -> Path:
    """Файл лога GUI-диагностики: log/diagnostics_gui.log рядом с EXE/скриптом."""
    base = _get_exe_dir()
    log_dir = base / "log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path(os.getcwd()).resolve()
        log_dir = base / "log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return base / "diagnostics_gui.log"
    return log_dir / "diagnostics_gui.log"


def _append_gui_log_line(line: str) -> None:
    """Мини-логгер на случай, если общего write_bot_log нет."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        fp = _get_log_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as f:
            f.write(f"{ts} - {line}\n")
    except Exception:
        pass




# -------------------- config.ini (debug flag) helpers --------------------

_CONFIG_INI_CACHE: Optional[Path] = None


def _locate_config_ini() -> Optional[Path]:
    """Ищем config.ini рядом с EXE/проектом.

    Требования:
    - модуль лежит в подпапке (например gui_win/),
    - config.ini лежит рядом с этой папкой (обычно уровнем выше),
    - в Nuitka onefile config.ini чаще всего рядом с «оригинальным EXE» (sys.argv[0]).
    """
    global _CONFIG_INI_CACHE

    try:
        if _CONFIG_INI_CACHE is not None and _CONFIG_INI_CACHE.exists():
            return _CONFIG_INI_CACHE
    except Exception:
        _CONFIG_INI_CACHE = None

    candidates: list[Path] = []

    # 1) Рядом с «оригинальным EXE» (важно для Nuitka onefile)
    try:
        exe_dir = _get_exe_dir()
        candidates.append(exe_dir / "config.ini")
    except Exception:
        exe_dir = None

    # 2) Рядом с runtime root (в source/standalone иногда это «корень проекта»)
    try:
        rt = _find_runtime_root()
        candidates.append(rt / "config.ini")
    except Exception:
        rt = None

    # 3) Рядом с текущим модулем и его родителями (классика: config.ini на уровень выше папки)
    try:
        here = Path(__file__).resolve().parent
        candidates.append(here / "config.ini")
        # на 1-6 уровней вверх
        for parent in list(here.parents)[:6]:
            candidates.append(parent / "config.ini")
    except Exception:
        pass

    # 4) На крайний случай: cwd
    try:
        candidates.append(Path(os.getcwd()).resolve() / "config.ini")
    except Exception:
        pass

    # 5) Пройтись вверх от папки EXE тоже полезно (иногда EXE лежит глубже)
    try:
        if exe_dir is not None:
            candidates.append(exe_dir / "config.ini")
            for parent in list(exe_dir.parents)[:6]:
                candidates.append(parent / "config.ini")
    except Exception:
        pass

    # dedupe (case-insensitive on Windows)
    seen = set()
    uniq: list[Path] = []
    for c in candidates:
        try:
            key = str(c).lower()
        except Exception:
            key = str(c)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    for c in uniq:
        try:
            if c.exists() and c.is_file():
                _CONFIG_INI_CACHE = c
                return c
        except Exception:
            continue

    _CONFIG_INI_CACHE = None
    return None


def _parse_bool(value: str) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on", "вкл", "да"):
        return True
    if v in ("0", "false", "no", "n", "off", "выкл", "нет"):
        return False
    return None


def _read_debug_from_config(config_path: Path) -> Optional[bool]:
    """Читает [credentials] debug=... из config.ini. Возвращает None, если не найдено/не распознано."""
    try:
        raw = config_path.read_bytes()
    except Exception:
        return None

    # decode максимально мягко
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        return None

    in_section = False
    sec_re = re.compile(r"^\s*\[\s*credentials\s*\]\s*$", re.IGNORECASE)
    any_sec_re = re.compile(r"^\s*\[\s*[^\]]+\s*\]\s*$")
    dbg_re = re.compile(r"^\s*debug\s*=\s*(.*?)\s*(?:[;#].*)?$", re.IGNORECASE)

    for line in text.splitlines():
        if sec_re.match(line):
            in_section = True
            continue
        if in_section and any_sec_re.match(line):
            # новый раздел, значит выходим
            in_section = False
        if not in_section:
            continue
        m = dbg_re.match(line)
        if not m:
            continue
        val = m.group(1)
        return _parse_bool(val)

    return None


def _write_debug_to_config(config_path: Path, enabled: bool) -> Tuple[bool, str]:
    """Аккуратно обновляет debug=... в [credentials], стараясь не ломать остальной файл."""
    try:
        raw = config_path.read_bytes()
    except Exception as e:
        return False, f"Не удалось прочитать config.ini: {e}"

    # определим переводы строк, чтобы не «дергать» формат
    newline = "\r\n" if b"\r\n" in raw else "\n"
    # BOM (UTF-8 подпись) иногда ломает чтение configparser, если читать как обычный utf-8.
    # ВАЖНО: utf-8-sig при *записи* добавляет BOM всегда, поэтому будем сохранять его только если он был изначально.
    has_bom = raw.startswith(b"\xef\xbb\xbf")

    text = None
    used_enc = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            used_enc = enc
            break
        except Exception:
            continue
    if text is None:
        return False, "Не удалось декодировать config.ini (utf-8/cp1251)."

    lines = text.splitlines()

    sec_re = re.compile(r"^\s*\[\s*credentials\s*\]\s*$", re.IGNORECASE)
    any_sec_re = re.compile(r"^\s*\[\s*[^\]]+\s*\]\s*$")
    dbg_line_re = re.compile(r"^(?P<prefix>\s*debug\s*=\s*)(?P<val>.*?)(?P<suffix>\s*(?:[;#].*)?)$",
                             re.IGNORECASE)

    in_section = False
    found_section = False
    updated = False
    insert_pos = None

    for i, line in enumerate(lines):
        if sec_re.match(line):
            in_section = True
            found_section = True
            continue

        if in_section and any_sec_re.match(line):
            # конец секции
            in_section = False
            if insert_pos is None:
                insert_pos = i  # вставим перед следующим разделом

        if in_section:
            m = dbg_line_re.match(line)
            if m:
                prefix = m.group("prefix")
                suffix = m.group("suffix") or ""
                val = "True" if enabled else "False"
                lines[i] = f"{prefix}{val}{suffix}"
                updated = True
                break

    # Если секция была, но debug не нашли, вставим перед следующим разделом или в конец файла
    if found_section and not updated:
        val = "True" if enabled else "False"
        new_line = f"debug = {val}"
        if insert_pos is None:
            lines.append(new_line)
        else:
            lines.insert(insert_pos, new_line)
        updated = True

    # Если секции вообще нет, допишем ее в конец
    if not found_section:
        val = "True" if enabled else "False"
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.extend(["[credentials]", f"debug = {val}"])
        updated = True

    if not updated:
        return False, "Не удалось обновить debug в config.ini (неожиданное состояние)."

    out_text = newline.join(lines) + newline

    # Выбор кодировки для записи: не добавляем BOM случайно.
    write_enc = used_enc or "utf-8"
    if write_enc == "utf-8-sig" and not has_bom:
        write_enc = "utf-8"

    try:
        tmp = config_path.with_suffix(config_path.suffix + ".tmp")
        tmp.write_bytes(out_text.encode(write_enc))
        tmp.replace(config_path)
        return True, f"debug = {'True' if enabled else 'False'}"
    except Exception as e:
        return False, f"Не удалось записать config.ini: {e}"



# -------------------- ленивый импорт PyQt + класс окна --------------------

_ENV_DIAG_CLASS = None


def _get_pyqt():
    """
    Возвращает нужные Qt сущности.
    Вынесено в функцию, чтобы модуль можно было импортировать без PyQt5.
    """
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QFontDatabase, QTextCursor
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QCheckBox,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QStyle,
        QVBoxLayout,
    )
    return (
        Qt,
        QTimer,
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QCheckBox,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QStyle,
        QVBoxLayout,
        QFontDatabase,
        QTextCursor,
    )


def _get_env_diag_class():
    global _ENV_DIAG_CLASS
    if _ENV_DIAG_CLASS is not None:
        return _ENV_DIAG_CLASS

    (
        Qt,
        QTimer,
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QCheckBox,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QStyle,
        QVBoxLayout,
        QFontDatabase,
        QTextCursor,
    ) = _get_pyqt()

    class EnvDiagnosticsWindow(QDialog):
        def __init__(self, parent=None, log_func: Optional[Callable[[str], None]] = None):
            super().__init__(parent)
            self.setObjectName("envDiagnosticsWindow")
            self.setWindowTitle("Диагностика окружения")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setAccessibleName("Окно диагностики окружения")
            self.setAccessibleDescription("Инструменты диагностики окружения и тесты watchdog")

            self.setMinimumSize(760, 520)
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
            self._pending_action: Optional[str] = None
            self._pending_until: float = 0.0

            self._confirm_timer = QTimer(self)
            self._confirm_timer.setSingleShot(True)
            self._confirm_timer.timeout.connect(self._clear_confirm_state)

            root = QVBoxLayout(self)
            root.setContentsMargins(20, 20, 20, 16)
            root.setSpacing(12)

            header = QLabel("Диагностика окружения")
            header.setObjectName("envDiagHeader")
            header.setAccessibleName("Заголовок окна диагностики")
            header.setAlignment(Qt.AlignLeft)
            header.setStyleSheet("font-size: 16pt; font-weight: 600;")
            root.addWidget(header)

            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFrameShadow(QFrame.Sunken)
            divider.setAccessibleName("Разделитель")
            root.addWidget(divider)

            self.status = QLabel("Готово. Нажми «Показать диагностику», чтобы вывести информацию.")
            self.status.setWordWrap(True)
            self.status.setAccessibleName("Статус")
            self.status.setAccessibleDescription("Строка статуса и подсказок")
            self.status.setFocusPolicy(Qt.StrongFocus)
            root.addWidget(self.status)

            content = QHBoxLayout()
            content.setSpacing(12)
            root.addLayout(content)

            # Левая колонка кнопок
            left = QVBoxLayout()
            left.setSpacing(8)
            content.addLayout(left, 0)

            self.btn_open_temp = QPushButton("Открыть временную папку EXE")
            self.btn_open_temp.setAccessibleName("Кнопка: открыть временную папку EXE")
            self.btn_open_temp.setAccessibleDescription("Открывает папку распаковки/корень runtime (Nuitka onefile/standalone)")
            self._try_set_icon(self.btn_open_temp, QStyle.SP_DirOpenIcon)
            self.btn_open_temp.clicked.connect(self._on_open_temp)
            left.addWidget(self.btn_open_temp)

            self.btn_open_exe = QPushButton("Открыть папку с EXE")
            self.btn_open_exe.setAccessibleName("Кнопка: открыть папку с EXE")
            self.btn_open_exe.setAccessibleDescription("Открывает папку, где лежит EXE или файл запуска")
            self._try_set_icon(self.btn_open_exe, QStyle.SP_DirIcon)
            self.btn_open_exe.clicked.connect(self._on_open_exe)
            left.addWidget(self.btn_open_exe)

            self.btn_open_startup = QPushButton("Открыть папку автозагрузки")
            self.btn_open_startup.setAccessibleName("Кнопка: открыть папку автозагрузки")
            self.btn_open_startup.setAccessibleDescription(
                "Открывает папку автозагрузки Windows (Startup). Обычно это папка текущего пользователя."
            )
            self._try_set_icon(self.btn_open_startup, QStyle.SP_DirOpenIcon)
            self.btn_open_startup.clicked.connect(self._on_open_startup)
            left.addWidget(self.btn_open_startup)

            # --- Debug флаг в config.ini ---
            self._config_path = _locate_config_ini()
            self._debug_checkbox_lock = False

            self.lbl_config = QLabel("")
            self.lbl_config.setWordWrap(True)
            self.lbl_config.setAccessibleName("Путь к config.ini")
            self.lbl_config.setAccessibleDescription("Показывает, какой config.ini найден рядом с программой")
            self.lbl_config.setFocusPolicy(Qt.StrongFocus)
            left.addWidget(self.lbl_config)

            self.chk_debug = QCheckBox("Debug в config.ini")
            self.chk_debug.setAccessibleName("Переключатель: Debug в config.ini")
            self.chk_debug.setAccessibleDescription("Включает или выключает debug в секции [credentials] файла config.ini")
            try:
                self.chk_debug.setFocusPolicy(Qt.StrongFocus)
            except Exception:
                pass
            self.chk_debug.stateChanged.connect(self._on_debug_state_changed)
            left.addWidget(self.chk_debug)

            # подтянем текущее значение
            self._refresh_config_debug_ui()


            self.btn_show_diag = QPushButton("Показать диагностику")
            self.btn_show_diag.setAccessibleName("Кнопка: показать диагностику")
            self.btn_show_diag.setAccessibleDescription("Собирает и выводит техническую информацию об окружении")
            self._try_set_icon(self.btn_show_diag, QStyle.SP_FileDialogInfoView)
            self.btn_show_diag.clicked.connect(self._on_show_diag)
            left.addWidget(self.btn_show_diag)

            self.btn_copy = QPushButton("Копировать диагностику")
            self.btn_copy.setAccessibleName("Кнопка: копировать диагностику")
            self.btn_copy.setAccessibleDescription("Копирует текст диагностики в буфер обмена")
            self._try_set_icon(self.btn_copy, QStyle.SP_DialogOpenButton)
            self.btn_copy.clicked.connect(self._on_copy_diag)
            left.addWidget(self.btn_copy)

            self.btn_test_log = QPushButton("Тестовая запись в лог")
            self.btn_test_log.setAccessibleName("Кнопка: тестовая запись в лог")
            self.btn_test_log.setAccessibleDescription("Пишет тестовую строку в log/diagnostics_gui.log рядом с EXE/скриптом")
            self._try_set_icon(self.btn_test_log, QStyle.SP_DialogSaveButton)
            self.btn_test_log.clicked.connect(self._on_test_log)
            left.addWidget(self.btn_test_log)

            line2 = QFrame()
            line2.setFrameShape(QFrame.HLine)
            line2.setFrameShadow(QFrame.Sunken)
            line2.setAccessibleName("Разделитель опасных действий")
            left.addWidget(line2)

            self.btn_exit = QPushButton("Аварийное завершение")
            self.btn_exit.setAccessibleName("Кнопка: аварийное завершение")
            self.btn_exit.setAccessibleDescription("Принудительно завершает процесс (требует подтверждения двойным нажатием)")
            self._try_set_icon(self.btn_exit, QStyle.SP_MessageBoxCritical)
            self.btn_exit.clicked.connect(self._on_emergency_exit)
            left.addWidget(self.btn_exit)

            self.btn_hang = QPushButton("Зависание программы")
            self.btn_hang.setAccessibleName("Кнопка: зависание программы")
            self.btn_hang.setAccessibleDescription("Намеренно блокирует поток (требует подтверждения двойным нажатием)")
            self._try_set_icon(self.btn_hang, QStyle.SP_BrowserStop)
            self.btn_hang.clicked.connect(self._on_hang)
            left.addWidget(self.btn_hang)

            left.addStretch(1)

            # Правая часть: вывод диагностики
            self.output = QPlainTextEdit()
            self.output.setReadOnly(True)
            self.output.setAccessibleName("Поле вывода диагностики")
            self.output.setAccessibleDescription("Текстовый вывод диагностики окружения")
            self.output.setFocusPolicy(Qt.StrongFocus)
            self.output.setPlaceholderText("Здесь появится диагностика после нажатия кнопки «Показать диагностику».")
            self.output.setObjectName("envDiagOutput")
            # Для скринридеров важно, чтобы можно было перемещать каретку стрелками.
            # Некоторые связки Qt+IA2 лучше себя ведут, когда включён текстовый интерактив.
            try:
                self.output.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            except Exception:
                pass
            try:
                self.output.setUndoRedoEnabled(False)
            except Exception:
                pass
            try:
                self.output.setCursorWidth(2)
            except Exception:
                pass

            # Удобнее глазами и для копирования: фиксированный шрифт, с переносом по ширине окна
            try:
                # NoWrap иногда делает длинные строки «невидимыми» для чтения по строкам.
                # WidgetWidth оставляет переносы визуальными, но текст остаётся копируемым.
                self.output.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            except Exception:
                pass
            try:
                self.output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            except Exception:
                pass

            content.addWidget(self.output, 1)

            # Низ: «Закрыть»
            button_box = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = button_box.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            close_btn.setAccessibleName("Закрыть окно диагностики")
            close_btn.setAccessibleDescription("Закрывает окно диагностики окружения")
            close_btn.setDefault(False)
            button_box.rejected.connect(self.reject)
            root.addWidget(button_box)

            # Tab order (важно для скринридеров)
            self.setTabOrder(self.status, self.btn_open_temp)
            self.setTabOrder(self.btn_open_temp, self.btn_open_exe)
            self.setTabOrder(self.btn_open_exe, self.btn_open_startup)
            self.setTabOrder(self.btn_open_startup, self.lbl_config)
            self.setTabOrder(self.lbl_config, self.chk_debug)
            self.setTabOrder(self.chk_debug, self.btn_show_diag)
            self.setTabOrder(self.btn_show_diag, self.btn_copy)
            self.setTabOrder(self.btn_copy, self.btn_test_log)
            self.setTabOrder(self.btn_test_log, self.btn_exit)
            self.setTabOrder(self.btn_exit, self.btn_hang)
            self.setTabOrder(self.btn_hang, self.output)
            self.setTabOrder(self.output, close_btn)
            self.status.setFocus(Qt.TabFocusReason)

        def _try_set_icon(self, btn: 'QPushButton', icon_id: 'QStyle.StandardPixmap') -> None:
            try:
                style = self.style()
                if style:
                    btn.setIcon(style.standardIcon(icon_id))
            except Exception:
                pass

        def _log(self, text: str) -> None:
            """Пишем в общий лог, если он есть. Иначе в локальный diagnostics_gui.log."""
            text = _one_line(text)
            try:
                if callable(self._log_func):
                    self._log_func(text)
                    return
            except Exception:
                pass

            try:
                from __main__ import write_bot_log  # type: ignore
                if callable(write_bot_log):
                    write_bot_log(text)
                    return
            except Exception:
                pass

            _append_gui_log_line(text)

        def _focus_output_from_start(self) -> None:
            """Дать фокус полю вывода и поставить каретку в начало (для чтения стрелками)."""

            def _do_focus() -> None:
                try:
                    self.output.setFocus(Qt.OtherFocusReason)
                except Exception:
                    try:
                        self.output.setFocus(Qt.TabFocusReason)
                    except Exception:
                        pass
                # Ставим каретку в начало и прокручиваем вверх.
                try:
                    self.output.moveCursor(QTextCursor.Start)
                except Exception:
                    try:
                        cur = self.output.textCursor()
                        cur.movePosition(QTextCursor.Start)
                        self.output.setTextCursor(cur)
                    except Exception:
                        pass
                try:
                    self.output.ensureCursorVisible()
                except Exception:
                    pass
                try:
                    sb = self.output.verticalScrollBar()
                    sb.setValue(sb.minimum())
                except Exception:
                    pass

            # Важно: фокус лучше ставить после выхода из обработчика клика.
            try:
                QTimer.singleShot(0, _do_focus)
            except Exception:
                _do_focus()

        # ---- confirm helpers ----

        def _clear_confirm_state(self) -> None:
            self._pending_action = None
            self._pending_until = 0.0

        def _confirm_danger(self, action: str) -> bool:
            """True, если подтверждено: повторное нажатие вовремя."""
            now = time.time()
            if self._pending_action == action and self._pending_until >= now:
                self._clear_confirm_state()
                return True

            self._pending_action = action
            self._pending_until = now + CONFIRM_TTL_SECONDS
            self._confirm_timer.start(CONFIRM_TTL_SECONDS * 1000)
            return False

        def _reset_confirm(self) -> None:
            self._clear_confirm_state()
            try:
                self._confirm_timer.stop()
            except Exception:
                pass

        def _set_status(self, message: str, ok: Optional[bool] = None) -> None:
            if ok is True:
                prefix = "OK: "
            elif ok is False:
                prefix = "Ошибка: "
            else:
                prefix = ""
            self.status.setText(prefix + message)


        # ---- config.ini / debug ----

        def _refresh_config_debug_ui(self) -> None:
            """Обновляет подпись и состояние чекбокса debug по config.ini."""
            cfg = getattr(self, "_config_path", None)
            if not cfg or not isinstance(cfg, Path):
                cfg = _locate_config_ini()
                self._config_path = cfg

            if not cfg:
                self.lbl_config.setText("config.ini: не найден (положи рядом с папкой проекта/EXE).")
                try:
                    self.chk_debug.setEnabled(False)
                except Exception:
                    pass
                self._debug_checkbox_lock = True
                try:
                    self.chk_debug.setChecked(False)
                except Exception:
                    pass
                self._debug_checkbox_lock = False
                return

            self.lbl_config.setText(f"config.ini: {cfg}")

            flag = _read_debug_from_config(cfg)
            if flag is None:
                # если ключа нет или значение странное, покажем выключено, но дадим включить
                flag = False

            self._debug_checkbox_lock = True
            try:
                self.chk_debug.setEnabled(True)
                self.chk_debug.setChecked(bool(flag))
            except Exception:
                pass
            self._debug_checkbox_lock = False

        def _on_debug_state_changed(self, state: int) -> None:
            """Записывает debug в config.ini при изменении чекбокса."""
            if getattr(self, "_debug_checkbox_lock", False):
                return

            cfg = getattr(self, "_config_path", None)
            if not cfg or not isinstance(cfg, Path):
                cfg = _locate_config_ini()
                self._config_path = cfg

            if not cfg:
                self._set_status("config.ini не найден: переключение debug недоступно.", ok=False)
                self._refresh_config_debug_ui()
                return

            enabled = bool(state)
            ok, msg = _write_debug_to_config(cfg, enabled)
            if ok:
                self._set_status(f"Обновлено: {msg}", ok=True)
                self._log(f"[ENV-DIAG GUI] config.ini debug changed -> {msg} (file={cfg})")
            else:
                self._set_status(msg, ok=False)
                # вернуть чекбокс к реальному значению
                self._refresh_config_debug_ui()


        # ---- actions ----

        def _on_open_temp(self) -> None:
            self._reset_confirm()
            path = _find_runtime_root()
            ok, msg = _open_folder(path)
            self._set_status(msg, ok)
            self._log(f"[ENV-DIAG GUI] open_temp: {path} (ok={ok})")

        def _on_open_exe(self) -> None:
            self._reset_confirm()
            path = _get_exe_dir()
            ok, msg = _open_folder(path)
            self._set_status(msg, ok)
            self._log(f"[ENV-DIAG GUI] open_exe_dir: {path} (ok={ok})")

        def _on_open_startup(self) -> None:
            """Открыть папку автозагрузки Windows (Startup)."""
            self._reset_confirm()

            user_startup, common_startup = _get_windows_startup_folders()
            target = None

            # Приоритет: пользовательская папка, затем общая.
            try:
                if user_startup and user_startup.exists():
                    target = user_startup
            except Exception:
                target = None

            if target is None:
                try:
                    if common_startup and common_startup.exists():
                        target = common_startup
                except Exception:
                    target = None

            if target is None:
                # В крайнем случае попробуем открыть даже если папка "не существует" (на некоторых сборках)
                # но _open_folder требует exists, поэтому дадим понятную ошибку.
                msg = "Папка автозагрузки не найдена (Startup)."
                if user_startup:
                    msg += f"\nПользовательская: {user_startup}"
                if common_startup:
                    msg += f"\nОбщая: {common_startup}"
                self._set_status(msg, ok=False)
                self._log(f"[ENV-DIAG GUI] open_startup_folder: not found (user={user_startup}, common={common_startup})")
                return

            ok, msg = _open_folder(target)
            # Чуть расширим статус: покажем оба пути, чтобы было понятно, где искать.
            extra = []
            if user_startup:
                extra.append(f"Пользовательская Startup: {user_startup}")
            if common_startup:
                extra.append(f"Общая Startup: {common_startup}")
            if extra:
                msg = msg + "\n\n" + "\n".join(extra)

            self._set_status(msg, ok)
            self._log(f"[ENV-DIAG GUI] open_startup_folder: {target} (ok={ok})")

        def _build_diag_text(self) -> str:
            unpack_root = _find_runtime_root()
            onefile_extract = _guess_onefile_extract_dir()
            exe_dir = _get_exe_dir()
            user_startup, common_startup = _get_windows_startup_folders()
            argv0 = _get_argv0()
            exe_path = Path(os.path.abspath(argv0)) if argv0 else Path(os.getcwd()).resolve()
            main_mod = sys.modules.get("__main__")
            main_file = getattr(main_mod, "__file__", None)
            nuitka_env = {k: v for k, v in os.environ.items() if k.upper().startswith("NUITKA_")}
            onefile_env = {k: v for k, v in os.environ.items() if k.upper().startswith("NUITKA_ONEFILE")}

            def p(x: object) -> str:
                return _safe_text(x)

            # ВАЖНО для скринридеров: без декоративных символов/эмодзи и без ведущих пробелов.
            # Тогда NVDA/JAWS стабильнее читают строки стрелками.
            lines = [
                "Диагностика окружения (GUI)",
                "==========================",
                "",
                "Пути:",
                f"Оригинальный EXE (sys.argv[0]): {p(exe_path)}",
                f"Папка оригинального EXE: {p(exe_dir)}",
                f"Папка распаковки onefile: {p(onefile_extract) if onefile_extract else '(не определена / не onefile)'}",
                f"Runtime root (кнопка 'временная папка'): {p(unpack_root)}",
                f"Текущая рабочая папка (cwd): {p(os.getcwd())}",
                f"config.ini: {p(_locate_config_ini()) if _locate_config_ini() else '(не найден)'}",
                f"Debug в config.ini ([credentials] debug): {p(_read_debug_from_config(_locate_config_ini()) if _locate_config_ini() else None)}",
                f"Startup (автозагрузка) пользовательская: {p(user_startup) if user_startup else '(не Windows)'}",
                f"Startup (автозагрузка) общая: {p(common_startup) if common_startup else '(не Windows)'}",
                "",
                "Пояснение:",
                "В Nuitka onefile sys.argv[0] обычно показывает, где лежит исходный EXE,",
                "а runtime/data лежит в папке распаковки (temp/cache) и виден через __file__.",
                "",
                "Окружение:",
                f"OS: {platform.system()} {platform.release()}",
                f"Platform: {platform.platform()}",
                f"Python/runtime: {p(sys.version.splitlines()[0])}",
                f"sys.executable: {p(sys.executable)}",
                f"__main__.__file__: {p(main_file) if main_file else '(нет)'}",
                f"__file__ (этот модуль): {p(Path(__file__).resolve())}",
                f"Nuitka compiled (__compiled__): {p(_is_nuitka_compiled())}",
                f"Nuitka onefile env detected: {p(_is_nuitka_onefile())}",
                "",
                "Логи:",
                f"Локальный лог GUI-диагностики: {p(_get_log_file())}",
            ]

            if onefile_env:
                lines.append("")
                lines.append("Onefile env (если есть):")
                for k, v in sorted(onefile_env.items()):
                    lines.append(f"{k}={v}")

            if nuitka_env:
                lines.append("")
                lines.append("NUITKA_* env:")
                for k, v in sorted(nuitka_env.items()):
                    lines.append(f"{k}={v}")

            return "\n".join(lines)

        def _on_show_diag(self) -> None:
            self._reset_confirm()
            try:
                text = self._build_diag_text()
            except Exception as e:
                err = f"Ошибка при сборке диагностики: {e}"
                tb = traceback.format_exc()
                # Пишем ошибку прямо в вывод, чтобы её можно было скопировать и прислать.
                self.output.setPlainText(err + "\n\n" + tb)
                self._focus_output_from_start()
                self._set_status(err, ok=False)
                self._log(f"[ENV-DIAG GUI] show_diagnostics failed: {err}")
                return

            self.output.setPlainText(text)
            # Фокус + каретка в начало (иначе некоторые скринридеры «цепляются» за последнюю строку).
            self._focus_output_from_start()
            self._set_status("Диагностика собрана. Фокус в поле вывода: можно читать стрелками и копировать.")
            self._log("[ENV-DIAG GUI] show_diagnostics")

        def _on_copy_diag(self) -> None:
            self._reset_confirm()
            text = self.output.toPlainText().strip()
            if not text:
                self._set_status("Сначала нажми «Показать диагностику».", ok=False)
                return
            try:
                QApplication.clipboard().setText(text)
                self._set_status("Диагностика скопирована в буфер обмена.", ok=True)
            except Exception:
                self._set_status("Не удалось скопировать диагностику в буфер обмена.", ok=False)

        def _on_test_log(self) -> None:
            self._reset_confirm()
            argv0 = _get_argv0()
            msg = f"[ENV-DIAG GUI] Тестовая запись в лог. EXE/argv0={os.path.abspath(argv0) if argv0 else os.getcwd()}"
            self._log(msg)
            _append_gui_log_line(msg)
            self._set_status(f"Тестовая запись сделана.\nФайл: {_get_log_file()}", ok=True)

        def _on_emergency_exit(self) -> None:
            if not self._confirm_danger("emergency_exit"):
                self._set_status(
                    "⛔ Аварийное завершение.\nПовтори нажатие «Аварийное завершение» в течение 10 секунд, чтобы подтвердить."
                )
                return

            self._set_status("⛔ Выполняю аварийное завершение процесса…")
            self._log("[ENV-DIAG GUI] emergency_exit confirmed -> os._exit(2)")

            QTimer.singleShot(700, lambda: os._exit(2))

        def _on_hang(self) -> None:
            if not self._confirm_danger("simulate_hang"):
                self._set_status(
                    "🧊 Зависание программы.\nПовтори нажатие «Зависание программы» в течение 10 секунд, чтобы подтвердить."
                )
                return

            self._set_status("🧊 Имитирую зависание. Процесс перестанет отвечать (это нормально для теста watchdog).")
            self._log("[ENV-DIAG GUI] simulate_hang confirmed -> blocking thread")

            try:
                QApplication.processEvents()
            except Exception:
                pass

            while True:
                time.sleep(1)

    _ENV_DIAG_CLASS = EnvDiagnosticsWindow
    return _ENV_DIAG_CLASS


# Храним окно по главному окну (weakref), чтобы не плодить копии
_WINDOWS = weakref.WeakKeyDictionary()


def open_env_diagnostics_window(main_window: Optional['QWidget']) -> None:
    """Открыть (или активировать) окно диагностики окружения для конкретного MainWindow."""
    if main_window is None:
        return

    # Ленивый импорт класса окна и Qt
    EnvDiagnosticsWindow = _get_env_diag_class()

    w = _WINDOWS.get(main_window)
    if w is None:
        # Попробуем забрать лог-функцию у главного окна, если она есть
        log_func = None
        for attr in ("write_bot_log", "write_log", "log", "append_log", "add_log_line"):
            try:
                candidate = getattr(main_window, attr, None)
                if callable(candidate):
                    log_func = candidate
                    break
            except Exception:
                pass

        w = EnvDiagnosticsWindow(parent=main_window, log_func=log_func)
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
