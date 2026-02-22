import threading
from typing import Optional

from waitress import create_server

from .runtime import PanelRuntime
from .utils import get_lan_ip

_DEFAULT_WAITRESS_THREADS = 8


class WebPanelServer:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.runtime = PanelRuntime(base_dir)
        self._lock = threading.Lock()
        self._server = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            app = self.runtime.ensure_app()
            cfg = self.runtime.config
            self._server = create_server(
                app,
                host=cfg.host,
                port=cfg.port,
                threads=_DEFAULT_WAITRESS_THREADS,
            )
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.close()
                except Exception:
                    pass
            self._server = None
            self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def url(self) -> str:
        cfg = self.runtime.config
        host = cfg.host
        if host in ("0.0.0.0", "::"):
            host = get_lan_ip()
        return f"http://{host}:{cfg.port}"
