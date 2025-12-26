# -*- coding: utf-8 -*-
"""
Windows autostart utilities and shared config helpers.
- Лог пишется только в папку "log/windows_startup.log" и только если включён debug.
- Корневой файл autostart_bootstrap.log больше не создаётся; если он есть — удаляется при импорте.

В этом выпуске:
- Усилен метод Планировщика задач: корректное определение текущего пользователя для /RU,
  пробный запуск с/без /IT и /RL, аккуратные кавычки в /TR, подробная диагностика stdout/stderr
  и кода возврата. Если schtasks отвечает отказом — вы увидите ЧЁТКУЮ причину в логе колбэка.
"""
from __future__ import annotations

import configparser
import io
import os
import subprocess
import sys
import tempfile
import time

# ------------------------ OS / paths helpers ------------------------

def _is_windows() -> bool:
    return os.name == "nt"


def get_launcher_exe():
    """Возвращает путь к onefile-стабу (если есть), иначе — текущий exe."""
    return os.environ.get("NUITKA_ONEFILE_PARENT", sys.executable)


def get_launcher_dir():
    return os.path.dirname(os.path.abspath(get_launcher_exe()))


# Определяем BASE_DIR и CONFIG_PATH единообразно для .exe/.py/onefile
if "NUITKA_ONEFILE_PARENT" in os.environ:
    BASE_DIR = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
elif getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(get_launcher_exe())
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")

# Nuitka onefile temp-dir fix
try:
    temp_indicator = os.path.join(os.environ.get("TEMP", ""), "onefile_")
    if (
        ("onefile_" in BASE_DIR.lower() or (temp_indicator and BASE_DIR.lower().startswith(temp_indicator.lower())))
        and "NUITKA_ONEFILE_PARENT" in os.environ
    ):
        BASE_DIR = os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
        CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
except Exception:
    pass


# Рабочая директория = BASE_DIR (важно для относительных путей)
def _enforce_cwd_to_base_dir():
    try:
        os.chdir(BASE_DIR)
    except Exception:
        pass


_enforce_cwd_to_base_dir()


# ------------------------ Debug & logging ------------------------

def _read_debug_enabled_from_config() -> bool:
    try:
        cfg = configparser.ConfigParser()
        if os.path.exists(CONFIG_PATH):
            for enc in ("utf-8", "utf-8-sig", "cp1251"):
                try:
                    cfg.read(CONFIG_PATH, encoding=enc)
                    break
                except Exception:
                    continue
        return cfg.getboolean("credentials", "debug", fallback=False)
    except Exception:
        return False


DEBUG_ENABLED = _read_debug_enabled_from_config()
# Папка с логами называется "log" (как и в остальном проекте)
LOG_DIR = os.path.join(BASE_DIR, "log")
LOG_FILE = os.path.join(LOG_DIR, "windows_startup.log")

# На всякий случай — выключаем и чистим «старый» корневой лог,
# который не должен существовать.
BOOTSTRAP_ROOT_LOG = os.path.join(BASE_DIR, "autostart_bootstrap.log")
def _purge_root_bootstrap_log():
    try:
        if os.path.exists(BOOTSTRAP_ROOT_LOG):
            os.remove(BOOTSTRAP_ROOT_LOG)
        # Маркер для других модулей (если они его уважают), чтобы не создавать этот файл.
        os.environ["DISABLE_AUTOSTART_BOOTSTRAP_LOG"] = "1"
    except Exception:
        pass


_purge_root_bootstrap_log()


def _debug_log(message: str) -> None:
    """Пишем лог ТОЛЬКО если включён DEBUG; только в LOG_DIR/windows_startup.log"""
    if not DEBUG_ENABLED:
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # Простая ротация ~5 МБ
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
                os.replace(LOG_FILE, LOG_FILE + ".old")
        except Exception:
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except Exception:
        pass


# --- Опциональная переопределялка --config из CLI ----------------------------
def _scan_cli_for_config_override():
    try:
        args = sys.argv[1:]
        for i, a in enumerate(args):
            al = a.lower()
            if al in ("--config", "/config"):
                if i + 1 < len(args):
                    p = args[i + 1].strip()
                    if p.startswith('"') and p.endswith('"'):
                        p = p[1:-1]
                    return os.path.abspath(os.path.expanduser(p))
    except Exception:
        pass
    return None


_cfg_override = _scan_cli_for_config_override()
if _cfg_override:
    CONFIG_PATH = _cfg_override


# --- Bootstrap diagnostics (видно только при DEBUG) --------------------------
def _bootstrap_diag():
    try:
        try:
            det_enabled, det_tray, det_method = detect_autorun()
        except Exception:
            det_enabled, det_tray, det_method = (False, None, "n/a")
        _debug_log("=== BOOT ===")
        _debug_log(f"cwd={os.getcwd()}")
        _debug_log(f"get_launcher_exe()={get_launcher_exe()}")
        _debug_log(f"BASE_DIR={BASE_DIR}")
        _debug_log(f"CONFIG_PATH={CONFIG_PATH} exists={os.path.exists(CONFIG_PATH)}")
        _debug_log(f"argv={sys.argv}")
        _debug_log(f"autorun_detected={det_enabled}, tray={det_tray}, method={det_method}")
    except Exception:
        pass


_bootstrap_diag()


# --- Robust INI reader (read-only) + diagnostics -----------------------------
def _read_ini(path: str):
    cfg = configparser.ConfigParser()
    if not os.path.exists(path):
        return cfg
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            cfg.read(path, encoding=enc)
            break
        except Exception:
            continue

    try:
        _debug_log("sections=" + ", ".join(cfg.sections()))
        for sec in cfg.sections():
            try:
                items = dict(cfg.items(sec))
                if sec.lower() == "credentials" and "token" in items:
                    t = items.get("token", "")
                    items["token"] = (t[:6] + "..." + t[-6:]) if len(t) > 12 else "***"
            except Exception:
                items = {}
            _debug_log(f"[{sec}] {items}")
    except Exception:
        pass
    return cfg


# ------------------------ Autostart core ------------------------

STARTUP_SECTION = "startup"           # безопасная секция
STARTUP_AUTORUN_KEY = "autorun"
STARTUP_TRAY_KEY = "start_in_tray"
RUN_VALUE_NAME = "AutoCraftBot"       # имя в реестре/папке Автозагрузка/Планировщике
STARTUP_METHOD_KEY = "method"         # 'auto' | 'startup' | 'registry' | 'schtask'


def load_startup_full() -> tuple:
    """
    Загружает (autorun, start_in_tray, method).
    По умолчанию метод -> 'startup' (папка Автозагрузка).
    """
    cfg = configparser.ConfigParser()
    det_enabled, det_tray, det_method = detect_autorun()
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    if STARTUP_SECTION not in cfg:
        cfg[STARTUP_SECTION] = {}

    autorun = cfg.getboolean(STARTUP_SECTION, STARTUP_AUTORUN_KEY, fallback=det_enabled)
    start_in_tray = cfg.getboolean(STARTUP_SECTION, STARTUP_TRAY_KEY, fallback=(det_tray or False))
    method = cfg.get(STARTUP_SECTION, STARTUP_METHOD_KEY, fallback="").strip().lower()

    if not method:
        if det_method in ("startup_bat", "startup_lnk"):
            method = "startup"
        elif det_method == "registry":
            method = "registry"
        elif det_method == "schtask":
            method = "schtask"
        else:
            method = "startup"

    if method not in ("auto", "startup", "registry", "schtask"):
        method = "startup"

    cfg[STARTUP_SECTION][STARTUP_AUTORUN_KEY] = "true" if autorun else "false"
    cfg[STARTUP_SECTION][STARTUP_TRAY_KEY] = "true" if start_in_tray else "false"
    cfg[STARTUP_SECTION][STARTUP_METHOD_KEY] = method
    try:
        _atomic_write(CONFIG_PATH, _config_to_str(cfg))
    except Exception:
        pass
    return autorun, start_in_tray, method


def save_startup_method(method: str) -> None:
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    if STARTUP_SECTION not in cfg:
        cfg[STARTUP_SECTION] = {}
    method = (method or "startup").lower()
    if method not in ("auto", "startup", "registry", "schtask"):
        method = "startup"
    cfg[STARTUP_SECTION][STARTUP_METHOD_KEY] = method
    _atomic_write(CONFIG_PATH, _config_to_str(cfg))


def _get_entry_and_dir():
    entry = _main_entry_path()
    return entry, os.path.dirname(entry)


def _config_path_for_entry(entry_path: str) -> str:
    """Возвращает config.ini рядом с entry (exe или скриптом)."""
    try:
        d = os.path.dirname(os.path.abspath(entry_path))
    except Exception:
        d = BASE_DIR
    return os.path.join(d, "config.ini")


def _compose_run_command_for_registry(start_in_tray: bool) -> str:
    entry, workdir = _get_entry_and_dir()
    base_cmd = _best_launch_command()
    cfg = _quote(_config_path_for_entry(entry))
    args = ""
    if start_in_tray:
        args += " --tray"
    args += f" --config {cfg}"
    return f'cmd.exe /c start "" /D {_quote(workdir)} {base_cmd} {args}'


def _schtasks_not_found(text: str) -> bool:
    t = (text or "").lower()
    needles = [
        "cannot find", "does not exist", "no tasks are running", "the system cannot find",
        "не найден", "не найдена", "не существует", "не удается найти", "не удаётся найти",
        "задача не найдена", "задача не существует",
    ]
    return any(n in t for n in needles)


def _quote(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def _main_entry_path() -> str:
    """Выбираем лучший таргет для запуска на Windows.
    Приоритет:
      1) Заморожено (.exe) -> stub exe (NUITKA_ONEFILE_PARENT или sys.executable)
      2) Если .py -> предпочесть соседний .exe с тем же стемом (bot-ok.exe и т.п.)
      3) Известные имена в папке проекта (bot-ok.exe / bot-ok.py)
      4) Запасной вариант -> argv[0]
    """
    if getattr(sys, "frozen", False):
        return os.path.abspath(get_launcher_exe())

    # 2) Не заморожено: пытаемся найти соседний exe с тем же именем
    try:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None) if main_mod else None
        if main_file:
            stem = os.path.splitext(os.path.basename(main_file))[0]
            candidate = os.path.join(BASE_DIR, f"{stem}.exe")
            if os.path.exists(candidate):
                return candidate
    except Exception:
        pass

    # 3) Известные имена
    cand = os.path.join(BASE_DIR, "bot-ok.exe")
    if os.path.exists(cand):
        return cand
    cand = os.path.join(BASE_DIR, "bot-ok.py")
    if os.path.exists(cand):
        return cand

    # 4) Последний шанс
    return os.path.abspath(sys.argv[0])


def _best_launch_command() -> str:
    """
    Возвращает команду запуска:
    - Если EXE: "<path-to-exe>"
    - Если PY:  "<python> <path-to-script.py>"
    """
    entry = _main_entry_path()
    # Если это .py и не frozen — запускаем текущим интерпретатором
    if entry.lower().endswith(".py") and not getattr(sys, "frozen", False):
        return f'{_quote(get_launcher_exe())} {_quote(entry)}'
    return _quote(entry)


def _compose_run_command(start_in_tray: bool) -> str:
    cmd = _best_launch_command()
    if start_in_tray:
        cmd += " --tray"
    return cmd


def _startup_folder_path() -> str:
    # %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")


def _startup_bat_path() -> str:
    return os.path.join(_startup_folder_path(), f"{RUN_VALUE_NAME}.bat")


def _startup_lnk_path() -> str:
    return os.path.join(_startup_folder_path(), f"{RUN_VALUE_NAME}.lnk")


# ------------------------ pywin32 helpers ------------------------

def _has_pywin32() -> bool:
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
        return True
    except Exception:
        return False


def _write_startup_shortcut(enabled: bool, start_in_tray: bool) -> bool:
    """Создать/удалить .lnk в Автозагрузке с корректным WorkingDirectory."""
    if not _is_windows():
        return False
    try:
        lnk_path = _startup_lnk_path()
        if not enabled:
            if os.path.exists(lnk_path):
                try:
                    os.remove(lnk_path)
                except Exception:
                    pass
            return True
        if not _has_pywin32():
            return False
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore

        entry, workdir = _get_entry_and_dir()
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(lnk_path)
        shortcut.TargetPath = entry
        args = ""
        if start_in_tray:
            args += " --tray"
        args += f" --config {_quote(_config_path_for_entry(entry))}"
        shortcut.Arguments = args.strip()
        shortcut.WorkingDirectory = workdir
        shortcut.IconLocation = entry
        try:
            shortcut.Description = "AutoCraft Bot — автозапуск"
        except Exception:
            pass
        shortcut.save()
        return os.path.exists(lnk_path)
    except Exception:
        return False


def _is_startup_shortcut_enabled() -> bool:
    try:
        return _is_windows() and os.path.exists(_startup_lnk_path())
    except Exception:
        return False


# ------------------------ Registry (HKCU\\Run) ------------------------

def read_autorun_registry() -> tuple:
    """
    Читать HKCU\\...\\Run.
    Возвращает (enabled: bool, start_in_tray: bool, current_cmd: str or '')
    """
    if not _is_windows():
        return (False, False, "")
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ
        ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
                enabled = isinstance(val, str) and len(val) > 0
                start_in_tray = "--tray" in (val or "")
                return (enabled, start_in_tray, val or "")
            except FileNotFoundError:
                return (False, False, "")
            except OSError:
                return (False, False, "")
    except Exception:
        return (False, False, "")


def write_autorun_registry(enabled: bool, start_in_tray: bool) -> bool:
    """Создать/удалить значение HKCU\\Run. True при успехе."""
    if not _is_windows():
        return False
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                cmd = _compose_run_command_for_registry(start_in_tray)
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        try:
            import winreg  # type: ignore
            key = winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            )
            try:
                if enabled:
                    cmd = _compose_run_command_for_registry(start_in_tray)
                    winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
            return True
        except Exception:
            return False


# ------------------------ Startup .bat ------------------------

def _write_startup_bat(enabled: bool, start_in_tray: bool) -> bool:
    """
    Fallback #1: .bat в Автозагрузке (не требует pywin32).
    """
    if not _is_windows():
        return False
    try:
        entry, workdir = _get_entry_and_dir()
        folder = _startup_folder_path()
        os.makedirs(folder, exist_ok=True)
        bat_path = _startup_bat_path()
        if enabled:
            cmd = _best_launch_command()
            if start_in_tray:
                cmd += " --tray"
            cmd += f" --config {_quote(_config_path_for_entry(entry))}"
            bat_text = (
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                f"start \"\" /D {_quote(workdir)} {cmd}\r\n"
            )
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_text)
        else:
            if os.path.exists(bat_path):
                try:
                    os.remove(bat_path)
                except Exception:
                    pass
        return True
    except Exception:
        return False


# ------------------------ Scheduled Task (schtasks) ------------------------

def _get_current_user_ru() -> str:
    """
    Локализованно определяем учетку для /RU.
    Возвращает строку вида 'DOMAIN\\User' или 'User'.
    Никаких паролей не требуется при использовании /IT.
    """
    user = None
    try:
        # os.getlogin может падать в некоторых сервисных средах
        user = os.getlogin()
    except Exception:
        pass
    if not user:
        user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    if domain and user and domain.upper() not in ("WORKGROUP", "BUILTIN", "NT AUTHORITY"):
        return f"{domain}\\{user}"
    return user or ""


def _format_process_diag(args, res) -> str:
    out = (res.stdout or "") + ("\n" if res.stdout and res.stderr else "") + (res.stderr or "")
    return (
        "Команда: " + " ".join(args) + "\n"
        f"Код возврата: {res.returncode}\n"
        f"Вывод:\n{out.strip()}"
    )


def _map_schtasks_reason(text_l: str) -> str:
    """
    Расширенное сопоставление ошибок schtasks (EN/RU).
    """
    if "access is denied" in text_l or "отказано в доступе" in text_l:
        return "Отказано в доступе. Запустите от имени нужной учётной записи (не чужой админ) и проверьте права."
    if "the task image is corrupt" in text_l or "образ задачи поврежден" in text_l or "образ задачи повреждён" in text_l:
        return "Повреждённая задача с таким именем. Удалите её вручную в Планировщике и повторите."
    if "the system cannot find" in text_l or "не удается найти" in text_l or "не удаётся найти" in text_l:
        return "Исполняемый файл или путь не найден. Проверьте наличие файла и доступность пути."
    if "a specified logon session does not exist" in text_l or "сеанс входа не существует" in text_l:
        return "Сеанс входа не существует. Создавайте задачу из интерактивной пользовательской сессии."
    if "the task name is invalid" in text_l or "недопустимое имя задачи" in text_l:
        return "Недопустимое имя задачи. Переименуйте задачу."
    if "user account restriction" in text_l or "ограничение учетной записи" in text_l or "ограничение учётной записи" in text_l:
        return "Ограничение учётной записи. Проверьте локальные политики безопасности."
    if "cannot create a file when that file already exists" in text_l or "указанный файл уже существует" in text_l or "already exists" in text_l:
        return "Задача с таким именем уже существует и защищена. Удалите её вручную и повторите."
    if "either the user name does not exist" in text_l or "пароль" in text_l or "password" in text_l:
        return "Для указанной учётной записи требуется пароль. Используйте режим /IT или создавайте задачу для текущего пользователя без пароля."
    if "invalid argument" in text_l or "недопустимый аргумент" in text_l or "unknown option" in text_l:
        return "Недопустимые аргументы для schtasks. Вероятно, параметр /RL или /IT не поддерживается этой версией Windows."
    if "the run level can be set" in text_l and "interactive" in text_l:
        return "Параметр /RL можно задавать только для интерактивных задач. Попробуем без /RL."
    return "Неизвестная ошибка Планировщика задач."


def _write_scheduled_task(enabled: bool, start_in_tray: bool, *, log=None) -> bool:
    """
    Планировщик задач (OnLogon). С улучшенными кавычками, /RU текущего пользователя и fallback-стратегией.
    Порядок попыток при создании:
      1) Базовый (как раньше): без /RU, /RL LIMITED
      2) Явный текущий пользователь: /RU <user> /IT /RL LIMITED  (без пароля)
      3) Если ругается на /RL — повторить /RU <user> /IT без /RL
    Во всех случаях логируем stdout/stderr и код возврата.
    """
    if not _is_windows():
        return False
    task_name = RUN_VALUE_NAME
    if enabled:
        entry, workdir = _get_entry_and_dir()
        cmd = _best_launch_command()
        if start_in_tray:
            cmd += " --tray"
        cmd += f" --config {_quote(_config_path_for_entry(entry))}"
        # Весь /TR — одна строка. Кавычки внутри параметра уже расставлены.
        tr = f'cmd.exe /c start "" /D {_quote(workdir)} {cmd}'

        def _try_create(args, tag: str):
            try:
                _debug_log(f"schtasks create [{tag}]: {' '.join(args)}")
                res = subprocess.run(args, capture_output=True, text=True, timeout=20)
                if res.returncode == 0:
                    if log:
                        try:
                            log(f"Планировщик задач: задача создана ({tag}).")
                        except Exception:
                            pass
                    return True, res
                else:
                    if log:
                        try:
                            reason = _map_schtasks_reason(((res.stdout or "") + (res.stderr or "")).lower())
                            log(f"Планировщик задач: не удалось создать ({tag}). Причина: {reason}")
                            diag = _format_process_diag(args, res)
                            log(diag)
                        except Exception:
                            pass
                    return False, res
            except Exception as e:
                if log:
                    try:
                        log(f"Планировщик задач: исключение при создании ({tag}): {e}")
                    except Exception:
                        pass
                return False, None

        # 1) Базовая попытка (как было раньше)
        create_args1 = [
            "schtasks", "/Create",
            "/SC", "ONLOGON",
            "/TN", task_name,
            "/TR", tr,
            "/RL", "LIMITED",
            "/F",
        ]
        ok, res1 = _try_create(create_args1, "base")
        if ok:
            return True

        # 2) Попытка с явным текущим пользователем без пароля (интерактивно)
        ru = _get_current_user_ru()
        if ru:
            create_args2 = [
                "schtasks", "/Create",
                "/SC", "ONLOGON",
                "/TN", task_name,
                "/TR", tr,
                "/RL", "LIMITED",
                "/RU", ru,
                "/IT",
                "/F",
            ]
            ok2, res2 = _try_create(create_args2, "ru+it+rl")
            if ok2:
                return True

            # 3) Если ругается на /RL — пробуем без /RL
            # (например, старые редакции Windows или политика домена)
            create_args3 = [
                "schtasks", "/Create",
                "/SC", "ONLOGON",
                "/TN", task_name,
                "/TR", tr,
                "/RU", ru,
                "/IT",
                "/F",
            ]
            ok3, res3 = _try_create(create_args3, "ru+it (no /RL)")
            if ok3:
                return True

        # Все попытки провалились
        return False
    else:
        try:
            res = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                if log:
                    try:
                        log("Планировщик задач: задача удалена.")
                    except Exception:
                        pass
                return True
            ok = _schtasks_not_found(res.stdout) or _schtasks_not_found(res.stderr)
            if log and not ok:
                try:
                    reason = _map_schtasks_reason(((res.stdout or "") + (res.stderr or "")).lower())
                    log(f"Планировщик задач: не удалось удалить. Причина: {reason}")
                    log(_format_process_diag(["schtasks", "/Delete", "/TN", task_name, "/F"], res))
                except Exception:
                    pass
            return ok
        except Exception as e:
            if log:
                try:
                    log(f"Планировщик задач: исключение при удалении: {e}")
                except Exception:
                    pass
            return False


# ------------------------ Detection helpers ------------------------

def _detect_startup_bat_tray_flag() -> bool:
    """Best-effort: прочитать .bat и понять, есть ли '--tray'."""
    try:
        p = _startup_bat_path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                c = f.read().lower()
            return "--tray" in c or "/tray" in c
    except Exception:
        pass
    return False


def _is_startup_bat_enabled() -> bool:
    return _is_windows() and os.path.exists(_startup_bat_path())


def _is_startup_lnk_enabled() -> bool:
    try:
        return _is_windows() and os.path.exists(_startup_lnk_path())
    except Exception:
        return False


def _is_schtask_enabled() -> bool:
    if not _is_windows():
        return False
    try:
        res = subprocess.run(["schtasks", "/Query", "/TN", RUN_VALUE_NAME], capture_output=True, text=True, timeout=5)
        return res.returncode == 0 and RUN_VALUE_NAME.lower() in (res.stdout or "").lower()
    except Exception:
        return False


def detect_autorun() -> tuple:
    """
    Определить, включён ли автозапуск (любым способом).
    Возвращает (enabled: bool, start_in_tray: Optional[bool], method: str)
    method in {"registry","startup_bat","startup_lnk","schtask","none"}
    start_in_tray может быть None, если нельзя точно понять.
    """
    reg_enabled, reg_tray, _ = read_autorun_registry()
    if reg_enabled:
        return True, reg_tray, "registry"
    if _is_startup_lnk_enabled():
        return True, None, "startup_lnk"
    if _is_startup_bat_enabled():
        return True, _detect_startup_bat_tray_flag(), "startup_bat"
    if _is_schtask_enabled():
        return True, None, "schtask"
    return False, None, "none"


# ------------------------ Apply / remove ------------------------

def remove_all_autorun() -> bool:
    _debug_log("remove_all_autorun() called")
    ok_reg = write_autorun_registry(False, False)
    ok_bat = _write_startup_bat(False, False)
    ok_task = _write_scheduled_task(False, False, log=None)
    try:
        lnk = _startup_lnk_path()
        if os.path.exists(lnk):
            os.remove(lnk)
    except Exception:
        pass
    det_enabled, _, _ = detect_autorun()
    return (ok_reg and ok_bat and ok_task) or (not det_enabled)


def apply_autorun_selected(enabled: bool, start_in_tray: bool, method: str, *, log=None) -> bool:
    _debug_log(f"apply_autorun_selected(enabled={enabled}, tray={start_in_tray}, method={method})")
    """
    Применить/удалить автозапуск выбранным методом.
    method: 'auto' | 'startup' | 'registry' | 'schtask'
    """
    if not _is_windows():
        if log:
            try:
                log("Автозапуск поддерживается только на Windows.")
            except Exception:
                pass
        return False

    method = (method or "startup").lower()
    if method not in ("auto", "startup", "registry", "schtask"):
        method = "startup"

    if not enabled:
        ok = remove_all_autorun()
        if ok and log:
            try:
                log("Автозапуск: все способы удалены (реестр, Автозагрузка, Планировщик).")
            except Exception:
                pass
        return ok

    # enabled=True: подчистим всё старое и применим выбранный способ
    try:
        remove_all_autorun()
    except Exception:
        pass

    if method == "auto":
        return apply_autorun(True, start_in_tray, log=log)

    if method == "startup":
        if _write_startup_shortcut(True, start_in_tray):
            if log:
                try:
                    log("Автозапуск включён: метод = startup_lnk (.lnk в папке Автозагрузка).")
                except Exception:
                    pass
            return True
        if _write_startup_bat(True, start_in_tray):
            if log:
                try:
                    log("Автозапуск включён: метод = startup_bat (.bat в папке Автозагрузка).")
                except Exception:
                    pass
            return True
        if log:
            try:
                log("Автозапуск: не удалось создать запись в папке Автозагрузка.")
            except Exception:
                pass
        return False

    if method == "registry":
        if write_autorun_registry(True, start_in_tray):
            if log:
                try:
                    log("Автозапуск включён: метод = registry (HKCU\\Run).")
                except Exception:
                    pass
            return True
        if log:
            try:
                log("Автозапуск: запись в реестр не удалась.")
            except Exception:
                pass
        return False

    if method == "schtask":
        if _write_scheduled_task(True, start_in_tray, log=log):
            if log:
                try:
                    log("Автозапуск включён: метод = schtask (Планировщик задач, OnLogon).")
                except Exception:
                    pass
            return True
        if log:
            try:
                log("Автозапуск: создание задачи в Планировщике не удалось.")
            except Exception:
                pass
        return False

    return False


def apply_autorun(enabled: bool, start_in_tray: bool, *, log=None) -> bool:
    """
    Best-effort:
      - Включение: Shortcut (.lnk) -> Registry -> Startup .bat -> Scheduled Task
      - Выключение: remove_all_autorun()
    """
    if not _is_windows():
        if log:
            log("Автозапуск поддерживается только на Windows.")
        return False

    if enabled:
        try:
            remove_all_autorun()
        except Exception:
            pass
        if _write_startup_shortcut(True, start_in_tray):
            if log:
                log("Автозапуск включён: метод = startup_lnk (.lnk в папке Автозагрузка).")
            return True
        if write_autorun_registry(True, start_in_tray):
            if log:
                log("Автозапуск включён: метод = registry (HKCU Run).")
            return True
        if _write_startup_bat(True, start_in_tray):
            if log:
                log("Автозапуск включён: метод = startup_bat (.bat в папке Автозагрузка).")
            return True
        if _write_scheduled_task(True, start_in_tray, log=log):
            if log:
                log("Автозапуск включён: метод = schtask (Планировщик задач, OnLogon).")
            return True
        if log:
            log("Автозапуск: не удалось применить ни одним способом.")
        return False
    else:
        ok = remove_all_autorun()
        if ok:
            if log:
                log("Автозапуск: все способы удалены (реестр, Автозагрузка, Планировщик).")
        else:
            if log:
                log("Автозапуск: не удалось полностью удалить, проверьте вручную.")
        return ok


def load_startup_settings() -> tuple:
    """
    Считать [startup] (autorun, start_in_tray) с разумными фоллбэками.
    """
    cfg = configparser.ConfigParser()
    det_enabled, det_tray, _ = detect_autorun()
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    if STARTUP_SECTION not in cfg:
        cfg[STARTUP_SECTION] = {}
    autorun = cfg.getboolean(STARTUP_SECTION, STARTUP_AUTORUN_KEY, fallback=det_enabled)
    # Если не можем понять tray по детекту (None) — берём из конфига (по умолчанию False)
    start_in_tray = cfg.getboolean(STARTUP_SECTION, STARTUP_TRAY_KEY, fallback=(det_tray or False))

    cfg[STARTUP_SECTION][STARTUP_AUTORUN_KEY] = "true" if autorun else "false"
    cfg[STARTUP_SECTION][STARTUP_TRAY_KEY] = "true" if start_in_tray else "false"
    try:
        _atomic_write(CONFIG_PATH, _config_to_str(cfg))
    except Exception:
        pass
    return autorun, start_in_tray


def save_startup_settings(autorun: bool, start_in_tray: bool) -> None:
    """Сохранить [startup] без трогания других разделов config.ini."""
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    if STARTUP_SECTION not in cfg:
        cfg[STARTUP_SECTION] = {}
    cfg[STARTUP_SECTION][STARTUP_AUTORUN_KEY] = "true" if autorun else "false"
    cfg[STARTUP_SECTION][STARTUP_TRAY_KEY] = "true" if start_in_tray else "false"
    _atomic_write(CONFIG_PATH, _config_to_str(cfg))


# ------------------------ Config atomic write ------------------------

def _config_to_str(cfg: configparser.ConfigParser) -> str:
    s = io.StringIO()
    cfg.write(s)
    return s.getvalue()


def _atomic_write(path: str, content: str, encoding: str = "utf-8"):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="._cfg_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        try:
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
