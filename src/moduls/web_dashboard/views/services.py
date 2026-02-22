from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..ops.base import run_operation
from ..ops.operations.services import list_services

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


class ServicesView(BaseView):
    route_base = "/services"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        result = list_services()
        services = result.get("data", []) if isinstance(result, dict) else result
        return self.render_template("services.html", services=services)

    @expose("/action/<name>/<action>", methods=["POST"])
    @has_access
    @permission_name("action")
    def action(self, name: str, action: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("ServicesView.list"))
        run_operation(
            operation=f"services.{action}",
            params={"name": name},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return redirect(url_for("ServicesView.list"))
