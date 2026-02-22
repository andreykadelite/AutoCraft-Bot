import subprocess
from typing import List, Dict


def list_tasks() -> List[Dict[str, str]]:
    cmd = ["schtasks", "/Query", "/FO", "LIST", "/V"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        output = result.stdout.strip()
        tasks: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        for line in output.splitlines():
            if not line.strip():
                if current:
                    tasks.append(current)
                    current = {}
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                current[key.strip()] = value.strip()
        if current:
            tasks.append(current)
        return tasks
    except Exception:
        return []


def run_task(name: str) -> dict:
    try:
        cmd = ["schtasks", "/Run", "/TN", name]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        ok = result.returncode == 0
        return {"ok": ok, "stdout": output.strip(), "stderr": "" if ok else output.strip()}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
