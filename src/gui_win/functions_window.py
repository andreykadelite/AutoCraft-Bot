# -*- coding: utf-8 -*-
"""
GUI: Окно «Функции»

Идея: functions_window.py сам находит GUI-модули рядом с собой (файлы, начинающиеся с "gui_win")
и сам строит кнопки. Ты добавляешь новый модуль, а окно само подхватывает его.

Стадии загрузки (как в менеджере модулей):
- gui_win_startrun* / startrun*  : кнопки доступны сразу после запуска
- gui_win_nostartrun* / nostartrun*: кнопки появляются только после авторизации (когда в __main__.authorized_users что-то есть)

Интерфейс модуля (метаданные лежат в самом модуле):
- FUNCTIONS_BUTTON_TEXT: str                  (обязательно) текст кнопки
- FUNCTIONS_ENTRYPOINT: str                   (обязательно) имя функции-открывашки, например open_xxx_window
- FUNCTIONS_STAGE: "startrun"|"nostartrun"    (необязательно; если нет, берём из префикса имени файла)
- FUNCTIONS_TOOLTIP: str                      (необязательно)
- FUNCTIONS_ICON: str                         (необязательно) имя QStyle стандартной иконки, например "SP_MessageBoxInformation"
- FUNCTIONS_ACCESSIBLE_NAME: str              (необязательно)
- FUNCTIONS_ACCESSIBLE_DESCRIPTION: str       (необязательно)
- FUNCTIONS_ORDER: int                        (необязательно) сортировка (меньше = выше)

Ленивый импорт:
- Окно старается читать метаданные из исходника (AST) без импорта.
- Реальный импорт и вызов entrypoint делается по клику.
- Если исходника нет (например, в некоторых сборках), метаданные читаются через импорт.

Файл можно расширять, не трогая основной gui.py.
"""

from __future__ import annotations

import ast
import os
import pkgutil
import sys
from dataclasses import dataclass
from importlib import import_module, util as importlib_util, machinery as importlib_machinery
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import weakref
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QFrame,
    QDialogButtonBox,
    QPushButton,
    QStyle,
    QMessageBox,
)


# -------------------- discovery + metadata --------------------

_ALLOWED_STAGES = {"startrun", "nostartrun"}


# -------------------- runtime helpers (EXE / Nuitka) --------------------

def _is_frozen_exe() -> bool:
    """Определяем, что код запущен из скомпилированного EXE.

    Важно: Nuitka обычно *не* выставляет sys.frozen, вместо этого в модулях появляется __compiled__.
    """
    try:
        if globals().get("__compiled__", False):
            return True
    except Exception:
        pass

    # Фолбэк-эвристика для Windows
    try:
        exe = (getattr(sys, "executable", "") or "").lower()
        bn = os.path.basename(exe)
        if exe.endswith(".exe") and bn not in ("python.exe", "pythonw.exe"):
            return True
    except Exception:
        pass

    return False


def _ensure_pkg_root_on_syspath() -> None:
    """Добавляем директорию EXE в sys.path, чтобы можно было импортировать внешние модули рядом с EXE.

    Это нужно, когда ты кладёшь (или обновляешь) gui_win_* файлы рядом с EXE без пересборки.
    """
    if not _is_frozen_exe():
        return
    if not __package__:
        return
    try:
        exe_dir = Path(sys.executable).resolve().parent
        s = str(exe_dir)
        if s not in sys.path:
            sys.path.insert(0, s)
    except Exception:
        pass


def _iter_package_stems() -> List[str]:
    """Пытаемся получить список модулей внутри пакета через import machinery.

    Это особенно полезно в сборках Nuitka, где исходников может не быть рядом на диске,
    но подмодули пакета доступны для импорта.
    """
    stems: set[str] = set()

    if not __package__:
        return []

    try:
        pkg = sys.modules.get(__package__)
        if pkg is None:
            pkg = import_module(__package__)
        pkg_path = list(getattr(pkg, "__path__", []) or [])
        if pkg_path:
            for m in pkgutil.iter_modules(pkg_path):
                stems.add(m.name)
    except Exception:
        pass

    return sorted(stems)


def _guess_common_scan_dirs(base_dir: Path) -> List[Path]:
    """Небольшая эвристика для нестандартных раскладок папок в dist/onefile."""
    out: List[Path] = []
    try:
        if __package__:
            pkg_rel = Path(*__package__.split("."))
            out.append(base_dir / pkg_rel)

            # Частая раскладка: рядом с EXE лежит папка moduls/<package>
            if not str(pkg_rel).lower().startswith("moduls"):
                out.append(base_dir / "moduls" / pkg_rel)
                out.append(base_dir / "moduls" / pkg_rel.name)

        # на всякий случай: gui_win может лежать прямо тут
        out.append(base_dir / "gui_win")
        out.append(base_dir / "moduls" / "gui_win")
        out.append(base_dir / "plugins" / "gui_win")
    except Exception:
        pass

    return out


def _preferred_scan_dirs() -> List[Path]:
    """Где искать gui_win_* модули.

    Важно для Nuitka:
    - В onefile/standalone исходники могут не лежать рядом (модули могут быть .pyd с ABI-суффиксом),
      а иногда папка пакета оказывается не там, где ожидается.
    Поэтому собираем набор директорий-«кандидатов» более агрессивно, но безопасно.

    Правило: чем ближе к EXE/argv0 и текущему модулю, тем выше приоритет.
    """
    dirs: List[Path] = []

    def _add(p: Path) -> None:
        try:
            if p and p.exists() and p.is_dir() and p not in dirs:
                dirs.append(p)
        except Exception:
            pass

    # 1) Папка EXE (для внешних gui_win_* рядом с .exe без пересборки)
    if _is_frozen_exe():
        try:
            _add(Path(sys.executable).resolve().parent)
        except Exception:
            pass

    # 2) Папка argv0 (иногда отличается от sys.executable)
    try:
        argv0_dir = Path(os.path.abspath(sys.argv[0])).resolve().parent
        _add(argv0_dir)
    except Exception:
        argv0_dir = None  # type: ignore

    # 3) Эвристика для типовых раскладок (dist/moduls/gui_win и т.п.)
    try:
        base = None
        if _is_frozen_exe():
            base = Path(sys.executable).resolve().parent
        elif argv0_dir is not None:
            base = argv0_dir
        if base is not None:
            for p in _guess_common_scan_dirs(base):
                _add(p)
    except Exception:
        pass

    # 4) Папка текущего модуля (в .py режиме это «истина»)
    try:
        _add(Path(__file__).resolve().parent)
    except Exception:
        pass

    # 5) Рабочая директория и её эвристики
    try:
        cwd = Path.cwd().resolve()
        _add(cwd)
        for p in _guess_common_scan_dirs(cwd):
            _add(p)
        if __package__:
            _add(cwd / Path(*__package__.split(".")))
    except Exception:
        pass

    return dirs


def _iter_dir_stems(scan_dir: Path) -> List[str]:
    """Собираем имена модулей из папки максимально надёжно.

    pkgutil иногда может давать неполный список в некоторых сборках/версиях,
    поэтому добавляем фолбэк по файловой системе.
    """
    stems: set[str] = set()

    try:
        for m in pkgutil.iter_modules([str(scan_dir)]):
            stems.add(m.name)
    except Exception:
        pass

    try:
        suffixes = tuple(importlib_machinery.all_suffixes())
        for p in scan_dir.iterdir():
            if not p.is_file():
                continue
            nm = p.name
            nm_l = nm.lower()
            if not (nm_l.startswith("gui_win") or nm_l.startswith("guiwin")):
                continue
            for suf in suffixes:
                if nm_l.endswith(suf):
                    stems.add(nm[: -len(suf)])
                    break
    except Exception:
        pass

    return sorted(stems)

@dataclass(frozen=True)
class _Plugin:
    name: str
    stage: str
    order: int
    button_text: str
    entrypoint: str
    tooltip: str
    icon_name: str
    acc_name: str
    acc_desc: str
    import_candidates: Tuple[str, ...]
    origin_hint: str


def _safe_read_text(path: Path, limit: int = 200_000) -> Optional[str]:
    try:
        data = path.read_text(encoding="utf-8", errors="ignore")
        if len(data) > limit:
            return data[:limit]
        return data
    except Exception:
        return None


def _ast_extract_constants(source: str) -> Dict[str, Any]:
    """
    Достаём константы вида NAME = <literal> (str/int/bool/None) из исходника.
    Никаких exec/import, только парсинг.
    """
    out: Dict[str, Any] = {}
    try:
        tree = ast.parse(source)
    except Exception:
        return out

    def _lit(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            val = _lit(node.value)
            if key.startswith("FUNCTIONS_"):
                out[key] = val
    return out


def _stage_from_name(module_name: str) -> str:
    """Определяем стадию загрузки по имени модуля.

    Поддерживаем оба варианта именования:
    - классика: startrun* / nostartrun*
    - новый префикс: gui_win_* (например gui_win_nostartrun_xxx / gui_win_startrun_xxx)
      или просто gui_win_xxx с заданным FUNCTIONS_STAGE внутри модуля.
    """
    n = module_name.lower()

    # Разберём имя на "токены", чтобы не спутать startrun внутри nostartrun
    tokens = re.split(r"[^a-z0-9]+", n)

    if "nostartrun" in tokens or n.startswith(("gui_win_nostartrun", "guiwin_nostartrun")):
        return "nostartrun"
    if "startrun" in tokens or n.startswith(("gui_win_startrun", "guiwin_startrun")):
        return "startrun"

    # Совместимость со старой схемой
    if n.startswith("nostartrun"):
        return "nostartrun"
    if n.startswith("startrun"):
        return "startrun"

    return "startrun"


def _build_import_candidates(stem: str) -> Tuple[str, ...]:
    """Кандидаты на импорт модуля.

    В EXE важно уметь импортировать модуль как:
    - полное имя пакета (moduls.gui_win.<stem>)
    - относительное имя (.<stem>)
    - top-level (<stem>) как фолбэк
    """
    candidates: List[str] = []

    if __package__:
        candidates.append(f"{__package__}.{stem}")
        candidates.append(f".{stem}")

    candidates.append(stem)

    # убираем дубли, сохраняя порядок
    uniq: List[str] = []
    for c in candidates:
        if c not in uniq:
            uniq.append(c)
    return tuple(uniq)


def _spec_origin_for_candidate(cand: str) -> Optional[str]:
    try:
        spec = importlib_util.find_spec(cand, package=__package__ if cand.startswith(".") else None)
        if spec and spec.origin:
            return spec.origin
    except Exception:
        return None
    return None


def _load_plugin_from_source(stem: str, py_path: Path) -> Optional[_Plugin]:
    src = _safe_read_text(py_path)
    if not src:
        return None

    if "FUNCTIONS_BUTTON_TEXT" not in src or "FUNCTIONS_ENTRYPOINT" not in src:
        return None

    meta = _ast_extract_constants(src)

    btn_text = meta.get("FUNCTIONS_BUTTON_TEXT")
    entry = meta.get("FUNCTIONS_ENTRYPOINT")
    if not isinstance(btn_text, str) or not btn_text.strip():
        return None
    if not isinstance(entry, str) or not entry.strip():
        return None

    stage = meta.get("FUNCTIONS_STAGE")
    if not isinstance(stage, str) or stage not in _ALLOWED_STAGES:
        stage = _stage_from_name(stem)

    order = meta.get("FUNCTIONS_ORDER")
    if not isinstance(order, int):
        order = 1000

    tooltip = meta.get("FUNCTIONS_TOOLTIP")
    if not isinstance(tooltip, str):
        tooltip = ""

    icon_name = meta.get("FUNCTIONS_ICON")
    if not isinstance(icon_name, str):
        icon_name = ""

    acc_name = meta.get("FUNCTIONS_ACCESSIBLE_NAME")
    if not isinstance(acc_name, str):
        acc_name = f"Кнопка: {btn_text}"

    acc_desc = meta.get("FUNCTIONS_ACCESSIBLE_DESCRIPTION")
    if not isinstance(acc_desc, str):
        acc_desc = ""

    return _Plugin(
        name=stem,
        stage=stage,
        order=order,
        button_text=btn_text.strip(),
        entrypoint=entry.strip(),
        tooltip=tooltip.strip(),
        icon_name=icon_name.strip(),
        acc_name=acc_name.strip(),
        acc_desc=acc_desc.strip(),
        import_candidates=_build_import_candidates(stem),
        origin_hint=str(py_path),
    )


def _load_plugin_from_import(stem: str) -> Optional[_Plugin]:
    """
    Фолбэк: если исходник недоступен (например, в некоторых сборках),
    читаем метаданные через импорт.
    """
    last_err: Optional[Exception] = None
    module = None

    for cand in _build_import_candidates(stem):
        try:
            if cand.startswith("."):
                module = import_module(cand, package=__package__)
            else:
                module = import_module(cand)
            break
        except Exception as e:
            last_err = e

    if module is None:
        return None

    btn_text = getattr(module, "FUNCTIONS_BUTTON_TEXT", None)
    entry = getattr(module, "FUNCTIONS_ENTRYPOINT", None)
    if not isinstance(btn_text, str) or not btn_text.strip():
        return None
    if not isinstance(entry, str) or not entry.strip():
        return None

    stage = getattr(module, "FUNCTIONS_STAGE", None)
    if not isinstance(stage, str) or stage not in _ALLOWED_STAGES:
        stage = _stage_from_name(stem)

    order = getattr(module, "FUNCTIONS_ORDER", None)
    if not isinstance(order, int):
        order = 1000

    tooltip = getattr(module, "FUNCTIONS_TOOLTIP", "")
    if not isinstance(tooltip, str):
        tooltip = ""

    icon_name = getattr(module, "FUNCTIONS_ICON", "")
    if not isinstance(icon_name, str):
        icon_name = ""

    acc_name = getattr(module, "FUNCTIONS_ACCESSIBLE_NAME", f"Кнопка: {btn_text}")
    if not isinstance(acc_name, str):
        acc_name = f"Кнопка: {btn_text}"

    acc_desc = getattr(module, "FUNCTIONS_ACCESSIBLE_DESCRIPTION", "")
    if not isinstance(acc_desc, str):
        acc_desc = ""

    origin_hint = ""
    try:
        origin_hint = str(getattr(module, "__file__", "") or "")
    except Exception:
        origin_hint = ""

    return _Plugin(
        name=stem,
        stage=stage,
        order=order,
        button_text=btn_text.strip(),
        entrypoint=entry.strip(),
        tooltip=tooltip.strip(),
        icon_name=icon_name.strip(),
        acc_name=acc_name.strip(),
        acc_desc=acc_desc.strip(),
        import_candidates=_build_import_candidates(stem),
        origin_hint=origin_hint or stem,
    )


def _discover_plugins() -> Tuple[List[_Plugin], List[_Plugin]]:
    """Ищем gui_win_* модули и собираем плагины.

    В EXE (Nuitka) модули могут быть:
    - как обычные .py файлы (в dist),
    - как расширения (.pyd) с ABI-суффиксом,
    - или вообще без исходников (тогда метаданные вытаскиваем через импорт).

    Поэтому:
    1) Сканируем директории (файловая система).
    2) Дополнительно спрашиваем сам пакет через pkgutil (если директории не отражают реальность сборки).
    """
    _ensure_pkg_root_on_syspath()

    try:
        self_stem = Path(__file__).resolve().stem
    except Exception:
        self_stem = "functions_window"

    star: List[_Plugin] = []
    no: List[_Plugin] = []

    seen: set[str] = set()

    def _consider_stem(stem: str, scan_dir: Optional[Path] = None) -> None:
        nonlocal star, no, seen

        if stem in seen:
            return
        seen.add(stem)

        if stem in (self_stem, "__init__"):
            return
        if stem.startswith("_"):
            return

        stem_l = stem.lower()
        if not (stem_l.startswith("gui_win") or stem_l.startswith("guiwin")):
            return

        plugin: Optional[_Plugin] = None

        # 1) Пытаемся читать исходник рядом с найденным файлом (быстро, без импорта PyQt)
        if scan_dir is not None:
            try:
                py_path = scan_dir / f"{stem}.py"
                if py_path.exists():
                    plugin = _load_plugin_from_source(stem, py_path)
            except Exception:
                plugin = None

        # 2) Фолбэк: читаем метаданные через импорт (важно для .pyd и сборок без .py)
        if plugin is None:
            plugin = _load_plugin_from_import(stem)

        # 3) Ещё один фолбэк: если importlib знает origin и он .py/.pyw
        if plugin is None:
            origin = None
            try:
                if __package__:
                    origin = _spec_origin_for_candidate(f"{__package__}.{stem}")
                if not origin:
                    origin = _spec_origin_for_candidate(stem)
            except Exception:
                origin = None

            if origin:
                try:
                    op = Path(origin)
                    if op.exists() and op.suffix.lower() in (".py", ".pyw"):
                        plugin = _load_plugin_from_source(stem, op)
                    else:
                        plugin = _load_plugin_from_import(stem)
                except Exception:
                    plugin = _load_plugin_from_import(stem)

        if plugin is None:
            return

        if plugin.stage == "nostartrun":
            no.append(plugin)
        else:
            star.append(plugin)

    # 1) Файловая система: директории-кандидаты
    for scan_dir in _preferred_scan_dirs():
        for stem in _iter_dir_stems(scan_dir):
            _consider_stem(stem, scan_dir)

    # 2) Пакетный список (на случай, когда файловая система «пустая», но импорт работает)
    for stem in _iter_package_stems():
        _consider_stem(stem, None)

    star.sort(key=lambda p: (p.order, p.name.lower()))
    no.sort(key=lambda p: (p.order, p.name.lower()))
    return star, no


def _is_authorized() -> bool:
    """Считаем авторизацией наличие __main__.authorized_users."""
    try:
        import __main__  # type: ignore

        users = getattr(__main__, "authorized_users", None)
        if users:
            return True
    except Exception:
        pass
    return False


# -------------------- UI --------------------


class FunctionsWindow(QDialog):
    """
    Окно «Функции».

    Кнопки собираются автоматически из модулей рядом с этим файлом.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("functionsWindow")
        self.setWindowTitle("Функции")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setAccessibleName("Окно функций")
        self.setAccessibleDescription("Окно с дополнительными инструментами и окнами")

        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)

        # Подхватываем стиль/палитру родителя, чтобы окно выглядело "в том же стиле"
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

        self._plugins_startrun, self._plugins_nostartrun = _discover_plugins()
        self._nostartrun_loaded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        header = QLabel("Функции")
        header.setObjectName("functionsHeader")
        header.setAccessibleName("Заголовок окна функций")
        header.setAlignment(Qt.AlignLeft)
        header.setStyleSheet("font-size: 16pt; font-weight: 600;")
        layout.addWidget(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setAccessibleName("Разделитель")
        layout.addWidget(divider)

        self.hint = QLabel(self._build_hint_text())
        self.hint.setWordWrap(True)
        self.hint.setAccessibleName("Подсказка")
        self.hint.setAccessibleDescription("Текст-подсказка о назначении окна")
        self.hint.setFocusPolicy(Qt.StrongFocus)
        layout.addWidget(self.hint)

        # Контейнер для кнопок
        self._buttons_layout = QVBoxLayout()
        self._buttons_layout.setSpacing(8)
        layout.addLayout(self._buttons_layout)

        # Секция: кнопки сразу
        self._add_section_label("Доступно сразу")
        self._star_buttons: List[QPushButton] = []
        for p in self._plugins_startrun:
            self._star_buttons.append(self._add_plugin_button(p))

        # Секция: после авторизации (появится, когда нужно)
        self._auth_section_label = QLabel("После авторизации")
        self._auth_section_label.setAccessibleName("Заголовок секции после авторизации")
        self._auth_section_label.setStyleSheet("font-weight: 600; margin-top: 8px;")
        self._auth_section_label.setVisible(False)
        self._buttons_layout.addWidget(self._auth_section_label)

        self._no_buttons: List[QPushButton] = []

        # Низ: «Закрыть»
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        self._close_btn = button_box.button(QDialogButtonBox.Close)
        self._close_btn.setText("Закрыть")
        self._close_btn.setAccessibleName("Закрыть окно функций")
        self._close_btn.setAccessibleDescription("Закрывает окно функций")
        self._close_btn.setDefault(True)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Следим за авторизацией и подгружаем nostartrun
        self._auth_timer = QTimer(self)
        self._auth_timer.timeout.connect(self._maybe_load_nostartrun)
        self._auth_timer.start(1000)
        self._maybe_load_nostartrun()

        # Tab order: подсказка -> первая кнопка -> ... -> закрыть
        self._rebuild_tab_order()

        self.hint.setFocus(Qt.TabFocusReason)

    def _build_hint_text(self) -> str:
        lines = [
            "Тут живут дополнительные окна и инструменты.",
            "",
            "Кнопки собираются автоматически из модулей рядом с этим файлом.",
            "Чтобы добавить новую функцию, добавь модуль с FUNCTIONS_BUTTON_TEXT и FUNCTIONS_ENTRYPOINT.",
            "",
            "Файл редактируется отдельно: gui_win/functions_window.py",
        ]
        if not self._plugins_startrun and not self._plugins_nostartrun:
            lines.append("")
            lines.append("⚠️ Плагины не найдены. Проверь, что рядом есть модули с метаданными.")
        return "\n".join(lines)

    def _add_section_label(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setAccessibleName(f"Заголовок секции: {text}")
        lbl.setStyleSheet("font-weight: 600; margin-top: 6px;")
        self._buttons_layout.addWidget(lbl)

    def _qstyle_icon(self, icon_name: str):
        if not icon_name:
            return None
        try:
            sp = getattr(QStyle, icon_name, None)
            if sp is None:
                return None
            return self.style().standardIcon(sp)
        except Exception:
            return None

    def _add_plugin_button(self, plugin: _Plugin) -> QPushButton:
        btn = QPushButton(plugin.button_text, self)
        icon = self._qstyle_icon(plugin.icon_name)
        if icon is not None:
            try:
                btn.setIcon(icon)
            except Exception:
                pass

        btn.setAccessibleName(plugin.acc_name or f"Кнопка: {plugin.button_text}")
        if plugin.acc_desc:
            btn.setAccessibleDescription(plugin.acc_desc)
        if plugin.tooltip:
            btn.setToolTip(plugin.tooltip)

        btn.clicked.connect(lambda _=False, p=plugin: self._invoke_plugin(p))
        self._buttons_layout.addWidget(btn)
        return btn

    def _maybe_load_nostartrun(self) -> None:
        if self._nostartrun_loaded:
            return
        if not self._plugins_nostartrun:
            # нечего грузить
            self._nostartrun_loaded = True
            try:
                self._auth_timer.stop()
            except Exception:
                pass
            return
        if not _is_authorized():
            return

        # Авторизованы: добавляем кнопки
        self._auth_section_label.setVisible(True)
        for p in self._plugins_nostartrun:
            self._no_buttons.append(self._add_plugin_button(p))

        self._nostartrun_loaded = True
        try:
            self._auth_timer.stop()
        except Exception:
            pass

        self._rebuild_tab_order()

    def _rebuild_tab_order(self) -> None:
        ordered: List[QPushButton] = []
        ordered.extend(self._star_buttons)
        ordered.extend(self._no_buttons)

        if ordered:
            self.setTabOrder(self.hint, ordered[0])
            for a, b in zip(ordered, ordered[1:]):
                self.setTabOrder(a, b)
            self.setTabOrder(ordered[-1], self._close_btn)
        else:
            self.setTabOrder(self.hint, self._close_btn)

    def _invoke_plugin(self, plugin: _Plugin) -> None:
        """Ленивый импорт: грузим модуль и вызываем entrypoint только по клику."""
        main_window = self.parent() or self

        module = None
        last_err: Optional[Exception] = None

        for cand in plugin.import_candidates:
            try:
                if cand.startswith("."):
                    module = import_module(cand, package=__package__)
                else:
                    module = import_module(cand)
                break
            except Exception as e:
                last_err = e

        # Фолбэк: если модуль попал в dist как файл (.py/.pyd), но не импортируется по имени
        # (например, добавлен как data-dir или лежит в нестандартной папке).
        if module is None and plugin.origin_hint:
            try:
                op = Path(plugin.origin_hint)
                if op.exists() and op.is_file():
                    full_name = f"{__package__}.{plugin.name}" if __package__ else plugin.name
                    spec = importlib_util.spec_from_file_location(full_name, str(op))
                    if spec and spec.loader:
                        module = importlib_util.module_from_spec(spec)
                        sys.modules[full_name] = module
                        spec.loader.exec_module(module)
            except Exception as e:
                last_err = e

        if module is None:
            QMessageBox.warning(
                self,
                "Функции",
                "Не удалось импортировать модуль.\n\n"
                f"Модуль: {plugin.name}\n"
                f"Источник: {plugin.origin_hint}\n"
                f"Ошибка: {last_err!r}",
            )
            return

        fn = getattr(module, plugin.entrypoint, None)
        if not callable(fn):
            QMessageBox.warning(
                self,
                "Функции",
                "В модуле нет нужной функции.\n\n"
                f"Модуль: {plugin.name}\n"
                f"Ищем: {plugin.entrypoint}\n"
                f"Источник: {plugin.origin_hint}",
            )
            return

        try:
            # Основной сценарий: entrypoint(main_window)
            fn(main_window)
        except TypeError:
            # Фолбэк: entrypoint()
            try:
                fn()
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Функции",
                    "Ошибка при выполнении функции.\n\n"
                    f"{e!r}",
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Функции",
                "Ошибка при выполнении функции.\n\n"
                f"{e!r}",
            )


# -------------------- open + button factory --------------------

# Храним окна по главному окну (weakref), чтобы не плодить копии
_WINDOWS = weakref.WeakKeyDictionary()


def open_functions_window(main_window):
    """Открыть (или активировать) окно функций для конкретного MainWindow."""
    if main_window is None:
        return

    w = _WINDOWS.get(main_window)
    if w is None:
        w = FunctionsWindow(parent=main_window)
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


def create_functions_button(main_window) -> QPushButton:
    """Создаёт кнопку «Функции» и вешает обработчик открытия окна."""
    btn = QPushButton("Функции", main_window)
    try:
        btn.setIcon(main_window.style().standardIcon(QStyle.SP_FileDialogDetailedView))
    except Exception:
        pass

    btn.setAccessibleName("Кнопка: Функции")
    btn.setAccessibleDescription("Открыть окно функций")
    btn.setToolTip("Открыть окно функций")
    btn.clicked.connect(lambda: open_functions_window(main_window))
    return btn
