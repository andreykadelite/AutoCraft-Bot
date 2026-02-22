from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from ..models.jobs import Job


_JOB_STATUS_LABELS = {
    "queued": "в очереди",
    "running": "выполняется",
    "success": "успешно",
    "failed": "ошибка",
}


def _format_job_status(_view, _context, model, _name):
    return _JOB_STATUS_LABELS.get(model.status, model.status)


class JobView(ModelView):
    datamodel = SQLAInterface(Job)
    list_columns = ["created_at", "operation", "status", "user", "source"]
    show_columns = list_columns + ["params", "stdout", "stderr", "started_at", "finished_at"]
    label_columns = {
        "created_at": "Создано",
        "started_at": "Начато",
        "finished_at": "Завершено",
        "operation": "Операция",
        "status": "Статус",
        "user": "Пользователь",
        "source": "Источник",
        "params": "Параметры",
        "stdout": "Вывод",
        "stderr": "Ошибки",
    }
    column_formatters = {"status": _format_job_status}
    column_formatters_detail = column_formatters
    base_permissions = ["can_list", "can_show"]
