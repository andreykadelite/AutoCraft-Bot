"""Web dashboard package for AutoCraft bot."""

from .config import PanelConfig, load_config, save_config, update_user_password
from .server import WebPanelServer

try:
    from .web_plugins.win_run import plugin as _winrun_plugin  # noqa: F401
except Exception:
    pass

try:
    from .web_plugins.notify_center import plugin as _notify_center_plugin  # noqa: F401
except Exception:
    pass

try:
    from .web_plugins.win_tts import plugin as _win_tts_plugin  # noqa: F401
except Exception:
    pass

__all__ = [
    "PanelConfig",
    "load_config",
    "save_config",
    "update_user_password",
    "WebPanelServer",
]
