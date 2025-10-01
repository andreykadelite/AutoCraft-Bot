import os
import re
import sys
import time
import asyncio
import ctypes
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Dict

import aiohttp
from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ==========================
#  Browser Installer Module
# ==========================
#
# Этот модуль полностью изолирует всё, что связано с УСТАНОВКОЙ БРАУЗЕРОВ:
# - клавиатуры и команды "Установить ..."
# - скачивание с прогрессом
# - проверка корректности скачанного файла
# - запуск тихого инсталлятора или fallback на winget
# - определение: новая установка / обновление (по версии файла)
#
# Для работы модулю нужны:
# - CTRL: объект, предоставляющий list_browsers()
# - in_browser_mode(message) -> bool: предикат режима
# - cmd_builder(text) -> filter: для точных команд внутри режима
# - is_windows() -> bool
#
# Экспортируемая функция:
#   register_install_handlers(dp, CTRL, in_browser_mode, cmd_builder, is_windows)
#
# ==========================

# Соответствие ключа инсталлятора к «человеческому» имени, как оно появляется в списке CTRL.list_browsers()
INSTALL_TO_BROWSER = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "firefox": "Mozilla Firefox",
    "opera": "Opera",
    "yandex": "Яндекс.Браузер",
}

# ---------- Общие утилиты (файловая версия, права и т.п.) ----------

def _get_file_version(path: Optional[str]) -> str:
    """Версия EXE (A.B.C.D) на Windows. Если не удалось — ''. """
    if os.name != "nt" or not path or not os.path.exists(path):
        return ""
    try:
        import ctypes.wintypes as wt
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = (ctypes.c_char * size)()
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data):
            return ""
        lptr = ctypes.c_void_p()
        lsize = wt.UINT()
        if not ctypes.windll.version.VerQueryValueW(data, "\\\\VarFileInfo\\\\Translation", ctypes.byref(lptr), ctypes.byref(lsize)):
            return ""
        lang = ctypes.c_ushort.from_address(lptr.value).value
        codepage = ctypes.c_ushort.from_address(lptr.value + 2).value
        sub_block = f"\\\\StringFileInfo\\\\{lang:04x}{codepage:04x}\\\\FileVersion"
        vptr = ctypes.c_void_p()
        vsize = wt.UINT()
        if ctypes.windll.version.VerQueryValueW(data, sub_block, ctypes.byref(vptr), ctypes.byref(vsize)) and vptr.value:
            s = ctypes.wstring_at(vptr.value, vsize.value).strip()
            m = re.search(r"(\\d+\\.\\d+\\.\\d+\\.\\d+)", s)
            return m.group(1) if m else s
        return ""
    except Exception:
        return ""

def _app_home() -> Path:
    """Рабочая папка рядом с exe/скриптом (совместимо с Nuitka onefile)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base = Path(os.environ.get("NUITKA_ONEFILE_PARENT", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    d = base / "installers"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore
    except Exception:
        return False

# ---------- Разрешение ссылок на официальные инсталляторы ----------

async def resolve_installer(vendor: str) -> Tuple[str, str]:
    """Возвращает (filename, url) для загрузки установщика из официальных источников."""
    vendor = vendor.lower()
    if vendor == "firefox":
        url = "https://download.mozilla.org/?product=firefox-latest&os=win64&lang=ru"
        return ("FirefoxSetup_latest_ru_win64.exe", url)
    if vendor == "edge":
        url = "https://go.microsoft.com/fwlink/?LinkID=2093437"
        return ("MicrosoftEdgeEnterpriseX64.msi", url)
    if vendor == "chrome":
        url = "https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi"
        return ("GoogleChromeStandaloneEnterprise64.msi", url)
    if vendor == "opera":
        # Ленд с оффлайн-сборкой; иногда отдаёт HTML. На это есть fallback через winget.
        url = "https://www.opera.com/download"
        return ("Opera_Offline_64.exe", url)
    if vendor in ("yandex", "яндекс", "yandexbrowser", "яндекс браузер"):
        url = "https://browser.yandex.com/download/?full=1"
        return ("Yandex_Browser_Offline.exe", url)
    raise ValueError(f"Неизвестный браузер: {vendor}")

# ---------- Скачивание с прогрессом ----------

async def download_with_progress(
    url: str,
    dest: Path,
    chunk: int = 1024 * 512,
    timeout: int = 90,
    progress_cb: Optional[Callable[[int, float, Optional[float]], asyncio.Future]] = None,
) -> None:
    """Скачиваем файл с редиректами, показывая прогресс."""
    timeout_cfg = aiohttp.ClientTimeout(total=timeout * 10)
    async with aiohttp.ClientSession(raise_for_status=True, timeout=timeout_cfg) as sess:
        async with sess.get(url, allow_redirects=True) as resp:
            total = resp.headers.get("Content-Length")
            total = int(total) if (total and total.isdigit()) else None
            total_mb = round(total / (1024*1024), 2) if total else None

            tmp = dest.with_suffix(".part")
            # подчистим залипший .part
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

            done = 0
            last_percent = -1
            with tmp.open("wb") as f:
                async for chunk_bytes in resp.content.iter_chunked(chunk):
                    f.write(chunk_bytes)
                    done += len(chunk_bytes)
                    if progress_cb:
                        if total:
                            percent = int(done * 100 / total)
                        else:
                            percent = min(99, last_percent + 1) if last_percent >= 0 else 0
                        if percent != last_percent:
                            last_percent = percent
                            await progress_cb(percent, round(done/(1024*1024), 2), total_mb)
            tmp.replace(dest)
            if progress_cb:
                await progress_cb(100, round((total or done)/(1024*1024), 2), total_mb)

# ---------- Валидация инсталлятора и команды установки ----------

def _is_valid_installer(path: Path) -> bool:
    """Проверяем, что это реально EXE/MSI (не HTML)."""
    try:
        if not path.exists() or path.stat().st_size < 100*1024:
            return False
        if path.suffix.lower() == ".msi":
            return True
        with path.open("rb") as f:
            sig = f.read(2)
        return sig == b"MZ"
    except Exception:
        return False

def run_silent_install(installer: Path, vendor: str) -> List[str]:
    """Команда для тихой установки."""
    v = vendor.lower()
    if installer.suffix.lower() == ".msi":
        if _is_admin():
            return ["msiexec", "/i", str(installer), "/qn", "/norestart"]
        else:
            return ["msiexec", "/i", str(installer), "/qn", "/norestart", "MSIINSTALLPERUSER=1", "ALLUSERS=2"]
    if v == "firefox":
        return [str(installer), "/S"]
    if v in ("chrome", "yandex", "edge"):
        return [str(installer), "/silent", "/install"]
    if v == "opera":
        return [str(installer), "/silent"]
    return [str(installer)]

def _has_winget() -> bool:
    from shutil import which
    return which("winget") is not None

def _winget_cmd_for_vendor(vendor_key: str) -> Optional[List[str]]:
    key = vendor_key.lower()
    if key == "opera":
        return ["winget", "install", "--id", "Opera.Opera", "-e", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"]
    return None

# ---------- Клавиатура и утилиты сообщений ----------

def _install_menu_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Установить Chrome"), KeyboardButton("Установить Firefox"))
    kb.row(KeyboardButton("Установить Edge"), KeyboardButton("Установить Opera"))
    kb.row(KeyboardButton("Установить Яндекс"))
    kb.row(KeyboardButton("↩️ Назад в меню модуля"))
    return kb

async def _edit_or_answer(msg_obj: Optional[types.Message], message: types.Message, text: str) -> types.Message:
    try:
        if msg_obj:
            await msg_obj.edit_text(text)
            return msg_obj
    except Exception:
        pass
    return await message.answer(text)

# ---------- Основной поток установки ----------

async def _install_flow(message: types.Message, vendor_label: str, vendor_key: str, CTRL, is_windows):
    """Скачивание и запуск установки c прогрессом, ретраями и финальным статусом."""
    # снимок ДО
    found_before = CTRL.list_browsers()
    installed_name = INSTALL_TO_BROWSER.get(vendor_key.lower())
    before_path = found_before.get(installed_name) if installed_name else None
    before_ver = _get_file_version(before_path)

    start_ts = time.time()
    home = _app_home()
    fname, url = await resolve_installer(vendor_key)
    dest = home / fname

    hdr = f"🛠 Установка: {vendor_label}"
    status = await message.answer(f"{hdr}\n\n🔽 Готовлюсь скачивать…\nИсточник: {url}\nПапка: {home}")

    last_progress_msg: Optional[types.Message] = None

    async def progress_cb(percent: int, done_mb: float, total_mb: Optional[float]):
        nonlocal last_progress_msg
        if total_mb is not None:
            text = f"{hdr}\n\n🔽 Скачивание: {percent}% ({done_mb} / {total_mb} МБ)"
        else:
            text = f"{hdr}\n\n🔽 Скачивание: {percent}% (~{done_mb} МБ)"
        last_progress_msg = await _edit_or_answer(last_progress_msg, message, text)

    last_err = None
    for attempt in range(1, 4):
        try:
            await download_with_progress(url, dest, progress_cb=progress_cb)
            break
        except Exception as e:
            last_err = e
            status = await _edit_or_answer(status, message, f"{hdr}\n\n⚠️ Попытка {attempt}/3 не удалась при скачивании: {e}")
            await asyncio.sleep(1.0 * attempt)
    else:
        await _edit_or_answer(status, message, f"{hdr}\n\n❌ Не удалось скачать инсталлятор: {last_err}")
        return

    await _edit_or_answer(status, message, f"{hdr}\n\n📦 Файл загружен: {dest.name}\nРазмер: {dest.stat().st_size // (1024*1024)} МБ")

    # проверка файла
    if not _is_valid_installer(dest):
        if vendor_key.lower() == "opera" and _has_winget():
            await _edit_or_answer(status, message, f"{hdr}\n\n⚠️ Получен неинсталляционный файл. Переключаюсь на установку через winget…")
            creationflags = 0x08000000 if is_windows() else 0
            try:
                wcmd = _winget_cmd_for_vendor("opera")
                proc = subprocess.Popen(wcmd, creationflags=creationflags)
            except Exception as e:
                await _edit_or_answer(status, message, f"{hdr}\n\n❌ Ошибка запуска winget: {e}")
                return
        else:
            await _edit_or_answer(status, message, f"{hdr}\n\n❌ Похоже, скачался не установщик (файл повреждён или HTML). Попробуй позже или установи вручную.")
            return
    else:
        await _edit_or_answer(status, message, f"{hdr}\n\nЗапускаю установку…")
        creationflags = 0x08000000 if is_windows() else 0
        try:
            cmd = run_silent_install(dest, vendor_key)
            proc = subprocess.Popen(cmd, creationflags=creationflags)
        except Exception as e:
            await _edit_or_answer(status, message, f"{hdr}\n\n❌ Ошибка запуска установки: {e}")
            return

    # мониторинг
    elapsed_msg: Optional[types.Message] = None
    elapsed = 0
    while True:
        code = proc.poll()
        if code is not None:
            break
        await asyncio.sleep(3)
        elapsed += 3
        text = f"{hdr}\n\n⚙️ Установка идёт… {elapsed} сек."
        elapsed_msg = await _edit_or_answer(elapsed_msg or status, message, text)

    total_s = int(time.time() - start_ts)
    if proc.returncode == 0:
        ok_text = f"{hdr}\n\n✅ Установка завершена за {total_s} сек."
        await _edit_or_answer(elapsed_msg or status, message, ok_text)
    else:
        fail_text = f"{hdr}\n\n⚠️ Установщик завершился с кодом {proc.returncode} за {total_s} сек.\nЕсли браузер не появился — запусти файл из папки installers вручную."
        await _edit_or_answer(elapsed_msg or status, message, fail_text)

    # снимок ПОСЛЕ и вывод честного статуса
    await asyncio.sleep(1.0)
    found_after = CTRL.list_browsers()
    after_path = found_after.get(installed_name) if installed_name else None
    after_ver = _get_file_version(after_path)

    if installed_name:
        was_present = installed_name in found_before
        is_present = installed_name in found_after
        if is_present and not was_present:
            ver_info = f" (версия {after_ver})" if after_ver else ""
            await message.answer(f"✅ Установлен новый браузер: {installed_name}{ver_info}\nПуть: {after_path}\nМожешь выбрать его через «🧭 Выбор браузера».")
        elif is_present and was_present:
            if before_ver and after_ver and before_ver != after_ver:
                await message.answer(f"🔄 Обновление завершено: {installed_name} {before_ver} → {after_ver}.")
            else:
                same_ver = f" (версия {after_ver})" if after_ver else ""
                await message.answer(f"ℹ️ {installed_name} уже был установлен{same_ver}. Обновления не обнаружено.")
        else:
            new_paths = {k: v for k, v in found_after.items() if k not in found_before}
            if new_paths:
                name, path = next(iter(new_paths.items()))
                ver = _get_file_version(path)
                vtxt = f" (версия {ver})" if ver else ""
                await message.answer(f"🧭 Похоже, появился новый браузер: {name}{vtxt}\nПуть: {path}\nВыбери его через «🧭 Выбор браузера».")
            else:
                await message.answer("ℹ️ Не удалось автоматически подтвердить новую установку. Проверь меню «🧭 Выбор браузера».")
    else:
        new_paths = {k: v for k, v in found_after.items() if k not in found_before}
        if new_paths:
            name, path = next(iter(new_paths.items()))
            ver = _get_file_version(path)
            vtxt = f" (версия {ver})" if ver else ""
            await message.answer(f"🧭 Обнаружен установленный браузер: {name}{vtxt}\nПуть: {path}")
        else:
            await message.answer("ℹ️ Установка завершена, но новый браузер не обнаружен автоматически.")

# ---------- Публичная точка входа ----------

def register_install_handlers(dp,
                              CTRL,
                              in_browser_mode,
                              cmd_builder,
                              is_windows):

    @dp.message_handler(cmd_builder("🛠 установить браузеры"))
    async def show_installers(message: types.Message):
        await message.answer("Выбери браузер для установки:", reply_markup=_install_menu_kb())

    @dp.message_handler(lambda m: in_browser_mode(m) and m.text and m.text.strip().lower() == "установить chrome")
    async def _i_chrome(message: types.Message):
        await _install_flow(message, "Google Chrome", "chrome", CTRL, is_windows)

    @dp.message_handler(lambda m: in_browser_mode(m) and m.text and m.text.strip().lower() == "установить firefox")
    async def _i_firefox(message: types.Message):
        await _install_flow(message, "Mozilla Firefox", "firefox", CTRL, is_windows)

    @dp.message_handler(lambda m: in_browser_mode(m) and m.text and m.text.strip().lower() == "установить edge")
    async def _i_edge(message: types.Message):
        await _install_flow(message, "Microsoft Edge", "edge", CTRL, is_windows)

    @dp.message_handler(lambda m: in_browser_mode(m) and m.text and m.text.strip().lower() == "установить opera")
    async def _i_opera(message: types.Message):
        await _install_flow(message, "Opera", "opera", CTRL, is_windows)

    def _is_yandex_install_cmd(m: types.Message) -> bool:
        t = getattr(m, "text", None)
        if not in_browser_mode(m) or not t:
            return False
        t = t.strip().lower()
        return t in {"установить яндекс", "установить яндекс.браузер"}

    @dp.message_handler(_is_yandex_install_cmd)
    async def _i_yandex(message: types.Message):
        await _install_flow(message, "Яндекс.Браузер", "yandex", CTRL, is_windows)
