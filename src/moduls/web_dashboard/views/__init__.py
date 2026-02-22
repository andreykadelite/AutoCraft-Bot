from .servers import ServerView
from .eventlogs import EventLogsView
from .services import ServicesView
from .processes import ProcessesView
from .metrics import MetricsView
from .storage import StorageView
from .networking import NetworkingView
from .tasks import TasksView
from .jobs import JobView
from .audit import AuditView
from .settings import SettingsView
from .terminal import TerminalView
from .file_manager import FileManagerView
from .remote_desktop import RemoteDesktopView
from .live_stream import LiveStreamView
from .registry_editor import RegistryEditorView
from .autostart import AutoStartView
from .autocraft_status import AutoCraftStatusView
from .autocraft_ops import AutoCraftOpsView
from .extensions import ExtensionsView
from .device_manager import DeviceManagerView
from .power import PowerView
from .communications import CommunicationCenterView
from .internal_messenger import InternalMessengerView
from .admin_broadcast import AdminBroadcastView
from .notify_center import SystemNotifyCenterView
from ..security import move_security_menu_to_admin


def register_views(appbuilder):
    category_admin = "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435"
    category_extensions = "\u0420\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u0438\u044f"
    category_functions = "\u0424\u0443\u043d\u043a\u0446\u0438\u0438"
    category_system = "\u0421\u0438\u0441\u0442\u0435\u043c\u0430"
    category_device = "\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e"
    category_monitoring = "\u041c\u043e\u043d\u0438\u0442\u043e\u0440\u0438\u043d\u0433"
    category_audit = "\u0410\u0443\u0434\u0438\u0442"
    category_autocraft = "AutoCraft"

    appbuilder.add_view(ServerView, "\u0421\u0435\u0440\u0432\u0435\u0440\u044b", category=category_admin)
    appbuilder.add_view(SettingsView, "\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", category=category_admin)
    appbuilder.add_view(AdminBroadcastView, "\u0420\u0430\u0441\u0441\u044b\u043b\u043a\u0430", category=category_admin)
    appbuilder.add_view(ExtensionsView, "\u041c\u0435\u043d\u0435\u0434\u0436\u0435\u0440 \u0440\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u0438\u0439", category=category_extensions)

    appbuilder.add_view(FileManagerView, "\u0424\u0430\u0439\u043b\u043e\u0432\u044b\u0439 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440", category=category_functions)
    appbuilder.add_view(RemoteDesktopView, "\u0423\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0439 \u0440\u0430\u0431\u043e\u0447\u0438\u0439 \u0441\u0442\u043e\u043b", category=category_functions)
    appbuilder.add_view(LiveStreamView, "\u041f\u0440\u044f\u043c\u0430\u044f \u0442\u0440\u0430\u043d\u0441\u043b\u044f\u0446\u0438\u044f", category=category_functions)

    appbuilder.add_view(EventLogsView, "\u0416\u0443\u0440\u043d\u0430\u043b\u044b Windows", category=category_system)
    appbuilder.add_view(ServicesView, "\u0421\u043b\u0443\u0436\u0431\u044b", category=category_system)
    appbuilder.add_view(ProcessesView, "\u041f\u0440\u043e\u0446\u0435\u0441\u0441\u044b", category=category_system)
    appbuilder.add_view(TasksView, "\u0417\u0430\u0434\u0430\u0447\u0438", category=category_system)
    appbuilder.add_view(TerminalView, "\u0422\u0435\u0440\u043c\u0438\u043d\u0430\u043b", category=category_system)
    appbuilder.add_view(RegistryEditorView, "\u0420\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u0440\u0435\u0435\u0441\u0442\u0440\u0430", category=category_system)
    appbuilder.add_view(AutoStartView, "\u0410\u0432\u0442\u043e\u0437\u0430\u043f\u0443\u0441\u043a", category=category_system)

    appbuilder.add_view(DeviceManagerView, "\u0414\u0438\u0441\u043f\u0435\u0442\u0447\u0435\u0440 \u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432", category=category_device)
    appbuilder.add_view(PowerView, "\u041f\u0438\u0442\u0430\u043d\u0438\u0435", category=category_device)

    appbuilder.add_view(MetricsView, "\u041c\u0435\u0442\u0440\u0438\u043a\u0438", category=category_monitoring)
    appbuilder.add_view(StorageView, "\u0425\u0440\u0430\u043d\u0438\u043b\u0438\u0449\u0435", category=category_monitoring)
    appbuilder.add_view(NetworkingView, "\u0421\u0435\u0442\u044c", category=category_monitoring)

    appbuilder.add_view(JobView, "\u0416\u0443\u0440\u043d\u0430\u043b \u0437\u0430\u0434\u0430\u043d\u0438\u0439", category=category_audit)
    appbuilder.add_view(AuditView, "\u0410\u0443\u0434\u0438\u0442", category=category_audit)

    appbuilder.add_view(AutoCraftStatusView, "\u0421\u0442\u0430\u0442\u0443\u0441 AutoCraft", category=category_autocraft)
    appbuilder.add_view(AutoCraftOpsView, "\u041e\u043f\u0435\u0440\u0430\u0446\u0438\u0438 AutoCraft", category=category_autocraft)

    appbuilder.add_view_no_menu(InternalMessengerView)
    appbuilder.add_view_no_menu(CommunicationCenterView)
    appbuilder.add_view_no_menu(SystemNotifyCenterView)

    move_security_menu_to_admin(appbuilder, category_admin)
