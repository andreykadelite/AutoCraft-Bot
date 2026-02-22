import psutil
from typing import Dict, List


def list_processes() -> Dict[str, object]:
    processes: List[Dict[str, object]] = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cpu": info.get("cpu_percent"),
                    "mem": info.get("memory_percent"),
                }
            )
        except Exception:
            continue

    processes.sort(key=lambda x: (x.get("cpu") or 0), reverse=True)
    return {"ok": True, "data": processes, "stdout": "", "stderr": ""}


def kill_process(pid: int) -> Dict[str, object]:
    try:
        proc = psutil.Process(int(pid))
        proc.terminate()
        return {"ok": True, "stdout": f"Процесс {pid} завершён", "stderr": ""}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
