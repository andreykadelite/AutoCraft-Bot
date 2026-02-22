import subprocess
import psutil


def list_interfaces() -> dict:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    interfaces = []
    for name, info in stats.items():
        addresses = [a.address for a in addrs.get(name, [])]
        interfaces.append(
            {
                "name": name,
                "isup": info.isup,
                "speed": info.speed,
                "addresses": addresses,
            }
        )
    return {"ok": True, "data": interfaces, "stdout": "", "stderr": ""}


def disable_interface(name: str) -> dict:
    try:
        cmd = ["netsh", "interface", "set", "interface", name, "admin=DISABLED"]
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
