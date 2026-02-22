import importlib
import threading

from flask import current_app, request, flash, redirect, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..security import panel_has_access as has_access
from ..config import load_config, save_config
from ..utils import parse_bool, parse_int
from ..db import db
from ..models.audit import AuditLog

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)


def _is_csrf_valid() -> bool:
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _load_webpanel_backend():
    last_error: Exception | None = None
    for module_name in ("startrunmodulwebpanel", "moduls.startrunmodulwebpanel"):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось импортировать модуль управления веб-панелью.")


def _write_audit(
    actor: str,
    action: str,
    result: bool,
    source: str = "web",
    target: str = "",
    details: str = "",
) -> None:
    try:
        ip = request.remote_addr or ""
    except Exception:
        ip = ""
    try:
        log = AuditLog(
            user=str(actor or "web"),
            action=str(action),
            target=str(target or ""),
            result="ok" if result else "fail",
            source=str(source or "web"),
            ip=str(ip or ""),
            details=str(details or ""),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


class SettingsView(BaseView):
    route_base = "/settings"
    default_view = "edit"
    base_permissions = ["can_edit", "can_action"]

    @expose("/", methods=["GET", "POST"])
    @has_access
    def edit(self):
        base_dir = current_app.config["BASE_DIR"]
        cfg = load_config(base_dir)
        can_restart = self.appbuilder.sm.has_access("can_action", self.class_permission_name)

        if request.method == "POST":
            actor = getattr(current_user, "username", "") or "web"
            host = (request.form.get("host") or cfg.host).strip()
            port = parse_int(request.form.get("port"), cfg.port)
            debug = parse_bool(request.form.get("debug"), cfg.debug)
            retention_days = parse_int(request.form.get("retention_days"), cfg.retention_days)
            refresh_seconds = parse_int(
                request.form.get("overview_refresh_seconds"),
                cfg.overview_refresh_seconds,
            )

            if refresh_seconds <= 0:
                refresh_seconds = 0
            else:
                refresh_seconds = max(2, min(refresh_seconds, 120))

            cfg.host = host
            cfg.port = port
            cfg.debug = debug
            cfg.retention_days = retention_days
            cfg.overview_refresh_seconds = refresh_seconds
            save_config(base_dir, cfg)
            _write_audit(
                actor=actor,
                action="panel_settings_update",
                result=True,
                source="web",
                details=(
                    f"host={cfg.host} port={cfg.port} debug={int(bool(cfg.debug))} "
                    f"retention={cfg.retention_days} refresh={cfg.overview_refresh_seconds}"
                ),
            )

            flash("Настройки сохранены. Для применения перезапустите панель.", "success")
            return redirect(url_for("SettingsView.edit"))

        return self.render_template("settings.html", cfg=cfg, can_restart=can_restart)

    @expose("/restart", methods=["POST"])
    @has_access
    @permission_name("action")
    def restart(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("SettingsView.edit"))

        try:
            backend = _load_webpanel_backend()
        except Exception as exc:
            flash(f"Не удалось загрузить модуль управления панелью: {exc}", "danger")
            return redirect(url_for("SettingsView.edit"))

        actor = getattr(current_user, "username", "") or "web"

        def _restart_worker() -> None:
            ok = False
            message = ""
            try:
                ok, message = backend.restart_panel_sync(actor=actor, source="web")
            except Exception as exc:
                message = str(exc) or repr(exc)
            log_line = (
                f"[WEB][SETTINGS] panel restart {'ok' if ok else 'fail'} actor={actor} details={message}"
            )
            logger = getattr(backend, "write_bot_log", None)
            if callable(logger):
                try:
                    logger(log_line)
                except Exception:
                    pass

        threading.Thread(
            target=_restart_worker,
            name="webpanel_restart_from_settings",
            daemon=True,
        ).start()

        flash(
            "Команда перезапуска отправлена. Панель будет временно недоступна во время перезапуска.",
            "warning",
        )
        return redirect(url_for("SettingsView.edit"))
