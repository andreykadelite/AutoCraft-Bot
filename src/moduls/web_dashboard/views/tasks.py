from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..ops.base import run_operation
from ..ops.operations.tasks import list_tasks

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


class TasksView(BaseView):
    route_base = "/tasks"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        tasks = list_tasks()
        return self.render_template("tasks.html", tasks=tasks)

    @expose("/run/<path:task_name>", methods=["POST"])
    @has_access
    @permission_name("action")
    def run_task(self, task_name: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("TasksView.list"))
        run_operation(
            operation="tasks.run",
            params={"name": task_name},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return redirect(url_for("TasksView.list"))
