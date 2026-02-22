import psutil
from flask_appbuilder import BaseView, expose
from ..security import panel_has_access as has_access


class StorageView(BaseView):
    route_base = "/storage"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    def list(self):
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append(
                    {
                        "device": part.device,
                        "mount": part.mountpoint,
                        "fstype": part.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                    }
                )
            except Exception:
                continue
        return self.render_template("storage.html", disks=disks)
