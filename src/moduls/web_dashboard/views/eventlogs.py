from flask import current_app, request
from flask_appbuilder import BaseView, expose
from ..security import panel_has_access as has_access

from ..ops.operations.eventlog import query_event_logs


class EventLogsView(BaseView):
    route_base = "/eventlogs"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    def list(self):
        log_name = (current_app.config.get("DEFAULT_EVENT_LOG") or "System")
        log_name = self._get_arg("log", log_name)
        level = self._get_arg("level", "")
        provider = self._get_arg("provider", "")
        event_id = self._get_arg("event_id", "")
        limit = self._get_arg("limit", "20")
        try:
            limit = int(limit)
        except Exception:
            limit = 20

        logs = query_event_logs(
            log_name=log_name,
            level=level,
            provider=provider,
            event_id=event_id,
            limit=limit,
        )

        return self.render_template(
            "eventlogs.html",
            logs=logs,
            log_name=log_name,
            level=level,
            provider=provider,
            event_id=event_id,
            limit=limit,
        )

    def _get_arg(self, name: str, default: str) -> str:
        value = (request.args.get(name) or "").strip()
        return value if value else default
