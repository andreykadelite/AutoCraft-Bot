from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from ..models.audit import AuditLog


_AUDIT_RESULT_LABELS = {
    "ok": "успешно",
    "fail": "ошибка",
}


def _format_audit_result(_view, _context, model, _name):
    return _AUDIT_RESULT_LABELS.get(model.result, model.result)


class AuditView(ModelView):
    datamodel = SQLAInterface(AuditLog)
    list_columns = ["created_at", "user", "action", "target", "result", "source", "ip"]
    show_columns = list_columns + ["details"]
    label_columns = {
        "created_at": "Время",
        "user": "Пользователь",
        "action": "Действие",
        "target": "Цель",
        "result": "Результат",
        "source": "Источник",
        "ip": "IP-адрес",
        "details": "Детали",
    }
    column_formatters = {"result": _format_audit_result}
    column_formatters_detail = column_formatters
    base_permissions = ["can_list", "can_show"]
