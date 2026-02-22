import configparser
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .utils import ensure_dir, parse_bool, parse_int, tail_file

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_RETENTION_DAYS = 7
DEFAULT_OVERVIEW_REFRESH_SECONDS = 10
_RESOURCE_DIRS = ("scripts", "tests", "migrations")
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
    admin_login: str = "admin"
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
            "admin_login": self.admin_login,
            "admin_password": self.admin_password,
        }
        return cfg


def _get_data_dir(base_dir: str) -> Path:
    return Path(base_dir) / "data"


def _get_log_dir(base_dir: str) -> Path:
    return Path(base_dir) / "log"


def get_db_path(base_dir: str) -> Path:
    return _get_data_dir(base_dir) / "appbuilder.db"


def get_config_path(base_dir: str) -> Path:
    return _get_data_dir(base_dir) / "config.ini"


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


def ensure_data_resources(base_dir: str) -> None:
    data_dir = _get_data_dir(base_dir)
    source_root = _find_resource_root(Path(base_dir))

    for name in _RESOURCE_DIRS:
        dest_dir = data_dir / name
        ensure_dir(dest_dir)
        if source_root:
            src_dir = source_root / name
            if src_dir.is_dir():
                _copy_tree(src_dir, dest_dir)
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

def ensure_data_dirs(base_dir: str) -> None:
    ensure_dir(_get_data_dir(base_dir))
    ensure_dir(_get_log_dir(base_dir))
    ensure_data_resources(base_dir)


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
    admin_login = panel.get("admin_login") or "admin"
    admin_password = panel.get("admin_password", "")

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
        admin_login=admin_login,
        admin_password=admin_password,
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
            "admin_login": cfg.admin_login,
            "admin_password": cfg.admin_password,
        }
        for k, v in desired_min.items():
            if panel.get(k) != v:
                need_write = True
                break

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
        "admin_login": cfg.admin_login,
        "admin_password": cfg.admin_password,
    }

    for k, v in desired.items():
        parser.set("panel", k, v)

    with config_path.open("w", encoding="utf-8") as f:
        parser.write(f)



def get_panel_log_path(base_dir: str) -> Path:
    return _get_log_dir(base_dir) / "panel.log"


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
    from werkzeug.security import generate_password_hash

    if base_dir is None:
        # Если base_dir не передали, пробуем оттуда, где лежит конфиг
        base_dir = os.path.abspath(os.getcwd())

    # Создаём приложение, чтобы гарантировать наличие таблиц и ролей
    try:
        from .app_factory import create_app
    except Exception:
        # Фолбек: обновим напрямую через SQLAlchemy
        create_app = None

    if create_app is not None:
        try:
            app, appbuilder, _runtime = create_app(base_dir, start_scheduler=False)
        except Exception:
            app = None
        if app is not None:
            with app.app_context():
                sm = appbuilder.sm
                role_obj = sm.find_role(role) or sm.add_role(role)
                user = sm.find_user(username=username)
                hashed = generate_password_hash(new_password)
                if hasattr(sm, "get_password_hash"):
                    try:
                        hashed = sm.get_password_hash(new_password)  # type: ignore[call-arg]
                    except Exception:
                        pass
                if user:
                    user.password = hashed
                    if role_obj and role_obj not in user.roles:
                        user.roles.append(role_obj)
                else:
                    sm.add_user(
                        username=username,
                        first_name="Admin",
                        last_name="User",
                        email="admin@localhost",
                        role=role_obj,
                        password=new_password,
                    )
                _commit_sm_session(sm)
            cfg.setup_complete = True
            cfg.admin_login = username
            cfg.admin_password = new_password
            save_config(base_dir, cfg)
            return

    # Прямое обновление через SQLAlchemy (если фабрика не импортировалась)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from flask_appbuilder.security.sqla.models import User, Role

    db_path = get_db_path(base_dir)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        User.metadata.create_all(engine)
    except Exception:
        pass
    Session = sessionmaker(bind=engine)
    session = Session()

    role_obj = session.query(Role).filter_by(name=role).first()
    if not role_obj:
        role_obj = Role(name=role)
        session.add(role_obj)
        session.commit()

    user = session.query(User).filter_by(username=username).first()
    if not user:
        user = User(
            first_name="Admin",
            last_name="User",
            username=username,
            email="admin@localhost",
            active=True,
        )
        user.roles.append(role_obj)
        session.add(user)

    user.password = generate_password_hash(new_password)
    session.commit()
    session.close()

    cfg.setup_complete = True
    cfg.admin_login = username
    cfg.admin_password = new_password
    save_config(base_dir, cfg)
