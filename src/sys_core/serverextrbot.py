#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Объединённый распаковщик для Python.zip, serverapibot.zip,
LibreHardwareMonitor.NET.10.zip и nircmd-x64.zip.

Поведение сохранено:
- при импорте или запуске сразу выполняется распаковка;
- сначала распаковывается Python.zip -> ./python;
- затем распаковывается serverapibot.zip -> ./serverapibot;
- затем распаковывается LibreHardwareMonitor.NET.10.zip -> ./data/LibreHardwareMonitor.NET.10;
- затем распаковывается nircmd-x64.zip -> ./data/nircmd-x64;
- если целевая папка уже не пуста, распаковка пропускается;
- поиск архива идёт в тех же ключевых местах, что и в исходных скриптах.
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class UnpackSpec:
    zip_name: str
    target_folder: str


SPECS: tuple[UnpackSpec, ...] = (
    UnpackSpec(zip_name="Python.zip", target_folder="python"),
    UnpackSpec(zip_name="serverapibot.zip", target_folder="serverapibot"),
    UnpackSpec(
        zip_name="LibreHardwareMonitor.NET.10.zip",
        target_folder=str(Path("data") / "LibreHardwareMonitor.NET.10"),
    ),
    UnpackSpec(
        zip_name="nircmd-x64.zip",
        target_folder=str(Path("data") / "nircmd-x64"),
    ),
)


def _source_project_dir() -> Path:
    """
    Resolve project root in source mode.
    If module is inside sys_core/moduls, project root is one level above.
    """
    here = Path(__file__).resolve().parent
    if here.name.lower() in ("sys_core", "moduls"):
        return here.parent
    return here


def _iter_onefile_dirs(temp_root: Path) -> Iterable[Path]:
    """
    Возвращает подходящие onefile_* папки внутри TEMP.

    Сначала пытаемся найти каталоги текущего процесса (как в исходниках),
    затем, если их нет, подстраховываемся любыми onefile_* каталогами.
    Это не ломает старое поведение, но чуть повышает шанс найти архив.
    """
    pid = os.getpid()
    current_pid_dirs = []
    fallback_dirs = []

    try:
        for sub in temp_root.iterdir():
            if not sub.is_dir():
                continue
            name = sub.name
            if name.startswith(f"onefile_{pid}_"):
                current_pid_dirs.append(sub)
            elif name.startswith("onefile_"):
                fallback_dirs.append(sub)
    except Exception:
        return ()

    return tuple(current_pid_dirs) + tuple(fallback_dirs)


def find_zip_file(zip_name: str) -> Optional[Path]:
    """
    Ищем zip в нескольких местах:
     1) В папке с распакованным бинарником: sys.executable.parent
     2) В папке исходного скрипта (debug-режим): __file__.parent
     3) В корне TEMP: tempfile.gettempdir()
     4) Во вложенных onefile_* папках внутри TEMP
    """
    # 1) Папка, в которую Nuitka распаковал exe / директория интерпретатора
    try:
        exec_dir = Path(sys.executable).resolve().parent
        candidate = exec_dir / zip_name
        if candidate.is_file():
            return candidate
    except Exception:
        pass

    # 2) Рядом со скриптом
    try:
        script_dir = _source_project_dir()
        candidate = script_dir / zip_name
        if candidate.is_file():
            return candidate
    except Exception:
        pass

    # 3) В корне TEMP
    try:
        temp_root = Path(tempfile.gettempdir())
        candidate = temp_root / zip_name
        if candidate.is_file():
            return candidate
    except Exception:
        temp_root = None

    # 4) В подпапках onefile_* внутри TEMP
    if temp_root is not None:
        for sub in _iter_onefile_dirs(temp_root):
            try:
                candidate = sub / zip_name
                if candidate.is_file():
                    return candidate
            except Exception:
                continue

    return None


def get_original_dir() -> Path:
    """
    Папка, где лежит оригинальный exe или сам скрипт.
    """
    return Path(sys.argv[0]).resolve().parent


def unpack_one(spec: UnpackSpec) -> bool:
    """
    Распаковывает один архив.

    Возвращает True, если архив найден и обработан без критической ошибки
    (в том числе если папка уже была заполнена), иначе False.
    """
    zip_path = find_zip_file(spec.zip_name)
    if not zip_path:
        print(f"[unpacker] Не найден файл {spec.zip_name}", file=sys.stderr)
        return False

    target_dir = get_original_dir() / spec.target_folder

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"[unpacker] Не удалось создать папку {target_dir}: {exc}", file=sys.stderr)
        return False

    try:
        if any(target_dir.iterdir()):
            print(f"[unpacker] Папка {spec.target_folder} уже заполнена — пропускаю.")
            return True
    except Exception as exc:
        print(
            f"[unpacker] Не удалось проверить содержимое папки {target_dir}: {exc}",
            file=sys.stderr,
        )
        return False

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
        print(f"[unpacker] Распаковал {spec.zip_name} -> {target_dir}")
        return True
    except zipfile.BadZipFile:
        print(f"[unpacker] Ошибка: {spec.zip_name} повреждён или не zip.", file=sys.stderr)
    except Exception as exc:
        print(f"[unpacker] Не удалось распаковать {spec.zip_name}: {exc}", file=sys.stderr)

    return False


def unpack_all() -> dict[str, bool]:
    """
    Распаковывает все архивы в заданном порядке.
    """
    results: dict[str, bool] = {}
    for spec in SPECS:
        results[spec.zip_name] = unpack_one(spec)
    return results


# Совместимость с возможными внешними вызовами
unpack = unpack_all


# Запускаем сразу при любом import или при старте,
# как это было в обоих исходных файлах.
unpack_all()


if __name__ == "__main__":
    # Ничего дополнительно не делаем: при запуске всё уже выполнено выше.
    pass
