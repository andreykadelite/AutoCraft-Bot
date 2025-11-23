import socket
import time
from typing import Dict, List, Optional

import psutil

from .utils import format_bytes, run_shell_command


def traffic_report() -> str:
    total = psutil.net_io_counters()
    lines = [
        "📡 Сводка по трафику",
        f"Системный аптайм: {round(time.time() - psutil.boot_time())} c",
        f"Всего отправлено: {format_bytes(total.bytes_sent)}, получено: {format_bytes(total.bytes_recv)}",
        f"Пакеты: {total.packets_sent} / {total.packets_recv}, ошибки: {total.errout} / {total.errin}",
        "",
        "Интерфейсы:",
    ]

    pernic = psutil.net_io_counters(pernic=True)
    if not pernic:
        return "Не удалось получить статистику интерфейсов."

    for name, stat in pernic.items():
        lines.append(
            f"- {name}: отправлено {format_bytes(stat.bytes_sent)}, получено {format_bytes(stat.bytes_recv)}, "
            f"пакеты {stat.packets_sent}/{stat.packets_recv}, ошибки {stat.errout}/{stat.errin}"
        )

    return "\n".join(lines)


def connections_overview(limit: int = 60) -> str:
    conns = psutil.net_connections(kind="inet")
    if not conns:
        return "Активные сетевые соединения не найдены."

    rows = []
    for c in conns:
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        status = c.status or "N/A"
        pid = c.pid or 0
        try:
            pname = psutil.Process(pid).name() if pid else "?"
        except Exception:
            pname = "?"
        proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
        rows.append((status, laddr, raddr, pid, pname, proto))

    rows.sort(key=lambda x: (0 if x[0] == "LISTEN" else 1, x[0], x[3], x[1]))
    lines = ["🔗 Активные соединения (макс. {0} строк)".format(limit)]
    header = f"{'Статус':<12} | {'Протокол':<8} | {'Локальный адрес':<22} | {'Удалённый адрес':<22} | {'PID':>6} | Имя процесса"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows[:limit]:
        status, laddr, raddr, pid, pname, proto = row
        lines.append(f"{status:<12} | {proto:<8} | {laddr:<22} | {raddr:<22} | {pid:>6} | {pname}")

    if len(rows) > limit:
        lines.append(f"... всего записей: {len(rows)}")
    return "\n".join(lines)


def listening_entries(limit: int = 80) -> List[Dict[str, Optional[str]]]:
    entries: List[Dict[str, Optional[str]]] = []
    conns = psutil.net_connections(kind="inet")
    for c in conns:
        if c.status not in {"LISTEN", "NONE"}:
            continue
        if not c.laddr:
            continue
        proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
        port = c.laddr.port
        pid = c.pid or 0
        try:
            pname = psutil.Process(pid).name() if pid else "?"
        except Exception:
            pname = "?"
        entries.append(
            {
                "port": port,
                "proto": proto,
                "pid": pid,
                "name": pname,
                "addr": f"{c.laddr.ip}:{port}",
            }
        )
    entries.sort(key=lambda x: (x["port"], x["proto"], x["pid"]))
    if limit:
        return entries[:limit]
    return entries


def list_listening_ports(limit: int = 80) -> str:
    conns = listening_entries(limit=limit)
    if not conns:
        return "Слушающие порты не найдены."

    lines = ["🛡️ Слушающие порты:"]
    for c in conns:
        port = c["port"]
        proto = c["proto"]
        pid = c["pid"]
        pname = c["name"]
        addr = c["addr"]
        lines.append(f"{addr:<25} {proto:<4} | PID {pid:>6} | {pname}")
    return "\n".join(lines)


def port_details(port: int) -> str:
    conns = [c for c in psutil.net_connections(kind="inet") if c.laddr and c.laddr.port == port]
    if not conns:
        return f"Порт {port} не используется."

    lines = [f"Информация по порту {port}:"]
    for c in conns:
        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        status = c.status or "N/A"
        proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
        pid = c.pid or 0
        try:
            pname = psutil.Process(pid).name() if pid else "?"
        except Exception:
            pname = "?"
        lines.append(f"{status:<12} | {proto:<4} | {laddr:<22} | {raddr:<22} | PID {pid:>6} | {pname}")
    return "\n".join(lines)


def kill_process_on_port(port: int) -> str:
    conns = [c for c in psutil.net_connections(kind="inet") if c.laddr and c.laddr.port == port and c.pid]
    if not conns:
        return f"Процесс, использующий порт {port}, не найден."

    killed = []
    errors: List[str] = []
    for c in conns:
        if not c.pid:
            continue
        try:
            proc = psutil.Process(c.pid)
            proc_name = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
            killed.append(f"{c.pid} ({proc_name})")
        except Exception as e:
            errors.append(f"PID {c.pid}: {e}")

    lines = [f"Попытка завершения процессов на порту {port}:"]
    if killed:
        lines.append("Завершены: " + ", ".join(killed))
    if errors:
        lines.append("Ошибки: " + "; ".join(errors))
    return "\n".join(lines)


def adapter_statuses() -> str:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    if not stats:
        return "Не удалось получить список адаптеров."

    lines = ["🖧 Сетевые адаптеры:"]
    for name, st in stats.items():
        speed = f"{st.speed} Мбит/с" if st.speed else "скорость н/д"
        status = "Включен" if st.isup else "Выключен"
        mtu = st.mtu
        addr_info = []
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                addr_info.append(f"IPv4: {a.address}/{a.netmask}")
            elif a.family == socket.AF_INET6:
                addr_info.append(f"IPv6: {a.address}")
            elif a.family == psutil.AF_LINK:
                addr_info.append(f"MAC: {a.address}")
        addr_text = "; ".join(addr_info) if addr_info else "адреса не найдены"
        lines.append(f"- {name}: {status}, {speed}, MTU {mtu}, {addr_text}")
    return "\n".join(lines)


def adapter_names() -> List[str]:
    stats = psutil.net_if_stats()
    if not stats:
        return []
    return sorted(stats.keys())


def enable_adapter(name: str) -> str:
    cmd = f'netsh interface set interface name="{name}" admin=ENABLED'
    stdout, stderr, code = run_shell_command(cmd)
    if code == 0:
        return f"Адаптер «{name}» включен."
    return f"Не удалось включить «{name}». Код {code}. {stderr or stdout}"


def disable_adapter(name: str) -> str:
    cmd = f'netsh interface set interface name="{name}" admin=DISABLED'
    stdout, stderr, code = run_shell_command(cmd)
    if code == 0:
        return f"Адаптер «{name}» отключен."
    return f"Не удалось отключить «{name}». Код {code}. {stderr or stdout}"


def running_processes(limit: int = 80) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for p in psutil.process_iter(attrs=["pid", "name", "exe"]):
        try:
            info = p.info
            pid = info.get("pid")
            name = info.get("name") or "Без имени"
            exe = info.get("exe") or ""
            items.append({"pid": pid, "name": name, "exe": exe})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    items.sort(key=lambda x: (x["name"].lower(), x["pid"]))
    return items[:limit]


def quick_scan(host: str, ports: List[int], timeout: float = 0.5) -> str:
    results = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            try:
                sock.connect((host, port))
                results.append((port, "открыт"))
            except Exception:
                results.append((port, "закрыт"))
    lines = [f"Сканирование {host}:", "Порт | Статус"]
    for port, state in results:
        lines.append(f"{port:<6} {state}")
    return "\n".join(lines)


def full_ports_report() -> str:
    entries = listening_entries(limit=0)
    if not entries:
        return "Слушающие порты не найдены."
    lines = ["Полный список слушающих портов:"]
    for e in entries:
        lines.append(f"{e['port']}/{e['proto']:<3} | PID {e['pid']:>6} | {e['name']} | {e['addr']}")
    return "\n".join(lines)


def network_overview() -> str:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    total = psutil.net_io_counters()
    lines = [
        "ℹ️ Информация о сети",
        f"Всего отправлено: {format_bytes(total.bytes_sent)}, получено: {format_bytes(total.bytes_recv)}",
        "",
        "Интерфейсы и адреса:",
    ]
    for name, st in stats.items():
        status = "Включен" if st.isup else "Выключен"
        speed = f"{st.speed} Мбит/с" if st.speed else "скорость н/д"
        lines.append(f"- {name}: {status}, {speed}, MTU {st.mtu}")
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                lines.append(f"    IPv4: {a.address} / {a.netmask}")
            elif a.family == socket.AF_INET6:
                lines.append(f"    IPv6: {a.address}")
            elif a.family == psutil.AF_LINK:
                lines.append(f"    MAC: {a.address}")
    return "\n".join(lines)
