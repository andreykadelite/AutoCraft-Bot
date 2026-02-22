from typing import Dict, List

import psutil


def list_services() -> Dict[str, object]:
    services: List[Dict[str, str]] = []
    if not hasattr(psutil, "win_service_iter"):
        return {"ok": False, "stdout": "", "stderr": "Доступно только на Windows."}

    for svc in psutil.win_service_iter():
        try:
            services.append(
                {
                    "name": svc.name(),
                    "display_name": svc.display_name(),
                    "status": svc.status(),
                }
            )
        except Exception:
            continue

    services.sort(key=lambda x: (x.get("name") or "").lower())
    return {"ok": True, "data": services, "stdout": "", "stderr": ""}


def _get_service(name: str):
    if not hasattr(psutil, "win_service_get"):
        raise RuntimeError("Доступно только на Windows")
    return psutil.win_service_get(name)


def start_service(name: str) -> Dict[str, object]:
    try:
        svc = _get_service(name)
        svc.start()
        return {"ok": True, "stdout": f"Служба {name} запущена", "stderr": ""}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def stop_service(name: str) -> Dict[str, object]:
    try:
        svc = _get_service(name)
        svc.stop()
        return {"ok": True, "stdout": f"Служба {name} остановлена", "stderr": ""}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def restart_service(name: str) -> Dict[str, object]:
    try:
        svc = _get_service(name)
        svc.stop()
        svc.start()
        return {"ok": True, "stdout": f"Служба {name} перезапущена", "stderr": ""}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
