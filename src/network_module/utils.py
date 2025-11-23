import math
import subprocess
from typing import List, Tuple

MAX_MSG_LEN = 3800


def format_bytes(value: float) -> str:
    """Форматирование байтов в удобный вид."""
    if value <= 0:
        return "0 Б"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    power = min(int(math.log(value, 1024)), len(units) - 1)
    converted = value / (1024 ** power)
    return f"{converted:.2f} {units[power]}"


def chunk_text(text: str, limit: int = MAX_MSG_LEN) -> List[str]:
    """Делит длинный текст на части по лимиту Telegram."""
    if not text:
        return [""]
    return [text[i: i + limit] for i in range(0, len(text), limit)]


def run_shell_command(cmd: str) -> Tuple[str, str, int]:
    """Запускает команду в shell и возвращает stdout, stderr, код выхода."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return stdout, stderr, result.returncode
