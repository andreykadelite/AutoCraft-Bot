import subprocess
from typing import Dict, List


def query_event_logs(
    log_name: str = "System",
    level: str = "",
    provider: str = "",
    event_id: str = "",
    limit: int = 20,
) -> List[str]:
    """Возвращает список строк событий (минимум для локальной машины)."""
    log_name = log_name or "System"
    limit = max(1, min(int(limit or 20), 200))

    query_parts = []
    if level:
        query_parts.append(f"Level={level}")
    if provider:
        query_parts.append(f"Provider[@Name='{provider}']")
    if event_id:
        query_parts.append(f"EventID={event_id}")

    query = ""
    if query_parts:
        query = "*[(" + " and ".join(query_parts) + ")]"

    cmd = [
        "wevtutil",
        "qe",
        log_name,
        "/f:text",
        f"/c:{limit}",
        "/rd:true",
    ]
    if query:
        cmd.append(f"/q:{query}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            return ["Записей не найдено."]
        return output.splitlines()
    except Exception as e:
        return [f"Ошибка чтения журнала: {e}"]


def query_event_logs_op(
    log_name: str = "System",
    level: str = "",
    provider: str = "",
    event_id: str = "",
    limit: int = 20,
) -> Dict[str, object]:
    data = query_event_logs(log_name, level, provider, event_id, limit)
    return {"ok": True, "data": data, "stdout": "", "stderr": ""}
