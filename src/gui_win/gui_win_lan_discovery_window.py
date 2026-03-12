# -*- coding: utf-8 -*-
"""
GUI: Локальное автообнаружение AutoCraft-Bot в LAN (окно PyQt5).

Что умеет:
- Режимы: server / client / multi
- Автообнаружение узлов AutoCraft-Bot в локальной сети по UDP (multicast + optional broadcast)
- Таблица найденных узлов и открытие веб-панели выбранного узла
- Настройки (порт, multicast group, интервалы, таймауты, реклама веб-панели)
- Автозапуск сервиса обнаружения по настройке (best effort при импорте модуля)

Модуль написан в стиле gui_win_plugins_manager_window.py: ленивый импорт PyQt5,
отдельный backend, безопасные fallback-ы, внимание к доступности для скринридеров.
"""

from __future__ import annotations

import configparser
import importlib
import ipaddress
import json
import os
import platform
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

import weakref


# -------------------- import aliasing --------------------
#
# Этот модуль в проекте может импортироваться разными именами
# (package/relative/top-level). Алиасы не дают создать две копии module state.
try:
    _this_mod = sys.modules.get(__name__)
    if _this_mod is not None:
        for _alias in (
            "gui_win.gui_win_lan_discovery_window",
            "moduls.gui_win.gui_win_lan_discovery_window",
            "gui_win_lan_discovery_window",
        ):
            sys.modules.setdefault(_alias, _this_mod)
except Exception:
    # Импорт никогда не должен ломаться из-за алиасов.
    pass


# -------------------- метаданные для functions_window.py --------------------

FUNCTIONS_BUTTON_TEXT = "LAN автообнаружение"
FUNCTIONS_ENTRYPOINT = "open_lan_discovery_window"
FUNCTIONS_STAGE = "startrun"
FUNCTIONS_ORDER = 46
FUNCTIONS_ICON = "SP_DriveNetIcon"
FUNCTIONS_TOOLTIP = "Поиск других экземпляров AutoCraft-Bot в локальной сети"
FUNCTIONS_ACCESSIBLE_NAME = "Кнопка: LAN автообнаружение"
FUNCTIONS_ACCESSIBLE_DESCRIPTION = "Открывает окно поиска AutoCraft-Bot в локальной сети"

if TYPE_CHECKING:  # pragma: no cover
    from PyQt5.QtWidgets import QWidget


# -------------------- ленивый импорт PyQt5 --------------------

_PYQT_IMPORTED = False
_GUI_BUILT = False


def _get_pyqt() -> None:
    global _PYQT_IMPORTED
    if _PYQT_IMPORTED:
        return

    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QEvent, QTimer
    from PyQt5.QtGui import QFontDatabase, QTextCursor
    from PyQt5.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QHeaderView,
        QScrollBar,
    )

    g = globals()
    g.update({
        "Qt": Qt,
        "QThread": QThread,
        "pyqtSignal": pyqtSignal,
        "QEvent": QEvent,
        "QTimer": QTimer,
        "QFontDatabase": QFontDatabase,
        "QTextCursor": QTextCursor,
        "QApplication": QApplication,
        "QCheckBox": QCheckBox,
        "QComboBox": QComboBox,
        "QDialog": QDialog,
        "QDialogButtonBox": QDialogButtonBox,
        "QDoubleSpinBox": QDoubleSpinBox,
        "QFormLayout": QFormLayout,
        "QGroupBox": QGroupBox,
        "QHBoxLayout": QHBoxLayout,
        "QLabel": QLabel,
        "QLineEdit": QLineEdit,
        "QMessageBox": QMessageBox,
        "QPushButton": QPushButton,
        "QPlainTextEdit": QPlainTextEdit,
        "QSpinBox": QSpinBox,
        "QTableWidget": QTableWidget,
        "QTableWidgetItem": QTableWidgetItem,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
        "QHeaderView": QHeaderView,
        "QScrollBar": QScrollBar,
    })

    _PYQT_IMPORTED = True


# -------------------- протокол и утилиты --------------------

_PROTO_NAME = "autocraft_lan_discovery"
_PROTO_VERSION = 1
_DEFAULT_GROUP = "239.255.67.67"
_DEFAULT_PORT = 37555
_DEFAULT_SECTION = "lan_discovery"


def _safe_text(obj: object) -> str:
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "<н/д>"


def _now() -> float:
    return time.time()


def _fmt_dt(ts: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except Exception:
        return "--:--:--"


def _normalize_path(path: str) -> str:
    path = (path or "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path or "/"


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        num = float(value)
        if num < lo:
            return float(lo)
        if num > hi:
            return float(hi)
        return num
    except Exception:
        return float(default)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "да", "on", "y"}:
        return True
    if s in {"0", "false", "no", "нет", "off", "n"}:
        return False
    return default


def _is_valid_ipv4(ip: Any) -> bool:
    try:
        socket.inet_aton(str(ip or "").strip())
        return True
    except Exception:
        return False


def _prefix_to_netmask(prefix: Any) -> str:
    try:
        pref = int(prefix)
        if not (0 <= pref <= 32):
            return ""
        return str(ipaddress.IPv4Network(f"0.0.0.0/{pref}").netmask)
    except Exception:
        return ""


def _compute_directed_broadcast(ip: str, netmask: str) -> str:
    try:
        if not (_is_valid_ipv4(ip) and _is_valid_ipv4(netmask)):
            return ""
        net = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
        if net.prefixlen >= 31:
            return ""
        bcast = str(net.broadcast_address)
        if bcast == "255.255.255.255":
            return ""
        return bcast
    except Exception:
        return ""


def _build_local_iface_record(ip: str, netmask: str = "", name: str = "", broadcast: str = "") -> Optional[Dict[str, str]]:
    ip = str(ip or "").strip()
    if not _is_valid_ipv4(ip):
        return None
    if ip.startswith("127."):
        return None

    netmask = str(netmask or "").strip()
    if netmask and not _is_valid_ipv4(netmask):
        netmask = ""

    directed_broadcast = str(broadcast or "").strip()
    if directed_broadcast and not _is_valid_ipv4(directed_broadcast):
        directed_broadcast = ""
    if directed_broadcast in {ip, "0.0.0.0", "255.255.255.255"}:
        directed_broadcast = ""
    if not directed_broadcast and netmask:
        directed_broadcast = _compute_directed_broadcast(ip, netmask)

    return {
        "ip": ip,
        "netmask": netmask,
        "broadcast": directed_broadcast,
        "name": str(name or "").strip(),
    }


def _local_ipv4_candidates_basic() -> List[str]:
    ips: List[str] = []
    seen = set()

    def _add(ip: str) -> None:
        if not ip:
            return
        ip = ip.strip()
        if ip.startswith("127."):
            return
        if ip in seen:
            return
        seen.add(ip)
        ips.append(ip)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM):
            try:
                _add(info[4][0])
            except Exception:
                pass
    except Exception:
        pass

    # Трюк с UDP connect не отправляет пакет, но помогает узнать исходящий интерфейс.
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.0.1"):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 80))
            _add(s.getsockname()[0])
        except Exception:
            pass
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass

    return ips


def _local_ipv4_interfaces() -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen = set()

    def _add(ip: str, netmask: str = "", name: str = "", broadcast: str = "") -> None:
        rec = _build_local_iface_record(ip=ip, netmask=netmask, name=name, broadcast=broadcast)
        if not rec:
            return
        key = (rec.get("ip") or "", rec.get("netmask") or "", rec.get("broadcast") or "")
        if key in seen:
            return
        seen.add(key)
        results.append(rec)

    # Предпочитаем psutil: он даёт IP/netmask/broadcast без локализации вывода команд.
    try:
        import psutil  # type: ignore

        for ifname, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                try:
                    if getattr(addr, "family", None) != socket.AF_INET:
                        continue
                    _add(
                        ip=str(getattr(addr, "address", "") or ""),
                        netmask=str(getattr(addr, "netmask", "") or ""),
                        name=str(ifname or ""),
                        broadcast=str(getattr(addr, "broadcast", "") or ""),
                    )
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback для Windows: через PowerShell берём IP + PrefixLength и сами считаем broadcast.
    if not results and os.name == "nt":
        ps_cmd = (
            "try { "
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | "
            "Where-Object { $_.IPAddress -and $_.IPAddress -ne '127.0.0.1' -and $_.PrefixLength -ne $null } | "
            "Select-Object InterfaceAlias,IPAddress,PrefixLength | ConvertTo-Json -Compress "
            "} catch { '' }"
        )
        try:
            raw = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            data = raw.decode("utf-8", errors="replace").strip()
            if data:
                parsed = json.loads(data)
                rows = parsed if isinstance(parsed, list) else [parsed]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ip = str(row.get("IPAddress") or "").strip()
                    netmask = _prefix_to_netmask(row.get("PrefixLength"))
                    name = str(row.get("InterfaceAlias") or "").strip()
                    _add(ip=ip, netmask=netmask, name=name)
        except Exception:
            pass

    # Самый простой fallback: только список IP без netmask/broadcast.
    if not results:
        for ip in _local_ipv4_candidates_basic():
            _add(ip=ip)

    return results


def _local_ipv4_candidates() -> List[str]:
    return [item.get("ip") or "" for item in _local_ipv4_interfaces() if item.get("ip")]


def _directed_broadcast_targets(ifaces: List[Dict[str, str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for iface in ifaces or []:
        try:
            bcast = str((iface or {}).get("broadcast") or "").strip()
        except Exception:
            bcast = ""
        if not bcast or bcast == "255.255.255.255":
            continue
        if not _is_valid_ipv4(bcast):
            continue
        if bcast in seen:
            continue
        seen.add(bcast)
        out.append(bcast)
    return out


def _guess_default_panel_port(main_obj: Any, cfg_path: Path, default: int = 5212) -> int:
    # Пытаемся аккуратно взять порт веб-панели из __main__/config.ini, иначе fallback 5212.
    for attr in (
        "WEB_PANEL_PORT",
        "web_panel_port",
        "PANEL_PORT",
        "panel_port",
        "WAITRESS_PORT",
        "waitress_port",
    ):
        try:
            val = getattr(main_obj, attr)
            if val is None:
                continue
            p = int(val)
            if 1 <= p <= 65535:
                return p
        except Exception:
            continue

    if cfg_path.exists():
        cp = configparser.ConfigParser()
        try:
            cp.read(cfg_path, encoding="utf-8")
            for sec in ("web_panel", "panel", "dashboard", "webdashboard"):
                if not cp.has_section(sec):
                    continue
                for key in ("port", "panel_port", "waitress_port"):
                    if cp[sec].get(key):
                        try:
                            p = int(cp[sec].get(key))
                            if 1 <= p <= 65535:
                                return p
                        except Exception:
                            pass
        except Exception:
            pass

    return default


def _url_from_service(ip: str, service: Dict[str, Any]) -> str:
    scheme = str(service.get("scheme") or "http").strip() or "http"
    port = int(service.get("port") or 0)
    path = _normalize_path(str(service.get("path") or "/"))
    host = str(service.get("host") or "").strip()
    host_for_url = ip
    if host and host not in {"0.0.0.0", "127.0.0.1", "localhost"}:
        # Если прислали явный внешний адрес, можно использовать его, но sender IP надёжнее.
        host_for_url = host
    if port <= 0:
        return ""
    return f"{scheme}://{host_for_url}:{port}{path}"


def _normalize_panel_url_for_browser(url: str) -> str:
    """
    Нормализует URL для открытия в браузере.
    0.0.0.0 заменяем на 127.0.0.1 (как в gui.py).
    """
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").strip()
        if host != "0.0.0.0":
            return (url or "").strip()
        port = f":{parts.port}" if parts.port else ""
        auth = ""
        if parts.username:
            auth = parts.username
            if parts.password:
                auth += f":{parts.password}"
            auth += "@"
        netloc = f"{auth}127.0.0.1{port}"
        return parts._replace(netloc=netloc).geturl()
    except Exception:
        return (url or "").strip()


def _parse_panel_url_parts(url: str) -> Dict[str, Any]:
    """
    Разбор URL панели в поля рекламы LAN.
    host=0.0.0.0 / 127.0.0.1 / localhost не сохраняем в override, чтобы по сети
    использовался реальный IP отправителя пакета.
    """
    out: Dict[str, Any] = {}
    raw = (url or "").strip()
    if not raw:
        return out
    try:
        parts = urlsplit(raw)
        scheme = (parts.scheme or "http").strip().lower() or "http"
        if scheme not in {"http", "https"}:
            scheme = "http"
        host = (parts.hostname or "").strip()
        port = int(parts.port or 0)
        path = _normalize_path(parts.path or "/")
        if port <= 0:
            return out
        host_override = ""
        if host and host not in {"0.0.0.0", "127.0.0.1", "localhost"}:
            host_override = host
        out = {
            "panel_scheme": scheme,
            "panel_port": port,
            "panel_path": path,
            "panel_host_override": host_override,
        }
        return out
    except Exception:
        return {}


# -------------------- сетевой сервис --------------------


class LanDiscoveryService:
    """Фоновый UDP-сервис для обнаружения узлов AutoCraft-Bot в LAN."""

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log_cb = log_cb
        self._lock = threading.RLock()
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._status: Dict[str, Any] = {
            "running": False,
            "mode": "multi",
            "udp_port": _DEFAULT_PORT,
            "multicast_group": _DEFAULT_GROUP,
            "bound": False,
            "last_error": "",
            "started_at": 0.0,
            "local_ips": [],
            "local_ifaces": [],
            "directed_broadcasts": [],
            "multicast_joined": False,
            "multicast_joined_interfaces": [],
        }
        self._settings: Dict[str, Any] = {}
        self._stop_evt = threading.Event()
        self._rx_thread: Optional[threading.Thread] = None
        self._tick_thread: Optional[threading.Thread] = None
        self._rx_sock: Optional[socket.socket] = None
        self._tx_sock: Optional[socket.socket] = None
        self._node_id: str = ""

    # ---- public API ----
    def start(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self.stop(no_lock=True)
            self._settings = dict(settings)
            self._node_id = str(settings.get("node_id") or uuid.uuid4())
            self._settings["node_id"] = self._node_id
            self._stop_evt.clear()
            self._peers = {}

            udp_port = int(settings.get("udp_port") or _DEFAULT_PORT)
            group = str(settings.get("multicast_group") or _DEFAULT_GROUP)
            local_ifaces = _local_ipv4_interfaces()
            local_ips = [str(item.get("ip") or "").strip() for item in local_ifaces if str(item.get("ip") or "").strip()]
            directed_broadcasts = _directed_broadcast_targets(local_ifaces)

            self._status.update({
                "running": False,
                "mode": str(settings.get("mode") or "multi"),
                "udp_port": udp_port,
                "multicast_group": group,
                "bound": False,
                "last_error": "",
                "started_at": _now(),
                "local_ips": local_ips,
                "local_ifaces": local_ifaces,
                "directed_broadcasts": directed_broadcasts,
                "multicast_joined": False,
                "multicast_joined_interfaces": [],
            })

            self._tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                self._tx_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            except Exception:
                pass
            self._tx_sock.settimeout(1.0)

            self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            # На Windows включаем эксклюзивное владение портом, чтобы второй экземпляр
            # сервиса не мог привязаться к тому же UDP-порту параллельно.
            if os.name == "nt":
                try:
                    opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                    if opt is not None:
                        self._rx_sock.setsockopt(socket.SOL_SOCKET, opt, 1)
                except Exception:
                    pass
            else:
                try:
                    self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except Exception:
                    pass
            try:
                self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            self._rx_sock.settimeout(1.0)
            try:
                self._rx_sock.bind(("", udp_port))
                self._status["bound"] = True
            except Exception as e:
                # Частая причина: порт занят или запрещён брандмауэром/политиками.
                self._status["last_error"] = _safe_text(e)
                self._log(f"[LAN] Не удалось привязать UDP порт {udp_port}: {e}")
                try:
                    self._rx_sock.close()
                except Exception:
                    pass
                self._rx_sock = None
                try:
                    if self._tx_sock:
                        self._tx_sock.close()
                except Exception:
                    pass
                self._tx_sock = None
                self._status["running"] = False
                self._stop_evt.set()
                return

            if _to_bool(settings.get("multicast_enabled"), True):
                joined_ifaces: List[str] = []
                join_errors: List[str] = []

                # На Windows и на многосетевых машинах лучше явно подписываться на каждую IPv4-карту,
                # а не надеяться на интерфейс по умолчанию. Это особенно важно в сетях без DHCP,
                # где default route и multicast route могут указывать не туда.
                for lip in (self._status.get("local_ips") or []):
                    try:
                        mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(str(lip)))
                        self._rx_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                        joined_ifaces.append(str(lip))
                    except Exception as e:
                        join_errors.append(f"{lip}: {_safe_text(e)}")

                if not joined_ifaces:
                    try:
                        mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0"))
                        self._rx_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                        joined_ifaces.append("0.0.0.0")
                    except Exception as e:
                        join_errors.append(f"default: {_safe_text(e)}")

                if joined_ifaces:
                    self._status["multicast_joined"] = True
                    self._status["multicast_joined_interfaces"] = list(joined_ifaces)
                else:
                    self._log(f"[LAN] Multicast join не удался ({group}:{udp_port}). Продолжаю с broadcast.")
                    if join_errors:
                        self._log(f"[LAN] Причины multicast join: {'; '.join(join_errors[:6])}")

            self._rx_thread = threading.Thread(target=self._rx_loop, name="AutoCraftLAN-RX", daemon=True)
            self._tick_thread = threading.Thread(target=self._tick_loop, name="AutoCraftLAN-TICK", daemon=True)
            self._rx_thread.start()
            self._tick_thread.start()
            self._status["running"] = True
            iface_summary = ", ".join(
                f"{item.get('ip')}/{item.get('netmask') or '?'} -> {item.get('broadcast') or 'n/a'}"
                for item in (self._status.get("local_ifaces") or [])
            ) or "нет"
            self._log(
                f"[LAN] Сервис обнаружения запущен. Режим={self._status['mode']}, "
                f"порт={udp_port}, group={group}, local_ips={', '.join(self._status['local_ips']) or 'нет'}, "
                f"ifaces={iface_summary}"
            )

            # Мгновенный первый анонс/поиск, чтобы пользователь увидел результат сразу.
            try:
                self.send_discover_now()
                if self._status["mode"] in {"server", "multi"}:
                    self.send_hello_now()
            except Exception as e:
                self._log(f"[LAN] Стартовый пакет не отправлен: {e}")

    def stop(self, no_lock: bool = False) -> None:
        lock_ctx = self._lock if not no_lock else _NullContext()
        with lock_ctx:
            if self._status.get("running"):
                try:
                    self.send_bye_now()
                except Exception:
                    pass
            self._stop_evt.set()

            for sock in (self._rx_sock, self._tx_sock):
                try:
                    if sock:
                        sock.close()
                except Exception:
                    pass
            self._rx_sock = None
            self._tx_sock = None

            for th in (self._rx_thread, self._tick_thread):
                try:
                    if th and th.is_alive():
                        th.join(timeout=1.2)
                except Exception:
                    pass
            self._rx_thread = None
            self._tick_thread = None

            self._status["running"] = False
            self._status["bound"] = False
            self._status["multicast_joined"] = False
            self._status["multicast_joined_interfaces"] = []

    def is_running(self) -> bool:
        return bool(self._status.get("running"))

    def restart(self, settings: Dict[str, Any]) -> None:
        self.start(settings)

    def send_discover_now(self) -> None:
        payload = self._build_payload("DISCOVER")
        self._send_packet(payload, fanout=True)
        self._log("[LAN] Отправлен DISCOVER")

    def send_hello_now(self) -> None:
        payload = self._build_payload("HELLO")
        self._send_packet(payload, fanout=True)
        self._log("[LAN] Отправлен HELLO")

    def send_bye_now(self) -> None:
        payload = self._build_payload("BYE")
        self._send_packet(payload, fanout=True)
        self._log("[LAN] Отправлен BYE")

    def cleanup_stale(self) -> int:
        removed = 0
        timeout_sec = float(self._settings.get("peer_timeout_sec") or 30.0)
        cutoff = _now() - timeout_sec
        with self._lock:
            for node_id in list(self._peers.keys()):
                if float(self._peers[node_id].get("last_seen", 0.0)) < cutoff:
                    self._peers.pop(node_id, None)
                    removed += 1
        if removed:
            self._log(f"[LAN] Удалено устаревших узлов: {removed}")
        return removed

    def clear_peers(self) -> None:
        with self._lock:
            self._peers = {}
        self._log("[LAN] Список узлов очищен")

    def remove_peer(self, node_id: str) -> bool:
        key = str(node_id or "").strip()
        if not key:
            return False
        removed = False
        with self._lock:
            if key in self._peers:
                self._peers.pop(key, None)
                removed = True
        if removed:
            self._log(f"[LAN] Узел удалён из списка: {key}")
        return removed

    def peers_snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [dict(v) for v in self._peers.values()]
        items.sort(key=lambda x: (x.get("instance_name") or "", x.get("ip") or ""))
        return items

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            st = dict(self._status)
            st["peer_count"] = len(self._peers)
        return st

    # ---- internals ----
    def _log(self, text: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(text)
                return
            except Exception:
                pass
        try:
            print(text)
        except Exception:
            pass

    def _build_payload(self, msg_type: str) -> Dict[str, Any]:
        s = self._settings
        local_ips = self._status.get("local_ips") or []
        payload: Dict[str, Any] = {
            "proto": _PROTO_NAME,
            "v": _PROTO_VERSION,
            "type": msg_type,
            "node_id": self._node_id,
            "instance_name": str(s.get("instance_name") or "AutoCraft"),
            "hostname": platform.node() or socket.gethostname(),
            "app": str(s.get("service_name") or "AutoCraft-Bot"),
            "app_version": str(s.get("app_version") or ""),
            "role": str(s.get("mode") or "multi"),
            "time": _now(),
            "udp_port": int(s.get("udp_port") or _DEFAULT_PORT),
            "local_ips": list(local_ips),
            "services": self._build_services_payload(),
        }
        return payload

    def _build_services_payload(self) -> Dict[str, Any]:
        s = self._settings
        services: Dict[str, Any] = {}
        if _to_bool(s.get("advertise_panel"), True):
            panel_port = int(s.get("panel_port") or 0)
            if panel_port > 0:
                host_override = str(s.get("panel_host_override") or "").strip()
                if host_override in {"localhost", "127.0.0.1"}:
                    # Для LAN это почти всегда бесполезно; сохраняем, но нормализуем на приёме по IP sender-а.
                    pass
                services["panel"] = {
                    "scheme": str(s.get("panel_scheme") or "http").strip() or "http",
                    "port": panel_port,
                    "path": _normalize_path(str(s.get("panel_path") or "/")),
                    "host": host_override or "0.0.0.0",
                    "api_token": str(s.get("panel_api_token") or "").strip(),
                    "capabilities": [
                        str(item).strip()
                        for item in (s.get("panel_capabilities") or [])
                        if str(item).strip()
                    ],
                    "proxy_protocol": int(s.get("panel_proxy_protocol") or 0),
                    "panel_version": str(
                        s.get("panel_runtime_version")
                        or s.get("app_version")
                        or ""
                    ).strip(),
                    "remote_control": {
                        "controller_enabled": _to_bool(s.get("remote_control_controller_enabled"), True),
                        "target_enabled": _to_bool(s.get("remote_control_target_enabled"), True),
                        "require_approval": _to_bool(s.get("remote_control_require_approval"), True),
                        "request_timeout_sec": int(
                            _clamp(s.get("remote_control_request_timeout_sec"), 30.0, 3600.0, 180.0)
                        ),
                        "grant_ttl_sec": int(
                            _clamp(s.get("remote_control_grant_ttl_sec"), 60.0, 86400.0, 1800.0)
                        ),
                    },
                }
        return services

    def _send_packet(self, payload: Dict[str, Any], fanout: bool = False, unicast_addr: Optional[tuple] = None) -> None:
        sock = self._tx_sock
        if not sock:
            return
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        sent_any = False
        if unicast_addr is not None:
            try:
                sock.sendto(data, unicast_addr)
                sent_any = True
            except Exception as e:
                self._log(f"[LAN] sendto(unicast) ошибка: {e}")
            return

        if not fanout:
            return

        udp_port = int(self._settings.get("udp_port") or _DEFAULT_PORT)
        local_ifaces = list(self._status.get("local_ifaces") or [])

        if _to_bool(self._settings.get("multicast_enabled"), True):
            group = str(self._settings.get("multicast_group") or _DEFAULT_GROUP)
            multicast_sent = False
            multicast_targets: List[str] = []

            # Отправляем multicast по всем известным IPv4-интерфейсам, чтобы в статических
            # сетях без DHCP пакет не ушёл только в "дефолтную" карту.
            for iface in local_ifaces:
                lip = str((iface or {}).get("ip") or "").strip()
                if not lip:
                    continue
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lip))
                    sock.sendto(data, (group, udp_port))
                    sent_any = True
                    multicast_sent = True
                    multicast_targets.append(lip)
                except Exception as e:
                    self._log(f"[LAN] sendto(multicast {group} via {lip}) ошибка: {e}")

            if not multicast_sent:
                try:
                    sock.sendto(data, (group, udp_port))
                    sent_any = True
                    multicast_sent = True
                except Exception as e:
                    self._log(f"[LAN] sendto(multicast {group}) ошибка: {e}")

        if _to_bool(self._settings.get("broadcast_enabled"), True):
            # В сетях со статическими IP limited broadcast 255.255.255.255 не всегда достаточно.
            # Поэтому дополнительно шлём directed broadcast для каждой найденной подсети.
            directed_targets = _directed_broadcast_targets(local_ifaces)
            for bcast in directed_targets:
                try:
                    sock.sendto(data, (bcast, udp_port))
                    sent_any = True
                except Exception as e:
                    self._log(f"[LAN] sendto(directed-broadcast {bcast}) ошибка: {e}")

            try:
                sock.sendto(data, ("255.255.255.255", udp_port))
                sent_any = True
            except Exception as e:
                self._log(f"[LAN] sendto(broadcast 255.255.255.255) ошибка: {e}")

        if not sent_any:
            self._log("[LAN] Не выбран способ рассылки (multicast/broadcast отключены)")

    def _rx_loop(self) -> None:
        while not self._stop_evt.is_set():
            sock = self._rx_sock
            if not sock:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                self._status["last_error"] = _safe_text(e)
                self._log(f"[LAN] Ошибка приема UDP: {e}")
                time.sleep(0.2)
                continue

            try:
                payload = json.loads(data.decode("utf-8", errors="replace"))
            except Exception:
                continue

            try:
                self._handle_payload(payload, addr)
            except Exception as e:
                self._status["last_error"] = _safe_text(e)
                self._log(f"[LAN] Ошибка обработки пакета: {e}")
                self._log(traceback.format_exc())

    def _handle_payload(self, payload: Dict[str, Any], addr: tuple) -> None:
        if payload.get("proto") != _PROTO_NAME:
            return
        if int(payload.get("v") or 0) != _PROTO_VERSION:
            return

        msg_type = str(payload.get("type") or "").upper()
        node_id = str(payload.get("node_id") or "")
        sender_ip = str(addr[0])
        if not node_id:
            return
        if node_id == self._node_id:
            return

        if msg_type in {"HELLO", "DISCOVER"}:
            self._upsert_peer(payload, sender_ip, msg_type)
        elif msg_type == "BYE":
            with self._lock:
                if node_id in self._peers:
                    self._peers.pop(node_id, None)
                    self._log(f"[LAN] Узел ушёл: {payload.get('instance_name') or sender_ip}")

        # Ответ на DISCOVER только в режимах server/multi.
        mode = str(self._settings.get("mode") or "multi")
        if msg_type == "DISCOVER" and mode in {"server", "multi"}:
            resp = self._build_payload("HELLO")
            src_port = int(payload.get("udp_port") or addr[1] or self._settings.get("udp_port") or _DEFAULT_PORT)
            self._send_packet(resp, unicast_addr=(sender_ip, src_port))

    def _upsert_peer(self, payload: Dict[str, Any], sender_ip: str, source_type: str) -> None:
        services = payload.get("services") or {}
        panel_info = services.get("panel") if isinstance(services, dict) else None
        panel_url = ""
        panel_api_token = ""
        panel_capabilities: List[str] = []
        panel_proxy_protocol = 0
        panel_version = ""
        panel_remote_controller_enabled = True
        panel_remote_target_enabled = True
        panel_remote_require_approval = True
        if isinstance(panel_info, dict):
            try:
                panel_url = _url_from_service(sender_ip, panel_info)
            except Exception:
                panel_url = ""
            panel_api_token = str(panel_info.get("api_token") or "").strip()
            raw_caps = panel_info.get("capabilities")
            if isinstance(raw_caps, list):
                panel_capabilities = [str(item).strip() for item in raw_caps if str(item).strip()]
            try:
                panel_proxy_protocol = int(panel_info.get("proxy_protocol") or 0)
            except Exception:
                panel_proxy_protocol = 0
            panel_version = str(panel_info.get("panel_version") or "").strip()
            remote_cfg = panel_info.get("remote_control")
            if isinstance(remote_cfg, dict):
                panel_remote_controller_enabled = _to_bool(
                    remote_cfg.get("controller_enabled"),
                    panel_remote_controller_enabled,
                )
                panel_remote_target_enabled = _to_bool(
                    remote_cfg.get("target_enabled"),
                    panel_remote_target_enabled,
                )
                panel_remote_require_approval = _to_bool(
                    remote_cfg.get("require_approval"),
                    panel_remote_require_approval,
                )

        peer = {
            "node_id": str(payload.get("node_id") or ""),
            "instance_name": str(payload.get("instance_name") or payload.get("hostname") or sender_ip),
            "hostname": str(payload.get("hostname") or ""),
            "app": str(payload.get("app") or "AutoCraft-Bot"),
            "app_version": str(payload.get("app_version") or ""),
            "role": str(payload.get("role") or "?"),
            "ip": sender_ip,
            "udp_port": int(payload.get("udp_port") or self._settings.get("udp_port") or _DEFAULT_PORT),
            "source": source_type,
            "last_seen": _now(),
            "last_seen_text": _fmt_dt(_now()),
            "services": services if isinstance(services, dict) else {},
            "panel_url": panel_url,
            "panel_api_token": panel_api_token,
            "panel_capabilities": panel_capabilities,
            "panel_proxy_protocol": panel_proxy_protocol,
            "panel_version": panel_version,
            "remote_control_controller_enabled": panel_remote_controller_enabled,
            "remote_control_target_enabled": panel_remote_target_enabled,
            "remote_control_require_approval": panel_remote_require_approval,
        }

        with self._lock:
            is_new = peer["node_id"] not in self._peers
            self._peers[peer["node_id"]] = peer

        if is_new:
            self._log(
                f"[LAN] Найден узел: {peer['instance_name']} ({peer['ip']}) роль={peer['role']} "
                f"panel={peer['panel_url'] or 'нет'}"
            )

    def _tick_loop(self) -> None:
        next_discover = 0.0
        next_hello = 0.0
        next_cleanup = 0.0

        while not self._stop_evt.is_set():
            now_ts = _now()
            mode = str(self._settings.get("mode") or "multi")
            discover_interval = float(self._settings.get("discover_interval_sec") or 5.0)
            announce_interval = float(self._settings.get("announce_interval_sec") or 10.0)
            cleanup_interval = min(5.0, max(1.0, float(self._settings.get("peer_timeout_sec") or 30.0) / 3.0))

            if mode in {"client", "multi"} and now_ts >= next_discover:
                try:
                    self.send_discover_now()
                except Exception as e:
                    self._log(f"[LAN] Ошибка DISCOVER: {e}")
                next_discover = now_ts + max(1.0, discover_interval)

            if mode in {"server", "multi"} and now_ts >= next_hello:
                try:
                    self.send_hello_now()
                except Exception as e:
                    self._log(f"[LAN] Ошибка HELLO: {e}")
                next_hello = now_ts + max(1.0, announce_interval)

            if now_ts >= next_cleanup:
                try:
                    self.cleanup_stale()
                except Exception:
                    pass
                next_cleanup = now_ts + cleanup_interval

            self._stop_evt.wait(0.3)


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# -------------------- бэкенд (интеграция с AutoCraft) --------------------


class LanDiscoveryBackend:
    """Хранит настройки, поднимает сетевой сервис и отдаёт данные GUI."""

    def __init__(self):
        try:
            import __main__ as main  # type: ignore
        except Exception:
            main = None  # type: ignore
        self._main = main

        base = Path(getattr(main, "base_dir", getattr(main, "BASE_DIR", Path.cwd()))).resolve()
        self.base_dir = base
        self.config_file = Path(
            getattr(main, "CONFIG_FILE", getattr(main, "CONFIG_PATH", base / "config.ini"))
        ).resolve()
        self._log_queue: "queue.SimpleQueue[str]" = queue.SimpleQueue()
        self._service = LanDiscoveryService(log_cb=self._enqueue_log)
        self._lock = threading.RLock()

        self._settings_cache: Optional[Dict[str, Any]] = None
        self._log_history: List[str] = []
        self._log_history_limit = 1200


    # ---- веб-панель (логика как в gui.py) ----
    def _get_webpanel_backend(self):
        """
        Возвращает backend веб-панели так же, как в gui.py:
        сначала startrunmodulwebpanel, затем moduls.startrunmodulwebpanel.
        """
        last_err = None
        for name in ("startrunmodulwebpanel", "moduls.startrunmodulwebpanel"):
            try:
                return importlib.import_module(name)
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        raise ImportError("Не удалось импортировать backend веб-панели")

    def _resolve_webpanel_url_like_gui(self) -> Dict[str, Any]:
        """
        Пытается получить URL панели с тем же приоритетом, что и gui.py:
        1) URL запущенного runtime (srv.url)
        2) fallback по конфигу веб-панели (host/port)
        Для LAN-рекламы разрешаем fallback по конфигу даже если панель сейчас не запущена.
        """
        info: Dict[str, Any] = {
            "ok": False,
            "running": False,
            "url": "",
            "source": "",
            "error": "",
        }

        try:
            backend = self._get_webpanel_backend()
        except Exception as e:
            info["error"] = f"Не удалось импортировать модуль веб-панели: {e}"
            return info

        srv = getattr(backend, "_server", None)

        try:
            checker = getattr(backend, "_is_panel_running", None)
            if callable(checker):
                info["running"] = bool(checker())
            elif srv is not None and hasattr(srv, "is_running"):
                info["running"] = bool(srv.is_running())
        except Exception:
            info["running"] = False

        # 1) runtime URL
        try:
            if srv is not None and hasattr(srv, "url"):
                url = str(srv.url() or "").strip()
                if url:
                    info["ok"] = True
                    info["url"] = url
                    info["source"] = "runtime"
                    return info
        except Exception:
            pass

        # 2) fallback на конфиг панели
        cfg = None
        try:
            if srv is not None:
                cfg = srv.runtime.config
        except Exception:
            cfg = None

        if cfg is None:
            try:
                panel_config = getattr(backend, "panel_config", None)
                load_cfg = getattr(panel_config, "load_config", None) if panel_config else None
                backend_base = getattr(backend, "base_dir", None) or self.base_dir
                if callable(load_cfg):
                    cfg = load_cfg(backend_base)
            except Exception:
                cfg = None

        if cfg is None:
            info["error"] = "Конфиг веб-панели недоступен"
            return info

        try:
            host = str(getattr(cfg, "host", "") or "").strip()
            port_val = getattr(cfg, "port", "")
            port = int(str(port_val).strip() or "0")
            if not host or not (1 <= port <= 65535):
                info["error"] = "В конфиге веб-панели не задан host/port"
                return info

            # Попробуем схему из конфига, если есть, иначе http.
            scheme = "http"
            for attr in ("scheme", "url_scheme"):
                try:
                    s = str(getattr(cfg, attr, "") or "").strip().lower()
                    if s in {"http", "https"}:
                        scheme = s
                        break
                except Exception:
                    pass
            if scheme == "http":
                for attr in ("https", "use_https", "ssl", "ssl_enabled"):
                    try:
                        v = getattr(cfg, attr, None)
                        if isinstance(v, bool) and v:
                            scheme = "https"
                            break
                        if str(v).strip().lower() in {"1", "true", "yes", "on"}:
                            scheme = "https"
                            break
                    except Exception:
                        pass

            path = "/"
            for attr in ("path", "base_path", "url_path", "root_path"):
                try:
                    p = str(getattr(cfg, attr, "") or "").strip()
                    if p:
                        path = _normalize_path(p)
                        break
                except Exception:
                    pass

            if host == "0.0.0.0":
                host_for_url = "127.0.0.1"
            else:
                host_for_url = host

            info["ok"] = True
            info["url"] = f"{scheme}://{host_for_url}:{port}{path}"
            info["source"] = "config"
            return info
        except Exception as e:
            info["error"] = _safe_text(e)
            return info

    def _sync_panel_advertisement_settings(self, settings: Dict[str, Any]) -> (Dict[str, Any], bool):
        """
        Обновляет panel_scheme/panel_port/panel_path/panel_host_override из текущих
        данных веб-панели. Возвращает (settings, changed).
        """
        out = dict(settings or {})
        changed = False

        resolved = self._resolve_webpanel_url_like_gui()
        url = str(resolved.get("url") or "").strip()
        if not url:
            return out, False

        parts = _parse_panel_url_parts(url)
        if not parts:
            return out, False

        # Для LAN не надо рекламировать 127.0.0.1 как host_override.
        if resolved.get("source") == "config":
            # Если URL нормализован для браузера и стал 127.0.0.1, пробуем взять исходный host из cfg
            # через отдельный разбор без forcing. Но безопасно: пустой override тоже корректен.
            if str(parts.get("panel_host_override") or "").strip() in {"127.0.0.1", "localhost"}:
                parts["panel_host_override"] = ""

        for key, value in parts.items():
            if out.get(key) != value:
                out[key] = value
                changed = True

        return out, changed

    def refresh_panel_advertisement_settings(self, persist: bool = True, silent: bool = True) -> Dict[str, Any]:
        """
        Принудительно перечитывает настройки, подтягивает актуальные данные веб-панели
        и при необходимости сохраняет их в config.ini.
        """
        settings = self.load_settings(force_reload=True)
        synced, changed = self._sync_panel_advertisement_settings(settings)
        if changed:
            self.save_settings(synced, emit_log=not silent)
            return synced
        return settings

    # ---- лог ----
    def _enqueue_log(self, text: str) -> None:
        line = f"{_fmt_dt(_now())} {text}"
        try:
            self._log_queue.put(line)
        except Exception:
            pass
        try:
            with self._lock:
                self._log_history.append(line)
                if len(self._log_history) > self._log_history_limit:
                    del self._log_history[: len(self._log_history) - self._log_history_limit]
        except Exception:
            pass
        self.log_to_main(text)

    def poll_logs(self, max_items: int = 200) -> List[str]:
        items: List[str] = []
        for _ in range(max_items):
            try:
                items.append(self._log_queue.get_nowait())
            except Exception:
                break
        return items

    def get_log_history(self, limit: int = 400) -> List[str]:
        try:
            cap = max(1, int(limit))
        except Exception:
            cap = 400
        with self._lock:
            if cap <= 0:
                return list(self._log_history)
            return list(self._log_history[-cap:])

    def log_to_main(self, text: str) -> None:
        for attr in ("write_bot_log", "write_plugin_log", "write_log"):
            fn = getattr(self._main, attr, None) if self._main else None
            if callable(fn):
                try:
                    fn(text)
                    return
                except Exception:
                    pass
        try:
            print(text)
        except Exception:
            pass

    # ---- настройки ----
    def _supported_proxy_capabilities(self) -> List[str]:
        for module_name in ("moduls.web_dashboard.remote_control", "web_dashboard.remote_control"):
            try:
                module = importlib.import_module(module_name)
                getter = getattr(module, "get_supported_capabilities", None)
                if callable(getter):
                    caps = getter()
                    if isinstance(caps, list):
                        return [str(item).strip() for item in caps if str(item).strip()]
            except Exception:
                continue
        return [
            "proxy_html_v1",
            "proxy_auth_roles_v1",
            "proxy_stream_v1",
            "proxy_static_v1",
        ]

    def _resolve_panel_runtime_meta(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "api_token": "",
            "proxy_protocol": 1,
            "capabilities": self._supported_proxy_capabilities(),
            "runtime_version": "",
        }

        for attr in ("APP_VERSION", "__version__", "VERSION", "PROGRAM_VERSION"):
            try:
                value = getattr(self._main, attr)
                if value:
                    out["runtime_version"] = str(value)
                    break
            except Exception:
                continue

        base_dir = str(self.base_dir)
        for module_name in ("moduls.web_dashboard.config", "web_dashboard.config"):
            try:
                module = importlib.import_module(module_name)
                loader = getattr(module, "load_config", None)
                if not callable(loader):
                    continue
                cfg = loader(base_dir)
                token = str(getattr(cfg, "api_token", "") or "").strip()
                if token:
                    out["api_token"] = token
                break
            except Exception:
                continue

        for module_name in ("moduls.web_dashboard.remote_control", "web_dashboard.remote_control"):
            try:
                module = importlib.import_module(module_name)
                getter = getattr(module, "get_proxy_protocol_version", None)
                if callable(getter):
                    out["proxy_protocol"] = int(getter() or 1)
                    break
            except Exception:
                continue
        return out

    def default_settings(self) -> Dict[str, Any]:
        host = platform.node() or socket.gethostname() or "PC"
        app_version = ""
        for attr in ("APP_VERSION", "__version__", "VERSION", "PROGRAM_VERSION"):
            try:
                val = getattr(self._main, attr)
                if val:
                    app_version = str(val)
                    break
            except Exception:
                continue

        panel_port = _guess_default_panel_port(self._main, self.config_file, default=5212)
        panel_scheme = "http"
        panel_path = "/"
        panel_host_override = ""
        try:
            resolved = self._resolve_webpanel_url_like_gui()
            parts = _parse_panel_url_parts(str(resolved.get("url") or ""))
            if parts:
                panel_port = int(parts.get("panel_port") or panel_port)
                panel_scheme = str(parts.get("panel_scheme") or panel_scheme)
                panel_path = str(parts.get("panel_path") or panel_path)
                panel_host_override = str(parts.get("panel_host_override") or panel_host_override)
        except Exception:
            pass

        runtime_meta = self._resolve_panel_runtime_meta()

        return {
            "enabled_on_start": False,
            "mode": "multi",  # server|client|multi
            "service_name": "AutoCraft-Bot",
            "instance_name": f"AutoCraft-{host}",
            "app_version": app_version,
            "node_id": str(uuid.uuid4()),
            "udp_port": _DEFAULT_PORT,
            "multicast_group": _DEFAULT_GROUP,
            "multicast_enabled": True,
            "broadcast_enabled": True,
            "discover_interval_sec": 5.0,
            "announce_interval_sec": 10.0,
            "peer_timeout_sec": 30.0,
            "advertise_panel": True,
            "panel_scheme": panel_scheme,
            "panel_port": panel_port,
            "panel_path": panel_path,
            "panel_host_override": panel_host_override,
            "panel_api_token": str(runtime_meta.get("api_token") or "").strip(),
            "panel_capabilities": list(runtime_meta.get("capabilities") or []),
            "panel_proxy_protocol": int(runtime_meta.get("proxy_protocol") or 1),
            "panel_runtime_version": str(runtime_meta.get("runtime_version") or app_version or "").strip(),
            "remote_control_controller_enabled": True,
            "remote_control_target_enabled": True,
            "remote_control_require_approval": True,
            "remote_control_auto_request_on_select": True,
            "remote_control_request_timeout_sec": 180,
            "remote_control_grant_ttl_sec": 1800,
        }

    def load_settings(self, force_reload: bool = False) -> Dict[str, Any]:
        with self._lock:
            if (not force_reload) and self._settings_cache is not None:
                return dict(self._settings_cache)

            settings = self.default_settings()
            cp = configparser.ConfigParser()
            if self.config_file.exists():
                try:
                    cp.read(self.config_file, encoding="utf-8")
                except Exception:
                    cp = configparser.ConfigParser()

            sec = _DEFAULT_SECTION
            if cp.has_section(sec):
                cfg = cp[sec]
                settings["enabled_on_start"] = _to_bool(cfg.get("enabled_on_start"), settings["enabled_on_start"])
                mode = str(cfg.get("mode", settings["mode"]))
                if mode not in {"server", "client", "multi"}:
                    mode = "multi"
                settings["mode"] = mode
                settings["service_name"] = str(cfg.get("service_name", settings["service_name"]))
                settings["instance_name"] = str(cfg.get("instance_name", settings["instance_name"]))
                settings["app_version"] = str(cfg.get("app_version", settings["app_version"]))
                settings["node_id"] = str(cfg.get("node_id", settings["node_id"])) or settings["node_id"]
                try:
                    settings["udp_port"] = max(1, min(65535, int(cfg.get("udp_port", settings["udp_port"]))))
                except Exception:
                    pass
                settings["multicast_group"] = str(cfg.get("multicast_group", settings["multicast_group"]))
                settings["multicast_enabled"] = _to_bool(cfg.get("multicast_enabled"), settings["multicast_enabled"])
                settings["broadcast_enabled"] = _to_bool(cfg.get("broadcast_enabled"), settings["broadcast_enabled"])
                settings["discover_interval_sec"] = _clamp(
                    cfg.get("discover_interval_sec"), 1.0, 300.0, settings["discover_interval_sec"]
                )
                settings["announce_interval_sec"] = _clamp(
                    cfg.get("announce_interval_sec"), 1.0, 300.0, settings["announce_interval_sec"]
                )
                settings["peer_timeout_sec"] = _clamp(
                    cfg.get("peer_timeout_sec"), 3.0, 3600.0, settings["peer_timeout_sec"]
                )
                settings["advertise_panel"] = _to_bool(cfg.get("advertise_panel"), settings["advertise_panel"])
                settings["panel_scheme"] = str(cfg.get("panel_scheme", settings["panel_scheme"])) or "http"
                try:
                    settings["panel_port"] = max(1, min(65535, int(cfg.get("panel_port", settings["panel_port"]))))
                except Exception:
                    pass
                settings["panel_path"] = _normalize_path(str(cfg.get("panel_path", settings["panel_path"])))
                settings["panel_host_override"] = str(cfg.get("panel_host_override", settings["panel_host_override"]))
                settings["remote_control_controller_enabled"] = _to_bool(
                    cfg.get("remote_control_controller_enabled"),
                    settings["remote_control_controller_enabled"],
                )
                settings["remote_control_target_enabled"] = _to_bool(
                    cfg.get("remote_control_target_enabled"),
                    settings["remote_control_target_enabled"],
                )
                settings["remote_control_require_approval"] = _to_bool(
                    cfg.get("remote_control_require_approval"),
                    settings["remote_control_require_approval"],
                )
                settings["remote_control_auto_request_on_select"] = _to_bool(
                    cfg.get("remote_control_auto_request_on_select"),
                    settings["remote_control_auto_request_on_select"],
                )
                settings["remote_control_request_timeout_sec"] = int(
                    _clamp(
                        cfg.get("remote_control_request_timeout_sec"),
                        30.0,
                        3600.0,
                        settings["remote_control_request_timeout_sec"],
                    )
                )
                settings["remote_control_grant_ttl_sec"] = int(
                    _clamp(
                        cfg.get("remote_control_grant_ttl_sec"),
                        60.0,
                        86400.0,
                        settings["remote_control_grant_ttl_sec"],
                    )
                )

            synced, changed = self._sync_panel_advertisement_settings(settings)
            settings = synced
            if changed:
                # Перезаписываем только если данные веб-панели реально изменились.
                self.save_settings(settings, emit_log=False)
                self._settings_cache = dict(settings)
                return dict(settings)

            self._settings_cache = dict(settings)
            return settings

    def save_settings(self, settings: Dict[str, Any], emit_log: bool = True) -> None:
        normalized = self._normalize_settings(settings)
        cp = configparser.ConfigParser()
        if self.config_file.exists():
            try:
                cp.read(self.config_file, encoding="utf-8")
            except Exception:
                cp = configparser.ConfigParser()
        sec = _DEFAULT_SECTION
        if not cp.has_section(sec):
            cp.add_section(sec)

        cp[sec]["enabled_on_start"] = "1" if normalized["enabled_on_start"] else "0"
        cp[sec]["mode"] = normalized["mode"]
        cp[sec]["service_name"] = str(normalized["service_name"])
        cp[sec]["instance_name"] = str(normalized["instance_name"])
        cp[sec]["app_version"] = str(normalized["app_version"])
        cp[sec]["node_id"] = str(normalized["node_id"])
        cp[sec]["udp_port"] = str(int(normalized["udp_port"]))
        cp[sec]["multicast_group"] = str(normalized["multicast_group"])
        cp[sec]["multicast_enabled"] = "1" if normalized["multicast_enabled"] else "0"
        cp[sec]["broadcast_enabled"] = "1" if normalized["broadcast_enabled"] else "0"
        cp[sec]["discover_interval_sec"] = str(float(normalized["discover_interval_sec"]))
        cp[sec]["announce_interval_sec"] = str(float(normalized["announce_interval_sec"]))
        cp[sec]["peer_timeout_sec"] = str(float(normalized["peer_timeout_sec"]))
        cp[sec]["advertise_panel"] = "1" if normalized["advertise_panel"] else "0"
        cp[sec]["panel_scheme"] = str(normalized["panel_scheme"])
        cp[sec]["panel_port"] = str(int(normalized["panel_port"]))
        cp[sec]["panel_path"] = _normalize_path(str(normalized["panel_path"]))
        cp[sec]["panel_host_override"] = str(normalized["panel_host_override"])
        cp[sec]["remote_control_controller_enabled"] = "1" if normalized["remote_control_controller_enabled"] else "0"
        cp[sec]["remote_control_target_enabled"] = "1" if normalized["remote_control_target_enabled"] else "0"
        cp[sec]["remote_control_require_approval"] = "1" if normalized["remote_control_require_approval"] else "0"
        cp[sec]["remote_control_auto_request_on_select"] = (
            "1" if normalized["remote_control_auto_request_on_select"] else "0"
        )
        cp[sec]["remote_control_request_timeout_sec"] = str(int(normalized["remote_control_request_timeout_sec"]))
        cp[sec]["remote_control_grant_ttl_sec"] = str(int(normalized["remote_control_grant_ttl_sec"]))

        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with self.config_file.open("w", encoding="utf-8") as f:
            cp.write(f)

        with self._lock:
            self._settings_cache = dict(normalized)

        if emit_log:
            self._enqueue_log("[LAN] Настройки сохранены")

    def _normalize_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        base = self.default_settings()
        out = dict(base)
        out.update(dict(settings or {}))
        mode = str(out.get("mode") or "multi")
        if mode not in {"server", "client", "multi"}:
            mode = "multi"
        out["mode"] = mode
        out["enabled_on_start"] = _to_bool(out.get("enabled_on_start"), False)
        out["service_name"] = str(out.get("service_name") or "AutoCraft-Bot").strip() or "AutoCraft-Bot"
        out["instance_name"] = str(out.get("instance_name") or out["service_name"]).strip() or out["service_name"]
        out["node_id"] = str(out.get("node_id") or uuid.uuid4())
        try:
            out["udp_port"] = max(1, min(65535, int(out.get("udp_port") or _DEFAULT_PORT)))
        except Exception:
            out["udp_port"] = _DEFAULT_PORT
        out["multicast_group"] = str(out.get("multicast_group") or _DEFAULT_GROUP).strip() or _DEFAULT_GROUP
        out["multicast_enabled"] = _to_bool(out.get("multicast_enabled"), True)
        out["broadcast_enabled"] = _to_bool(out.get("broadcast_enabled"), True)
        out["discover_interval_sec"] = _clamp(out.get("discover_interval_sec"), 1.0, 300.0, 5.0)
        out["announce_interval_sec"] = _clamp(out.get("announce_interval_sec"), 1.0, 300.0, 10.0)
        out["peer_timeout_sec"] = _clamp(out.get("peer_timeout_sec"), 3.0, 3600.0, 30.0)
        out["advertise_panel"] = _to_bool(out.get("advertise_panel"), True)
        scheme = str(out.get("panel_scheme") or "http").strip().lower()
        if scheme not in {"http", "https"}:
            scheme = "http"
        out["panel_scheme"] = scheme
        try:
            out["panel_port"] = max(1, min(65535, int(out.get("panel_port") or 5212)))
        except Exception:
            out["panel_port"] = 5212
        out["panel_path"] = _normalize_path(str(out.get("panel_path") or "/"))
        out["panel_host_override"] = str(out.get("panel_host_override") or "").strip()
        out["app_version"] = str(out.get("app_version") or "")
        out["remote_control_controller_enabled"] = _to_bool(out.get("remote_control_controller_enabled"), True)
        out["remote_control_target_enabled"] = _to_bool(out.get("remote_control_target_enabled"), True)
        out["remote_control_require_approval"] = _to_bool(out.get("remote_control_require_approval"), True)
        out["remote_control_auto_request_on_select"] = _to_bool(
            out.get("remote_control_auto_request_on_select"),
            True,
        )
        out["remote_control_request_timeout_sec"] = int(
            _clamp(out.get("remote_control_request_timeout_sec"), 30.0, 3600.0, 180.0)
        )
        out["remote_control_grant_ttl_sec"] = int(
            _clamp(out.get("remote_control_grant_ttl_sec"), 60.0, 86400.0, 1800.0)
        )
        runtime_meta = self._resolve_panel_runtime_meta()
        out["panel_api_token"] = str(runtime_meta.get("api_token") or out.get("panel_api_token") or "").strip()
        out["panel_proxy_protocol"] = int(runtime_meta.get("proxy_protocol") or out.get("panel_proxy_protocol") or 1)
        out["panel_runtime_version"] = str(
            runtime_meta.get("runtime_version")
            or out.get("panel_runtime_version")
            or out.get("app_version")
            or ""
        ).strip()
        caps = runtime_meta.get("capabilities") or out.get("panel_capabilities") or []
        if isinstance(caps, list):
            out["panel_capabilities"] = [str(item).strip() for item in caps if str(item).strip()]
        else:
            out["panel_capabilities"] = []
        return out

    # ---- сервис ----
    def start_service(self, settings: Optional[Dict[str, Any]] = None) -> None:
        if settings is None:
            settings = self.load_settings()
        norm = self._normalize_settings(settings)
        self.save_settings(norm)  # чтобы node_id и прочее зафиксировались
        self._service.start(norm)

    def stop_service(self) -> None:
        self._service.stop()
        self._enqueue_log("[LAN] Сервис остановлен")

    def restart_service(self, settings: Optional[Dict[str, Any]] = None) -> None:
        if settings is None:
            settings = self.load_settings()
        norm = self._normalize_settings(settings)
        self.save_settings(norm)
        self._service.restart(norm)

    def refresh_now(self) -> None:
        if not self._service.is_running():
            self._enqueue_log("[LAN] Сервис не запущен")
            return
        self._service.send_discover_now()
        mode = str(self.load_settings().get("mode") or "multi")
        if mode in {"server", "multi"}:
            self._service.send_hello_now()

    def clear_peers(self) -> None:
        self._service.clear_peers()

    def remove_peer(self, node_id: str) -> bool:
        return bool(self._service.remove_peer(node_id))

    def cleanup_stale(self) -> int:
        return self._service.cleanup_stale()

    def get_status(self) -> Dict[str, Any]:
        return self._service.status_snapshot()

    def get_peers(self) -> List[Dict[str, Any]]:
        return self._service.peers_snapshot()

    def try_autostart_from_config(self) -> None:
        try:
            settings = self.load_settings()
            if not settings.get("enabled_on_start"):
                return
            if self._service.is_running():
                self._enqueue_log("[LAN] Сервис уже запущен, повторный автозапуск пропускаю")
                return
            self._enqueue_log("[LAN] Автозапуск сервиса включён, запускаю...")
            self.start_service(settings)
        except Exception as e:
            self._enqueue_log(f"[LAN] Автозапуск не удался: {e}")


_BACKEND_SINGLETON: Optional[LanDiscoveryBackend] = None
_BACKEND_LOCK = threading.Lock()


def get_lan_discovery_backend() -> LanDiscoveryBackend:
    global _BACKEND_SINGLETON
    with _BACKEND_LOCK:
        if _BACKEND_SINGLETON is None:
            _BACKEND_SINGLETON = LanDiscoveryBackend()
        return _BACKEND_SINGLETON


# -------------------- GUI (создаётся по требованию) --------------------


def _ensure_gui_built() -> None:
    global _GUI_BUILT
    if _GUI_BUILT:
        return
    _get_pyqt()

    class BackgroundTask(QThread):
        progress = pyqtSignal(str)
        finished = pyqtSignal(object, object)

        def __init__(self, fn: Callable[[Callable[[str], None]], Any], parent=None):
            super().__init__(parent)
            self._fn = fn

        def run(self):  # type: ignore[override]
            try:
                res = self._fn(self.progress.emit)
                self.finished.emit(res, None)
            except Exception as e:
                self.finished.emit(None, e)

    class LanDiscoveryWindow(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("lanDiscoveryWindow")
            self.setWindowTitle("LAN автообнаружение AutoCraft")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setMinimumSize(980, 650)
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

            self._backend = get_lan_discovery_backend()
            self._task: Optional[BackgroundTask] = None
            self._peers: List[Dict[str, Any]] = []
            self._log_user_scrolling = False
            self._last_peers_signature = None
            self._last_peer_ids = tuple()
            self._last_settings_signature = None


            root = QVBoxLayout(self)
            root.setContentsMargins(16, 16, 16, 12)
            root.setSpacing(8)

            title = QLabel("Локальное автообнаружение AutoCraft-Bot")
            title.setStyleSheet("font-size: 15pt; font-weight: 600;")
            title.setAccessibleName("Заголовок окна LAN автообнаружения")
            root.addWidget(title)

            self.status_lbl = QLabel("Готово. Настройте режим и нажмите «Запустить».")
            self.status_lbl.setWordWrap(True)
            self.status_lbl.setAccessibleName("Строка статуса LAN обнаружения")
            root.addWidget(self.status_lbl)

            self.selected_lbl = QLabel("Выбор: узел не выбран.")
            self.selected_lbl.setWordWrap(True)
            self.selected_lbl.setAccessibleName("Выбранный узел")
            self.selected_lbl.setAccessibleDescription(
                "Показывает, какой узел выбран в таблице ниже. "
                "Чтобы выбрать узел, перейди в таблицу Tab-ом и используй стрелки вверх/вниз."
            )
            root.addWidget(self.selected_lbl)

            # Верхняя зона с настройками
            top = QHBoxLayout()
            top.setSpacing(10)
            root.addLayout(top)

            # Левый блок: режим/сеть
            left_col = QVBoxLayout()
            left_col.setSpacing(8)
            top.addLayout(left_col, 1)

            mode_box = QGroupBox("Режим")
            mode_form = QFormLayout(mode_box)
            mode_form.setContentsMargins(10, 10, 10, 10)
            mode_form.setSpacing(6)

            self.mode_combo = QComboBox()
            self.mode_combo.addItem("Сервер (отвечает на поиск)", "server")
            self.mode_combo.addItem("Клиент (ищет серверы/узлы)", "client")
            self.mode_combo.addItem("Мульти (и сервер, и клиент)", "multi")
            self.mode_combo.setAccessibleName("Выбор режима LAN обнаружения")
            mode_form.addRow("Режим:", self.mode_combo)

            self.enabled_on_start_cb = QCheckBox("Автозапуск сервиса при старте AutoCraft")
            self.enabled_on_start_cb.setAccessibleName("Автозапуск сервиса LAN")
            mode_form.addRow(self.enabled_on_start_cb)

            self.instance_edit = QLineEdit()
            self.instance_edit.setPlaceholderText("Например: AutoCraft-Server-18")
            self.instance_edit.setAccessibleName("Имя экземпляра в сети")
            mode_form.addRow("Имя узла:", self.instance_edit)

            self.service_name_edit = QLineEdit()
            self.service_name_edit.setAccessibleName("Имя приложения в протоколе")
            mode_form.addRow("Приложение:", self.service_name_edit)

            self.version_edit = QLineEdit()
            self.version_edit.setAccessibleName("Версия приложения")
            mode_form.addRow("Версия:", self.version_edit)

            left_col.addWidget(mode_box)

            net_box = QGroupBox("Сеть")
            net_form = QFormLayout(net_box)
            net_form.setContentsMargins(10, 10, 10, 10)
            net_form.setSpacing(6)

            self.udp_port_spin = QSpinBox()
            self.udp_port_spin.setRange(1, 65535)
            self.udp_port_spin.setAccessibleName("UDP порт LAN обнаружения")
            net_form.addRow("UDP порт:", self.udp_port_spin)

            self.multicast_group_edit = QLineEdit()
            self.multicast_group_edit.setPlaceholderText(_DEFAULT_GROUP)
            self.multicast_group_edit.setAccessibleName("Multicast группа")
            net_form.addRow("Multicast group:", self.multicast_group_edit)

            self.multicast_cb = QCheckBox("Использовать multicast")
            self.multicast_cb.setAccessibleName("Переключатель multicast")
            net_form.addRow(self.multicast_cb)

            self.broadcast_cb = QCheckBox("Использовать broadcast (резерв)")
            self.broadcast_cb.setAccessibleName("Переключатель broadcast")
            net_form.addRow(self.broadcast_cb)

            self.discover_interval_spin = QDoubleSpinBox()
            self.discover_interval_spin.setRange(1.0, 300.0)
            self.discover_interval_spin.setDecimals(1)
            self.discover_interval_spin.setSingleStep(0.5)
            self.discover_interval_spin.setSuffix(" сек")
            self.discover_interval_spin.setAccessibleName("Интервал DISCOVER")
            net_form.addRow("DISCOVER:", self.discover_interval_spin)

            self.announce_interval_spin = QDoubleSpinBox()
            self.announce_interval_spin.setRange(1.0, 300.0)
            self.announce_interval_spin.setDecimals(1)
            self.announce_interval_spin.setSingleStep(0.5)
            self.announce_interval_spin.setSuffix(" сек")
            self.announce_interval_spin.setAccessibleName("Интервал HELLO")
            net_form.addRow("HELLO:", self.announce_interval_spin)

            self.peer_timeout_spin = QDoubleSpinBox()
            self.peer_timeout_spin.setRange(3.0, 3600.0)
            self.peer_timeout_spin.setDecimals(1)
            self.peer_timeout_spin.setSingleStep(1.0)
            self.peer_timeout_spin.setSuffix(" сек")
            self.peer_timeout_spin.setAccessibleName("Таймаут узла")
            net_form.addRow("Таймаут узла:", self.peer_timeout_spin)

            left_col.addWidget(net_box)

            # Правый блок: веб-панель/управление
            right_col = QVBoxLayout()
            right_col.setSpacing(8)
            top.addLayout(right_col, 1)

            panel_box = QGroupBox("Реклама веб-панели")
            panel_form = QFormLayout(panel_box)
            panel_form.setContentsMargins(10, 10, 10, 10)
            panel_form.setSpacing(6)

            self.advertise_panel_cb = QCheckBox("Показывать веб-панель другим узлам")
            self.advertise_panel_cb.setAccessibleName("Переключатель рекламы веб панели")
            panel_form.addRow(self.advertise_panel_cb)

            self.panel_scheme_combo = QComboBox()
            self.panel_scheme_combo.addItem("HTTP", "http")
            self.panel_scheme_combo.addItem("HTTPS", "https")
            self.panel_scheme_combo.setAccessibleName("Схема веб панели")
            panel_form.addRow("Схема:", self.panel_scheme_combo)

            self.panel_port_spin = QSpinBox()
            self.panel_port_spin.setRange(1, 65535)
            self.panel_port_spin.setAccessibleName("Порт веб панели")
            panel_form.addRow("Порт панели:", self.panel_port_spin)

            self.panel_path_edit = QLineEdit()
            self.panel_path_edit.setPlaceholderText("/")
            self.panel_path_edit.setAccessibleName("Путь веб панели")
            panel_form.addRow("Путь:", self.panel_path_edit)

            self.panel_host_override_edit = QLineEdit()
            self.panel_host_override_edit.setPlaceholderText("Оставь пустым для IP узла")
            self.panel_host_override_edit.setAccessibleName("Переопределение адреса панели")
            panel_form.addRow("Host override:", self.panel_host_override_edit)

            right_col.addWidget(panel_box)

            action_box = QGroupBox("Управление")
            action_layout = QVBoxLayout(action_box)
            action_layout.setContentsMargins(10, 10, 10, 10)
            action_layout.setSpacing(6)

            row1 = QHBoxLayout()
            row1.setSpacing(6)
            action_layout.addLayout(row1)
            self.start_btn = QPushButton("Запустить")
            self.start_btn.clicked.connect(self._start_service)
            self.start_btn.setAccessibleName("Кнопка запуска сервиса LAN")
            self.start_btn.setAccessibleDescription("Запускает сетевой сервис автообнаружения по текущим настройкам.")
            row1.addWidget(self.start_btn)
            self.stop_btn = QPushButton("Остановить")
            self.stop_btn.clicked.connect(self._stop_service)
            self.stop_btn.setAccessibleName("Кнопка остановки сервиса LAN")
            self.stop_btn.setAccessibleDescription("Останавливает сетевой сервис автообнаружения.")
            row1.addWidget(self.stop_btn)
            self.restart_btn = QPushButton("Применить и перезапустить")
            self.restart_btn.clicked.connect(self._restart_service)
            self.restart_btn.setAccessibleName("Кнопка перезапуска сервиса LAN")
            self.restart_btn.setAccessibleDescription("Сохраняет настройки и перезапускает сервис автообнаружения.")
            row1.addWidget(self.restart_btn)

            row2 = QHBoxLayout()
            row2.setSpacing(6)
            action_layout.addLayout(row2)
            self.save_btn = QPushButton("Сохранить настройки")
            self.save_btn.clicked.connect(self._save_settings_only)
            row2.addWidget(self.save_btn)
            self.refresh_panel_adv_btn = QPushButton("Обновить данные панели")
            self.refresh_panel_adv_btn.clicked.connect(self._refresh_panel_advertisement)
            self.refresh_panel_adv_btn.setAccessibleName("Кнопка обновления данных веб панели")
            self.refresh_panel_adv_btn.setAccessibleDescription(
                "Считывает актуальные host/port/путь веб-панели из настроек панели и обновляет поля рекламы."
            )
            row2.addWidget(self.refresh_panel_adv_btn)

            self.refresh_now_btn = QPushButton("Отправить поиск сейчас")
            self.refresh_now_btn.clicked.connect(self._refresh_now)
            row2.addWidget(self.refresh_now_btn)
            self.cleanup_btn = QPushButton("Очистить устаревшие")
            self.cleanup_btn.clicked.connect(self._cleanup_stale)
            row2.addWidget(self.cleanup_btn)

            row3 = QHBoxLayout()
            row3.setSpacing(6)
            action_layout.addLayout(row3)
            self.clear_btn = QPushButton("Очистить список")
            self.clear_btn.clicked.connect(self._clear_peers)
            row3.addWidget(self.clear_btn)
            self.open_panel_btn = QPushButton("Открыть панель узла")
            self.open_panel_btn.clicked.connect(self._open_selected_panel)
            self.open_panel_btn.setAccessibleName("Кнопка открытия панели выбранного узла")
            self.open_panel_btn.setAccessibleDescription("Открывает веб-панель выбранного в таблице узла. Горячая клавиша: Enter в таблице.")
            self.open_panel_btn.setEnabled(False)
            row3.addWidget(self.open_panel_btn)

            self.info_lbl = QLabel(
                "Подсказка: для Windows добавь правило Firewall для AutoCraft/порта UDP, иначе узлы могут не видеть друг друга."
            )
            self.info_lbl.setWordWrap(True)
            action_layout.addWidget(self.info_lbl)

            right_col.addWidget(action_box)
            right_col.addStretch(1)

            # Таблица узлов
            self.table = QTableWidget(0, 9, self)
            self.table.setHorizontalHeaderLabels([
                "Имя",
                "Приложение",
                "IP",
                "Роль",
                "Хост",
                "Версия",
                "Источник",
                "Последний пакет",
                "Веб-панель",
            ])
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setSelectionMode(QTableWidget.SingleSelection)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            header_view = self.table.horizontalHeader()
            header_view.setSectionResizeMode(QHeaderView.ResizeToContents)
            header_view.setStretchLastSection(True)
            self.table.itemDoubleClicked.connect(self._open_selected_panel)
            self.table.installEventFilter(self)
            self.table.setAccessibleName("Таблица найденных узлов AutoCraft")
            self.table.setAccessibleDescription(
                "Список обнаруженных в локальной сети экземпляров AutoCraft-Bot. "
                "Навигация: Tab чтобы перейти в таблицу, стрелки вверх/вниз для выбора строки, "
                "Enter чтобы открыть веб-панель выбранного узла."
            )
            self.table.setFocusPolicy(Qt.StrongFocus)
            try:
                # Чтобы Tab не прыгал по ячейкам (это плохо для скринридера), а уходил на следующий контрол.
                self.table.setTabKeyNavigation(False)
            except Exception:
                pass
            try:
                self.table.setAlternatingRowColors(True)
            except Exception:
                pass
            self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
            root.addWidget(self.table, 1)

            # Лог
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setPlaceholderText("Лог LAN автообнаружения")
            self.log_view.setTextInteractionFlags(Qt.TextSelectableByKeyboard | Qt.TextSelectableByMouse)
            self.log_view.setFocusPolicy(Qt.StrongFocus)
            self.log_view.installEventFilter(self)
            try:
                self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            except Exception:
                pass
            root.addWidget(self.log_view, 1)

            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            close_btn = buttons.button(QDialogButtonBox.Close)
            close_btn.setText("Закрыть")
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

            self._timer = QTimer(self)
            self._timer.setInterval(700)
            self._timer.timeout.connect(self._poll_backend)
            self._timer.start()

            self._load_into_form()
            self._poll_backend(force=True)

        # ---- form <-> settings ----
        def _load_into_form(self) -> None:
            s = self._backend.load_settings(force_reload=True)
            self._apply_settings_to_form(s)
            self._last_settings_signature = self._make_settings_signature(s)

        def _make_settings_signature(self, settings: Dict[str, Any]) -> tuple:
            return (
                str(settings.get("mode") or "multi"),
                bool(settings.get("enabled_on_start")),
                str(settings.get("instance_name") or ""),
                str(settings.get("service_name") or ""),
                str(settings.get("app_version") or ""),
                int(settings.get("udp_port") or _DEFAULT_PORT),
                str(settings.get("multicast_group") or _DEFAULT_GROUP),
                bool(settings.get("multicast_enabled")),
                bool(settings.get("broadcast_enabled")),
                float(settings.get("discover_interval_sec") or 5.0),
                float(settings.get("announce_interval_sec") or 10.0),
                float(settings.get("peer_timeout_sec") or 30.0),
                bool(settings.get("advertise_panel")),
                str(settings.get("panel_scheme") or "http"),
                int(settings.get("panel_port") or 5212),
                str(settings.get("panel_path") or "/"),
                str(settings.get("panel_host_override") or ""),
            )

        def _apply_settings_to_form(self, s: Dict[str, Any]) -> None:
            idx = max(0, self.mode_combo.findData(s.get("mode") or "multi"))
            self.mode_combo.setCurrentIndex(idx)
            self.enabled_on_start_cb.setChecked(bool(s.get("enabled_on_start")))
            self.instance_edit.setText(str(s.get("instance_name") or ""))
            self.service_name_edit.setText(str(s.get("service_name") or "AutoCraft-Bot"))
            self.version_edit.setText(str(s.get("app_version") or ""))
            self.udp_port_spin.setValue(int(s.get("udp_port") or _DEFAULT_PORT))
            self.multicast_group_edit.setText(str(s.get("multicast_group") or _DEFAULT_GROUP))
            self.multicast_cb.setChecked(bool(s.get("multicast_enabled")))
            self.broadcast_cb.setChecked(bool(s.get("broadcast_enabled")))
            self.discover_interval_spin.setValue(float(s.get("discover_interval_sec") or 5.0))
            self.announce_interval_spin.setValue(float(s.get("announce_interval_sec") or 10.0))
            self.peer_timeout_spin.setValue(float(s.get("peer_timeout_sec") or 30.0))
            self.advertise_panel_cb.setChecked(bool(s.get("advertise_panel")))
            sidx = max(0, self.panel_scheme_combo.findData(str(s.get("panel_scheme") or "http")))
            self.panel_scheme_combo.setCurrentIndex(sidx)
            self.panel_port_spin.setValue(int(s.get("panel_port") or 5212))
            self.panel_path_edit.setText(str(s.get("panel_path") or "/"))
            self.panel_host_override_edit.setText(str(s.get("panel_host_override") or ""))

        def _sync_settings_from_backend_if_needed(self) -> None:
            try:
                if self._task and self._task.isRunning():
                    return
            except Exception:
                pass

            try:
                latest = self._backend.load_settings(force_reload=True)
            except Exception:
                return

            sig = self._make_settings_signature(latest)
            if sig == self._last_settings_signature:
                return

            try:
                self._apply_settings_to_form(latest)
                self._last_settings_signature = sig
                self._append_log("[LAN] Настройки обновлены из внешнего источника (синхронизация GUI/Web).")
            except Exception:
                pass

        def _collect_form(self) -> Dict[str, Any]:
            current = self._backend.load_settings()
            current.update(
                {
                    "mode": self.mode_combo.currentData() or "multi",
                    "enabled_on_start": self.enabled_on_start_cb.isChecked(),
                    "instance_name": self.instance_edit.text().strip(),
                    "service_name": self.service_name_edit.text().strip(),
                    "app_version": self.version_edit.text().strip(),
                    "udp_port": int(self.udp_port_spin.value()),
                    "multicast_group": self.multicast_group_edit.text().strip() or _DEFAULT_GROUP,
                    "multicast_enabled": self.multicast_cb.isChecked(),
                    "broadcast_enabled": self.broadcast_cb.isChecked(),
                    "discover_interval_sec": float(self.discover_interval_spin.value()),
                    "announce_interval_sec": float(self.announce_interval_spin.value()),
                    "peer_timeout_sec": float(self.peer_timeout_spin.value()),
                    "advertise_panel": self.advertise_panel_cb.isChecked(),
                    "panel_scheme": self.panel_scheme_combo.currentData() or "http",
                    "panel_port": int(self.panel_port_spin.value()),
                    "panel_path": self.panel_path_edit.text().strip() or "/",
                    "panel_host_override": self.panel_host_override_edit.text().strip(),
                }
            )
            return current

        # ---- background task helper ----
        def _run_task(self, fn: Callable[[Callable[[str], None]], Any], success_text: str = "") -> None:
            if self._task and self._task.isRunning():
                return
            self._set_controls_enabled(False)
            self._task = BackgroundTask(fn, self)
            self._task.progress.connect(self._append_log)

            def _finished(_, err):
                self._set_controls_enabled(True)
                if err:
                    self._append_log(f"[ОШИБКА] {err}")
                    self._set_status(f"Ошибка: {err}")
                else:
                    if success_text:
                        self._append_log(success_text)
                        self._set_status(success_text)
                self._poll_backend(force=True)
                try:
                    QApplication.beep()
                except Exception:
                    pass

            self._task.finished.connect(_finished)
            self._task.start()

        def _set_controls_enabled(self, enabled: bool) -> None:
            for w in (
                self.start_btn,
                self.stop_btn,
                self.restart_btn,
                self.save_btn,
                self.refresh_panel_adv_btn,
                self.refresh_now_btn,
                self.cleanup_btn,
                self.clear_btn,
                self.open_panel_btn,
            ):
                w.setEnabled(enabled)

        # ---- actions ----
        def _save_settings_only(self) -> None:
            try:
                self._backend.save_settings(self._collect_form())
                try:
                    latest = self._backend.load_settings(force_reload=True)
                    self._last_settings_signature = self._make_settings_signature(latest)
                except Exception:
                    pass
                self._set_status("Настройки сохранены.")
            except Exception as e:
                self._append_log(f"[ОШИБКА] {e}")
                self._set_status(f"Ошибка сохранения: {e}")

        def _refresh_panel_advertisement(self) -> None:
            """Подтягивает актуальные параметры веб-панели (scheme/port/path/host override) и обновляет форму."""
            try:
                s = self._backend.refresh_panel_advertisement_settings(persist=True, silent=False)
                # Обновим поля формы только для блока панели, чтобы не сбить ввод пользователя в других полях.
                sidx = max(0, self.panel_scheme_combo.findData(str(s.get("panel_scheme") or "http")))
                self.panel_scheme_combo.setCurrentIndex(sidx)
                self.panel_port_spin.setValue(int(s.get("panel_port") or 5212))
                self.panel_path_edit.setText(str(s.get("panel_path") or "/"))
                self.panel_host_override_edit.setText(str(s.get("panel_host_override") or ""))
                try:
                    self._last_settings_signature = self._make_settings_signature(s)
                except Exception:
                    pass
                self._set_status("Данные веб-панели обновлены.")
            except Exception as e:
                self._append_log(f"[ОШИБКА] {e}")
                self._set_status(f"Ошибка обновления панели: {e}")


        def _start_service(self) -> None:
            settings = self._collect_form()
            self._run_task(lambda cb: self._task_start(settings, cb), "Сервис LAN обнаружения запущен.")

        def _stop_service(self) -> None:
            self._run_task(lambda cb: self._task_stop(cb), "Сервис LAN обнаружения остановлен.")

        def _restart_service(self) -> None:
            settings = self._collect_form()
            self._run_task(lambda cb: self._task_restart(settings, cb), "Сервис перезапущен с новыми настройками.")

        def _refresh_now(self) -> None:
            try:
                self._backend.save_settings(self._collect_form())
                self._backend.refresh_now()
                self._set_status("Пакет поиска отправлен.")
            except Exception as e:
                self._append_log(f"[ОШИБКА] {e}")
                self._set_status(f"Ошибка отправки: {e}")

        def _cleanup_stale(self) -> None:
            try:
                removed = self._backend.cleanup_stale()
                self._set_status(f"Очищено устаревших узлов: {removed}")
                self._refresh_table_if_needed(force=True)
            except Exception as e:
                self._append_log(f"[ОШИБКА] {e}")

        def _clear_peers(self) -> None:
            self._backend.clear_peers()
            self._refresh_table_if_needed(force=True)
            self._set_status("Список узлов очищен.")

        def _open_selected_panel(self):
            peer = self._current_peer()
            if not peer:
                self._set_status("Не выбран узел.")
                return
            url = str(peer.get("panel_url") or "").strip()
            if not url:
                self._set_status("У выбранного узла не опубликована веб-панель.")
                return
            url = _normalize_panel_url_for_browser(url)
            try:
                webbrowser.open(url)
                self._set_status(f"Открываю: {url}")
            except Exception as e:
                self._append_log(f"[ОШИБКА] Не удалось открыть браузер: {e}")

        def _task_start(self, settings: Dict[str, Any], progress: Callable[[str], None]) -> None:
            progress("Сохраняю настройки...")
            self._backend.save_settings(settings)
            progress("Запускаю сетевой сервис...")
            self._backend.start_service(settings)

        def _task_stop(self, progress: Callable[[str], None]) -> None:
            progress("Останавливаю сетевой сервис...")
            self._backend.stop_service()

        def _task_restart(self, settings: Dict[str, Any], progress: Callable[[str], None]) -> None:
            progress("Сохраняю настройки...")
            self._backend.save_settings(settings)
            progress("Перезапускаю сетевой сервис...")
            self._backend.restart_service(settings)

        # ---- table + polling ----
        def _on_table_selection_changed(self) -> None:
            peer = self._current_peer()
            if not peer:
                try:
                    self.open_panel_btn.setEnabled(False)
                except Exception:
                    pass
                try:
                    self.selected_lbl.setText("Выбор: узел не выбран.")
                except Exception:
                    pass
                return

            inst = str(peer.get("instance_name") or peer.get("hostname") or peer.get("ip") or "узел")
            ip = str(peer.get("ip") or "")
            role = str(peer.get("role") or "")
            panel = str(peer.get("panel_url") or "").strip() or "нет"
            try:
                self.open_panel_btn.setEnabled(True)
            except Exception:
                pass
            try:
                self.selected_lbl.setText(f"Выбор: {inst} ({ip}) роль={role}. Панель: {panel}")
            except Exception:
                pass

        def _make_peers_signature(self, peers: List[Dict[str, Any]]) -> tuple:
            # Сигнатура нужна, чтобы не перерисовывать таблицу каждые 700 мс и не мучить скринридер.
            return tuple(
                (
                    str(p.get("node_id") or ""),
                    str(p.get("instance_name") or ""),
                    str(p.get("app") or ""),
                    str(p.get("ip") or ""),
                    str(p.get("role") or ""),
                    str(p.get("hostname") or ""),
                    str(p.get("app_version") or ""),
                    str(p.get("source") or ""),
                    str(p.get("last_seen_text") or ""),
                    str(p.get("panel_url") or ""),
                )
                for p in (peers or [])
            )

        def _refresh_table_if_needed(self, force: bool = False) -> None:
            try:
                peers = self._backend.get_peers()
            except Exception:
                peers = []

            peer_ids = tuple(str(p.get("node_id") or "") for p in peers)
            sig = self._make_peers_signature(peers)

            if not force:
                # 1) Если ничего не изменилось — вообще не трогаем таблицу.
                if sig == self._last_peers_signature:
                    self._peers = peers
                    return

                # 2) Если пользователь сейчас стоит фокусом в таблице и состав узлов не менялся,
                #    не перерисовываем (иначе NVDA/другие скринридеры начинают "дёргаться").
                try:
                    if self.table.hasFocus() and peer_ids == self._last_peer_ids:
                        self._peers = peers
                        return
                except Exception:
                    pass

            self._refresh_table(peers)
            self._last_peers_signature = sig
            self._last_peer_ids = peer_ids

        def _current_peer(self) -> Optional[Dict[str, Any]]:
            sel = self.table.selectionModel().selectedRows()
            if not sel:
                return None
            row = sel[0].row()
            if 0 <= row < len(self._peers):
                return self._peers[row]
            return None

        def _poll_backend(self, force: bool = False) -> None:
            for line in self._backend.poll_logs():
                self._append_log(line)
            self._sync_settings_from_backend_if_needed()
            self._refresh_table_if_needed()
            self._refresh_status_line(force=force)

        def _refresh_status_line(self, force: bool = False) -> None:
            st = self._backend.get_status()
            running = bool(st.get("running"))
            txt = (
                f"Сервис: {'запущен' if running else 'остановлен'} | "
                f"Режим: {st.get('mode') or '-'} | "
                f"Узлов: {st.get('peer_count', 0)} | "
                f"Порт: {st.get('udp_port') or '-'} | "
                f"Multicast: {'да' if st.get('multicast_joined') else 'нет'}"
            )
            try:
                directed_count = len(st.get('directed_broadcasts') or [])
            except Exception:
                directed_count = 0
            if directed_count:
                txt += f" | Directed broadcast: {directed_count}"
            err = str(st.get("last_error") or "").strip()
            if err:
                txt += f" | Ошибка: {err}"
            self.status_lbl.setText(txt)
            # Пока выполняется фон-задача, управление уже отключено в _run_task.
            try:
                if self._task and self._task.isRunning():
                    return
            except Exception:
                pass

            try:
                self.start_btn.setEnabled(not running)
            except Exception:
                pass
            self.stop_btn.setEnabled(running)
            self.refresh_now_btn.setEnabled(running)
            self.cleanup_btn.setEnabled(running)
            # Кнопки настройки доступны всегда (они не требуют запущенного сервиса).
            try:
                self.save_btn.setEnabled(True)
                self.refresh_panel_adv_btn.setEnabled(True)
                self.restart_btn.setEnabled(True)
            except Exception:
                pass
            if force:
                pass

        def _refresh_table(self, peers: Optional[List[Dict[str, Any]]] = None) -> None:
            prev_node = None
            prev_col = 0
            prev_row = -1

            try:
                cur_peer = self._current_peer()
                if cur_peer:
                    prev_node = cur_peer.get("node_id")
                cur_item = self.table.currentItem()
                if cur_item is not None:
                    prev_row = int(cur_item.row())
                    prev_col = int(cur_item.column())
            except Exception:
                pass

            if peers is None:
                try:
                    peers = self._backend.get_peers()
                except Exception:
                    peers = []
            self._peers = peers

            # Перерисовка таблицы может мешать скринридеру, поэтому делаем это аккуратно.
            self.table.setUpdatesEnabled(False)
            self.table.blockSignals(True)
            try:
                self.table.setRowCount(len(peers))

                for row, p in enumerate(peers):
                    values = [
                        str(p.get("instance_name") or ""),
                        str(p.get("app") or ""),
                        str(p.get("ip") or ""),
                        str(p.get("role") or ""),
                        str(p.get("hostname") or ""),
                        str(p.get("app_version") or ""),
                        str(p.get("source") or ""),
                        str(p.get("last_seen_text") or _fmt_dt(float(p.get("last_seen") or 0))),
                        str(p.get("panel_url") or ""),
                    ]
                    for col, text in enumerate(values):
                        it = QTableWidgetItem(text)
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                        self.table.setItem(row, col, it)

                try:
                    self.table.resizeColumnsToContents()
                except Exception:
                    pass

                # Восстановим выбор/текущую строку (важно для стрелочной навигации).
                target_row = None
                if prev_node:
                    for r, p in enumerate(peers):
                        if p.get("node_id") == prev_node:
                            target_row = r
                            break

                if target_row is None and 0 <= prev_row < len(peers):
                    target_row = prev_row

                if target_row is not None and 0 <= int(target_row) < len(peers):
                    try:
                        safe_col = max(0, min(self.table.columnCount() - 1, int(prev_col)))
                        self.table.selectRow(int(target_row))
                        self.table.setCurrentCell(int(target_row), safe_col)
                    except Exception:
                        pass
            finally:
                self.table.blockSignals(False)
                self.table.setUpdatesEnabled(True)

            self._on_table_selection_changed()

        # ---- log helpers ----
        def _append_log(self, text: str) -> None:
            edit = self.log_view
            vsb = edit.verticalScrollBar()  # type: QScrollBar
            try:
                saved_scroll = vsb.value()
                was_at_bottom = saved_scroll >= (vsb.maximum() - 1)
            except Exception:
                saved_scroll = None
                was_at_bottom = True
            try:
                saved_cursor = QTextCursor(edit.textCursor())
            except Exception:
                saved_cursor = None

            edit.appendPlainText(text)

            if self._log_user_scrolling or (not was_at_bottom):
                if saved_cursor is not None:
                    try:
                        edit.setTextCursor(saved_cursor)
                    except Exception:
                        pass
                if saved_scroll is not None:
                    try:
                        vsb.setValue(saved_scroll)
                    except Exception:
                        pass
            else:
                try:
                    edit.moveCursor(QTextCursor.End)
                except Exception:
                    pass
                try:
                    vsb.setValue(vsb.maximum())
                except Exception:
                    pass

        def _set_status(self, text: str) -> None:
            self.status_lbl.setText(text)

        def eventFilter(self, obj, event):
            if obj is self.table and event.type() == QEvent.KeyPress:
                key = event.key()
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    self._open_selected_panel()
                    return True

            if obj is self.log_view:
                if event.type() == QEvent.KeyPress:
                    key = event.key()
                    if key in (Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Home, Qt.Key_Left):
                        self._log_user_scrolling = True
                    elif key in (Qt.Key_End,):
                        self._log_user_scrolling = False
                    else:
                        if not self._log_user_scrolling:
                            vsb = self.log_view.verticalScrollBar()
                            try:
                                self._log_user_scrolling = vsb.value() < vsb.maximum()
                            except Exception:
                                self._log_user_scrolling = True
                elif event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                    vsb = self.log_view.verticalScrollBar()
                    try:
                        self._log_user_scrolling = vsb.value() < vsb.maximum()
                    except Exception:
                        self._log_user_scrolling = True
                elif event.type() == QEvent.FocusOut:
                    self._log_user_scrolling = False

            return super().eventFilter(obj, event)

    globals()["BackgroundTask"] = BackgroundTask
    globals()["LanDiscoveryWindow"] = LanDiscoveryWindow
    _GUI_BUILT = True


# -------------------- открытие окна --------------------

_WINDOWS = weakref.WeakKeyDictionary()


def open_lan_discovery_window(main_window: Optional["QWidget"]) -> None:
    if main_window is None:
        return
    _ensure_gui_built()

    w = _WINDOWS.get(main_window)
    if w is None:
        w = LanDiscoveryWindow(parent=main_window)
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


# -------------------- автостарт сервиса (best effort) --------------------

_AUTOSTART_HOOK_INSTALLED = False
_AUTOSTART_ATTEMPT_LOCK = threading.RLock()


def _get_main_module():
    try:
        import __main__ as main  # type: ignore
        return main
    except Exception:
        return None


def _is_child_process() -> bool:
    """Определяем дочерний процесс watchdog-схемы (как в webpanel-модуле)."""
    try:
        argv = [str(a).strip().lower() for a in (sys.argv or [])]
        if "--child" in argv or any(a.endswith("--child") for a in argv):
            return True
    except Exception:
        pass
    try:
        main = _get_main_module()
        if main is not None:
            for key in ("IS_CHILD", "is_child", "child_process", "WATCHDOG_CHILD", "RUN_CHILD", "run_child"):
                if bool(getattr(main, key, False)):
                    return True
    except Exception:
        pass
    return False


def _should_autostart_in_this_process() -> bool:
    """По умолчанию автозапуск LAN выполняем только в child-процессе."""
    try:
        val = os.environ.get("AUTOCRAFT_LAN_AUTOSTART_ANY_PROCESS", "").strip().lower()
        if val in {"1", "true", "yes", "on"}:
            return True
    except Exception:
        pass
    if __name__ == "__main__":
        return True
    return _is_child_process()


def _autostart_token_attr() -> str:
    return "_autocraft_lan_autostart_done"


def _was_autostart_attempted() -> bool:
    main = _get_main_module()
    if main is None:
        return False
    try:
        return bool(getattr(main, _autostart_token_attr(), False))
    except Exception:
        return False


def _mark_autostart_attempted() -> None:
    main = _get_main_module()
    if main is None:
        return
    try:
        setattr(main, _autostart_token_attr(), True)
    except Exception:
        pass


def _install_mainwindow_autostart_hook() -> None:
    """
    Если модуль импортировали до создания MainWindow, аккуратно вешаем fallback-хук.

    Это не заменяет ранний импорт из gui.py / bot-ok.py, но позволяет безопасно
    инициировать автозапуск после создания главного окна, если хост всё-таки
    импортировал данный модуль заранее.
    """
    global _AUTOSTART_HOOK_INSTALLED
    if _AUTOSTART_HOOK_INSTALLED:
        return

    gui_mod = sys.modules.get("gui")
    if gui_mod is None:
        return

    main_cls = getattr(gui_mod, "MainWindow", None)
    if main_cls is None:
        return

    try:
        already = bool(getattr(main_cls, "_lan_autostart_hooked", False))
    except Exception:
        already = False
    if already:
        _AUTOSTART_HOOK_INSTALLED = True
        return

    original_init = getattr(main_cls, "__init__", None)
    if not callable(original_init):
        return

    def _wrapped_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            try_start_lan_discovery_service(force=True)
        except Exception:
            pass

    try:
        setattr(main_cls, "__init__", _wrapped_init)
        setattr(main_cls, "_lan_autostart_hooked", True)
        _AUTOSTART_HOOK_INSTALLED = True
    except Exception:
        pass


def try_start_lan_discovery_service(force: bool = False) -> bool:
    """
    Best effort запуск LAN-сервиса по config.ini.

    Возвращает True, если была сделана реальная попытка автозапуска в текущем процессе.
    Повторные вызовы в рамках одного запуска процесса тихо игнорируются.
    """
    try:
        if not force and not _should_autostart_in_this_process():
            return False

        with _AUTOSTART_ATTEMPT_LOCK:
            if _was_autostart_attempted():
                return False

            backend = get_lan_discovery_backend()
            settings = backend.load_settings()
            if not _to_bool(settings.get("enabled_on_start"), False):
                return False

            _mark_autostart_attempted()
            backend.try_autostart_from_config()
            return True
    except Exception:
        return False


# Если модуль импортировали рано, пробуем повесить fallback-хук на MainWindow.
try:
    _install_mainwindow_autostart_hook()
except Exception:
    pass


# Автостарт при импорте: только если в config включено enabled_on_start=1.
# Если модуль импортирован рано, а __main__ ещё не полностью готов, backend всё равно
# использует fallback-и и не должен падать.
try:
    try_start_lan_discovery_service()
except Exception:
    pass
