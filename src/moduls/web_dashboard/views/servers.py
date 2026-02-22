from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from ..models.servers import Server


class ServerView(ModelView):
    datamodel = SQLAInterface(Server)
    list_template = "server_list.html"
    list_columns = ["name", "address", "connection_method", "health", "last_seen", "tags"]
    show_columns = list_columns + ["notes"]
    edit_columns = ["name", "address", "connection_method", "health", "tags", "notes"]
    add_columns = ["name", "address", "connection_method", "health", "tags", "notes"]
    label_columns = {
        "name": "Имя",
        "address": "Адрес",
        "connection_method": "Метод подключения",
        "health": "Состояние",
        "last_seen": "Последний отклик",
        "tags": "Теги",
        "notes": "Заметки",
    }
    base_permissions = ["can_list", "can_show", "can_add", "can_edit", "can_delete"]
