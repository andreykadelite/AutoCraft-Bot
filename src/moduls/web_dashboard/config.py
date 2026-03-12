import configparser
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .utils import ensure_dir, parse_bool, parse_int, tail_file

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_RETENTION_DAYS = 7
DEFAULT_OVERVIEW_REFRESH_SECONDS = 10
DEBUG_LOG_SECTION = "panel_debug_logging"
DEBUG_LOG_LEVELS = ("MAX", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_DEBUG_LOG_ENABLED = False
DEFAULT_DEBUG_LOG_LEVEL = "MAX"
DEFAULT_DEBUG_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_DEBUG_LOG_BACKUP_COUNT = 7
_DEBUG_LOG_MIN_BYTES = 64 * 1024
_DEBUG_LOG_MAX_BYTES = 512 * 1024 * 1024
_DEBUG_LOG_MIN_BACKUP_COUNT = 1
_DEBUG_LOG_MAX_BACKUP_COUNT = 30
_DEBUG_LOG_FILENAME = "panel_debug.log"
_RESOURCE_DIRS = ("scripts", "tests", "migrations")
_RESOURCE_SYNC_LOCK = threading.Lock()
_RESOURCE_SYNC_DONE: set[str] = set()
_DEFAULT_PANEL_ROLES = ("Super Admin", "Admin", "Operator", "Viewer", "Auditor")
_ROLE_ALIAS_TO_CANONICAL = {
    "super admin": "Super Admin",
    "superadmin": "Super Admin",
    "суперадмин": "Super Admin",
    "супер админ": "Super Admin",
    "admin": "Admin",
    "administrator": "Admin",
    "админ": "Admin",
    "администратор": "Admin",
    "operator": "Operator",
    "оператор": "Operator",
    "viewer": "Viewer",
    "наблюдатель": "Viewer",
    "auditor": "Auditor",
    "аудитор": "Auditor",
}
_USER_SECRET_TABLE = "panel_user_credentials"
_MIN_PASSWORD_LEN = 6
_PASSWORD_STATE_SAVED = "saved"
_PASSWORD_STATE_HASH_ONLY = "hash_only"
_PASSWORD_STATE_MISSING = "missing"
_FALLBACK_FILES: dict[str, str] = {
    "scripts/run_panel.py": (
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "base_dir = Path(__file__).resolve().parent.parent\n"
        "if base_dir.name.lower() == \"data\":\n"
        "    base_dir = base_dir.parent\n"
        "moduls_dir = base_dir / \"moduls\"\n"
        "if str(moduls_dir) not in sys.path:\n"
        "    sys.path.insert(0, str(moduls_dir))\n"
        "\n"
        "from web_dashboard.scripts.run_panel import main\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    ),
    "scripts/init_first_run.py": (
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "base_dir = Path(__file__).resolve().parent.parent\n"
        "if base_dir.name.lower() == \"data\":\n"
        "    base_dir = base_dir.parent\n"
        "moduls_dir = base_dir / \"moduls\"\n"
        "if str(moduls_dir) not in sys.path:\n"
        "    sys.path.insert(0, str(moduls_dir))\n"
        "\n"
        "from web_dashboard.scripts.init_first_run import main\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    ),
    "scripts/reset_password.py": (
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "base_dir = Path(__file__).resolve().parent.parent\n"
        "if base_dir.name.lower() == \"data\":\n"
        "    base_dir = base_dir.parent\n"
        "moduls_dir = base_dir / \"moduls\"\n"
        "if str(moduls_dir) not in sys.path:\n"
        "    sys.path.insert(0, str(moduls_dir))\n"
        "\n"
        "from web_dashboard.scripts.reset_password import main\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    ),
    "tests/test_health.py": (
        "import sys\n"
        "import tempfile\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "\n"
        "base_dir = Path(__file__).resolve().parent.parent\n"
        "if base_dir.name.lower() == \"data\":\n"
        "    base_dir = base_dir.parent\n"
        "moduls_dir = base_dir / \"moduls\"\n"
        "if str(base_dir) not in sys.path:\n"
        "    sys.path.insert(0, str(base_dir))\n"
        "if str(moduls_dir) not in sys.path:\n"
        "    sys.path.insert(0, str(moduls_dir))\n"
        "\n"
        "from moduls.web_dashboard.app_factory import create_app\n"
        "from moduls.web_dashboard.db import db\n"
        "\n"
        "\n"
        "class HealthTestCase(unittest.TestCase):\n"
        "    def test_health_endpoint(self):\n"
        "        with tempfile.TemporaryDirectory() as tmp:\n"
        "            base_dir = Path(tmp)\n"
        "            app, _ab, _ctx = create_app(str(base_dir), start_scheduler=False)\n"
        "            client = app.test_client()\n"
        "            resp = client.get(\"/health\")\n"
        "            self.assertEqual(resp.status_code, 200)\n"
        "            data = resp.get_json()\n"
        "            self.assertEqual(data.get(\"status\"), \"ok\")\n"
        "            with app.app_context():\n"
        "                db.session.remove()\n"
        "                db.engine.dispose()\n"
        "            for handler in list(app.logger.handlers):\n"
        "                try:\n"
        "                    handler.close()\n"
        "                except Exception:\n"
        "                    pass\n"
        "                app.logger.removeHandler(handler)\n"
        "\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    unittest.main()\n"
    ),
    "migrations/env.py": (
        "import os\n"
        "from pathlib import Path\n"
        "from logging.config import fileConfig\n"
        "\n"
        "from alembic import context\n"
        "from sqlalchemy import engine_from_config, pool\n"
        "\n"
        "from moduls.web_dashboard.app_factory import create_app\n"
        "from moduls.web_dashboard.db import db\n"
        "\n"
        "config = context.config\n"
        "fileConfig(config.config_file_name)\n"
        "\n"
        "\n"
        "def _get_base_dir() -> str:\n"
        "    env = os.environ.get(\"PANEL_BASE_DIR\")\n"
        "    if env:\n"
        "        return env\n"
        "    cfg_path = getattr(config, \"config_file_name\", \"\") or \"\"\n"
        "    if cfg_path:\n"
        "        cfg_dir = Path(cfg_path).resolve().parent\n"
        "        if cfg_dir.name.lower() == \"data\":\n"
        "            return str(cfg_dir.parent)\n"
        "        return str(cfg_dir)\n"
        "    return os.getcwd()\n"
        "\n"
        "\n"
        "def _get_app():\n"
        "    base_dir = _get_base_dir()\n"
        "    app, _ab, _ctx = create_app(base_dir, start_scheduler=False)\n"
        "    return app\n"
        "\n"
        "\n"
        "app = _get_app()\n"
        "\n"
        "with app.app_context():\n"
        "    target_metadata = db.metadata\n"
        "\n"
        "\n"
        "def run_migrations_offline():\n"
        "    url = config.get_main_option(\"sqlalchemy.url\")\n"
        "    context.configure(\n"
        "        url=url,\n"
        "        target_metadata=target_metadata,\n"
        "        literal_binds=True,\n"
        "        compare_type=True,\n"
        "    )\n"
        "\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
        "\n"
        "\n"
        "def run_migrations_online():\n"
        "    connectable = engine_from_config(\n"
        "        config.get_section(config.config_ini_section),\n"
        "        prefix=\"sqlalchemy.\",\n"
        "        poolclass=pool.NullPool,\n"
        "    )\n"
        "\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(\n"
        "            connection=connection,\n"
        "            target_metadata=target_metadata,\n"
        "            compare_type=True,\n"
        "        )\n"
        "\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "\n"
        "\n"
        "if context.is_offline_mode():\n"
        "    run_migrations_offline()\n"
        "else:\n"
        "    run_migrations_online()\n"
    ),
    "migrations/README.md": (
        "# Migrations\n"
        "\n"
        "Use Alembic to manage database migrations.\n"
        "\n"
        "Example:\n"
        "    flask db revision --autogenerate -m \"init\"\n"
        "    flask db upgrade\n"
        "\n"
        "Set PANEL_BASE_DIR if needed.\n"
    ),
    "migrations/script.py.mako": (
        "\"\"\"${message}\n"
        "\n"
        "Revision ID: ${up_revision}\n"
        "Revises: ${down_revision | comma,n}\n"
        "Create Date: ${create_date}\n"
        "\"\"\"\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        "# revision identifiers, used by Alembic.\n"
        "revision = ${repr(up_revision)}\n"
        "down_revision = ${repr(down_revision)}\n"
        "branch_labels = ${repr(branch_labels)}\n"
        "depends_on = ${repr(depends_on)}\n"
        "\n"
        "\n"
        "def upgrade():\n"
        "    ${upgrades if upgrades else \"pass\"}\n"
        "\n"
        "\n"
        "def downgrade():\n"
        "    ${downgrades if downgrades else \"pass\"}\n"
    ),
}
_LIMITS_LUA_SCRIPTS: dict[str, str] = {
    "resources/redis/lua_scripts/acquire_moving_window.lua": "local timestamp = tonumber(ARGV[1])\nlocal limit = tonumber(ARGV[2])\nlocal expiry = tonumber(ARGV[3])\nlocal amount = tonumber(ARGV[4])\n\nif amount > limit then\n    return false\nend\n\nlocal entry = redis.call('lindex', KEYS[1], limit - amount)\n\nif entry and tonumber(entry) >= timestamp - expiry then\n    return false\nend\nlocal entries = {}\nfor i = 1, amount do\n    entries[i] = timestamp\nend\n\nfor i=1,#entries,5000 do\n    redis.call('lpush', KEYS[1], unpack(entries, i, math.min(i+4999, #entries)))\nend\nredis.call('ltrim', KEYS[1], 0, limit - 1)\nredis.call('expire', KEYS[1], expiry)\n\nreturn true\n",
    "resources/redis/lua_scripts/acquire_sliding_window.lua": "-- Time is in milliseconds in this script: TTL, expiry...\n\nlocal limit = tonumber(ARGV[1])\nlocal expiry = tonumber(ARGV[2]) * 1000\nlocal amount = tonumber(ARGV[3])\n\nif amount > limit then\n    return false\nend\n\nlocal current_ttl = tonumber(redis.call('pttl', KEYS[2]))\n\nif current_ttl > 0 and current_ttl < expiry then\n    -- Current window expired, shift it to the previous window\n    redis.call('rename', KEYS[2], KEYS[1])\n    redis.call('set', KEYS[2], 0, 'PX', current_ttl + expiry)\nend\n\nlocal previous_count = tonumber(redis.call('get', KEYS[1])) or 0\nlocal previous_ttl = tonumber(redis.call('pttl', KEYS[1])) or 0\nlocal current_count = tonumber(redis.call('get', KEYS[2])) or 0\ncurrent_ttl = tonumber(redis.call('pttl', KEYS[2])) or 0\n\n-- If the values don't exist yet, consider the TTL is 0\nif previous_ttl <= 0 then\n    previous_ttl = 0\nend\nif current_ttl <= 0 then\n    current_ttl = 0\nend\nlocal weighted_count = math.floor(previous_count * previous_ttl / expiry) + current_count\n\nif (weighted_count + amount) > limit then\n    return false\nend\n\n-- If the current counter exists, increase its value\nif redis.call('exists', KEYS[2]) == 1 then\n    redis.call('incrby', KEYS[2], amount)\nelse\n    -- Otherwise, set the value with twice the expiry time\n    redis.call('set', KEYS[2], amount, 'PX', expiry * 2)\nend\n\nreturn true\n",
    "resources/redis/lua_scripts/clear_keys.lua": "local keys = redis.call('keys', KEYS[1])\nlocal res = 0\n\nfor i=1,#keys,5000 do\n    res = res + redis.call(\n        'del', unpack(keys, i, math.min(i+4999, #keys))\n    )\nend\n\nreturn res\n",
    "resources/redis/lua_scripts/incr_expire.lua": "local current\nlocal amount = tonumber(ARGV[2])\ncurrent = redis.call(\"incrby\", KEYS[1], amount)\n\nif tonumber(current) == amount then\n    redis.call(\"expire\", KEYS[1], ARGV[1])\nend\n\nreturn current\n",
    "resources/redis/lua_scripts/moving_window.lua": "local len = tonumber(ARGV[2])\nlocal expiry = tonumber(ARGV[1])\n\n-- Binary search to find the oldest valid entry in the window\nlocal function oldest_entry(high, target)\n    local low = 0\n    local result = nil\n\n    while low <= high do\n        local mid = math.floor((low + high) / 2)\n        local val = tonumber(redis.call('lindex', KEYS[1], mid))\n\n        if val and val >= target then\n            result = mid\n            low = mid + 1\n        else\n            high = mid - 1\n        end\n    end\n\n    return result\nend\n\nlocal index = oldest_entry(len - 1, expiry)\n\nif index then\n    local count = index + 1\n    local oldest = tonumber(redis.call('lindex', KEYS[1], index))\n    return {tostring(oldest), count}\nend\n",
    "resources/redis/lua_scripts/sliding_window.lua": "local expiry = tonumber(ARGV[1]) * 1000\nlocal previous_count = redis.call('get', KEYS[1])\nlocal previous_ttl = redis.call('pttl', KEYS[1])\nlocal current_count = redis.call('get', KEYS[2])\nlocal current_ttl = redis.call('pttl', KEYS[2])\n\nif current_ttl > 0 and current_ttl < expiry then\n    -- Current window expired, shift it to the previous window\n    redis.call('rename', KEYS[2], KEYS[1])\n    redis.call('set', KEYS[2], 0, 'PX', current_ttl + expiry)\n    previous_count = redis.call('get', KEYS[1])\n    previous_ttl = redis.call('pttl', KEYS[1])\n    current_count = redis.call('get', KEYS[2])\n    current_ttl = redis.call('pttl', KEYS[2])\nend\n\nreturn {previous_count, previous_ttl, current_count, current_ttl}\n",
}

_ALEMBIC_DATA_TEMPLATE = (
    "[alembic]\n"
    "script_location = %(here)s/migrations\n"
    "sqlalchemy.url = sqlite:///%(here)s/appbuilder.db\n"
    "\n"
    "[loggers]\n"
    "keys = root,sqlalchemy,alembic\n"
    "\n"
    "[handlers]\n"
    "keys = console\n"
    "\n"
    "[formatters]\n"
    "keys = generic\n"
    "\n"
    "[logger_root]\n"
    "level = WARN\n"
    "handlers = console\n"
    "qualname =\n"
    "\n"
    "[logger_sqlalchemy]\n"
    "level = WARN\n"
    "handlers =\n"
    "qualname = sqlalchemy.engine\n"
    "\n"
    "[logger_alembic]\n"
    "level = INFO\n"
    "handlers =\n"
    "qualname = alembic\n"
    "\n"
    "[handler_console]\n"
    "class = StreamHandler\n"
    "args = (sys.stderr,)\n"
    "level = NOTSET\n"
    "formatter = generic\n"
    "\n"
    "[formatter_generic]\n"
    "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
)


@dataclass
class PanelConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    debug: bool = False
    secret_key: str = ""
    setup_complete: bool = False
    retention_days: int = DEFAULT_RETENTION_DAYS
    overview_refresh_seconds: int = DEFAULT_OVERVIEW_REFRESH_SECONDS
    api_token: str = ""
    admin_login: str = ""
    admin_password: str = ""

    def to_ini(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg["panel"] = {
            "host": self.host,
            "port": str(self.port),
            "debug": "1" if self.debug else "0",
            "secret_key": self.secret_key,
            "setup_complete": "1" if self.setup_complete else "0",
            "retention_days": str(self.retention_days),
            "overview_refresh_seconds": str(self.overview_refresh_seconds),
            "api_token": self.api_token,
        }
        return cfg


def _normalize_debug_log_level(value: str | None) -> str:
    level = str(value or DEFAULT_DEBUG_LOG_LEVEL).strip().upper()
    if level not in DEBUG_LOG_LEVELS:
        return DEFAULT_DEBUG_LOG_LEVEL
    return level


def _normalize_debug_log_max_bytes(value: int) -> int:
    return max(_DEBUG_LOG_MIN_BYTES, min(int(value), _DEBUG_LOG_MAX_BYTES))


def _normalize_debug_log_backup_count(value: int) -> int:
    return max(_DEBUG_LOG_MIN_BACKUP_COUNT, min(int(value), _DEBUG_LOG_MAX_BACKUP_COUNT))


@dataclass
class PanelDebugLogConfig:
    enabled: bool = DEFAULT_DEBUG_LOG_ENABLED
    level: str = DEFAULT_DEBUG_LOG_LEVEL
    max_bytes: int = DEFAULT_DEBUG_LOG_MAX_BYTES
    backup_count: int = DEFAULT_DEBUG_LOG_BACKUP_COUNT

    def normalized(self) -> "PanelDebugLogConfig":
        return PanelDebugLogConfig(
            enabled=bool(self.enabled),
            level=_normalize_debug_log_level(self.level),
            max_bytes=_normalize_debug_log_max_bytes(self.max_bytes),
            backup_count=_normalize_debug_log_backup_count(self.backup_count),
        )

    def to_ini(self) -> dict[str, str]:
        normalized = self.normalized()
        return {
            "enabled": "1" if normalized.enabled else "0",
            "level": normalized.level,
            "max_bytes": str(normalized.max_bytes),
            "backup_count": str(normalized.backup_count),
        }


def _get_data_dir(base_dir: str) -> Path:
    return Path(base_dir) / "data"


def _get_log_dir(base_dir: str) -> Path:
    return Path(base_dir) / "log"


def get_db_path(base_dir: str) -> Path:
    return _get_data_dir(base_dir) / "appbuilder.db"


def get_config_path(base_dir: str) -> Path:
    return _get_data_dir(base_dir) / "config.ini"


def _normalize_base_dir_key(base_dir: str) -> str:
    try:
        return str(Path(base_dir).resolve()).casefold()
    except Exception:
        return os.path.abspath(base_dir).casefold()


def _find_resource_root(base_dir: Path) -> Optional[Path]:
    candidates: list[Path] = []

    def _add_candidate(candidate: Path) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    _add_candidate(base_dir)
    package_path = Path(__file__).resolve()
    try:
        parents = list(package_path.parents)
        if len(parents) > 2:
            _add_candidate(parents[2])
        if len(parents) > 3:
            _add_candidate(parents[3])
    except Exception:
        pass
    _add_candidate(Path.cwd())

    for candidate in candidates:
        if (
            (candidate / "alembic.ini").is_file()
            or (candidate / "scripts").is_dir()
            or (candidate / "tests").is_dir()
            or (candidate / "migrations").is_dir()
        ):
            return candidate
    return None


def _copy_tree(src: Path, dest: Path) -> None:
    for item in src.rglob("*"):
        if "__pycache__" in item.parts:
            continue
        if item.is_dir():
            ensure_dir(dest / item.relative_to(src))
            continue
        if item.suffix.lower() in (".pyc", ".pyo"):
            continue
        target = dest / item.relative_to(src)
        if target.exists():
            continue
        ensure_dir(target.parent)
        shutil.copy2(item, target)


def _write_file_if_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def _ensure_fallback_files(data_dir: Path, prefix: str) -> None:
    for rel_path, content in _FALLBACK_FILES.items():
        if not rel_path.startswith(prefix + "/"):
            continue
        dest = data_dir / rel_path
        _write_file_if_missing(dest, content)

def _ensure_limits_lua_scripts(data_dir: Path) -> None:
    limits_root = data_dir / "limits_resources"
    for rel_path, content in _LIMITS_LUA_SCRIPTS.items():
        dest = limits_root / rel_path
        _write_file_if_missing(dest, content)


def _find_limits_package_root() -> Optional[Path]:
    try:
        import importlib.util
    except Exception:
        return None

    try:
        spec = importlib.util.find_spec("limits")
    except Exception:
        return None
    if not spec:
        return None

    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        for loc in locations:
            try:
                return Path(loc)
            except Exception:
                continue

    origin = getattr(spec, "origin", None)
    if origin:
        try:
            return Path(origin).resolve().parent
        except Exception:
            return Path(origin).parent
    return None


def ensure_limits_runtime_ready(base_dir: str) -> None:
    """
    Prepare fallback Lua scripts for the ``limits`` package before importing
    Flask-AppBuilder internals. This is critical for frozen/Nuitka builds where
    package data files can be missing.
    """
    data_dir = _get_data_dir(base_dir)
    try:
        ensure_dir(data_dir)
    except Exception:
        return

    _ensure_limits_lua_scripts(data_dir)
    fallback_root = data_dir / "limits_resources"
    fallback_probe = fallback_root / "resources" / "redis" / "lua_scripts" / "moving_window.lua"
    if not fallback_probe.is_file():
        return

    pkg_root = _find_limits_package_root()
    if pkg_root is not None:
        target_dir = pkg_root / "resources" / "redis" / "lua_scripts"
        if not (target_dir / "moving_window.lua").is_file():
            try:
                ensure_dir(target_dir)
                source_dir = fallback_root / "resources" / "redis" / "lua_scripts"
                for script in source_dir.glob("*.lua"):
                    dest = target_dir / script.name
                    if not dest.exists():
                        dest.write_bytes(script.read_bytes())
            except Exception:
                pass

    try:
        from limits import util as limits_util
    except Exception:
        return

    roots = getattr(limits_util, "_autocraft_fallback_roots", None)
    if not isinstance(roots, list):
        roots = []
    fallback_key = str(fallback_root)
    if fallback_key not in roots:
        roots.append(fallback_key)
    limits_util._autocraft_fallback_roots = roots

    if getattr(limits_util, "_autocraft_data_patched", False):
        return

    original = limits_util.get_package_data

    def _get_package_data(path: str) -> bytes:
        try:
            return original(path)
        except Exception:
            for root_text in getattr(limits_util, "_autocraft_fallback_roots", []) or []:
                try:
                    candidate = Path(root_text) / path
                except Exception:
                    continue
                if candidate.is_file():
                    return candidate.read_bytes()

            package_root = _find_limits_package_root()
            if package_root is not None:
                candidate = package_root / path
                if candidate.is_file():
                    return candidate.read_bytes()
            raise

    limits_util.get_package_data = _get_package_data
    limits_util._autocraft_data_patched = True


def _ensure_resource_minimum(data_dir: Path) -> None:
    for name in _RESOURCE_DIRS:
        ensure_dir(data_dir / name)
        _ensure_fallback_files(data_dir, name)

    alembic_path = data_dir / "alembic.ini"
    if alembic_path.exists():
        try:
            content = alembic_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""
        if "script_location = migrations" in content and "sqlalchemy.url = sqlite:///data/appbuilder.db" in content:
            alembic_path.write_text(_ALEMBIC_DATA_TEMPLATE, encoding="utf-8")
    else:
        _write_file_if_missing(alembic_path, _ALEMBIC_DATA_TEMPLATE)

    _ensure_limits_lua_scripts(data_dir)


def ensure_data_resources(base_dir: str) -> None:
    data_dir = _get_data_dir(base_dir)
    cache_key = _normalize_base_dir_key(base_dir)
    with _RESOURCE_SYNC_LOCK:
        already_synced = cache_key in _RESOURCE_SYNC_DONE
        if not already_synced:
            source_root = _find_resource_root(Path(base_dir))
            for name in _RESOURCE_DIRS:
                dest_dir = data_dir / name
                ensure_dir(dest_dir)
                if source_root:
                    src_dir = source_root / name
                    if src_dir.is_dir():
                        _copy_tree(src_dir, dest_dir)
        _ensure_resource_minimum(data_dir)
        _RESOURCE_SYNC_DONE.add(cache_key)

def ensure_data_dirs(base_dir: str) -> None:
    ensure_dir(_get_data_dir(base_dir))
    ensure_dir(_get_log_dir(base_dir))
    ensure_data_resources(base_dir)


def load_debug_log_config(base_dir: str) -> PanelDebugLogConfig:
    ensure_data_dirs(base_dir)
    config_path = get_config_path(base_dir)
    parser = configparser.ConfigParser()
    if config_path.exists():
        try:
            parser.read(config_path, encoding="utf-8")
        except Exception:
            parser = configparser.ConfigParser()

    section = parser[DEBUG_LOG_SECTION] if parser.has_section(DEBUG_LOG_SECTION) else {}
    cfg = PanelDebugLogConfig(
        enabled=parse_bool(section.get("enabled"), DEFAULT_DEBUG_LOG_ENABLED),
        level=_normalize_debug_log_level(section.get("level")),
        max_bytes=_normalize_debug_log_max_bytes(
            parse_int(section.get("max_bytes"), DEFAULT_DEBUG_LOG_MAX_BYTES)
        ),
        backup_count=_normalize_debug_log_backup_count(
            parse_int(section.get("backup_count"), DEFAULT_DEBUG_LOG_BACKUP_COUNT)
        ),
    ).normalized()

    need_write = not parser.has_section(DEBUG_LOG_SECTION)
    if not need_write:
        desired = cfg.to_ini()
        for key, value in desired.items():
            if section.get(key) != value:
                need_write = True
                break
    if need_write:
        save_debug_log_config(base_dir, cfg)
    return cfg


def save_debug_log_config(base_dir: str, cfg: PanelDebugLogConfig) -> None:
    ensure_data_dirs(base_dir)
    config_path = get_config_path(base_dir)
    parser = configparser.ConfigParser()
    if config_path.exists():
        try:
            parser.read(config_path, encoding="utf-8")
        except Exception:
            parser = configparser.ConfigParser()

    if not parser.has_section(DEBUG_LOG_SECTION):
        parser.add_section(DEBUG_LOG_SECTION)

    desired = cfg.normalized().to_ini()
    for key, value in desired.items():
        parser.set(DEBUG_LOG_SECTION, key, value)

    with config_path.open("w", encoding="utf-8") as f:
        parser.write(f)


def load_config(base_dir: str) -> PanelConfig:
    ensure_data_dirs(base_dir)
    config_path = get_config_path(base_dir)

    if not config_path.exists():
        cfg = PanelConfig(
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            debug=False,
            secret_key=secrets.token_urlsafe(32),
            setup_complete=False,
            retention_days=DEFAULT_RETENTION_DAYS,
            api_token=secrets.token_urlsafe(32),
        )
        save_config(base_dir, cfg)
        return cfg

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    panel = parser["panel"] if parser.has_section("panel") else {}

    # Берём ключи из файла, а если их нет — генерим и потом дозаписываем
    # (при этом НЕ стираем другие секции в config.ini)
    secret_key = panel.get("secret_key") or secrets.token_urlsafe(32)
    api_token = panel.get("api_token") or secrets.token_urlsafe(32)
    cfg = PanelConfig(
        host=panel.get("host", DEFAULT_HOST),
        port=parse_int(panel.get("port"), DEFAULT_PORT),
        debug=parse_bool(panel.get("debug"), False),
        secret_key=secret_key,
        setup_complete=parse_bool(panel.get("setup_complete"), False),
        retention_days=parse_int(panel.get("retention_days"), DEFAULT_RETENTION_DAYS),
        overview_refresh_seconds=parse_int(
            panel.get("overview_refresh_seconds"),
            DEFAULT_OVERVIEW_REFRESH_SECONDS,
        ),
        api_token=api_token,
        admin_login="",
        admin_password="",
    )

    # Сохраняем ТОЛЬКО если реально нужно дописать новые ключи/токены.
    need_write = False
    if not parser.has_section("panel"):
        need_write = True
    else:
        desired_min = {
            "host": cfg.host,
            "port": str(cfg.port),
            "debug": "1" if cfg.debug else "0",
            "secret_key": cfg.secret_key,
            "setup_complete": "1" if cfg.setup_complete else "0",
            "retention_days": str(cfg.retention_days),
            "overview_refresh_seconds": str(cfg.overview_refresh_seconds),
            "api_token": cfg.api_token,
        }
        for k, v in desired_min.items():
            if panel.get(k) != v:
                need_write = True
                break
        if "admin_login" in panel or "admin_password" in panel:
            need_write = True

    if need_write:
        save_config(base_dir, cfg)

    return cfg


def save_config(base_dir: str, cfg: PanelConfig) -> None:
    """
    Сохраняет конфиг панели в data/config.ini, НЕ затирая другие секции.

    Почему так: общий data/config.ini в AutoCraft часто делят несколько модулей.
    Старый вариант перезаписывал файл целиком и выкидывал чужие секции
    (например, [webpanel_autostart]), из‑за чего настройка "автозапуск" пропадала
    после следующего запуска.
    """
    ensure_data_dirs(base_dir)
    config_path = get_config_path(base_dir)

    parser = configparser.ConfigParser()

    # Если файл уже есть — читаем и сохраняем все "чужие" секции
    if config_path.exists():
        try:
            parser.read(config_path, encoding="utf-8")
        except Exception:
            # Если файл повреждён/нечитаем — не падаем, создадим свежий
            parser = configparser.ConfigParser()

    if not parser.has_section("panel"):
        parser.add_section("panel")

    desired = {
        "host": cfg.host,
        "port": str(cfg.port),
        "debug": "1" if cfg.debug else "0",
        "secret_key": cfg.secret_key,
        "setup_complete": "1" if cfg.setup_complete else "0",
        "retention_days": str(cfg.retention_days),
        "overview_refresh_seconds": str(cfg.overview_refresh_seconds),
        "api_token": cfg.api_token,
    }

    for k, v in desired.items():
        parser.set("panel", k, v)
    parser.remove_option("panel", "admin_login")
    parser.remove_option("panel", "admin_password")

    with config_path.open("w", encoding="utf-8") as f:
        parser.write(f)



def get_panel_log_path(base_dir: str) -> Path:
    return _get_log_dir(base_dir) / _DEBUG_LOG_FILENAME


def tail_panel_log(base_dir: str, lines: int = 100) -> str:
    return tail_file(get_panel_log_path(base_dir), lines=lines)


def _commit_sm_session(sm) -> None:
    session_candidate = None
    if hasattr(sm, "get_session"):
        session_candidate = sm.get_session
    elif hasattr(sm, "appbuilder") and hasattr(sm.appbuilder, "get_session"):
        session_candidate = sm.appbuilder.get_session

    if session_candidate is None:
        from .db import db

        session_candidate = db.session

    if hasattr(session_candidate, "commit"):
        session_candidate.commit()
        return
    if callable(session_candidate):
        session = session_candidate()
        session.commit()


def _persist_panel_setup_state(
    cfg: PanelConfig,
    base_dir: str,
    *,
    setup_complete: Optional[bool] = None,
) -> None:
    if setup_complete is not None:
        cfg.setup_complete = bool(setup_complete)
    cfg.admin_login = ""
    cfg.admin_password = ""
    save_config(base_dir, cfg)


def _is_sqlite_locked_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "database is locked" in text
        or "database table is locked" in text
        or "database schema is locked" in text
    )


def _normalize_role_key(role_name: str) -> str:
    value = (role_name or "").strip()
    if not value:
        return ""
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold()


def _canonicalize_role_name(role_name: str) -> str:
    value = (role_name or "").strip()
    if not value:
        return ""
    return _ROLE_ALIAS_TO_CANONICAL.get(_normalize_role_key(value), value)


def _is_super_admin_role_name(role_name: str) -> bool:
    return _canonicalize_role_name(role_name) == "Super Admin"


def _password_state(plain_password: str, password_hash: str) -> str:
    if (plain_password or "").strip():
        return _PASSWORD_STATE_SAVED
    if (password_hash or "").strip():
        return _PASSWORD_STATE_HASH_ONLY
    return _PASSWORD_STATE_MISSING


def _normalize_username(username: str) -> str:
    value = (username or "").strip()
    if not value:
        raise ValueError("Логин пользователя не указан.")
    if len(value) < 3:
        raise ValueError("Логин должен содержать минимум 3 символа.")
    if len(value) > 64:
        raise ValueError("Логин слишком длинный (максимум 64 символа).")
    if any(ch.isspace() for ch in value):
        raise ValueError("Логин не должен содержать пробелы.")
    return value


def _normalize_display_name(name: str, username: str) -> str:
    value = (name or "").strip()
    if value:
        return value[:120]
    return username


def _validate_password_value(password: str) -> str:
    value = (password or "").strip()
    if len(value) < _MIN_PASSWORD_LEN:
        raise ValueError(f"Пароль должен быть минимум {_MIN_PASSWORD_LEN} символов.")
    return value


def _ensure_user_secret_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_USER_SECRET_TABLE} (
            username TEXT PRIMARY KEY,
            plain_password TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _with_secret_db(
    base_dir: str,
    worker: Callable[[sqlite3.Connection], Any],
    *,
    max_attempts: int = 3,
) -> Any:
    db_path = get_db_path(base_dir)
    ensure_data_dirs(base_dir)
    last_error: Exception | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=15)
            conn.row_factory = sqlite3.Row
            _ensure_user_secret_table(conn)
            result = worker(conn)
            conn.commit()
            return result
        except sqlite3.OperationalError as exc:
            last_error = exc
            if _is_sqlite_locked_error(exc) and attempt < max_attempts:
                time.sleep(0.2 * attempt)
                continue
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось выполнить операцию с хранилищем паролей панели.")


def remember_user_password(base_dir: str, username: str, password: str) -> None:
    login = _normalize_username(username)
    pwd = _validate_password_value(password)

    def _worker(conn: sqlite3.Connection) -> None:
        conn.execute(
            f"""
            INSERT INTO {_USER_SECRET_TABLE} (username, plain_password, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                plain_password=excluded.plain_password,
                updated_at=CURRENT_TIMESTAMP
            """,
            (login, pwd),
        )

    _with_secret_db(base_dir, _worker)


def forget_user_password(base_dir: str, username: str) -> None:
    login = _normalize_username(username)

    def _worker(conn: sqlite3.Connection) -> None:
        conn.execute(f"DELETE FROM {_USER_SECRET_TABLE} WHERE username = ?", (login,))

    _with_secret_db(base_dir, _worker)


def get_stored_user_password(base_dir: str, username: str) -> str:
    login = _normalize_username(username)

    def _worker(conn: sqlite3.Connection) -> str:
        cur = conn.execute(
            f"SELECT plain_password FROM {_USER_SECRET_TABLE} WHERE username = ?",
            (login,),
        )
        row = cur.fetchone()
        if not row:
            return ""
        return str(row["plain_password"] or "")

    return _with_secret_db(base_dir, _worker)


def _load_password_map(base_dir: str) -> dict[str, str]:
    def _worker(conn: sqlite3.Connection) -> dict[str, str]:
        cur = conn.execute(f"SELECT username, plain_password FROM {_USER_SECRET_TABLE}")
        result: dict[str, str] = {}
        for row in cur.fetchall():
            key = str(row["username"] or "").strip()
            if key:
                result[key] = str(row["plain_password"] or "")
        return result

    return _with_secret_db(base_dir, _worker)


def _ensure_default_roles_in_session(session) -> None:
    from flask_appbuilder.security.sqla.models import Role

    existing = {
        str(item.name or "").strip()
        for item in session.query(Role).all()
        if str(item.name or "").strip()
    }
    for role_name in _DEFAULT_PANEL_ROLES:
        if role_name not in existing:
            session.add(Role(name=role_name))


def _get_or_create_role_in_session(session, role_name: str):
    from flask_appbuilder.security.sqla.models import Role

    canonical = _canonicalize_role_name(role_name) or "Viewer"
    role_obj = session.query(Role).filter_by(name=canonical).first()
    if role_obj is None:
        role_obj = Role(name=canonical)
        session.add(role_obj)
        session.flush()
    return role_obj


def _ensure_user_has_role_in_session(session, user, role_name: str) -> None:
    role_obj = _get_or_create_role_in_session(session, role_name)
    if role_obj not in (getattr(user, "roles", None) or []):
        user.roles.append(role_obj)


def _sync_super_admin_permissions_in_session(session) -> None:
    from flask_appbuilder.security.sqla.models import PermissionView

    super_role = _get_or_create_role_in_session(session, "Super Admin")
    existing_ids = {
        int(getattr(item, "id", 0) or 0)
        for item in (getattr(super_role, "permissions", None) or [])
        if int(getattr(item, "id", 0) or 0) > 0
    }
    for perm_view in session.query(PermissionView).all():
        perm_id = int(getattr(perm_view, "id", 0) or 0)
        if perm_id > 0 and perm_id in existing_ids:
            continue
        if perm_view not in (getattr(super_role, "permissions", None) or []):
            super_role.permissions.append(perm_view)
            if perm_id > 0:
                existing_ids.add(perm_id)


def _with_security_session(
    base_dir: str,
    worker: Callable[[Any], Any],
    *,
    max_attempts: int = 3,
) -> Any:
    ensure_data_dirs(base_dir)
    ensure_limits_runtime_ready(base_dir)

    from flask_appbuilder.security.sqla.models import User
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import sessionmaker

    db_path = get_db_path(base_dir)

    last_error: Exception | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"timeout": 15},
        )
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            try:
                User.metadata.create_all(engine)
            except Exception:
                pass
            result = worker(session)
            session.commit()
            return result
        except OperationalError as exc:
            session.rollback()
            last_error = exc
            if _is_sqlite_locked_error(exc) and attempt < max_attempts:
                time.sleep(0.2 * attempt)
                continue
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            try:
                session.close()
            except Exception:
                pass
            try:
                engine.dispose()
            except Exception:
                pass
    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось выполнить операцию с пользователями панели.")


def list_panel_roles(base_dir: str) -> list[str]:
    def _worker(session):
        from flask_appbuilder.security.sqla.models import Role

        _ensure_default_roles_in_session(session)
        roles: set[str] = set()
        for item in session.query(Role).all():
            raw_name = str(item.name or "").strip()
            if not raw_name:
                continue
            canonical = _canonicalize_role_name(raw_name) or raw_name
            roles.add(canonical)
        for item in _DEFAULT_PANEL_ROLES:
            roles.add(item)
        return sorted(roles)

    return _with_security_session(base_dir, _worker)


def list_panel_users(base_dir: str) -> list[dict[str, Any]]:
    password_map = _load_password_map(base_dir)

    def _worker(session):
        from flask_appbuilder.security.sqla.models import User

        _ensure_default_roles_in_session(session)
        rows = session.query(User).order_by(User.username.asc()).all()
        result: list[dict[str, Any]] = []
        super_admin_sync_done = False
        for user in rows:
            username = str(getattr(user, "username", "") or "").strip()
            if not username:
                continue
            first_name = str(getattr(user, "first_name", "") or "").strip()
            last_name = str(getattr(user, "last_name", "") or "").strip()
            full_name = f"{first_name} {last_name}".strip() or username
            roles = sorted(
                {
                    _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
                    for role in (getattr(user, "roles", None) or [])
                    if _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
                }
            )
            if "Super Admin" in roles:
                _ensure_user_has_role_in_session(session, user, "Super Admin")
                if not super_admin_sync_done:
                    _sync_super_admin_permissions_in_session(session)
                    super_admin_sync_done = True
            plain_password = str(password_map.get(username) or "")
            password_hash = str(getattr(user, "password", "") or "")
            result.append(
                {
                    "id": int(getattr(user, "id", 0) or 0),
                    "username": username,
                    "name": full_name,
                    "roles": roles,
                    "active": bool(getattr(user, "active", True)),
                    "password_saved": bool(plain_password),
                    "password_state": _password_state(plain_password, password_hash),
                }
            )
        return result

    return _with_security_session(base_dir, _worker)


def get_panel_bootstrap_state(base_dir: str) -> dict[str, Any]:
    users = list_panel_users(base_dir)
    has_users = bool(users)
    super_admin_users = []
    for item in users:
        roles = [str(role).strip() for role in (item.get("roles") or []) if str(role).strip()]
        if "Super Admin" in roles:
            super_admin_users.append(str(item.get("username") or "").strip())
    return {
        "has_users": has_users,
        "has_super_admin": bool(super_admin_users),
        "super_admin_users": super_admin_users,
        "user_count": len(users),
    }


def get_panel_start_block_reason(base_dir: str) -> str:
    state = get_panel_bootstrap_state(base_dir)
    if not bool(state.get("has_users")):
        return (
            "Панель не может быть запущена: не найдено ни одного пользователя. "
            "Создайте первого пользователя с ролью Super Admin."
        )
    if not bool(state.get("has_super_admin")):
        return (
            "Панель не может быть запущена: отсутствует пользователь с ролью Super Admin. "
            "Создайте первого Super Admin или назначьте роль существующему пользователю."
        )
    return ""


def can_start_panel(base_dir: str) -> bool:
    return not bool(get_panel_start_block_reason(base_dir))


def sync_panel_setup_state(base_dir: str) -> None:
    cfg = load_config(base_dir)
    state = get_panel_bootstrap_state(base_dir)
    _persist_panel_setup_state(cfg, base_dir, setup_complete=bool(state.get("has_super_admin")))


def _create_user_in_session(
    session,
    username: str,
    display_name: str,
    password: str,
    role_name: str,
) -> None:
    from flask_appbuilder.security.sqla.models import User
    from werkzeug.security import generate_password_hash

    _ensure_default_roles_in_session(session)
    existing = session.query(User).filter_by(username=username).first()
    if existing is not None:
        raise ValueError(f"Пользователь '{username}' уже существует.")

    canonical_role_name = _canonicalize_role_name(role_name) or "Viewer"
    role_obj = _get_or_create_role_in_session(session, canonical_role_name)

    user = User(
        first_name=display_name,
        last_name="",
        username=username,
        email=f"{username}@localhost",
        active=True,
    )
    user.roles.append(role_obj)
    user.password = generate_password_hash(password)
    session.add(user)
    if _is_super_admin_role_name(canonical_role_name):
        _sync_super_admin_permissions_in_session(session)


def create_panel_user(
    base_dir: str,
    username: str,
    display_name: str,
    password: str,
    role: str = "Viewer",
) -> None:
    login = _normalize_username(username)
    name = _normalize_display_name(display_name, login)
    pwd = _validate_password_value(password)
    role_name = _canonicalize_role_name((role or "Viewer").strip() or "Viewer")
    state = get_panel_bootstrap_state(base_dir)
    if not bool(state.get("has_super_admin")) and role_name != "Super Admin":
        raise ValueError(
            "Пока в панели нет Super Admin, нового пользователя можно создать только с ролью Super Admin."
        )

    _with_security_session(
        base_dir,
        lambda session: _create_user_in_session(session, login, name, pwd, role_name),
    )
    remember_user_password(base_dir, login, pwd)
    sync_panel_setup_state(base_dir)


def _is_last_super_admin(session, user) -> bool:
    from flask_appbuilder.security.sqla.models import User

    role_names = {
        _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
        for role in (getattr(user, "roles", None) or [])
        if _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
    }
    if "Super Admin" not in role_names:
        return False
    other_super_admins = 0
    others = session.query(User).filter(User.id != user.id).all()
    for other in others:
        other_roles = {
            _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
            for role in (getattr(other, "roles", None) or [])
            if _canonicalize_role_name(str(getattr(role, "name", "") or "").strip())
        }
        if "Super Admin" in other_roles:
            other_super_admins += 1
    return other_super_admins <= 0


def delete_panel_user(base_dir: str, username: str) -> None:
    login = _normalize_username(username)
    if login.lower() == "public":
        raise ValueError("Системного пользователя 'public' удалять нельзя.")

    def _worker(session):
        from flask_appbuilder.security.sqla.models import User

        user = session.query(User).filter_by(username=login).first()
        if user is None:
            raise ValueError(f"Пользователь '{login}' не найден.")
        if _is_last_super_admin(session, user):
            raise ValueError("Нельзя удалить последнего пользователя с ролью Super Admin.")
        session.delete(user)

    _with_security_session(base_dir, _worker)
    forget_user_password(base_dir, login)
    sync_panel_setup_state(base_dir)


def set_panel_user_password(
    base_dir: str,
    username: str,
    new_password: str,
    role: Optional[str] = None,
) -> None:
    login = _normalize_username(username)
    pwd = _validate_password_value(new_password)
    role_name = _canonicalize_role_name((role or "").strip())

    def _worker(session):
        from flask_appbuilder.security.sqla.models import User
        from werkzeug.security import generate_password_hash

        _ensure_default_roles_in_session(session)
        user = session.query(User).filter_by(username=login).first()
        if user is None:
            raise ValueError(f"Пользователь '{login}' не найден.")
        if role_name:
            _ensure_user_has_role_in_session(session, user, role_name)
            if _is_super_admin_role_name(role_name):
                _sync_super_admin_permissions_in_session(session)
        user.password = generate_password_hash(pwd)

    _with_security_session(base_dir, _worker)
    remember_user_password(base_dir, login, pwd)


def set_panel_user_role(
    base_dir: str,
    username: str,
    role: str,
    *,
    replace_existing: bool = True,
) -> None:
    login = _normalize_username(username)
    role_name = _canonicalize_role_name((role or "").strip())
    if not role_name:
        raise ValueError("Роль пользователя не выбрана.")

    def _worker(session):
        from flask_appbuilder.security.sqla.models import User

        _ensure_default_roles_in_session(session)
        user = session.query(User).filter_by(username=login).first()
        if user is None:
            raise ValueError(f"Пользователь '{login}' не найден.")

        current_roles = {
            _canonicalize_role_name(str(getattr(item, "name", "") or "").strip())
            for item in (getattr(user, "roles", None) or [])
            if _canonicalize_role_name(str(getattr(item, "name", "") or "").strip())
        }
        if (
            bool(replace_existing)
            and "Super Admin" in current_roles
            and role_name != "Super Admin"
            and _is_last_super_admin(session, user)
        ):
            raise ValueError("Нельзя снять роль Super Admin у последнего пользователя с этой ролью.")

        role_obj = _get_or_create_role_in_session(session, role_name)
        if bool(replace_existing):
            user.roles = [role_obj]
        elif role_obj not in (getattr(user, "roles", None) or []):
            user.roles.append(role_obj)

        if _is_super_admin_role_name(role_name):
            _sync_super_admin_permissions_in_session(session)

    _with_security_session(base_dir, _worker)
    sync_panel_setup_state(base_dir)


def get_panel_user_credentials(base_dir: str, username: str) -> dict[str, Any]:
    login = _normalize_username(username)
    password = get_stored_user_password(base_dir, login)
    users = list_panel_users(base_dir)
    for item in users:
        if str(item.get("username") or "") == login:
            return {
                "username": login,
                "password": password,
                "name": item.get("name", login),
                "roles": list(item.get("roles") or []),
                "active": bool(item.get("active", True)),
                "password_state": str(item.get("password_state") or ""),
            }
    raise ValueError(f"Пользователь '{login}' не найден.")


def _upsert_user_password_direct(
    username: str,
    new_password: str,
    role: str,
    base_dir: str,
    *,
    display_name: str = "Admin",
    max_attempts: int = 3,
) -> None:
    login = _normalize_username(username)
    pwd = _validate_password_value(new_password)
    role_name = _canonicalize_role_name((role or "Admin").strip() or "Admin")
    name = _normalize_display_name(display_name, login)

    def _worker(session):
        from flask_appbuilder.security.sqla.models import User
        from werkzeug.security import generate_password_hash

        _ensure_default_roles_in_session(session)
        role_obj = _get_or_create_role_in_session(session, role_name)

        user = session.query(User).filter_by(username=login).first()
        if user is None:
            user = User(
                first_name=name,
                last_name="",
                username=login,
                email=f"{login}@localhost",
                active=True,
            )
            user.roles.append(role_obj)
            session.add(user)
        elif role_obj not in user.roles:
            user.roles.append(role_obj)
        if _is_super_admin_role_name(role_name):
            _sync_super_admin_permissions_in_session(session)

        user.password = generate_password_hash(pwd)

    _with_security_session(base_dir, _worker, max_attempts=max_attempts)
    remember_user_password(base_dir, login, pwd)


def update_user_password(
    cfg: PanelConfig,
    username: str,
    new_password: str,
    role: str = "Admin",
    base_dir: Optional[str] = None,
) -> None:
    """
    Обновляет (или создаёт) пользователя в FAB БД, задаёт пароль и роль.
    Используется Telegram-модулем для смены пароля.
    """
    if base_dir is None:
        # Если base_dir не передали, пробуем оттуда, где лежит конфиг
        base_dir = os.path.abspath(os.getcwd())
    login = _normalize_username(username)
    pwd = _validate_password_value(new_password)
    role_name = _canonicalize_role_name((role or "Admin").strip() or "Admin")
    state = get_panel_bootstrap_state(base_dir)
    if not bool(state.get("has_super_admin")) and role_name != "Super Admin":
        raise ValueError(
            "Пока в панели нет Super Admin, пароль/роль можно задать только для Super Admin."
        )

    # Быстрый путь: обновляем напрямую в SQLite (минимум зависимостей и I/O).
    try:
        _upsert_user_password_direct(login, pwd, role_name, base_dir, display_name="Admin")
        sync_panel_setup_state(base_dir)
        return
    except Exception as direct_exc:
        last_error: Exception | None = direct_exc

    # Фолбек: через create_app (сохраняем обратную совместимость для нестандартных окружений).
    try:
        from .app_factory import create_app
    except Exception as app_import_exc:
        create_app = None
        last_error = app_import_exc

    if create_app is not None:
        try:
            app, appbuilder, _runtime = create_app(base_dir, start_scheduler=False)
        except Exception as app_exc:
            last_error = app_exc
            app = None
        if app is not None:
            from werkzeug.security import generate_password_hash

            with app.app_context():
                sm = appbuilder.sm
                role_obj = sm.find_role(role_name) or sm.add_role(role_name)
                if _is_super_admin_role_name(role_name):
                    try:
                        from flask_appbuilder.security.sqla.models import PermissionView

                        session = sm.get_session() if callable(getattr(sm, "get_session", None)) else sm.get_session
                        all_permissions = session.query(PermissionView).all()
                        for perm_view in all_permissions:
                            try:
                                sm.add_permission_role(role_obj, perm_view)
                            except Exception:
                                pass
                    except Exception:
                        pass
                user = sm.find_user(username=login)
                hashed = generate_password_hash(pwd)
                if hasattr(sm, "get_password_hash"):
                    try:
                        hashed = sm.get_password_hash(pwd)  # type: ignore[call-arg]
                    except Exception:
                        pass
                if user:
                    user.password = hashed
                    if role_obj and role_obj not in user.roles:
                        user.roles.append(role_obj)
                else:
                    sm.add_user(
                        username=login,
                        first_name="Admin",
                        last_name="User",
                        email=f"{login}@localhost",
                        role=role_obj,
                        password=pwd,
                    )
                _commit_sm_session(sm)
            remember_user_password(base_dir, login, pwd)
            sync_panel_setup_state(base_dir)
            return

    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось обновить пароль пользователя панели.")
