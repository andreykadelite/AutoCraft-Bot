from __future__ import annotations
import codecs
import datetime as dt
import logging
import platform
import os
import sys
import time
import threading
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Tuple

from flask import Flask, jsonify, redirect, render_template, request, url_for, flash, g, session

from .config import (
    PanelConfig,
    PanelDebugLogConfig,
    create_panel_user,
    get_panel_bootstrap_state,
    get_db_path,
    get_panel_log_path,
    load_config,
    load_debug_log_config,
    save_config,
    save_debug_log_config,
)
from .db import db
from .utils import ensure_dir

_APP_CACHE_LOCK = threading.Lock()
_APP_CACHE: dict[str, tuple[Flask, Any, Dict[str, Any]]] = {}

_PANEL_LOGGER_NAME = "panel"
_REQUEST_LOGGER_NAME = "panel.request"
_PANEL_HANDLER_TAG = "_panel_handler"
_REQUEST_SLOW_MS = 1500.0
_REQUEST_NOISY_PATHS = {
    "/remote-desktop/snapshot",
    "/remote-desktop/status",
    "/remote-desktop/stream",
}
_REQUEST_NOISY_PREFIXES = ("/static/",)
_SESSION_BOOT_KEY = "panel_boot_id"

_PANEL_HANDLER: RotatingFileHandler | None = None
_PANEL_HANDLER_BASE: str | None = None
_PANEL_HANDLER_SIGNATURE: tuple[str, str, int, int] | None = None
_LOGGING_LOCK = threading.Lock()


def _build_flask_config(base_dir: str, cfg: PanelConfig) -> Dict[str, Any]:
    from flask_appbuilder.security.manager import AUTH_DB
    db_path = get_db_path(base_dir)
    return {
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SECRET_KEY": cfg.secret_key,
        "WTF_CSRF_ENABLED": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": 60 * 60,
        "AUTH_TYPE": AUTH_DB,
        "AUTH_USERNAME_CI": False,
        "APP_NAME": "Панель управления AutoCraft",
        "BABEL_DEFAULT_LOCALE": "ru",
        "LANGUAGES": {"ru": {"flag": "ru", "name": "Русский"}},
    }


def _ensure_db_tables(app: Flask) -> None:
    with app.app_context():
        db.create_all()


def _grant_view_permissions(
    sm,
    role,
    view_name: str,
    permissions: list[str],
    menu_label: str | None = None,
) -> None:
    perms = list(permissions)
    if "menu_access" not in perms:
        perms.insert(0, "menu_access")
    for perm in perms:
        perm_view = None
        if perm == "menu_access":
            if menu_label:
                perm_view = sm.find_permission_view_menu(perm, menu_label)
            if not perm_view:
                perm_view = sm.find_permission_view_menu(perm, view_name)
        else:
            perm_view = sm.find_permission_view_menu(perm, view_name)
        if perm_view:
            sm.add_permission_role(role, perm_view)


def _remove_permission_role(sm, role, perm_view) -> None:
    for name in ("remove_permission_role", "del_permission_role"):
        func = getattr(sm, name, None)
        if callable(func):
            try:
                func(role, perm_view)
                return
            except Exception:
                pass
    try:
        if perm_view in role.permissions:
            role.permissions.remove(perm_view)
    except Exception:
        pass


def _revoke_view_permissions(
    sm,
    role,
    view_name: str,
    permissions: list[str],
    menu_label: str | None = None,
) -> None:
    perms = list(permissions)
    if "menu_access" not in perms:
        perms.insert(0, "menu_access")
    for perm in perms:
        if perm == "menu_access":
            perm_view = None
            if menu_label:
                perm_view = sm.find_permission_view_menu(perm, menu_label)
            if not perm_view:
                perm_view = sm.find_permission_view_menu(perm, view_name)
        else:
            perm_view = sm.find_permission_view_menu(perm, view_name)
        if perm_view:
            _remove_permission_role(sm, role, perm_view)


def _grant_menu_access(sm, role, menu_name: str) -> None:
    if not menu_name:
        return
    perm_view = sm.find_permission_view_menu("menu_access", menu_name)
    if perm_view:
        sm.add_permission_role(role, perm_view)


def _build_view_menu_map(appbuilder: AppBuilder) -> tuple[dict[str, str], dict[str, str]]:
    menu = getattr(appbuilder, "menu", None)
    if not menu:
        return {}, {}
    view_menu: dict[str, str] = {}
    view_category: dict[str, str] = {}
    for category in getattr(menu, "menu", []):
        category_label = getattr(category, "name", None) or getattr(category, "label", None)
        for child in getattr(category, "childs", []):
            baseview = getattr(child, "baseview", None)
            if not baseview:
                continue
            view_name = type(baseview).__name__
            label = getattr(child, "name", None) or getattr(child, "label", None)
            if view_name and label:
                view_menu[view_name] = label
            if view_name and category_label:
                view_category[view_name] = category_label
    return view_menu, view_category


def _get_sm_session(sm):
    if hasattr(sm, "get_session"):
        try:
            return sm.get_session()  # type: ignore[call-arg]
        except Exception:
            return sm.get_session  # type: ignore[return-value]
    if hasattr(sm, "appbuilder") and hasattr(sm.appbuilder, "get_session"):
        return sm.appbuilder.get_session
    return db.session


def _setup_roles(appbuilder: AppBuilder) -> None:
    sm = appbuilder.sm

    roles = {
        "Super Admin": sm.find_role("Super Admin") or sm.add_role("Super Admin"),
        "Admin": sm.find_role("Admin") or sm.add_role("Admin"),
        "Operator": sm.find_role("Operator") or sm.add_role("Operator"),
        "Viewer": sm.find_role("Viewer") or sm.add_role("Viewer"),
        "Auditor": sm.find_role("Auditor") or sm.add_role("Auditor"),
    }

    # Super Admin получает все разрешения
    from flask_appbuilder.security.sqla.models import PermissionView

    session = _get_sm_session(sm)
    all_permissions = session.query(PermissionView).all()
    for perm_view in all_permissions:
        sm.add_permission_role(roles["Super Admin"], perm_view)

    # Карта прав для остальных ролей (view_name -> permissions)
    role_map = {
        "Admin": {
            "WacIndexView": ["can_index"],
            "ServerView": ["can_list", "can_action"],
            "FileManagerView": ["can_list", "can_action"],
            "RemoteDesktopView": ["can_list", "can_action"],
            "LiveStreamView": ["can_list", "can_action"],
            "EventLogsView": ["can_list"],
            "ServicesView": ["can_list", "can_action"],
            "ProcessesView": ["can_list", "can_action"],
            "TerminalView": ["can_list", "can_action"],
            "DeviceManagerView": ["can_list", "can_action"],
            "PowerView": ["can_list"],
            "InternalMessengerView": ["can_list", "can_action"],
            "CommunicationCenterView": ["can_list", "can_action"],
            "SystemNotifyCenterView": ["can_list"],
            "MetricsView": ["can_list"],
            "StorageView": ["can_list"],
            "NetworkingView": ["can_list"],
            "TasksView": ["can_list", "can_action"],
            "JobView": ["can_list", "can_show"],
            "AuditView": ["can_list", "can_show"],
            "ExtensionsView": ["can_list", "can_action"],
            "SettingsView": ["can_edit", "can_action"],
            "AdminBroadcastView": ["can_list", "can_action"],
            "AutoCraftStatusView": ["can_list"],
            "AutoCraftOpsView": ["can_list", "can_action"],
            "RegistryEditorView": ["can_list", "can_action"],
        },
        "Operator": {
            "WacIndexView": ["can_index"],
            "ServerView": ["can_list", "can_action"],
            "EventLogsView": ["can_list"],
            "ServicesView": ["can_list", "can_action"],
            "ProcessesView": ["can_list", "can_action"],
            "TerminalView": ["can_list", "can_action"],
            "FileManagerView": ["can_list", "can_action"],
            "RemoteDesktopView": ["can_list", "can_action"],
            "LiveStreamView": ["can_list", "can_action"],
            "DeviceManagerView": ["can_list", "can_action"],
            "PowerView": ["can_list"],
            "InternalMessengerView": ["can_list", "can_action"],
            "CommunicationCenterView": ["can_list", "can_action"],
            "SystemNotifyCenterView": ["can_list"],
            "MetricsView": ["can_list"],
            "TasksView": ["can_list", "can_action"],
            "JobView": ["can_list", "can_show"],
            "ExtensionsView": ["can_list", "can_action"],
            "AutoCraftStatusView": ["can_list"],
            "AutoCraftOpsView": ["can_list", "can_action"],
            "RegistryEditorView": ["can_list"],
        },
        "Viewer": {
            "WacIndexView": ["can_index"],
            "ServerView": ["can_list"],
            "EventLogsView": ["can_list"],
            "ServicesView": ["can_list"],
            "ProcessesView": ["can_list"],
            "MetricsView": ["can_list"],
            "StorageView": ["can_list"],
            "NetworkingView": ["can_list"],
            "FileManagerView": ["can_list"],
            "DeviceManagerView": ["can_list"],
            "PowerView": ["can_list"],
            "AutoCraftStatusView": ["can_list"],
            "RegistryEditorView": ["can_list"],
            "LiveStreamView": ["can_list"],
            "InternalMessengerView": ["can_list"],
            "CommunicationCenterView": ["can_list"],
            "SystemNotifyCenterView": ["can_list"],
        },
        "Auditor": {
            "WacIndexView": ["can_index"],
            "JobView": ["can_list", "can_show"],
            "AuditView": ["can_list", "can_show"],
            "InternalMessengerView": ["can_list", "can_action"],
            "CommunicationCenterView": ["can_list", "can_action"],
            "SystemNotifyCenterView": ["can_list"],
        },
    }

    view_menu_map, view_category_map = _build_view_menu_map(appbuilder)
    for role_name, views in role_map.items():
        role = roles[role_name]
        categories: set[str] = set()
        for view_name, perms in views.items():
            _grant_view_permissions(
                sm,
                role,
                view_name,
                perms,
                menu_label=view_menu_map.get(view_name),
            )
            category_label = view_category_map.get(view_name)
            if category_label:
                categories.add(category_label)
        for category_label in categories:
            _grant_menu_access(sm, role, category_label)

    # По умолчанию в разделе питания:
    # - Super Admin/Admin имеют can_list + can_action
    # - Operator/Viewer имеют только can_list
    power_menu_label = view_menu_map.get("PowerView")
    power_action_perm = sm.find_permission_view_menu("can_action", "PowerView")
    for role_name in ("Super Admin", "Admin", "Operator", "Viewer"):
        role = roles.get(role_name)
        if not role:
            continue
        power_perms = ["can_list"]
        if role_name in ("Super Admin", "Admin"):
            power_perms.append("can_action")
        _grant_view_permissions(
            sm,
            role,
            "PowerView",
            power_perms,
            menu_label=power_menu_label,
        )
        if role_name in ("Operator", "Viewer") and power_action_perm:
            _remove_permission_role(sm, role, power_action_perm)

    session.commit()


def _setup_routes(app: Flask, cfg: PanelConfig) -> None:
    @app.route("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "version": "1.0",
                "db_ok": True,
                "last_metrics": app.config.get("LAST_METRICS_AT"),
                "uptime": int(time.time() - app.config.get("STARTED_AT", time.time())),
            }
        )

    @app.route("/ready")
    def ready():
        return jsonify({"ready": True})

    @app.route("/favicon.ico")
    def favicon():
        try:
            from flask import send_from_directory

            static_folder = app.static_folder or ""
            return send_from_directory(static_folder, "favicon.ico")
        except Exception:
            return ("", 204)

    @app.route("/setup", methods=["GET", "POST"])
    def setup_admin():
        base_dir = app.config["BASE_DIR"]
        state = get_panel_bootstrap_state(base_dir)
        if bool(state.get("has_super_admin")):
            return redirect(url_for("AppBuilder.index"))

        default_login = "admin"
        if request.method == "POST":
            login = (request.form.get("login") or "").strip() or "admin"
            default_login = login
            pwd1 = (request.form.get("password") or "").strip()
            pwd2 = (request.form.get("password2") or "").strip()

            if len(login) < 3 or (" " in login):
                flash("Логин должен быть не короче 3 символов и без пробелов.", "warning")
            elif len(pwd1) < 6:
                flash("Пароль должен быть минимум 6 символов", "warning")
            elif pwd1 != pwd2:
                flash("Пароли не совпадают", "danger")
            else:
                try:
                    create_panel_user(base_dir, login, login, pwd1, role="Super Admin")
                except Exception as exc:
                    flash(f"Не удалось создать Super Admin: {exc}", "danger")
                    return render_template(
                        "setup.html",
                        default_login=default_login,
                        fixed_role="Super Admin",
                    )

                try:
                    cfg.setup_complete = True
                    save_config(base_dir, cfg)
                except Exception:
                    pass
                flash(f"Пользователь {login} (Super Admin) создан.", "success")
                return redirect(url_for("AppBuilder.index"))

        return render_template(
            "setup.html",
            default_login=default_login,
            fixed_role="Super Admin",
        )


def _ensure_log_file(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > 0:
            return
    except Exception:
        return
    try:
        ensure_dir(log_path.parent)
        log_path.write_bytes(codecs.BOM_UTF8)
    except Exception:
        pass


def _log_level_from_message(message: str) -> int:
    text = message.strip().lower()
    if text.startswith("[error]"):
        return logging.ERROR
    if text.startswith("[warn]"):
        return logging.WARNING
    if text.startswith("[req]") or text.startswith("[res]"):
        return logging.DEBUG
    return logging.INFO


def _mojibake_score(text: str) -> int:
    if not text:
        return 0
    return (
        text.count("Р") * 2
        + text.count("С") * 2
        + text.count("вЂ") * 3
        + text.count("Ð") * 2
        + text.count("Ñ") * 2
        + text.count("\uFFFD") * 5
    )


def _repair_cp1251_utf8_mojibake(text: str) -> str:
    if not text:
        return text
    source_score = _mojibake_score(text)
    if source_score < 4:
        return text
    try:
        candidate = text.encode("cp1251").decode("utf-8")
    except Exception:
        return text
    if not candidate:
        return text
    candidate_score = _mojibake_score(candidate)
    if candidate_score >= source_score:
        return text
    has_cyr = any(("А" <= ch <= "я") or ch in "Ёё" for ch in candidate)
    if not has_cyr and any(ord(ch) > 127 for ch in text):
        return text
    return candidate


class _MojibakeSafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            rendered = record.getMessage()
            fixed = _repair_cp1251_utf8_mojibake(rendered)
            if fixed != rendered:
                clone = logging.makeLogRecord(record.__dict__.copy())
                clone.msg = fixed
                clone.args = ()
                record = clone
        except Exception:
            pass
        return super().format(record)


def _attach_logger(logger: logging.Logger, handler: logging.Handler, level: int) -> None:
    for existing in logger.handlers:
        if getattr(existing, _PANEL_HANDLER_TAG, False):
            existing.setLevel(level)
            logger.setLevel(level)
            logger.propagate = False
            return
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def _remove_panel_handlers(logger: logging.Logger) -> None:
    for existing in list(logger.handlers):
        if not getattr(existing, _PANEL_HANDLER_TAG, False):
            continue
        try:
            logger.removeHandler(existing)
        except Exception:
            pass


def _resolve_runtime_log_level(level_name: str) -> int:
    text = str(level_name or "").strip().upper()
    if text == "MAX":
        return logging.NOTSET
    if text == "DEBUG":
        return logging.DEBUG
    if text == "INFO":
        return logging.INFO
    if text == "WARNING":
        return logging.WARNING
    if text == "ERROR":
        return logging.ERROR
    if text == "CRITICAL":
        return logging.CRITICAL
    return logging.NOTSET


def _is_verbose_level(level: int) -> bool:
    return level <= logging.DEBUG


def _external_logger_level(level: int) -> int:
    if level <= logging.NOTSET:
        return logging.DEBUG
    if level <= logging.DEBUG:
        return logging.INFO
    if level <= logging.INFO:
        return logging.WARNING
    return level


def _close_panel_handler() -> None:
    global _PANEL_HANDLER, _PANEL_HANDLER_BASE, _PANEL_HANDLER_SIGNATURE
    if _PANEL_HANDLER is None:
        return
    try:
        _PANEL_HANDLER.close()
    except Exception:
        pass
    _PANEL_HANDLER = None
    _PANEL_HANDLER_BASE = None
    _PANEL_HANDLER_SIGNATURE = None


def _get_panel_handler(base_dir: str, log_cfg: PanelDebugLogConfig, level: int) -> RotatingFileHandler:
    global _PANEL_HANDLER, _PANEL_HANDLER_BASE, _PANEL_HANDLER_SIGNATURE
    normalized = log_cfg.normalized()
    log_path = get_panel_log_path(base_dir)
    signature = (
        base_dir,
        str(log_path),
        int(normalized.max_bytes),
        int(normalized.backup_count),
    )
    if _PANEL_HANDLER is not None and _PANEL_HANDLER_SIGNATURE == signature and _PANEL_HANDLER_BASE == base_dir:
        _PANEL_HANDLER.setLevel(level)
        return _PANEL_HANDLER

    _close_panel_handler()
    _ensure_log_file(log_path)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=normalized.max_bytes,
        backupCount=normalized.backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        _MojibakeSafeFormatter("%(asctime)s %(levelname)-8s %(name)s:%(lineno)d - %(message)s")
    )
    setattr(handler, _PANEL_HANDLER_TAG, True)
    _PANEL_HANDLER = handler
    _PANEL_HANDLER_BASE = base_dir
    _PANEL_HANDLER_SIGNATURE = signature
    return handler


def _is_debug_logging_enabled(base_dir: str | None) -> bool:
    if not base_dir:
        return False
    try:
        cfg = load_debug_log_config(base_dir)
        return bool(cfg.enabled)
    except Exception:
        return False


def _ensure_panel_logger(base_dir: str, log_cfg: PanelDebugLogConfig) -> logging.Logger:
    normalized = log_cfg.normalized()
    logger = logging.getLogger(_PANEL_LOGGER_NAME)
    if not normalized.enabled:
        _remove_panel_handlers(logger)
        return logger
    level = _resolve_runtime_log_level(normalized.level)
    handler = _get_panel_handler(base_dir, normalized, level)
    _attach_logger(logger, handler, level)
    return logger


def _append_panel_log(base_dir: str | None, message: str) -> None:
    message = _repair_cp1251_utf8_mojibake(message)
    level = _log_level_from_message(message)
    logger = logging.getLogger(_PANEL_LOGGER_NAME)
    if logger.handlers:
        try:
            logger.log(level, message)
            return
        except Exception:
            pass

    if not base_dir or not _is_debug_logging_enabled(base_dir):
        return

    try:
        log_path = get_panel_log_path(base_dir)
    except Exception:
        return

    try:
        ensure_dir(log_path.parent)
        _ensure_log_file(log_path)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


_SENSITIVE_FIELDS = ("password", "passwd", "secret", "token", "api", "csrf", "authorization")
_MAX_LOG_VALUE_LEN = 200


def _clip_log_value(value: Any) -> str:
    text = str(value)
    if len(text) > _MAX_LOG_VALUE_LEN:
        return text[:_MAX_LOG_VALUE_LEN] + "...(truncated)"
    return text


def _mask_sensitive(key: str, value: Any) -> str:
    low = key.lower()
    if any(item in low for item in _SENSITIVE_FIELDS):
        return "***"
    if isinstance(value, (list, tuple)):
        return str([_clip_log_value(v) for v in value])
    return _clip_log_value(value)


def _safe_mapping(data: Any) -> dict[str, Any]:
    if not data:
        return {}
    if hasattr(data, "to_dict"):
        try:
            data = data.to_dict(flat=False)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        try:
            data = dict(data)
        except Exception:
            return {}
    return {str(k): _mask_sensitive(str(k), v) for k, v in data.items()}


def _format_request_line(debug: bool = False) -> str:
    try:
        parts = [f"{request.method} {request.path}"]
    except Exception:
        return "<request unavailable>"

    try:
        args = _safe_mapping(request.args)
        if args:
            parts.append(f"args={args}")
    except Exception:
        pass

    if debug:
        try:
            view_args = _safe_mapping(getattr(request, "view_args", None))
            if view_args:
                parts.append(f"view_args={view_args}")
        except Exception:
            pass
        try:
            form = _safe_mapping(request.form)
            if form:
                parts.append(f"form={form}")
        except Exception:
            pass
        try:
            if request.mimetype == "application/json":
                json_data = request.get_json(silent=True)
            else:
                json_data = None
            if isinstance(json_data, dict):
                json_data = _safe_mapping(json_data)
            elif json_data is not None:
                json_data = _clip_log_value(json_data)
            if json_data:
                parts.append(f"json={json_data}")
        except Exception:
            pass
        try:
            parts.append(f"content_type={request.content_type}")
        except Exception:
            pass

    return " ".join(parts)


def _is_noisy_path(path: str) -> bool:
    if not path:
        return False
    if path in _REQUEST_NOISY_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _REQUEST_NOISY_PREFIXES)


def _pick_request_log_level(status_code: int, duration_ms: float | None) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    if duration_ms is not None and duration_ms >= _REQUEST_SLOW_MS:
        return logging.INFO
    return logging.INFO


def _setup_request_logging(app: Flask) -> None:
    @app.before_request
    def _log_request_start():
        g.request_id = uuid.uuid4().hex[:8]
        g.request_started_at = time.monotonic()

    @app.after_request
    def _log_request_end(response):
        request_logger = logging.getLogger(_REQUEST_LOGGER_NAME)
        path = request.path or ""
        verbose = bool(app.config.get("PANEL_DEBUG_LOG_VERBOSE", False))
        try:
            size = response.calculate_content_length()
        except Exception:
            size = None
        started_at = getattr(g, "request_started_at", None)
        duration_ms = None
        if started_at is not None:
            try:
                duration_ms = (time.monotonic() - started_at) * 1000.0
            except Exception:
                duration_ms = None

        level = _pick_request_log_level(response.status_code, duration_ms)
        if _is_noisy_path(path) and response.status_code < 400:
            if not verbose or not request_logger.isEnabledFor(logging.DEBUG):
                return response
            level = logging.DEBUG

        req_id = getattr(g, "request_id", "-")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        if level >= logging.WARNING or verbose:
            line = _format_request_line(debug=verbose)
            request_logger.log(
                level,
                "request req_id=%s %s status=%s duration_ms=%.1f size=%s ip=%s",
                req_id,
                line,
                response.status_code,
                duration_ms or 0.0,
                size,
                ip,
            )
        else:
            request_logger.log(
                level,
                "request req_id=%s method=%s path=%s status=%s duration_ms=%.1f size=%s ip=%s",
                req_id,
                request.method,
                path,
                response.status_code,
                duration_ms or 0.0,
                size,
                ip,
            )
        return response


def _setup_session_restart_guard(app: Flask) -> None:
    app.config["SESSION_BOOT_ID"] = uuid.uuid4().hex

    @app.before_request
    def _enforce_session_boot():
        if getattr(g, "autocraft_proxy_request", False):
            return None
        from flask_login import current_user, logout_user

        try:
            is_authenticated = bool(current_user.is_authenticated)
        except Exception:
            # Защита от некорректных cookie (_user_id), например старых proxy-сессий.
            for key in ("_user_id", "_fresh", "_id"):
                try:
                    session.pop(key, None)
                except Exception:
                    pass
            return None

        if not is_authenticated:
            return None

        expected = app.config.get("SESSION_BOOT_ID")
        stored = session.get(_SESSION_BOOT_KEY)
        if expected and stored == expected:
            return None

        try:
            logout_user()
        except Exception:
            pass
        session.clear()

        is_api = (request.path or "").startswith("/api/")
        if is_api:
            return (
                jsonify(
                    {
                        "error": "session_expired",
                        "message": "Сессия завершена после перезапуска панели. Войдите снова.",
                    }
                ),
                401,
            )

        flash("Сессия завершена после перезапуска панели. Войдите снова.", "warning")

        endpoint = request.endpoint or ""
        if endpoint.endswith(".login") or endpoint.endswith(".login_progress") or endpoint.endswith(
            ".login_progress_status"
        ):
            return None

        return redirect(url_for("PanelAuthDBView.login", next=request.url))


def _setup_message_notifications(app: Flask) -> None:
    from flask_login import current_user
    from sqlalchemy import func

    from .models.audit import AuditLog
    from .models.internal_messenger import InternalChatMessage, InternalChatState
    from .models.messages import UserMessage
    from .models.user_notification_state import (
        UserNotificationState,
        ensure_user_notification_state_schema,
    )
    from .unread_counters import count_communications_unread, count_messenger_unread

    @app.before_request
    def _load_message_notifications():
        if getattr(g, "autocraft_proxy_request", False):
            return None
        path = request.path or ""
        if path.startswith("/static/"):
            return None
        try:
            is_authenticated = bool(current_user.is_authenticated)
        except Exception:
            for key in ("_user_id", "_fresh", "_id"):
                try:
                    session.pop(key, None)
                except Exception:
                    pass
            return None
        if not is_authenticated:
            return None

        try:
            unread_count = count_communications_unread(int(current_user.id))
        except Exception:
            unread_count = 0

        g.unread_messages_count = unread_count
        last_seen = session.get("messages_unread_seen", 0) or 0
        if unread_count > last_seen:
            g.new_message_notice = True
            session["messages_unread_seen"] = unread_count
        elif unread_count < last_seen:
            session["messages_unread_seen"] = unread_count

        try:
            unread_chat_count = count_messenger_unread(int(current_user.id))
        except Exception:
            unread_chat_count = 0

        g.unread_chat_messages_count = unread_chat_count
        chat_last_seen = session.get("messenger_unread_seen", 0) or 0
        if unread_chat_count > chat_last_seen:
            g.new_chat_message_notice = True
            session["messenger_unread_seen"] = unread_chat_count
        elif unread_chat_count < chat_last_seen:
            session["messenger_unread_seen"] = unread_chat_count

        system_unread_count = 0
        try:
            ensure_user_notification_state_schema()
            username = (getattr(current_user, "username", "") or "").strip()
            if username:
                latest_system_id = (
                    db.session.query(func.max(AuditLog.id))
                    .filter(
                        AuditLog.user == username,
                        AuditLog.source.in_(("web", "api")),
                        AuditLog.action != "login",
                    )
                    .scalar()
                )
                latest_system_id = int(latest_system_id or 0)
                state = (
                    db.session.query(UserNotificationState)
                    .filter(UserNotificationState.user_id == current_user.id)
                    .first()
                )
                if not state:
                    latest_messenger_id = (
                        db.session.query(func.max(InternalChatMessage.id))
                        .join(
                            InternalChatState,
                            InternalChatState.thread_id == InternalChatMessage.thread_id,
                        )
                        .filter(
                            InternalChatState.user_id == int(current_user.id),
                            InternalChatState.is_hidden.is_(False),
                            InternalChatMessage.sender_id != int(current_user.id),
                        )
                        .scalar()
                    )
                    latest_messenger_id = int(latest_messenger_id or 0)
                    latest_communications_id = (
                        db.session.query(func.max(UserMessage.id))
                        .filter(
                            UserMessage.recipient_id == int(current_user.id),
                            UserMessage.deleted_by_recipient.is_(False),
                        )
                        .scalar()
                    )
                    latest_communications_id = int(latest_communications_id or 0)
                    state = UserNotificationState(
                        user_id=int(current_user.id),
                        system_last_read_audit_id=latest_system_id,
                        system_last_shown_audit_id=latest_system_id,
                        messenger_last_shown_message_id=latest_messenger_id,
                        communications_last_shown_message_id=latest_communications_id,
                    )
                    db.session.add(state)
                    db.session.commit()

                cleared_before_id = int(
                    getattr(state, "system_history_cleared_before_audit_id", 0) or 0
                )
                effective_read_id = max(
                    int(state.system_last_read_audit_id or 0),
                    int(cleared_before_id),
                )
                unread_query = db.session.query(func.count(AuditLog.id)).filter(
                    AuditLog.user == username,
                    AuditLog.source.in_(("web", "api")),
                    AuditLog.action != "login",
                )
                if effective_read_id > 0:
                    unread_query = unread_query.filter(
                        AuditLog.id > int(effective_read_id)
                    )
                system_unread_count = int(unread_query.scalar() or 0)
        except Exception:
            system_unread_count = 0

        g.system_unread_count = system_unread_count

        return None


def _configure_logging(
    app: Flask,
    base_dir: str,
    debug_log_cfg: PanelDebugLogConfig,
) -> PanelDebugLogConfig:
    normalized = debug_log_cfg.normalized()
    panel_level = _resolve_runtime_log_level(normalized.level)
    request_level = logging.DEBUG if _is_verbose_level(panel_level) else max(logging.INFO, panel_level)
    ops_level = logging.DEBUG if _is_verbose_level(panel_level) else max(logging.INFO, panel_level)
    external_level = _external_logger_level(panel_level)
    log_path = str(get_panel_log_path(base_dir))

    managed_loggers = [
        app.logger,
        logging.getLogger(_PANEL_LOGGER_NAME),
        logging.getLogger(_REQUEST_LOGGER_NAME),
        logging.getLogger("panel.ops"),
        logging.getLogger("panel.frontend"),
        logging.getLogger("panel.remote_desktop"),
        logging.getLogger("panel.plugins"),
        logging.getLogger("py.warnings"),
    ]
    external_logger_names = (
        "werkzeug",
        "waitress",
        "waitress.server",
        "waitress.queue",
        "waitress.task",
        "flask_appbuilder",
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "apscheduler",
    )

    with _LOGGING_LOCK:
        try:
            if not normalized.enabled:
                for logger in managed_loggers:
                    _remove_panel_handlers(logger)
                for name in external_logger_names:
                    _remove_panel_handlers(logging.getLogger(name))
                logging.captureWarnings(False)
                _close_panel_handler()
                app.config["PANEL_DEBUG_LOG_ENABLED"] = False
                app.config["PANEL_DEBUG_LOG_LEVEL"] = normalized.level
                app.config["PANEL_DEBUG_LOG_VERBOSE"] = False
                app.config["PANEL_DEBUG_LOG_MAX_BYTES"] = normalized.max_bytes
                app.config["PANEL_DEBUG_LOG_BACKUP_COUNT"] = normalized.backup_count
                app.config["PANEL_DEBUG_LOG_PATH"] = log_path
                return normalized

            handler = _get_panel_handler(base_dir, normalized, panel_level)
            _attach_logger(app.logger, handler, panel_level)
            _attach_logger(logging.getLogger(_PANEL_LOGGER_NAME), handler, panel_level)
            _attach_logger(logging.getLogger(_REQUEST_LOGGER_NAME), handler, request_level)
            _attach_logger(logging.getLogger("panel.ops"), handler, ops_level)
            _attach_logger(logging.getLogger("panel.frontend"), handler, ops_level)
            _attach_logger(logging.getLogger("panel.remote_desktop"), handler, ops_level)
            _attach_logger(logging.getLogger("panel.plugins"), handler, ops_level)

            for name in external_logger_names:
                _attach_logger(logging.getLogger(name), handler, external_level)

            logging.captureWarnings(True)
            _attach_logger(logging.getLogger("py.warnings"), handler, logging.WARNING)
            app.config["PANEL_DEBUG_LOG_ENABLED"] = True
            app.config["PANEL_DEBUG_LOG_LEVEL"] = normalized.level
            app.config["PANEL_DEBUG_LOG_VERBOSE"] = _is_verbose_level(panel_level)
            app.config["PANEL_DEBUG_LOG_MAX_BYTES"] = normalized.max_bytes
            app.config["PANEL_DEBUG_LOG_BACKUP_COUNT"] = normalized.backup_count
            app.config["PANEL_DEBUG_LOG_PATH"] = log_path
            return normalized
        except Exception:
            app.config["PANEL_DEBUG_LOG_ENABLED"] = False
            app.config["PANEL_DEBUG_LOG_LEVEL"] = normalized.level
            app.config["PANEL_DEBUG_LOG_VERBOSE"] = False
            app.config["PANEL_DEBUG_LOG_MAX_BYTES"] = normalized.max_bytes
            app.config["PANEL_DEBUG_LOG_BACKUP_COUNT"] = normalized.backup_count
            app.config["PANEL_DEBUG_LOG_PATH"] = log_path
            return normalized


def apply_runtime_debug_logging(
    app: Flask,
    base_dir: str,
    debug_log_cfg: PanelDebugLogConfig | None = None,
    persist: bool = False,
) -> PanelDebugLogConfig:
    cfg = (debug_log_cfg or load_debug_log_config(base_dir)).normalized()
    if persist:
        save_debug_log_config(base_dir, cfg)
    return _configure_logging(app, base_dir, cfg)


def _setup_error_handlers(app: Flask) -> None:
    from werkzeug.exceptions import HTTPException
    import traceback

    @app.errorhandler(Exception)
    def _handle_exception(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        base_dir = app.config.get("BASE_DIR")
        try:
            app.logger.exception("Ошибка панели: %s", exc)
        except Exception:
            pass
        try:
            verbose = bool(app.config.get("PANEL_DEBUG_LOG_VERBOSE", False))
            info = [
                "=== Ошибка панели ===",
                f"Local: {dt.datetime.now().isoformat()}",
                f"Request: {_format_request_line(verbose)}",
                f"Remote: {request.remote_addr}",
                f"User-Agent: {request.headers.get('User-Agent', '')}" ,
                f"Exception: {repr(exc)}",
                traceback.format_exc(),
                "=== Конец ошибки ===",
            ]
            _append_panel_log(base_dir, "\n".join(info))
        except Exception:
            pass
        log_hint = app.config.get("PANEL_DEBUG_LOG_PATH")
        if not log_hint and base_dir:
            try:
                log_hint = str(get_panel_log_path(base_dir))
            except Exception:
                log_hint = ""
        try:
            log_hint = str(Path(log_hint)) if log_hint else "log/panel_debug.log"
        except Exception:
            log_hint = "log/panel_debug.log"
        return (f"Внутренняя ошибка сервера. Подробности записаны в {log_hint}", 500)


def _pick_onefile_dir(path: Path) -> Path | None:
    for parent in [path] + list(path.parents):
        name = parent.name.lower()
        if name.startswith("onefile_") or name.startswith("onefil") or name.startswith("_mei"):
            return parent
    return None


def _guess_onefile_extract_dir() -> Path | None:
    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if value:
            try:
                p = Path(value)
                if p.exists():
                    return p if p.is_dir() else p.parent
            except Exception:
                pass

    try:
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None)
        if main_file:
            p = Path(main_file).resolve()
            found = _pick_onefile_dir(p)
            if found:
                return found
    except Exception:
        pass

    try:
        p = Path(sys.executable).resolve()
        found = _pick_onefile_dir(p)
        if found:
            return found
    except Exception:
        pass

    return None


def _find_limits_resource_root(base_dir: str) -> Path | None:
    candidates: list[Path] = []

    def _add(path: Path | None) -> None:
        if not path:
            return
        try:
            path = path.resolve()
        except Exception:
            path = Path(str(path))
        if path not in candidates:
            candidates.append(path)

    base_path = Path(base_dir)
    _add(base_path / "data" / "limits_resources")
    _add(base_path / "moduls" / "web_dashboard" / "limits_resources")
    _add(base_path / "web_dashboard" / "limits_resources")

    try:
        exe_dir = Path(sys.executable).resolve().parent
        _add(exe_dir / "moduls" / "web_dashboard" / "limits_resources")
        _add(exe_dir / "web_dashboard" / "limits_resources")
    except Exception:
        pass

    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            mp = Path(meipass)
            _add(mp / "moduls" / "web_dashboard" / "limits_resources")
            _add(mp / "web_dashboard" / "limits_resources")
    except Exception:
        pass

    try:
        argv_dir = Path(sys.argv[0]).resolve().parent
        _add(argv_dir / "moduls" / "web_dashboard" / "limits_resources")
        _add(argv_dir / "web_dashboard" / "limits_resources")
    except Exception:
        pass

    onefile_dir = _guess_onefile_extract_dir()
    if onefile_dir:
        _add(onefile_dir / "moduls" / "web_dashboard" / "limits_resources")
        _add(onefile_dir / "web_dashboard" / "limits_resources")

    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if value:
            env_path = Path(value)
            _add(env_path / "moduls" / "web_dashboard" / "limits_resources")
            _add(env_path / "web_dashboard" / "limits_resources")

    _add(Path(__file__).resolve().parent / "limits_resources")
    _add(Path.cwd() / "moduls" / "web_dashboard" / "limits_resources")
    _add(Path.cwd() / "web_dashboard" / "limits_resources")

    for root in candidates:
        probe = root / "resources" / "redis" / "lua_scripts" / "moving_window.lua"
        if probe.is_file():
            return root
    return None


def _copy_tree_files(src: Path, dest: Path) -> None:
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        if "__pycache__" in item.parts:
            continue
        rel = item.relative_to(src)
        target = dest / rel
        if target.exists():
            continue
        try:
            ensure_dir(target.parent)
            target.write_bytes(item.read_bytes())
        except Exception:
            continue


def _load_embedded_fab_templates() -> dict[str, str] | None:
    try:
        from .fab_templates_data import FAB_TEMPLATES
    except Exception:
        return None

    if not isinstance(FAB_TEMPLATES, dict):
        return None
    return FAB_TEMPLATES


def _write_embedded_fab_templates(templates_dir: Path, templates: dict[str, str]) -> bool:
    wrote = False
    for rel_path, content in templates.items():
        dest = templates_dir / rel_path
        if dest.exists():
            continue
        try:
            ensure_dir(dest.parent)
            dest.write_text(content, encoding="utf-8")
            wrote = True
        except Exception:
            continue

    return wrote


def _ensure_embedded_fab_templates(templates_dir: Path) -> bool:
    templates = _load_embedded_fab_templates()
    if not templates:
        return False
    return _write_embedded_fab_templates(templates_dir, templates)


def _looks_russian(text: str) -> bool:
    return any("А" <= ch <= "я" or ch in ("Ё", "ё") for ch in text)


def _ensure_auth_templates(templates_dir: Path, templates: dict[str, str] | None) -> None:
    if not templates:
        return
    targets = (
        "appbuilder/general/security/login_db.html",
        "appbuilder/general/security/login_progress.html",
    )
    for rel_path in targets:
        content = templates.get(rel_path)
        if not content:
            continue
        dest = templates_dir / rel_path
        try:
            if dest.is_file():
                try:
                    existing = dest.read_text(encoding="utf-8")
                except Exception:
                    existing = ""
                if existing and _looks_russian(existing):
                    continue
            ensure_dir(dest.parent)
            dest.write_text(content, encoding="utf-8")
        except Exception:
            continue


def _get_fab_resource_paths() -> tuple[Path | None, Path | None]:
    try:
        import flask_appbuilder
        pkg_root = Path(flask_appbuilder.__file__).resolve().parent
    except Exception:
        return None, None

    templates_path = pkg_root / "templates"
    static_path = pkg_root / "static" / "appbuilder"
    if not templates_path.is_dir():
        templates_path = None
    if not static_path.is_dir():
        static_path = None
    return templates_path, static_path


def _resolve_fab_static(static_dir: Path, fab_static: Path | None) -> Path | None:
    local_root = static_dir / "appbuilder"
    sentinel_local = local_root / "css" / "bootstrap.min.css"

    if sentinel_local.is_file():
        return local_root

    if fab_static and (fab_static / "css" / "bootstrap.min.css").is_file():
        return fab_static

    return None


def _find_limits_package_root() -> Path | None:
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
            return Path(loc)

    if spec.origin:
        try:
            return Path(spec.origin).resolve().parent
        except Exception:
            return Path(spec.origin).parent

    return None


def _patch_limits_resources(base_dir: str) -> None:
    fallback_root = _find_limits_resource_root(base_dir)
    if not fallback_root:
        return

    fallback_dir = fallback_root / "resources" / "redis" / "lua_scripts"

    pkg_root = _find_limits_package_root()
    if pkg_root is not None:
        target_dir = pkg_root / "resources" / "redis" / "lua_scripts"
        if not (target_dir / "moving_window.lua").is_file():
            try:
                ensure_dir(target_dir)
                for script in fallback_dir.glob("*.lua"):
                    dest = target_dir / script.name
                    if not dest.exists():
                        dest.write_bytes(script.read_bytes())
            except Exception:
                pass

    try:
        import limits
        from limits import util as limits_util
    except Exception:
        return

    if getattr(limits_util, "_autocraft_patched", False):
        return

    original = limits_util.get_package_data

    def _get_package_data(path: str) -> bytes:
        try:
            return original(path)
        except Exception:
            candidate = fallback_root / path
            if candidate.is_file():
                return candidate.read_bytes()
            if pkg_root is not None:
                candidate = pkg_root / path
                if candidate.is_file():
                    return candidate.read_bytes()
            raise

    limits_util.get_package_data = _get_package_data
    limits_util._autocraft_patched = True


def _patch_fab_messages() -> None:
    try:
        from flask_appbuilder import const as fab_const
    except Exception:
        return

    fab_const.FLAMSG_ERR_SEC_ACCESS_DENIED = "Доступ запрещен"
    fab_const.LOGMSG_ERR_SEC_ACCESS_DENIED = "Доступ запрещен для: %s на: %s"
    try:
        from flask_appbuilder.security import decorators as sec_decorators
    except Exception:
        return

    sec_decorators.FLAMSG_ERR_SEC_ACCESS_DENIED = fab_const.FLAMSG_ERR_SEC_ACCESS_DENIED
    sec_decorators.LOGMSG_ERR_SEC_ACCESS_DENIED = fab_const.LOGMSG_ERR_SEC_ACCESS_DENIED


def _resolve_resource_dir(base_dir: str) -> Path:
    candidates: list[Path] = []

    def _add(path: Path | None) -> None:
        if not path:
            return
        try:
            path = path.resolve()
        except Exception:
            path = Path(str(path))
        if path not in candidates:
            candidates.append(path)

    for env in ("PANEL_WEB_DASHBOARD_ROOT", "PANEL_WEB_DASHBOARD_PATH", "WEB_DASHBOARD_DIR"):
        value = os.environ.get(env)
        if value:
            _add(Path(value))

    base_path = Path(base_dir)
    _add(base_path / "moduls" / "web_dashboard")
    _add(base_path / "web_dashboard")
    _add(base_path / "data" / "web_dashboard")

    try:
        parent_path = base_path.parent
        _add(parent_path / "moduls" / "web_dashboard")
        _add(parent_path / "web_dashboard")
    except Exception:
        pass

    try:
        exe_dir = Path(sys.executable).resolve().parent
        _add(exe_dir / "moduls" / "web_dashboard")
        _add(exe_dir / "web_dashboard")
    except Exception:
        pass

    try:
        argv_dir = Path(sys.argv[0]).resolve().parent
        _add(argv_dir / "moduls" / "web_dashboard")
        _add(argv_dir / "web_dashboard")
    except Exception:
        pass

    onefile_dir = _guess_onefile_extract_dir()
    if onefile_dir:
        _add(onefile_dir / "moduls" / "web_dashboard")
        _add(onefile_dir / "web_dashboard")

    for env in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        value = os.environ.get(env)
        if value:
            env_path = Path(value)
            _add(env_path / "moduls" / "web_dashboard")
            _add(env_path / "web_dashboard")
            try:
                parent_env = env_path.parent
                _add(parent_env / "moduls" / "web_dashboard")
                _add(parent_env / "web_dashboard")
            except Exception:
                pass

    def _scan_temp_onefile() -> None:
        temp_root = os.environ.get("TEMP") or os.environ.get("TMP") or os.environ.get("TMPDIR")
        if not temp_root:
            return
        temp_path = Path(temp_root)

        try:
            pid_prefix = f"onefile_{os.getpid()}_"
        except Exception:
            pid_prefix = None

        matched: list[Path] = []
        for candidate in temp_path.iterdir():
            if not candidate.is_dir():
                continue
            name = candidate.name
            lower = name.lower()
            if lower.startswith("onefile_") or lower.startswith("onefil") or lower.startswith("_mei"):
                matched.append(candidate)

        if pid_prefix:
            for candidate in matched:
                if candidate.name.lower().startswith(pid_prefix):
                    _add(candidate / "moduls" / "web_dashboard")
                    _add(candidate / "web_dashboard")
                    return

        matched.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for candidate in matched:
            _add(candidate / "moduls" / "web_dashboard")
            _add(candidate / "web_dashboard")

    _scan_temp_onefile()

    _add(Path(__file__).resolve().parent)
    _add(Path.cwd() / "moduls" / "web_dashboard")
    _add(Path.cwd() / "web_dashboard")

    for root in candidates:
        templates_dir = root / "templates"
        static_dir = root / "static"
        if templates_dir.is_dir() and static_dir.is_dir():
            return root
        if root.name == "templates":
            parent = root.parent
            if (parent / "static").is_dir():
                return parent
        if root.name == "static":
            parent = root.parent
            if (parent / "templates").is_dir():
                return parent

    checked = ", ".join(str(p) for p in candidates[:6])
    raise FileNotFoundError(
        "Не удалось найти папку web_dashboard с templates и static. "
        "Проверьте, что web_dashboard находится рядом с модулем или задайте "
        "PANEL_WEB_DASHBOARD_ROOT. Проверены пути: " + checked
    )


def _get_device_name() -> str:
    name = (
        platform.node()
        or os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or ""
    )
    return name.strip()


def _normalize_base_dir(base_dir: str) -> str:
    try:
        return str(Path(base_dir).resolve()).casefold()
    except Exception:
        return os.path.abspath(base_dir).casefold()


def create_app(base_dir: str, start_scheduler: bool = True) -> Tuple[Flask, Any, Dict[str, Any]]:
    ensure_dir(Path(base_dir))
    cache_key = _normalize_base_dir(base_dir)
    with _APP_CACHE_LOCK:
        cached = _APP_CACHE.get(cache_key)
    if cached:
        app, appbuilder, context = cached
        debug_log_cfg = load_debug_log_config(base_dir)
        applied_debug_cfg = apply_runtime_debug_logging(app, base_dir, debug_log_cfg)
        context["debug_log_config"] = applied_debug_cfg
        if start_scheduler and not context.get("scheduler"):
            cfg = context.get("config") or load_config(base_dir)
            try:
                from .tasks.scheduler import start_scheduler as _start_scheduler

                scheduler, last_metrics_at = _start_scheduler(app, cfg)
                context["scheduler"] = scheduler
                context["last_metrics_at"] = last_metrics_at
            except Exception as exc:
                _append_panel_log(base_dir, f"[ERROR] Не удалось запустить планировщик: {repr(exc)}")
        return app, appbuilder, context

    cfg = load_config(base_dir)
    debug_log_cfg = load_debug_log_config(base_dir)

    _ensure_panel_logger(base_dir, debug_log_cfg)

    _append_panel_log(base_dir, f"[INIT] base_dir={base_dir}")
    _append_panel_log(base_dir, f"[INIT] db_path={get_db_path(base_dir)}")

    _patch_limits_resources(base_dir)
    _patch_fab_messages()
    fab_templates, fab_static = _get_fab_resource_paths()
    if fab_templates:
        _append_panel_log(base_dir, f"[INIT] fab_templates={fab_templates}")
    if fab_static:
        _append_panel_log(base_dir, f"[INIT] fab_static={fab_static}")

    from flask_appbuilder import AppBuilder
    from .security import PanelSecurityManager

    resource_root = _resolve_resource_dir(base_dir)
    _append_panel_log(base_dir, f"[INIT] resource_root={resource_root}")
    templates_dir = resource_root / "templates"
    static_dir = resource_root / "static"

    embedded_templates = _load_embedded_fab_templates()
    fab_static_root = _resolve_fab_static(static_dir, fab_static)
    if fab_static_root:
        _append_panel_log(base_dir, f"[INIT] fab_static_root={fab_static_root}")
    else:
        _append_panel_log(base_dir, "[WARN] Не найдены статические файлы FAB (css/js).")
    try:
        appbuilder_template = templates_dir / "appbuilder" / "base.html"
        if not appbuilder_template.is_file() and fab_templates:
            _copy_tree_files(fab_templates, templates_dir)
            _append_panel_log(base_dir, "[INIT] Скопированы шаблоны FAB в папку templates")
        if not appbuilder_template.is_file() and embedded_templates:
            if _write_embedded_fab_templates(templates_dir, embedded_templates):
                _append_panel_log(base_dir, "[INIT] Добавлены встроенные шаблоны FAB")
            else:
                _append_panel_log(
                    base_dir,
                    "[WARN] Встроенные шаблоны FAB доступны, но не удалось записать их на диск",
                )
        if not appbuilder_template.is_file() and not embedded_templates:
            _append_panel_log(base_dir, "[WARN] Встроенные шаблоны FAB не найдены")
        _ensure_auth_templates(templates_dir, embedded_templates)
    except Exception as exc:
        _append_panel_log(base_dir, f"[WARN] Не удалось подготовить шаблоны FAB: {repr(exc)}")

    app = Flask(
        __name__,
        static_folder=str(static_dir),
        template_folder=str(templates_dir),
    )

    if fab_static_root:
        try:
            app.config["FAB_STATIC_FOLDER"] = str(fab_static_root)
        except Exception:
            _append_panel_log(base_dir, "[WARN] Не удалось установить FAB_STATIC_FOLDER")

    try:
        from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

        loaders = []
        if app.jinja_loader:
            loaders.append(app.jinja_loader)
        if fab_templates:
            loaders.append(FileSystemLoader(str(fab_templates)))
        if embedded_templates:
            loaders.append(DictLoader(embedded_templates))
        if len(loaders) > 1:
            app.jinja_loader = ChoiceLoader(loaders)
    except Exception as exc:
        _append_panel_log(base_dir, f"[ERROR] Ошибка подключения шаблонов FAB: {repr(exc)}")

    try:
        appbuilder_template = templates_dir / "appbuilder" / "base.html"
        _append_panel_log(
            base_dir,
            f"[INIT] appbuilder_template_exists={appbuilder_template.is_file()} "
            f"embedded_fab_templates={'yes' if embedded_templates else 'no'}",
        )
    except Exception:
        pass

    app.config.update(_build_flask_config(base_dir, cfg))
    app.config["DEBUG"] = cfg.debug
    app.config["BASE_DIR"] = base_dir
    app.config["RESOURCE_ROOT"] = str(resource_root)
    app.config["DEVICE_NAME"] = _get_device_name()
    app.config["STARTED_AT"] = time.time()
    try:
        from flask_wtf.csrf import generate_csrf

        if "csrf_token" not in app.jinja_env.globals:
            app.jinja_env.globals["csrf_token"] = generate_csrf
    except Exception:
        pass

    db.init_app(app)

    from .views.dashboard import WacIndexView

    with app.app_context():
        appbuilder = AppBuilder(app, db.session, indexview=WacIndexView, security_manager_class=PanelSecurityManager)
        app.appbuilder = appbuilder

        try:
            appbuilder.add_css("css/panel.css")
            appbuilder.add_js("js/panel.js")
        except Exception:
            pass

        from .views import register_views
        from .api import api_bp

        register_views(appbuilder)
        app.register_blueprint(api_bp, url_prefix="/api")

        try:
            _ensure_db_tables(app)
            from .models.internal_messenger import ensure_internal_messenger_schema
            from .models.remote_access import ensure_remote_access_schema
            from .models.user_notification_state import ensure_user_notification_state_schema

            ensure_internal_messenger_schema()
            ensure_user_notification_state_schema()
            ensure_remote_access_schema()
        except Exception as exc:
            _append_panel_log(base_dir, f"[ERROR] Ошибка создания таблиц: {repr(exc)}")
            raise

        save_config(base_dir, cfg)
        _setup_roles(appbuilder)

        try:
            from .plugins import register_plugins

            register_plugins(appbuilder, app, base_dir, resource_root)
        except Exception as exc:
            _append_panel_log(base_dir, f"[WARN] Не удалось загрузить расширения: {repr(exc)}")
        try:
            # После регистрации расширений появляются новые PermissionView.
            # Повторная синхронизация гарантирует полный доступ роли Super Admin.
            _setup_roles(appbuilder)
        except Exception as exc:
            _append_panel_log(base_dir, f"[WARN] Не удалось повторно синхронизировать роли: {repr(exc)}")

    _setup_routes(app, cfg)
    debug_log_cfg = apply_runtime_debug_logging(app, base_dir, debug_log_cfg)
    _setup_request_logging(app)
    try:
        from .remote_control import (
            register_inbound_proxy_auth,
        )

        register_inbound_proxy_auth(app)
    except Exception as exc:
        _append_panel_log(base_dir, f"[WARN] Failed to initialize inbound remote proxy auth: {repr(exc)}")
    _setup_session_restart_guard(app)
    try:
        from .remote_control import register_outbound_proxy, register_template_context

        register_outbound_proxy(app)
        register_template_context(app)
    except Exception as exc:
        _append_panel_log(base_dir, f"[WARN] Failed to initialize remote proxy bridge: {repr(exc)}")
    _setup_message_notifications(app)
    _setup_error_handlers(app)

    scheduler = None
    last_metrics_at = None
    if start_scheduler:
        from .tasks.scheduler import start_scheduler

        scheduler, last_metrics_at = start_scheduler(app, cfg)

    context = {
        "config": cfg,
        "debug_log_config": debug_log_cfg,
        "scheduler": scheduler,
        "last_metrics_at": last_metrics_at,
    }
    with _APP_CACHE_LOCK:
        _APP_CACHE[cache_key] = (app, appbuilder, context)
    return app, appbuilder, context
