#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import tempfile
import zipfile
from pathlib import Path

ZIP_NAME = 'serverapibot.zip'
TARGET_FOLDER = 'serverapibot'

def find_zip_file(zip_name: str = ZIP_NAME) -> Path:
    """
    Ищем zip в нескольких местах:
     1) В папке с распакованным бинарником: sys.executable.parent
     2) В папке исходного скрипта (debug-режим): __file__.parent
     3) В корне temp: tempfile.gettempdir()
     4) Во вложенных onefile_*/ папках внутри temp
    """
    # 1) папка, в которую Nuitka распаковал exe
    try:
        exec_dir = Path(sys.executable).parent
        candidate = exec_dir / zip_name
        if candidate.is_file():
            return candidate
    except Exception:
        pass

    # 2) рядом со скриптом (если вы не упакованы)
    try:
        script_dir = Path(__file__).parent
        candidate = script_dir / zip_name
        if candidate.is_file():
            return candidate
    except Exception:
        pass

    # 3) прямо в корне TEMP
    temp_root = Path(tempfile.gettempdir())
    candidate = temp_root / zip_name
    if candidate.is_file():
        return candidate

    # 4) в подпапках onefile_<PID>_* внутри TEMP
    pid = os.getpid()
    for sub in temp_root.iterdir():
        if not sub.is_dir():
            continue
        if sub.name.startswith(f"onefile_{pid}_"):
            candidate = sub / zip_name
            if candidate.is_file():
                return candidate

    # не нашли
    return None

def get_original_dir() -> Path:
    """
    Папка, где лежит ваш оригинальный exe (или скрипт).
    """
    return Path(sys.argv[0]).parent

def unpack():
    zip_path = find_zip_file()
    if not zip_path:
        print(f"[unpacker] Не найден файл {ZIP_NAME}", file=sys.stderr)
        return

    target_dir = get_original_dir() / TARGET_FOLDER
    # создаём папку, если нужно
    target_dir.mkdir(parents=True, exist_ok=True)

    # если уже есть файлы — считаем, что распаковано
    if any(target_dir.iterdir()):
        print(f"[unpacker] Папка {TARGET_FOLDER} уже заполнена — пропускаю.")
        return

    # распаковываем
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(target_dir)
        print(f"[unpacker] Распаковал {ZIP_NAME} -> {target_dir}")
    except zipfile.BadZipFile:
        print(f"[unpacker] Ошибка: {ZIP_NAME} повреждён или не zip.", file=sys.stderr)
    except Exception as e:
        print(f"[unpacker] Не удалось распаковать: {e}", file=sys.stderr)

# сразу запускаем при любом import или при старте
unpack()
