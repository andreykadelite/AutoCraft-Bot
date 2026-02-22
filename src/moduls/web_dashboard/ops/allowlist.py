from .operations.services import list_services, start_service, stop_service, restart_service
from .operations.eventlog import query_event_logs_op
from .operations.processes import list_processes, kill_process
from .operations.metrics import collect_metrics
from .operations.networking import list_interfaces, disable_interface
from .operations.tasks import list_tasks, run_task
from .operations.device_manager import (
    enable_device,
    disable_device,
    restart_device,
    rescan_devices,
)
from .operations.autocraft import (
    autocraft_start,
    autocraft_stop,
    autocraft_kill,
    autocraft_restart_full,
    autocraft_api_start,
    autocraft_api_stop,
    autocraft_api_restart,
    autocraft_plugins_scan,
    autocraft_plugins_reload,
    autocraft_autorun_enable,
    autocraft_autorun_disable,
    autocraft_autorun_configure,
    autocraft_bot_check,
    autocraft_bot_settings_save,
    autocraft_local_api_settings_save,
)
from .operations.registry import (
    registry_create_key,
    registry_delete_key,
    registry_rename_key,
    registry_import,
    registry_set_value,
    registry_delete_value,
)
from .operations.autostart import (
    autostart_folder_add,
    autostart_folder_remove,
    autostart_folder_set_enabled,
    autostart_registry_add,
    autostart_registry_remove,
    autostart_registry_set_enabled,
    autostart_task_add,
    autostart_task_remove,
    autostart_task_set_enabled,
)
from .operations.live_stream import start_stream, stop_stream
from .operations.power import (
    power_shutdown,
    power_restart,
    power_sleep,
    power_hibernate,
)

ALLOWLIST = {
    "services.list": {
        "roles": ["Super Admin", "Admin", "Operator", "Viewer"],
        "func": list_services,
    },
    "services.start": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": start_service,
    },
    "services.stop": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": stop_service,
    },
    "services.restart": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": restart_service,
    },
    "eventlog.query": {
        "roles": ["Super Admin", "Admin", "Operator", "Viewer", "Auditor"],
        "func": query_event_logs_op,
    },
    "process.list": {
        "roles": ["Super Admin", "Admin", "Operator", "Viewer"],
        "func": list_processes,
    },
    "process.kill": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": kill_process,
    },
    "metrics.collect": {
        "roles": ["Super Admin", "Admin"],
        "func": collect_metrics,
    },
    "network.interfaces.list": {
        "roles": ["Super Admin", "Admin", "Operator", "Viewer"],
        "func": list_interfaces,
    },
    "network.interface.disable": {
        "roles": ["Super Admin", "Admin"],
        "func": disable_interface,
    },
    "tasks.list": {
        "roles": ["Super Admin", "Admin", "Operator", "Viewer"],
        "func": list_tasks,
    },
    "tasks.run": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": run_task,
    },
    "autocraft.start": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_start,
    },
    "autocraft.stop": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_stop,
    },
    "autocraft.kill": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_kill,
    },
    "autocraft.restart_full": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_restart_full,
    },
    "autocraft.api.start": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_api_start,
    },
    "autocraft.api.stop": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_api_stop,
    },
    "autocraft.api.restart": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_api_restart,
    },
    "autocraft.plugins.scan": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_plugins_scan,
    },
    "autocraft.plugins.reload": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_plugins_reload,
    },
    "autocraft.autorun.enable": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_autorun_enable,
    },
    "autocraft.autorun.disable": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_autorun_disable,
    },
    "autocraft.autorun.configure": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_autorun_configure,
    },
    "autocraft.bot.check": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": autocraft_bot_check,
    },
    "autocraft.bot.settings.save": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_bot_settings_save,
    },
    "autocraft.local_api.settings.save": {
        "roles": ["Super Admin", "Admin"],
        "func": autocraft_local_api_settings_save,
    },
    "device.enable": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": enable_device,
    },
    "device.disable": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": disable_device,
    },
    "device.restart": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": restart_device,
    },
    "device.rescan": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": rescan_devices,
    },
    "registry.key.create": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_create_key,
    },
    "registry.key.delete": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_delete_key,
    },
    "registry.key.rename": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_rename_key,
    },
    "registry.value.set": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_set_value,
    },
    "registry.value.delete": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_delete_value,
    },
    "registry.import": {
        "roles": ["Super Admin", "Admin"],
        "func": registry_import,
    },
    "autostart.folder.add": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_folder_add,
    },
    "autostart.folder.remove": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_folder_remove,
    },
    "autostart.folder.set_enabled": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_folder_set_enabled,
    },
    "autostart.registry.add": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_registry_add,
    },
    "autostart.registry.remove": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_registry_remove,
    },
    "autostart.registry.set_enabled": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_registry_set_enabled,
    },
    "autostart.task.add": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_task_add,
    },
    "autostart.task.remove": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_task_remove,
    },
    "autostart.task.set_enabled": {
        "roles": ["Super Admin", "Admin"],
        "func": autostart_task_set_enabled,
    },
    "stream.start": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": start_stream,
    },
    "stream.stop": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": stop_stream,
    },
    "power.shutdown": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": power_shutdown,
    },
    "power.restart": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": power_restart,
    },
    "power.sleep": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": power_sleep,
    },
    "power.hibernate": {
        "roles": ["Super Admin", "Admin", "Operator"],
        "func": power_hibernate,
    },
}
