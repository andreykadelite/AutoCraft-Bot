import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List, Tuple

import psutil
from aiogram import types
from aiogram.dispatcher import Dispatcher, FSMContext

# --- Клавиатура "Утилиты" из keymenu (мягкий импорт, чтобы ничего не ломалось) ---
try:
    from keymenu import get_utilities_keyboard as _get_utilities_keyboard  # type: ignore
except Exception:
    try:
        from moduls.keymenu import get_utilities_keyboard as _get_utilities_keyboard  # type: ignore
    except Exception:
        _get_utilities_keyboard = None  # type: ignore

BACK_TO_UTILITIES_TEXT = "⬅️ Назад в утилиты"

def _utilities_keyboard() -> types.ReplyKeyboardMarkup:
    """Возвращает клавиатуру меню 'Утилиты' из keymenu. Если keymenu недоступен — безопасный fallback."""
    if _get_utilities_keyboard:
        try:
            kb = _get_utilities_keyboard()
            if kb:
                return kb
        except Exception:
            pass
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Назад")
    return kb

import browser_installer
import inspect
from functools import wraps

# --- Регистрация утилиты в реестре (для динамического меню) ---

def _register_self_in_utilities_registry() -> None:
    """Регистрирует кнопку/команду запуска модуля в utilities_registry.

    Важно: регистрация не должна ломать модуль, поэтому здесь максимально мягкие try/except.
    """
    try:
        # Частый случай: utilities_registry.py лежит рядом/в PYTHONPATH
        from utilities_registry import register_utility  # type: ignore
    except Exception:
        try:
            # Если реестр расположен внутри пакета moduls
            from moduls.utilities_registry import register_utility  # type: ignore
        except Exception:
            register_utility = None  # type: ignore

    if not register_utility:
        return

    try:
        register_utility(
            key="nostartrun_browser_control",
            title="Управление браузером",
            trigger_text="Управление браузером",
            group="utilities",
            order=320,
            description="Открывает модуль управления браузером (beta).",
        )
    except Exception:
        # Реестр не должен валить модуль
        pass


# Регистрируемся сразу при импорте (так кнопка попадёт в динамическое меню)
_register_self_in_utilities_registry()

# --- Константы и конфиг ---

CONFIG_PATH = Path(__file__).with_name("browser_config.json")

SEARCH_ENGINES = {
    "Google": "https://www.google.com/search?q={query}",
    "Яндекс": "https://yandex.ru/search/?text={query}",
    "Bing": "https://www.bing.com/search?q={query}",
    "DuckDuckGo": "https://duckduckgo.com/?q={query}"
}
DEFAULT_SEARCH = "Яндекс"

# Известные браузеры: имя -> (exe_name, типичные пути)
KNOWN_BROWSERS: Dict[str, Tuple[str, List[str]]] = {
    "Google Chrome": (
        "chrome.exe",
        [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
    ),
    "Microsoft Edge": (
        "msedge.exe",
        [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ],
    ),
    "Mozilla Firefox": (
        "firefox.exe",
        [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ],
    ),
    "Brave": (
        "brave.exe",
        [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
    ),
    "Opera": (
        "opera.exe",
        [
            str(Path.home() / r"AppData\Local\Programs\Opera\opera.exe"),
            str(Path.home() / r"AppData\Local\Programs\Opera GX\opera.exe"),
        ],
    ),
    "Vivaldi": (
        "vivaldi.exe",
        [
            r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
            r"C:\Program Files (x86)\Vivaldi\Application\vivaldi.exe",
        ],
    ),
    "Яндекс.Браузер": (
        "browser.exe",
        [
            str(Path.home() / r"AppData\Local\Yandex\YandexBrowser\Application\browser.exe"),
            r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
            r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
        ],
    ),
}

CHROMIUM_FAMILY = {"Google Chrome", "Microsoft Edge", "Brave", "Opera", "Vivaldi", "Яндекс.Браузер"}

HOME_URL = {
    "chromium": "chrome://newtab/",
    "firefox": "about:home",
}


# Предупреждение о статусе модуля (beta)
BETA_WARNING_TEXT = (
    "⚠️ МОДУЛЬ «Управление браузером» — в разработке (beta)\n\n"
    "• На разных системах возможны нестабильности: зависания, пустые окна, "
    "ошибки Selenium/CDP, странное поведение вкладок.\n"
    "• Если что‑то «поехало», закрой окно браузера вручную или нажми «⛔ Закрыть браузер».\n"
    "• Лучше всего работают Windows 10/11 и актуальные Chrome/Edge/Firefox. "
    "Экзотические сборки Chromium могут вести себя непредсказуемо.\n"
    "• Это нормально, что иногда команды доходят с задержкой — подождите пару секунд.\n\n"
    "Если словил баг — это не ты, это мы. Спасибо за терпение!"
)
# --- Утилиты ---

def _is_windows() -> bool:
    return os.name == "nt"

def _load_cfg() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"selected_browser": None, "search_engine": DEFAULT_SEARCH}

def _save_cfg(cfg: dict):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _sanitize_url(text: str) -> str:
    text = text.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", text):
        return text
    if re.match(r"^[\w\-\.]+\.[a-zA-Z]{2,}(/.*)?$", text):
        return "http://" + text
    return text

def _which_exists(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None

def _find_in_program_files(exe_name: str) -> Optional[str]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in [p for p in roots if p]:
        for base, _, files in os.walk(root):
            if exe_name in files:
                return os.path.join(base, exe_name)
    return None

def _find_by_registry() -> Dict[str, str]:
    result: Dict[str, str] = {}
    try:
        import winreg
    except Exception:
        return result

    def read_clients(root):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Clients\StartMenuInternet") as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                        with winreg.OpenKey(key, rf"{sub}\shell\open\command") as ckey:
                            cmd, _ = winreg.QueryValueEx(ckey, None)
                            m = re.search(r'"([^"]+\.exe)"', cmd, re.IGNORECASE)
                            path = m.group(1) if m else None
                            if path and os.path.exists(path):
                                exe = os.path.basename(path).lower()
                                name = next((n for n,(en,_) in KNOWN_BROWSERS.items() if en == exe), None)
                                result[name or os.path.basename(path)] = path
                    except OSError:
                        break
        except OSError:
            pass

    try:
        import winreg
        read_clients(winreg.HKEY_LOCAL_MACHINE)
        read_clients(winreg.HKEY_CURRENT_USER)
    except Exception:
        pass
    return {k: v for k, v in result.items() if v}

def detect_browsers() -> Dict[str, str]:
    found: Dict[str, str] = {}

    found.update({k: v for k, v in _find_by_registry().items() if v})

    for name, (exe_name, paths) in KNOWN_BROWSERS.items():
        if name in found and os.path.exists(found[name]):
            continue
        p = _which_exists(paths)
        if p:
            found[name] = p

    for name, (exe_name, paths) in KNOWN_BROWSERS.items():
        if name in found and os.path.exists(found[name]):
            continue
        p = _find_in_program_files(exe_name)
        if p:
            found[name] = p

    return found

def _process_running(exe_name: str) -> Optional[int]:
    exe_name = exe_name.lower()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info.get("name") or "").lower() == exe_name:
                return p.info["pid"]
        except Exception:
            continue
    return None

def _free_port(start=9222, end=9333) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Не удалось найти свободный порт для remote debugging.")

# --- Бэкенды управления ---

@dataclass
class BrowserChoice:
    name: str
    exe_path: str
    exe_name: str

class SeleniumBackend:
    """Управление через Selenium (Edge/Chrome/Firefox)."""

    def __init__(self, choice: BrowserChoice):
        self.choice = choice
        self.driver = None
        self.family = "firefox" if "firefox" in choice.exe_name.lower() else "chromium"

    def start(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.firefox.options import Options as FirefoxOptions

        name = self.choice.name
        exe = self.choice.exe_path

        if name == "Microsoft Edge" or self.choice.exe_name.lower() == "msedge.exe":
            opts = EdgeOptions()
            user_dir = str(Path(__file__).with_name("profiles") / "edge")
            os.makedirs(user_dir, exist_ok=True)
            opts.add_argument(f"--user-data-dir={user_dir}")
            self.driver = webdriver.Edge(options=opts)
        elif name == "Mozilla Firefox" or self.choice.exe_name.lower() == "firefox.exe":
            opts = FirefoxOptions()
            profile_dir = Path(__file__).with_name("profiles") / "firefox"
            os.makedirs(profile_dir, exist_ok=True)
            self.driver = webdriver.Firefox(options=opts)
            self.family = "firefox"
        else:
            # Chromium-ветка (Chrome/Brave/Vivaldi/Opera/Yandex — пытаемся через ChromeDriver)
            opts = ChromeOptions()
            opts.binary_location = exe
            user_dir = str(Path(__file__).with_name("profiles") / "chromium")
            os.makedirs(user_dir, exist_ok=True)
            opts.add_argument(f"--user-data-dir={user_dir}")
            self.driver = webdriver.Chrome(options=opts)
            self.family = "chromium"

        self.driver.set_page_load_timeout(30)

    def ensure(self):
        if self.driver is None:
            self.start()
        return True

    def open_url(self, url: str) -> bool:
        self.ensure()
        self.driver.get(url)
        return True

    def search(self, engine_url: str, query: str) -> bool:
        url = engine_url.format(query=re.sub(r"\s+", "+", query.strip()))
        return self.open_url(url)

    def back(self) -> bool:
        self.ensure()
        self.driver.back()
        return True

    def forward(self) -> bool:
        self.ensure()
        self.driver.forward()
        return True

    def reload(self) -> bool:
        self.ensure()
        self.driver.refresh()
        return True

    def home(self) -> bool:
        self.ensure()
        target = HOME_URL["firefox"] if self.family == "firefox" else HOME_URL["chromium"]
        self.driver.get(target)
        return True

    def tab_new(self) -> bool:
        self.ensure()
        self.driver.execute_script("window.open('');")
        self.driver.switch_to.window(self.driver.window_handles[-1])
        return True

    def tab_close(self) -> bool:
        self.ensure()
        current = self.driver.current_window_handle
        handles = self.driver.window_handles
        self.driver.close()
        rest = [h for h in handles if h != current]
        if rest:
            self.driver.switch_to.window(rest[-1])
        return True

    def tab_next(self) -> bool:
        self.ensure()
        hs = self.driver.window_handles
        idx = hs.index(self.driver.current_window_handle)
        self.driver.switch_to.window(hs[(idx + 1) % len(hs)])
        return True

    def tab_prev(self) -> bool:
        self.ensure()
        hs = self.driver.window_handles
        idx = hs.index(self.driver.current_window_handle)
        self.driver.switch_to.window(hs[(idx - 1) % len(hs)])
        return True

    def quit(self) -> bool:
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
        except Exception:
            pass
        return True

class CDPBackend:
    """Минимальный DevTools (Chromium) для fallback: навигация, вкладки, перезагрузка, закрытие."""
    def __init__(self, choice: BrowserChoice):
        self.choice = choice
        self.proc = None
        self.port = None
        self.current_target_id = None
        self.ws = None

    def _http_get_json(self, path: str):
        import requests
        url = f"http://127.0.0.1:{self.port}{path}"
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json()

    def _connect_ws(self, ws_url: str):
        from websocket import create_connection
        self.ws = create_connection(ws_url, timeout=5)

    def _send(self, method: str, params: dict = None, sessionId: str = None):
        import json as _json
        if params is None:
            params = {}
        self._msg_id += 1
        payload = {"id": self._msg_id, "method": method, "params": params}
        if sessionId:
            payload["sessionId"] = sessionId
        self.ws.send(_json.dumps(payload))
        while True:
            resp = _json.loads(self.ws.recv())
            if "id" in resp and resp["id"] == self._msg_id:
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                return resp.get("result", {})

    def _attach_to(self, target_id: str) -> str:
        res = self._send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        return res["sessionId"]

    def _list_pages(self) -> List[dict]:
        return [t for t in self._http_get_json("/json/list") if t.get("type") == "page"]

    def start(self):
        # Choose a persistent profile dir per-browser to avoid single-instance attach issues
        requested_port = _free_port()
        self.port = requested_port
        user_dir = Path(__file__).with_name("profiles") / f"cdp_{Path(self.choice.exe_path).stem.lower()}"
        os.makedirs(user_dir, exist_ok=True)

        # Ask the browser to expose DevTools on a predictable port and collect hints about the final port
        args = [
            self.choice.exe_path,
            f"--remote-debugging-port={requested_port}",
            f"--user-data-dir={str(user_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--new-window",
        ]
        creationflags = 0x08000000 if _is_windows() else 0
        self.proc = subprocess.Popen(args, creationflags=creationflags)

        base_candidates = [
            user_dir / "DevToolsActivePort",
            user_dir / "Default" / "DevToolsActivePort",
        ]
        start_wait = time.time()
        detected_port = None
        while time.time() - start_wait < 60:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError("Браузер завершился до инициализации DevTools.")
            candidates = set(base_candidates)
            candidates.update(user_dir.glob("**/DevToolsActivePort"))
            for candidate in candidates:
                if candidate.exists():
                    try:
                        lines = candidate.read_text(encoding="utf-8").strip().splitlines()
                    except Exception:
                        continue
                    if lines and lines[0].isdigit():
                        detected_port = int(lines[0])
                        break
            if detected_port:
                self.port = detected_port
                break
            time.sleep(0.1)

        import requests
        ws_url = None
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError("Браузер завершился до инициализации DevTools.")
            try:
                ver = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=1).json()
                ws_url = ver.get("webSocketDebuggerUrl")
                if ws_url:
                    break
            except requests.exceptions.RequestException:
                time.sleep(0.2)
                continue

        if not ws_url:
            # last resort: try to query typical localhost endpoint on a few ports
            for port in range(9222, 9334):
                if port == self.port:
                    continue
                try:
                    ver = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=0.5).json()
                    candidate_ws = ver.get("webSocketDebuggerUrl")
                    if candidate_ws:
                        self.port = port
                        ws_url = candidate_ws
                        break
                except requests.exceptions.RequestException:
                    continue

        if not ws_url:
            raise RuntimeError(f"Не удалось подключиться к DevTools WebSocket (порт: {self.port}).")

        self._connect_ws(ws_url)
        self._msg_id = 0

        # Ensure at least one page target exists
        pages = self._list_pages()
        if pages:
            self.current_target_id = pages[0]["id"]
        else:
            res = self._send("Target.createTarget", {"url": "about:blank"})
            self.current_target_id = res["targetId"]


    def ensure(self):
        if self.ws is None or self.current_target_id is None:
            self.start()
        return True

    def _with_session(self):
        self.ensure()
        session_id = self._attach_to(self.current_target_id)
        self._send("Page.enable", {}, sessionId=session_id)
        self._send("Runtime.enable", {}, sessionId=session_id)
        return session_id

    def open_url(self, url: str) -> bool:
        session = self._with_session()
        self._send("Page.navigate", {"url": url}, sessionId=session)
        return True

    def search(self, engine_url: str, query: str) -> bool:
        url = engine_url.format(query=re.sub(r"\s+", "+", query.strip()))
        return self.open_url(url)

    def reload(self) -> bool:
        session = self._with_session()
        self._send("Page.reload", {"ignoreCache": False}, sessionId=session)
        return True

    def back(self) -> bool:
        session = self._with_session()
        hist = self._send("Page.getNavigationHistory", {}, sessionId=session)
        idx = hist.get("currentIndex", 0)
        entries = hist.get("entries", [])
        if idx > 0:
            entry_id = entries[idx - 1]["id"]
            self._send("Page.navigateToHistoryEntry", {"entryId": entry_id}, sessionId=session)
            return True
        return False

    def forward(self) -> bool:
        session = self._with_session()
        hist = self._send("Page.getNavigationHistory", {}, sessionId=session)
        idx = hist.get("currentIndex", 0)
        entries = hist.get("entries", [])
        if idx < len(entries) - 1:
            entry_id = entries[idx + 1]["id"]
            self._send("Page.navigateToHistoryEntry", {"entryId": entry_id}, sessionId=session)
            return True
        return False

    def home(self) -> bool:
        return self.open_url(HOME_URL["chromium"])

    def tab_new(self) -> bool:
        self.ensure()
        res = self._send("Target.createTarget", {"url": "about:blank"})
        self.current_target_id = res["targetId"]
        return True

    def tab_close(self) -> bool:
        self.ensure()
        self._send("Target.closeTarget", {"targetId": self.current_target_id})
        pages = self._list_pages()
        if pages:
            self.current_target_id = pages[-1]["id"]
        else:
            self.current_target_id = None
        return True

    def _all_page_ids(self) -> List[str]:
        return [p["id"] for p in self._list_pages()]

    def tab_next(self) -> bool:
        self.ensure()
        ids = self._all_page_ids()
        if not ids:
            return False
        if self.current_target_id not in ids:
            self.current_target_id = ids[0]
            return True
        idx = ids.index(self.current_target_id)
        self.current_target_id = ids[(idx + 1) % len(ids)]
        return True

    def tab_prev(self) -> bool:
        self.ensure()
        ids = self._all_page_ids()
        if not ids:
            return False
        if self.current_target_id not in ids:
            self.current_target_id = ids[0]
            return True
        idx = ids.index(self.current_target_id)
        self.current_target_id = ids[(idx - 1) % len(ids)]
        return True

    def quit(self) -> bool:
        try:
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    self.proc.kill()
            self.proc = None
            self.current_target_id = None
        except Exception:
            pass
        return True

class UniversalController:
    """Выбирает лучший бэкенд (Selenium, иначе CDP для Chromium)."""
    def __init__(self):
        self.cfg = _load_cfg()
        self.found = detect_browsers()
        self.backend = None  # type: Optional[object]
        self.lock = asyncio.Lock()

    # --- Конфиг ---
    def list_browsers(self) -> Dict[str, str]:
        self.found = detect_browsers()
        return self.found

    def get_selected(self) -> Optional[BrowserChoice]:
        name = self.cfg.get("selected_browser")
        if not name:
            return None
        path = self.found.get(name)
        if not path:
            self.found = detect_browsers()
            path = self.found.get(name)
            if not path:
                return None
        return BrowserChoice(name=name, exe_path=path, exe_name=os.path.basename(path))

    def set_selected(self, name: str) -> bool:
        if name not in self.found:
            return False
        self.cfg["selected_browser"] = name
        _save_cfg(self.cfg)
        try:
            if self.backend:
                self.backend.quit()
        except Exception:
            pass
        self.backend = None
        return True

    def get_search_engine(self) -> str:
        se = self.cfg.get("search_engine") or DEFAULT_SEARCH
        if se not in SEARCH_ENGINES:
            se = DEFAULT_SEARCH
        return se

    def set_search_engine(self, name: str) -> bool:
        if name not in SEARCH_ENGINES:
            return False
        self.cfg["search_engine"] = name
        _save_cfg(self.cfg)
        return True

    # --- Бэкенд выбор/инициализация ---
    def _ensure_backend(self, choice: BrowserChoice):
        if self.backend:
            return
        try:
            self.backend = SeleniumBackend(choice)
            self.backend.ensure()
            return
        except Exception:
            self.backend = None

        if choice.name in CHROMIUM_FAMILY:
            self.backend = CDPBackend(choice)
            self.backend.ensure()
        else:
            raise RuntimeError("Для выбранного браузера недоступен Selenium, а CDP не поддерживается.")

    # --- Операции (с таймаутом) ---
    async def _run(self, func, *args, timeout: float = 45.0) -> bool:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(None, func, *args), timeout=timeout)

    async def open_url(self, choice: BrowserChoice, url: str) -> bool:
        self._ensure_backend(choice)
        url = _sanitize_url(url)
        return await self._run(self.backend.open_url, url)

    async def search(self, choice: BrowserChoice, query: str, engine: Optional[str] = None) -> bool:
        self._ensure_backend(choice)
        engine = engine or self.get_search_engine()
        template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES[DEFAULT_SEARCH])
        return await self._run(self.backend.search, template, query)

    async def nav_back(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.back)

    async def nav_forward(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.forward)

    async def nav_reload(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.reload)

    async def nav_home(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.home)

    async def tab_new(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.tab_new)

    async def tab_close(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.tab_close)

    async def tab_next(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.tab_next)

    async def tab_prev(self, choice: BrowserChoice) -> bool:
        self._ensure_backend(choice)
        return await self._run(self.backend.tab_prev)

    async def quit(self, choice: BrowserChoice) -> bool:
        if self.backend:
            try:
                return await self._run(self.backend.quit)
            finally:
                self.backend = None
        return True

# --- Telegram UI ---

USER_STATE: Dict[int, Dict[str, str]] = {}
CTRL = UniversalController()
BUSY = asyncio.Lock()
CANCEL_INPUT_TEXT = "Отменить ввод адреса"

def _main_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔗 Открыть ссылку", "🔎 Поиск")
    kb.row("⬅️ Назад", "➡️ Вперёд", "🔄 Обновить")
    kb.row("🆕 Новая вкладка", "❌ Закрыть вкладку")
    kb.row("◀️ Пред. вкладка", "▶️ След. вкладка")
    kb.row("🏠 Домой", "🧭 Выбор браузера")
    kb.row("🛠 Установить браузеры")
    kb.row("⚙️ Поисковик", "⛔ Закрыть браузер")
    kb.row(BACK_TO_UTILITIES_TEXT)
    return kb

def _cancel_input_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(CANCEL_INPUT_TEXT)
    return kb

def _browsers_inline(found: Dict[str, str]) -> types.InlineKeyboardMarkup:
    # Оставляем для обратной совместимости (может использоваться где-то ещё)
    kb = types.InlineKeyboardMarkup()
    row: List[types.InlineKeyboardButton] = []
    for i, name in enumerate(sorted(found.keys())):
        btn = types.InlineKeyboardButton(text=name, callback_data=f"bsel::{name}")
        row.append(btn)
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    return kb

def _browsers_reply(found: Dict[str, str]) -> types.ReplyKeyboardMarkup:
    """
    Клавиатура выбора браузера НИЖЕ поля ввода:
    - кнопки вида 'Выбрать <Название>'
    - внизу отдельная кнопка '↩️ Назад в меню модуля'
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    names = sorted(found.keys())
    # Делаем по две кнопки в ряд
    row: List[str] = []
    for name in names:
        row.append(f"Выбрать {name}")
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    kb.row("↩️ Назад в меню модуля")
    return kb

def _search_engines_inline() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    row: List[types.InlineKeyboardButton] = []
    for i, name in enumerate(SEARCH_ENGINES.keys()):
        btn = types.InlineKeyboardButton(text=name, callback_data=f"se::{name}")
        row.append(btn)
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    return kb

def _get_user_id(message_or_call) -> int:
    if isinstance(message_or_call, types.Message):
        return message_or_call.from_user.id
    return message_or_call.from_user.id

async def _safe_reply(message: types.Message, text: str):
    try:
        await message.answer(text)
    except Exception:
        pass

def error_reporter(handler):
    """Декоратор: ловим исключения и шлём их в Telegram.
    Плюс аккуратно пробрасываем только те kwargs, которые поддерживает целевой хендлер.
    """
    sig = inspect.signature(handler)

    @wraps(handler)
    async def wrapper(message: types.Message, *args, **kwargs):
        bound_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        try:
            return await handler(message, *args, **bound_kwargs)
        except asyncio.TimeoutError:
            await _safe_reply(message, "⏳ Команда зависла и была прервана по таймауту. Попробуй ещё раз.")
        except Exception as e:
            await _safe_reply(message, f"❌ Ошибка: {type(e).__name__}: {e}")
    return wrapper


class TelegramProgress:
    """Простой индикатор прогресса для длительных операций с браузером."""

    def __init__(self, message: types.Message, start_text: str, *, interval: float = 2.0):
        self.message = message
        self.start_text = start_text
        self.interval = interval
        self._progress_message: Optional[types.Message] = None
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._final_text: Optional[str] = None

    async def __aenter__(self):
        try:
            self._progress_message = await self.message.answer(self.start_text)
        except Exception:
            self._progress_message = None
            return self
        self._task = asyncio.create_task(self._animate())
        return self

    async def _animate(self):
        spinner = itertools.cycle(("⏳", "⌛", "🕒", "🕤"))
        while not self._stop.is_set():
            await asyncio.sleep(self.interval)
            if self._stop.is_set() or not self._progress_message:
                break
            frame = next(spinner)
            try:
                await self.message.bot.edit_message_text(
                    f"{frame} {self.start_text}",
                    chat_id=self._progress_message.chat.id,
                    message_id=self._progress_message.message_id,
                )
            except Exception:
                continue

    def done(self, final_text: str):
        self._final_text = final_text

    async def __aexit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if not self._progress_message:
            return False
        final_text = self._final_text
        if exc_type and final_text is None:
            final_text = "⚠️ Операция завершилась с ошибкой."
        elif final_text is None:
            final_text = "✅ Готово."
        try:
            await self.message.bot.edit_message_text(
                final_text,
                chat_id=self._progress_message.chat.id,
                message_id=self._progress_message.message_id,
            )
        except Exception:
            pass
        return False

# --- Helpers для «режима модуля», чтобы не ловить чужие сообщения ---

def _set_mode(uid: int, mode: Optional[str]):
    st = USER_STATE.get(uid, {})
    if mode is None:
        st.pop("mode", None)
    else:
        st["mode"] = mode
    USER_STATE[uid] = st if st else {}

def _in_mode(uid: int, mode: str) -> bool:
    return USER_STATE.get(uid, {}).get("mode") == mode

def _in_browser_mode(message: types.Message) -> bool:
    return _in_mode(_get_user_id(message), "browser")

def _cmd(expected_text: str):
    expected = expected_text.strip().lower()
    return lambda m: _in_browser_mode(m) and m.text and m.text.strip().lower() == expected

def _awaiting_input_filter(message: types.Message) -> bool:
    st = USER_STATE.get(_get_user_id(message), {})
    return st.get("mode") == "browser" and st.get("await") in {"open_url", "search"}
# === Installers: menu + download + silent install ============================
import ctypes
import aiohttp

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

async def resolve_installer(vendor: str) -> Tuple[str, str]:
    """
    Возвращает (filename, url) для загрузки установщика из официальных источников.
    """
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
        # Ленд с оффлайн-сборкой. Оттуда придёт редирект на актуальный exe.
        url = "https://www.opera.com/download"
        return ("Opera_Offline_64.exe", url)
    if vendor in ("yandex", "яндекс", "yandexbrowser", "яндекс браузер"):
        url = "https://browser.yandex.com/download/?full=1"
        return ("Yandex_Browser_Offline.exe", url)
    raise ValueError(f"Неизвестный браузер: {vendor}")

async def download_with_progress(url: str, dest: Path, chunk: int = 1024 * 256, timeout: int = 90) -> None:
    """Скачиваем файл с редиректами, сохраняем во временный .part и атомарно переименовываем."""
    async with aiohttp.ClientSession(raise_for_status=True, timeout=aiohttp.ClientTimeout(total=timeout*5)) as sess:
        async with sess.get(url, allow_redirects=True) as resp:
            tmp = dest.with_suffix(".part")
            with tmp.open("wb") as f:
                async for chunk_bytes in resp.content.iter_chunked(chunk):
                    f.write(chunk_bytes)
            tmp.replace(dest)

def run_silent_install(installer: Path, vendor: str) -> subprocess.Popen:
    """Запускаем тихую установку. Для MSI — через msiexec, для EXE — вендор-специфичные ключи."""
    vendor = vendor.lower()
    if installer.suffix.lower() == ".msi":
        cmd = ["msiexec", "/i", str(installer), "/qn", "/norestart"]
    else:
        if vendor == "firefox":
            cmd = [str(installer), "/S"]
        elif vendor == "chrome":
            cmd = [str(installer), "/silent", "/install"]
        elif vendor == "opera":
            cmd = [str(installer), "/silent"]
        elif vendor in ("yandex", "яндекс", "yandexbrowser", "яндекс браузер"):
            cmd = [str(installer), "/silent", "/install"]
        else:
            cmd = [str(installer)]
    creationflags = 0x08000000 if _is_windows() else 0
    try:
        return subprocess.Popen(cmd, creationflags=creationflags)
    except Exception:
        return subprocess.Popen([str(installer)], creationflags=creationflags)

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton



def register_handlers(dp: Dispatcher):

    # Вход в режим
    @dp.message_handler(lambda m: m.text and m.text.strip().lower() == "управление браузером")
    @error_reporter
    async def handle_browser_control(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        _set_mode(uid, "browser")
        USER_STATE.get(uid, {}).pop("await", None)

        # Показать одноразовое предупреждение о beta-статусе
        st = USER_STATE.get(uid, {})
        if not st.get("beta_warned"):
            try:
                await message.answer(BETA_WARNING_TEXT)
            except Exception:
                pass
            st["beta_warned"] = True
            USER_STATE[uid] = st


        if not _is_windows():
            await message.answer("⚠️ Модуль рассчитан на Windows. На этой ОС управление ограничено.")
        if not CTRL.list_browsers():
            await message.answer("Не нашёл ни одного браузера. Поставь Edge/Chrome/Firefox и повтори.")
            return
        sel = CTRL.get_selected()
        se = CTRL.get_search_engine()
        text = [
            "🌐 Режим: Управление браузером",
            f"— Браузер по умолчанию: {sel.name if sel else 'не выбран'}",
            f"— Поисковик: {se}",
            "Выбери действие на клавиатуре ниже 👇",
        ]
        await message.answer("\n".join(text), reply_markup=_main_keyboard())

    # Выбор браузера — ПОКАЗЫВАЕМ REPLY-КЛАВИАТУРУ НИЖЕ ПОЛЯ ВВОДА
    @dp.message_handler(_cmd("🧭 выбор браузера"))
    @error_reporter
    async def choose_browser(message: types.Message, state: FSMContext = None):
        found = CTRL.list_browsers()
        if not found:
            await message.answer("Не нашёл установленные браузеры :(", reply_markup=_main_keyboard())
            return
        kb = _browsers_reply(found)
        await message.answer("Выбери браузер по умолчанию (кнопки ниже поля ввода):", reply_markup=kb)

    # Обработка выбора из REPLY-клавиатуры "Выбрать <Имя>"
    @dp.message_handler(lambda m: _in_browser_mode(m) and m.text and m.text.strip().lower().startswith("выбрать "))
    @error_reporter
    async def on_browser_selected_reply(message: types.Message, state: FSMContext = None):
        found = CTRL.list_browsers()
        # Вырезаем имя после "Выбрать "
        name = message.text.strip()[8:].strip()
        if name not in found:
            await message.answer("Такого браузера не найдено. Попробуй снова.", reply_markup=_browsers_reply(found))
            return
        ok = CTRL.set_selected(name)
        if ok:
            await message.answer(f"✅ Браузер по умолчанию: {name}", reply_markup=_main_keyboard())
        else:
            await message.answer("❌ Не удалось выбрать браузер.", reply_markup=_browsers_reply(found))

    # Инлайн-хендлер для обратной совместимости (если вдруг прилетят старые callback-и)
    @dp.callback_query_handler(lambda c: c.data and c.data.startswith("bsel::"))
    async def on_browser_selected(call: types.CallbackQuery, state: FSMContext = None):
        try:
            name = call.data.split("::", 1)[1]
            ok = CTRL.set_selected(name)
            if ok:
                await call.message.edit_text(f"✅ Браузер по умолчанию: {name}")
            else:
                await call.message.edit_text("Не удалось выбрать браузер.")
            await call.message.answer("Готово. Что дальше?", reply_markup=_main_keyboard())
            await call.answer()
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}", reply_markup=_main_keyboard())
            try:
                await call.answer()
            except Exception:
                pass

    # Выбор поисковика (инлайн оставляем)
    @dp.message_handler(_cmd("⚙️ поисковик"))
    @error_reporter
    async def choose_search_engine(message: types.Message, state: FSMContext = None):
        await message.answer("Выбери поисковик по умолчанию:", reply_markup=_search_engines_inline())

    @dp.callback_query_handler(lambda c: c.data and c.data.startswith("se::"))
    async def on_search_selected(call: types.CallbackQuery, state: FSMContext = None):
        try:
            name = call.data.split("::", 1)[1]
            ok = CTRL.set_search_engine(name)
            if ok:
                await call.message.edit_text(f"✅ Поисковик по умолчанию: {name}")
            else:
                await call.message.edit_text("Не удалось установить поисковик.")
            await call.message.answer("Что делаем дальше?", reply_markup=_main_keyboard())
            await call.answer()
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}", reply_markup=_main_keyboard())
            try:
                await call.answer()
            except Exception:
                pass

    # Открыть ссылку
    @dp.message_handler(_cmd("🔗 открыть ссылку"))
    @error_reporter
    async def want_open_link(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        st = USER_STATE.get(uid, {})
        st["await"] = "open_url"
        USER_STATE[uid] = st
        await message.answer("Пришли ссылку (или домен без http:// — я сам разберусь):", reply_markup=_cancel_input_keyboard())

    @dp.message_handler(lambda m: _in_browser_mode(m) and m.text and m.text.strip().lower() == CANCEL_INPUT_TEXT.lower() and USER_STATE.get(_get_user_id(m), {}).get("await") == "open_url")
    @error_reporter
    async def cancel_open_link(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        st = USER_STATE.get(uid, {})
        st.pop("await", None)
        USER_STATE[uid] = st
        await message.answer("Ввод адреса отменён.", reply_markup=_main_keyboard())

    # Поиск
    @dp.message_handler(_cmd("🔎 поиск"))
    @error_reporter
    async def want_search(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        st = USER_STATE.get(uid, {})
        st["await"] = "search"
        USER_STATE[uid] = st
        await message.answer("Что ищем? Напиши запрос:")

    # Навигация — Назад
    @dp.message_handler(_cmd("⬅️ назад"))
    @error_reporter
    async def nav_back(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.nav_back(sel)
        await message.answer("◀️ Назад" if ok else "Не удалось отправить команду Назад.")

    # Навигация — Вперёд
    @dp.message_handler(_cmd("➡️ вперёд"))
    @error_reporter
    async def nav_forward(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.nav_forward(sel)
        await message.answer("▶️ Вперёд" if ok else "Не удалось отправить команду Вперёд.")

    # Обновить
    @dp.message_handler(_cmd("🔄 обновить"))
    @error_reporter
    async def nav_reload(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.nav_reload(sel)
        await message.answer("♻️ Обновлено" if ok else "Не удалось обновить страницу.")

    # Домой
    @dp.message_handler(_cmd("🏠 домой"))
    @error_reporter
    async def nav_home(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.nav_home(sel)
        await message.answer("🏠 Домой" if ok else "Не удалось открыть домашнюю страницу.")

    # Новая вкладка
    @dp.message_handler(_cmd("🆕 новая вкладка"))
    @error_reporter
    async def tab_new(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.tab_new(sel)
        await message.answer("➕ Новая вкладка" if ok else "Не удалось создать вкладку.")

    # Закрыть вкладку
    @dp.message_handler(_cmd("❌ закрыть вкладку"))
    @error_reporter
    async def tab_close(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.tab_close(sel)
        await message.answer("🗑 Вкладка закрыта" if ok else "Не удалось закрыть вкладку.")

    # След./Пред. вкладка
    @dp.message_handler(_cmd("▶️ след. вкладка"))
    @error_reporter
    async def tab_next(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.tab_next(sel)
        await message.answer("▶️ Следующая вкладка" if ok else "Не удалось переключиться на следующую.")

    @dp.message_handler(_cmd("◀️ пред. вкладка"))
    @error_reporter
    async def tab_prev(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.tab_prev(sel)
        await message.answer("◀️ Предыдущая вкладка" if ok else "Не удалось переключиться на предыдущую.")

    # Закрыть браузер
    @dp.message_handler(_cmd("⛔ закрыть браузер"))
    @error_reporter
    async def quit_browser(message: types.Message, state: FSMContext = None):
        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».")
            return
        async with BUSY:
            ok = await CTRL.quit(sel)
        await message.answer("🛑 Браузер закрыт" if ok else "Не удалось закрыть браузер.")

    # ↩️ Назад в меню модуля — просто показать главную клавиатуру этого модуля
    @dp.message_handler(lambda m: _in_browser_mode(m) and m.text and m.text.strip().lower() in {"назад в меню модуля", "↩️ назад в меню модуля"})
    @error_reporter
    async def back_to_module_menu(message: types.Message, state: FSMContext = None):
        await message.answer("Возвращаю в меню модуля управления браузером.", reply_markup=_main_keyboard())

    # ⬅️ Назад в утилиты — выход из режима модуля и возврат в меню утилит (keymenu)
    @dp.message_handler(lambda m: _in_browser_mode(m) and m.text and m.text.strip().lower() in {BACK_TO_UTILITIES_TEXT.lower(), "назад в утилиты"})
    @error_reporter
    async def back_to_utilities(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        st = USER_STATE.get(uid, {})
        st.pop("await", None)
        st.pop("mode", None)
        USER_STATE[uid] = st if st else {}
        await message.answer("Возвращаю в меню утилит.", reply_markup=_utilities_keyboard())

    # Обработка текста ТОЛЬКО когда реально ждём URL или поисковый запрос
    async def handle_text(message: types.Message, state: FSMContext = None):
        uid = _get_user_id(message)
        st = USER_STATE.get(uid, {})
        awaiting = st.get("await")
        if st.get("mode") != "browser" or awaiting not in {"open_url", "search"}:
            return

        sel = CTRL.get_selected()
        if not sel:
            await message.answer("Сначала выбери браузер через «🧭 Выбор браузера».", reply_markup=_main_keyboard())
            return

        if awaiting == "open_url":
            text = message.text.strip()
            async with TelegramProgress(message, "⏳ Открываю ссылку, подождите...") as progress:
                async with BUSY:
                    ok = await CTRL.open_url(sel, text)
                if ok:
                    progress.done(f"✅ Ссылка отправлена в браузер: {text}")
                else:
                    progress.done("❌ Не удалось открыть ссылку.")
        elif awaiting == "search":
            query = message.text.strip()
            async with TelegramProgress(message, "⏳ Выполняю поиск, подождите...") as progress:
                async with BUSY:
                    ok = await CTRL.search(sel, query, None)
                if ok:
                    progress.done(f"🔎 Поиск по запросу: “{query}”")
                else:
                    progress.done("❌ Не удалось выполнить поиск.")

        st.pop("await", None)
        USER_STATE[uid] = st
        await message.answer("Готово. Что дальше?", reply_markup=_main_keyboard())

    dp.register_message_handler(handle_text, _awaiting_input_filter)


    # Регистрируем хендлеры установки (в отдельном модуле)
    browser_installer.register_install_handlers(dp, CTRL, _in_browser_mode, _cmd, _is_windows)
