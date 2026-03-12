import os
import sys
import time
from pathlib import Path

from web_dashboard.config import get_panel_start_block_reason
from web_dashboard.server import WebPanelServer


def guess_base_dir() -> str:
    env = os.environ.get("PANEL_BASE_DIR")
    if env:
        return env
    if "NUITKA_ONEFILE_PARENT" in os.environ:
        return os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return str(Path.cwd())


def main() -> None:
    base_dir = guess_base_dir()
    block_reason = get_panel_start_block_reason(base_dir)
    if block_reason:
        print(block_reason)
        print("Сначала создайте первого Super Admin (например, через scripts/init_first_run.py).")
        return
    srv = WebPanelServer(base_dir)
    srv.start()
    print(f"Панель запущена: {srv.url()}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Остановка...")
        srv.stop()


if __name__ == "__main__":
    main()
