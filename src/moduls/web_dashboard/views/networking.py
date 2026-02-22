import psutil
from flask_appbuilder import BaseView, expose
from ..security import panel_has_access as has_access


class NetworkingView(BaseView):
    route_base = "/networking"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    def list(self):
        interfaces = []
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()

        for name, addr_list in addrs.items():
            iface = {
                "name": name,
                "isup": stats.get(name).isup if name in stats else False,
                "speed": stats.get(name).speed if name in stats else 0,
                "addresses": [],
            }
            for addr in addr_list:
                iface["addresses"].append(str(addr.address))
            interfaces.append(iface)

        return self.render_template("networking.html", interfaces=interfaces)
