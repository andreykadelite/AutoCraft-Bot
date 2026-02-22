from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_wtf.csrf import validate_csrf

from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api

from ..ops.operations.autocraft import (
    autocraft_activated_users_clear,
    collect_autocraft_status,
    collect_autocraft_logs,
    list_autocraft_plugins,
)
from ..utils import parse_int

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


class AutoCraftStatusView(BaseView):
    route_base = "/autocraft/status"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        base_dir = current_app.config.get("BASE_DIR")
        status = collect_autocraft_status(base_dir)
        logs = collect_autocraft_logs(base_dir, lines=140)
        plugins = list_autocraft_plugins(base_dir)
        return self.render_template(
            "autocraft_status.html",
            status=status,
            logs=logs,
            plugins=plugins,
            can_write=self.appbuilder.sm.has_access("can_action", self.class_permission_name),
        )

    @expose("/data")
    @has_access_api
    @permission_name("list")
    def data(self):
        base_dir = current_app.config.get("BASE_DIR")
        return jsonify(collect_autocraft_status(base_dir))

    @expose("/logs")
    @has_access_api
    @permission_name("list")
    def logs(self):
        base_dir = current_app.config.get("BASE_DIR")
        lines = parse_int(request.args.get("lines"), 140)
        lines = max(20, min(lines, 800))
        return jsonify(collect_autocraft_logs(base_dir, lines=lines))

    @expose("/plugins")
    @has_access_api
    @permission_name("list")
    def plugins(self):
        base_dir = current_app.config.get("BASE_DIR")
        return jsonify(list_autocraft_plugins(base_dir))

    @expose("/activated-users/clear", methods=["POST"])
    @has_access
    @permission_name("action")
    def clear_activated_users(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoCraftStatusView.list"))

        base_dir = current_app.config.get("BASE_DIR")
        result = autocraft_activated_users_clear(base_dir)
        if result.get("ok"):
            flash(result.get("stdout") or "База пользователей очищена.", "success")
        else:
            flash(result.get("stderr") or "Не удалось очистить базу пользователей.", "danger")
        return redirect(url_for("AutoCraftStatusView.list"))
