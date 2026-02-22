import os
import socket
from pathlib import Path
from typing import Iterable


def get_package_root() -> Path:
    return Path(__file__).resolve().parent


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def tail_file(path: Path, lines: int = 100) -> str:
    if not path.exists():
        return "Лог-файл не найден."
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            data = f.readlines()
    except Exception:
        return "Не удалось прочитать лог-файл."

    if lines <= 0:
        return ""
    return "".join(data[-lines:]).strip()


def get_lan_ip() -> str:
    # Безопасный способ получить локальный IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    v = str(value).strip().lower()
    if v in ("1", "true", "yes", "y", "on", "да"):
        return True
    if v in ("0", "false", "no", "n", "off", "нет"):
        return False
    return default


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def safe_join(parts: Iterable[str]) -> str:
    return "/".join([p.strip("/") for p in parts if p is not None])
